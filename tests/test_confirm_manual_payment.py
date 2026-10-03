"""payment_service.confirm_manual_payment — activates a manual
(bank-transfer-screenshot) payment once the admin confirms it
(app/handlers.py's confirm_payment). Runs against a real (in-memory sqlite)
database, same pattern and caveat as tests/test_payment_service.py: this
does not exercise Postgres-specific TIMESTAMPTZ behaviour, only the
activation logic.
"""
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Payment, Subscription, SubscriptionStatus
from app.db.repositories import SubscriptionRepository, UserRepository
from app.domain.time import utcnow
from app.services import payment_service


@pytest_asyncio.fixture
async def sqlite_session_factory(monkeypatch):
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


async def _seed_pending_screenshot_payment(
    sqlite_session_factory, telegram_id: int
) -> int:
    """Mirrors app/handlers.py's receive_screenshot: a fresh PENDING
    Subscription plus a "manual" Payment pointing at it, created
    unconditionally regardless of whatever else the user already has.
    Returns the placeholder subscription's id."""
    async with sqlite_session_factory() as session:
        # Matches receive_screenshot: get_or_create, since the payer may
        # already be a known user (e.g. renewing) rather than a first-timer.
        user = await UserRepository.get_or_create(telegram_id, session=session)

        pending = Subscription(user_id=user.id, status=SubscriptionStatus.PENDING, expires_at=None)
        session.add(pending)
        await session.flush()

        session.add(
            Payment(
                subscription_id=pending.id,
                provider="manual",
                provider_ref=f"manual-{telegram_id}-1",
                amount=Decimal("55"),
                currency="PLN",
            )
        )
        await session.commit()
        return pending.id


async def test_no_live_subscription_activates_the_placeholder_directly(
    sqlite_session_factory,
):
    pending_id = await _seed_pending_screenshot_payment(sqlite_session_factory, 2001)

    activated = await payment_service.confirm_manual_payment(pending_id)

    assert activated.id == pending_id
    assert activated.status == SubscriptionStatus.ACTIVE
    assert utcnow() < activated.expires_at


async def test_live_subscription_is_reused_and_placeholder_left_inert(
    sqlite_session_factory,
):
    """The scenario this whole task is about: a payer who already has a
    live subscription sends a screenshot anyway (e.g. paying by bank
    transfer for a renewal). Confirming it must extend the LIVE row, not
    activate the placeholder as a second subscription."""
    live = await payment_service.activate_manual_admin_grant(telegram_id=2002)
    original_expires_at = live.expires_at

    pending_id = await _seed_pending_screenshot_payment(sqlite_session_factory, 2002)

    activated = await payment_service.confirm_manual_payment(pending_id)

    assert activated.id == live.id
    assert activated.id != pending_id
    expected = original_expires_at + timedelta(days=payment_service.SUBSCRIPTION_DAYS)
    assert abs((activated.expires_at - expected).total_seconds()) < 5

    async with sqlite_session_factory() as session:
        placeholder = await session.get(Subscription, pending_id)
        payment = (
            await session.execute(
                select(Payment).where(Payment.provider_ref == "manual-2002-1")
            )
        ).scalar_one()

    # Placeholder is untouched — still PENDING, never activated or deleted.
    assert placeholder.status == SubscriptionStatus.PENDING
    # The payment that was recorded against it now points at the live row.
    assert payment.subscription_id == live.id

    async with sqlite_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == live.user_id)
        )
        live_rows = [
            s
            for s in result.scalars().all()
            if s.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING)
        ]
    assert len(live_rows) == 1


async def test_confirming_while_expiring_reuses_and_resets_reminder_field(
    sqlite_session_factory,
):
    live = await payment_service.activate_manual_admin_grant(telegram_id=2003)
    async with sqlite_session_factory() as session:
        await SubscriptionRepository.update_status(
            live.id, SubscriptionStatus.EXPIRING, session=session
        )
        await SubscriptionRepository.set_last_warning_days_left(live.id, 2, session=session)
        await session.commit()

    pending_id = await _seed_pending_screenshot_payment(sqlite_session_factory, 2003)

    activated = await payment_service.confirm_manual_payment(pending_id)

    assert activated.id == live.id
    assert activated.status == SubscriptionStatus.ACTIVE
    assert activated.last_warning_days_left is None
