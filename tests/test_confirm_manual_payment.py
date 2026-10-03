"""payment_service.confirm_manual_payment — activates a manual
(bank-transfer-screenshot) payment once the admin confirms it
(app/handlers.py's confirm_payment). Runs against a real (in-memory sqlite)
database, same pattern and caveat as tests/test_payment_service.py: this
does not exercise Postgres-specific TIMESTAMPTZ behaviour, only the
activation logic.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Payment, Subscription, SubscriptionStatus
from app.db.repositories import SubscriptionRepository, UserRepository
from app.domain.subscription import InvalidTransitionError
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


async def test_double_tap_with_no_live_subscription_does_not_extend_twice(
    sqlite_session_factory,
):
    """Case (a): the placeholder itself gets activated. A second tap (double
    click, or a retried callback after a timeout) must be rejected the same
    way an already-processed payment always is — not silently re-extend."""
    pending_id = await _seed_pending_screenshot_payment(sqlite_session_factory, 2004)

    first = await payment_service.confirm_manual_payment(pending_id)

    with pytest.raises(InvalidTransitionError):
        await payment_service.confirm_manual_payment(pending_id)

    async with sqlite_session_factory() as session:
        reloaded = await session.get(Subscription, pending_id)
    assert reloaded.expires_at == first.expires_at


async def test_double_tap_with_live_subscription_reuse_does_not_extend_twice(
    sqlite_session_factory,
):
    """Case (b): the Payment gets re-pointed onto an existing live
    subscription and THAT row is extended. A second tap must be rejected —
    the placeholder no longer owns a Payment, even though it is still
    (and stays) PENDING."""
    live = await payment_service.activate_manual_admin_grant(telegram_id=2005)
    pending_id = await _seed_pending_screenshot_payment(sqlite_session_factory, 2005)

    first = await payment_service.confirm_manual_payment(pending_id)
    assert first.id == live.id

    with pytest.raises(InvalidTransitionError):
        await payment_service.confirm_manual_payment(pending_id)

    async with sqlite_session_factory() as session:
        reloaded = await session.get(Subscription, live.id)
    assert reloaded.expires_at == first.expires_at


async def test_concurrent_double_tap_cannot_be_verified_under_sqlite(
    sqlite_session_factory,
):
    """Best-effort concurrency check, and a documented limitation rather
    than a correctness proof: SQLite has no real row-level locking, so
    get_by_id's for_update=True (SELECT ... FOR UPDATE) is a no-op here —
    confirmed by actually firing two "concurrent" confirms of the same
    placeholder below and observing that BOTH succeed. On Postgres, the
    second transaction's SELECT ... FOR UPDATE blocks until the first
    commits, then sees the no-longer-PENDING row and correctly raises
    InvalidTransitionError instead — that guarantee is verified by reading
    the code (get_by_id's with_for_update call, confirm_manual_payment's
    guard), not by this test, since sqlite cannot exercise it.

    The sequential double-tap tests above are what actually verify the
    guard logic itself works; this one only confirms the code does not
    crash either way sqlite happens to interleave it.
    """
    pending_id = await _seed_pending_screenshot_payment(sqlite_session_factory, 2006)

    results = await asyncio.gather(
        payment_service.confirm_manual_payment(pending_id),
        payment_service.confirm_manual_payment(pending_id),
        return_exceptions=True,
    )

    assert all(
        isinstance(r, Subscription) or isinstance(r, InvalidTransitionError)
        for r in results
    )
