"""SubscriptionRepository.list_active_or_expiring — backs the admin
/subscribers report (app/handlers.py). Runs against a real (sqlite) session
via the db_session fixture, since the handler tests mock this method out
entirely and would never catch a wrong relationship name or a bad status
filter here.
"""
from datetime import timedelta
from decimal import Decimal

from app.db.models import Payment, Subscription, SubscriptionStatus, User
from app.db.repositories import SubscriptionRepository
from app.domain.time import utcnow


async def test_list_active_or_expiring_returns_only_active_and_expiring(db_session):
    now = utcnow()

    active_user = User(telegram_id=1)
    expiring_user = User(telegram_id=2)
    expired_user = User(telegram_id=3)
    pending_user = User(telegram_id=4)
    db_session.add_all([active_user, expiring_user, expired_user, pending_user])
    await db_session.flush()

    active_sub = Subscription(
        user_id=active_user.id, status=SubscriptionStatus.ACTIVE, expires_at=now + timedelta(days=20)
    )
    expiring_sub = Subscription(
        user_id=expiring_user.id, status=SubscriptionStatus.EXPIRING, expires_at=now + timedelta(days=2)
    )
    expired_sub = Subscription(
        user_id=expired_user.id, status=SubscriptionStatus.EXPIRED, expires_at=now - timedelta(days=1)
    )
    pending_sub = Subscription(user_id=pending_user.id, status=SubscriptionStatus.PENDING, expires_at=None)
    db_session.add_all([active_sub, expiring_sub, expired_sub, pending_sub])
    await db_session.flush()

    db_session.add(
        Payment(
            subscription_id=active_sub.id,
            provider="stripe",
            provider_ref="ref-1",
            amount=Decimal("55"),
            currency="PLN",
        )
    )
    await db_session.commit()

    results = await SubscriptionRepository.list_active_or_expiring(session=db_session)

    assert {s.id for s in results} == {active_sub.id, expiring_sub.id}

    by_id = {s.id: s for s in results}
    assert by_id[active_sub.id].user.telegram_id == 1
    assert len(by_id[active_sub.id].payments) == 1
    assert by_id[active_sub.id].payments[0].provider == "stripe"
    assert by_id[expiring_sub.id].payments == []


async def test_list_active_or_expiring_is_read_only(db_session):
    user = User(telegram_id=1)
    db_session.add(user)
    await db_session.flush()
    subscription = Subscription(
        user_id=user.id, status=SubscriptionStatus.ACTIVE, expires_at=utcnow() + timedelta(days=10)
    )
    db_session.add(subscription)
    await db_session.commit()

    await SubscriptionRepository.list_active_or_expiring(session=db_session)

    assert not db_session.in_transaction() or not db_session.dirty
    assert not db_session.new
    assert not db_session.deleted
