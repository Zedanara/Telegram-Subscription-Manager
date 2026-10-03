"""app.handlers.confirm_payment — the admin-confirms-a-manual-screenshot-payment
callback. This path activates a subscription directly (update_status) instead
of going through app.services.payment_service._activate_subscription, so the
multi-day-reminders renewal reset (last_warning_days_left -> None) had to be
added here separately too; this covers just that addition.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.handlers as handlers
from app.db.models import Subscription, SubscriptionStatus
from app.domain.subscription import InvalidTransitionError


def _make_callback(bot) -> SimpleNamespace:
    callback = SimpleNamespace()
    callback.from_user = SimpleNamespace(id=handlers.ADMIN_ID)
    callback.data = "confirm_payment:1"
    callback.bot = bot
    callback.message = SimpleNamespace(caption="original caption", edit_caption=AsyncMock())
    callback.answer = AsyncMock()
    return callback


def _make_bot() -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.send_message = AsyncMock()
    return bot


async def test_successful_confirmation_resets_last_warning_days_left(monkeypatch):
    subscription = Subscription(
        id=1, user_id=1, status=SubscriptionStatus.ACTIVE, expires_at=datetime.now(timezone.utc)
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "update_status", AsyncMock(return_value=subscription)
    )
    reset_mock = AsyncMock()
    monkeypatch.setattr(handlers.SubscriptionRepository, "set_last_warning_days_left", reset_mock)
    monkeypatch.setattr(handlers.UserRepository, "get_by_id", AsyncMock(return_value=None))

    callback = _make_callback(_make_bot())

    await handlers.confirm_payment(callback)

    reset_mock.assert_awaited_once_with(subscription.id, None)


async def test_already_processed_payment_does_not_reset(monkeypatch):
    """InvalidTransitionError (double-tap / already-confirmed) must refuse
    before the reset — there is no activation to reset anything for."""
    monkeypatch.setattr(
        handlers.SubscriptionRepository,
        "update_status",
        AsyncMock(side_effect=InvalidTransitionError(SubscriptionStatus.ACTIVE, SubscriptionStatus.ACTIVE)),
    )
    reset_mock = AsyncMock()
    monkeypatch.setattr(handlers.SubscriptionRepository, "set_last_warning_days_left", reset_mock)

    callback = _make_callback(_make_bot())

    await handlers.confirm_payment(callback)

    reset_mock.assert_not_called()
    callback.answer.assert_awaited_once()


async def test_non_admin_is_silently_ignored(monkeypatch):
    reset_mock = AsyncMock()
    monkeypatch.setattr(handlers.SubscriptionRepository, "set_last_warning_days_left", reset_mock)
    update_mock = AsyncMock()
    monkeypatch.setattr(handlers.SubscriptionRepository, "update_status", update_mock)

    callback = _make_callback(_make_bot())
    callback.from_user = SimpleNamespace(id=handlers.ADMIN_ID + 1)

    await handlers.confirm_payment(callback)

    update_mock.assert_not_called()
    reset_mock.assert_not_called()
    callback.answer.assert_awaited_once()
