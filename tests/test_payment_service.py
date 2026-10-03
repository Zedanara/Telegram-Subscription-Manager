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


async def test_renewal_restarts_expires_at_from_now_rather_than_extending_it(
    sqlite_session_factory,
):
    """Documents current behaviour per the multi-day-reminders task: paying
    again while still ACTIVE restarts the 30-day clock from now, it does not
    add 30 days on top of the existing expires_at.

    Both activation calls happen moments apart in real time, so the two
    expires_at values alone would look identical either way — the test
    instead gives the existing subscription a implausibly-far-out expires_at
    (as if it still had 100 days left) before renewing. An additive renewal
    would push that another 30 days out (~130 days from now); a
    restart-from-now renewal overwrites it with ~30 days from now — the two
    outcomes are ~100 days apart, unmistakably different.
    """
    first = await payment_service.activate_manual_admin_grant(telegram_id=888)
    far_future_expires_at = first.expires_at + timedelta(days=100)

    async with sqlite_session_factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == first.id)
            .values(expires_at=far_future_expires_at)
        )
        await session.commit()

    renewed = await payment_service.activate_manual_admin_grant(telegram_id=888)

    assert renewed.expires_at < far_future_expires_at - timedelta(days=50)
    assert utcnow() + timedelta(days=29) < renewed.expires_at < utcnow() + timedelta(days=31)


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
