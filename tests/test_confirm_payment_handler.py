"""app.handlers.confirm_payment — the admin-confirms-a-manual-screenshot-payment
callback. Activation itself is delegated entirely to
payment_service.confirm_manual_payment (mocked here, exercised for real in
tests/test_confirm_manual_payment.py) — this file only covers the handler's
own responsibilities: admin gating, the already-processed guard, and
notifying the subscriber/admin once activation succeeds.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.handlers as handlers
from app.db.models import Subscription, SubscriptionStatus, User
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


async def test_non_admin_is_silently_ignored(monkeypatch):
    confirm_mock = AsyncMock()
    monkeypatch.setattr(handlers, "confirm_manual_payment", confirm_mock)

    callback = _make_callback(_make_bot())
    callback.from_user = SimpleNamespace(id=handlers.ADMIN_ID + 1)

    await handlers.confirm_payment(callback)

    confirm_mock.assert_not_called()
    callback.answer.assert_awaited_once()


async def test_successful_confirmation_notifies_subscriber_and_admin(monkeypatch):
    subscription = Subscription(
        id=1, user_id=7, status=SubscriptionStatus.ACTIVE, expires_at=datetime.now(timezone.utc)
    )
    confirm_mock = AsyncMock(return_value=subscription)
    monkeypatch.setattr(handlers, "confirm_manual_payment", confirm_mock)
    monkeypatch.setattr(
        handlers.UserRepository,
        "get_by_id",
        AsyncMock(return_value=User(id=7, telegram_id=1590739481)),
    )

    bot = _make_bot()
    callback = _make_callback(bot)

    await handlers.confirm_payment(callback)

    confirm_mock.assert_awaited_once_with(1)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs["chat_id"] == 1590739481
    callback.message.edit_caption.assert_awaited_once()
    callback.answer.assert_awaited_once()


async def test_already_processed_payment_shows_alert_without_notifying_anyone(monkeypatch):
    confirm_mock = AsyncMock(
        side_effect=InvalidTransitionError(SubscriptionStatus.ACTIVE, SubscriptionStatus.ACTIVE)
    )
    monkeypatch.setattr(handlers, "confirm_manual_payment", confirm_mock)
    get_by_id_mock = AsyncMock()
    monkeypatch.setattr(handlers.UserRepository, "get_by_id", get_by_id_mock)

    bot = _make_bot()
    callback = _make_callback(bot)

    await handlers.confirm_payment(callback)

    get_by_id_mock.assert_not_called()
    bot.send_message.assert_not_called()
    callback.message.edit_caption.assert_not_called()
    callback.answer.assert_awaited_once()
    assert "уже обработана" in callback.answer.call_args.args[0]
