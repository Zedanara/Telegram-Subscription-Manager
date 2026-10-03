"""Exercises the shared activation transaction (app/services/payment_service.py)
against a real (in-memory sqlite) database, for both callers: the Stripe
checkout path and the admin manual-grant path added for clients who pay
outside Stripe entirely.

Runs against sqlite via a monkeypatched get_session — see
tests/conftest.py's db_session fixture docstring for the same caveat here:
this does not exercise Postgres-specific TIMESTAMPTZ behaviour, only the
activation transaction's logic (user/subscription/payment creation, the
state machine transition, idempotency).
"""
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Payment, Subscription, SubscriptionStatus
from app.db.repositories import SubscriptionRepository
from app.domain.time import utcnow
from app.services import payment_service


@pytest_asyncio.fixture
async def sqlite_session_factory(monkeypatch):
    """Points payment_service.get_session at a throwaway sqlite database and
    hands back its sessionmaker so tests can independently verify what was
    written."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _get_session():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(payment_service, "get_session", _get_session)

    yield session_factory

    await engine.dispose()


async def test_activate_paid_checkout_activates_subscription(sqlite_session_factory):
    subscription = await payment_service.activate_paid_checkout(
        telegram_id=111, session_id="cs_test_1", amount=Decimal("45"), currency="PLN"
    )

    assert subscription is not None
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.expires_at is not None


async def test_activate_manual_admin_grant_activates_subscription(sqlite_session_factory):
    subscription = await payment_service.activate_manual_admin_grant(telegram_id=222)

    assert subscription is not None
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.expires_at is not None


async def test_activate_manual_admin_grant_records_manual_admin_payment(
    sqlite_session_factory,
):
    subscription = await payment_service.activate_manual_admin_grant(telegram_id=333)

    async with sqlite_session_factory() as verify_session:
        result = await verify_session.execute(
            select(Payment).where(Payment.subscription_id == subscription.id)
        )
        payment = result.scalar_one()

    assert payment.provider == payment_service.PROVIDER_MANUAL_ADMIN
    assert payment.provider_ref  # non-empty, unique per activation


async def test_manual_admin_grant_and_stripe_checkout_share_the_same_activation_path(
    sqlite_session_factory,
):
    """Regression guard: both public entry points must route through the same
    _activate_subscription transaction rather than reimplementing it — if one
    of them is ever changed to bypass it, this test's assumptions about
    identical outcomes (state, expiry window) stop holding."""
    stripe_sub = await payment_service.activate_paid_checkout(
        telegram_id=444, session_id="cs_test_shared", amount=Decimal("45"), currency="PLN"
    )
    manual_sub = await payment_service.activate_manual_admin_grant(telegram_id=555)

    assert stripe_sub.status == manual_sub.status == SubscriptionStatus.ACTIVE
    # Both expire ~30 days out (SUBSCRIPTION_DAYS), not some independently
    # chosen window a parallel reimplementation could drift on.
    delta = abs((stripe_sub.expires_at - manual_sub.expires_at).total_seconds())
    assert delta < 5


async def test_activate_manual_admin_grant_renews_existing_active_subscription(
    sqlite_session_factory,
):
    """Granting twice for the same client (e.g. admin double-taps /activate)
    must renew the existing subscription, not create a second one — same
    _find_or_create_subscription behaviour the Stripe path relies on."""
    first = await payment_service.activate_manual_admin_grant(telegram_id=666)
    second = await payment_service.activate_manual_admin_grant(telegram_id=666)

    assert second.id == first.id
    assert second.status == SubscriptionStatus.ACTIVE
    assert second.expires_at >= first.expires_at


async def test_early_renewal_extends_from_the_existing_expiry_not_from_now(
    sqlite_session_factory,
):
    """Renewing early (while still ACTIVE, 3 days left — the common case of
    a payer renewing just ahead of the expiration-warning window) must not
    cost those days: the new expiry is the OLD expiry plus 30 days, not just
    now plus 30.
    """
    first = await payment_service.activate_manual_admin_grant(telegram_id=1001)
    near_future_expires_at = utcnow() + timedelta(days=3)

    async with sqlite_session_factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == first.id)
            .values(expires_at=near_future_expires_at)
        )
        await session.commit()

    renewed = await payment_service.activate_manual_admin_grant(telegram_id=1001)

    assert renewed.id == first.id
    expected = near_future_expires_at + timedelta(days=payment_service.SUBSCRIPTION_DAYS)
    assert abs((renewed.expires_at - expected).total_seconds()) < 5


async def test_renewal_after_expiry_restarts_from_now_on_a_fresh_subscription(
    sqlite_session_factory,
):
    """A user whose most recent subscription has gone EXPIRED (not live) is
    not reused — see _find_or_create_subscription — and the fresh
    subscription they get instead starts its 30 days from now, not from the
    dead row's stale expires_at."""
    first = await payment_service.activate_manual_admin_grant(telegram_id=1002)

    async with sqlite_session_factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == first.id)
            .values(status=SubscriptionStatus.EXPIRED)
        )
        await session.commit()

    renewed = await payment_service.activate_manual_admin_grant(telegram_id=1002)

    assert renewed.id != first.id
    assert renewed.status == SubscriptionStatus.ACTIVE
    assert utcnow() + timedelta(days=29) < renewed.expires_at < utcnow() + timedelta(days=31)


async def test_renewal_while_expiring_reuses_that_subscription_additively(
    sqlite_session_factory,
):
    """The fix this task is named after: a payer renewing while EXPIRING
    must extend that same row (old expiry + 30 days, exactly one live
    subscription afterward) rather than getting a second, brand-new one and
    leaving the EXPIRING row behind for auto_kick to act on despite the
    fresh payment."""
    first = await payment_service.activate_manual_admin_grant(telegram_id=1003)
    expiring_expires_at = utcnow() + timedelta(days=2)

    async with sqlite_session_factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == first.id)
            .values(status=SubscriptionStatus.EXPIRING, expires_at=expiring_expires_at)
        )
        await session.commit()

    renewed = await payment_service.activate_manual_admin_grant(telegram_id=1003)

    assert renewed.id == first.id
    assert renewed.status == SubscriptionStatus.ACTIVE
    expected = expiring_expires_at + timedelta(days=payment_service.SUBSCRIPTION_DAYS)
    assert abs((renewed.expires_at - expected).total_seconds()) < 5

    async with sqlite_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == first.user_id)
        )
        live = [
            s
            for s in result.scalars().all()
            if s.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING)
        ]
    assert len(live) == 1


async def test_renewal_resets_last_warning_days_left(sqlite_session_factory):
    """A renewal must give the next cycle all three reminders again — see
    app/jobs/expiration_warnings.py."""
    first = await payment_service.activate_manual_admin_grant(telegram_id=999)

    async with sqlite_session_factory() as session:
        await SubscriptionRepository.set_last_warning_days_left(
            first.id, 2, session=session
        )
        await session.commit()

    renewed = await payment_service.activate_manual_admin_grant(telegram_id=999)

    assert renewed.id == first.id
    assert renewed.last_warning_days_left is None
