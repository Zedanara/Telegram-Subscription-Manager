"""app.handlers.admin_activate — the admin-only /activate command for
clients who paid outside Stripe entirely (see app/services/payment_service.py
for the shared activation transaction this reuses).

Resolution goes through a forwarded message, not bot.get_chat(@username):
a live check against the real Telegram Bot API (not just aiogram) confirmed
getChat cannot resolve a private user's @username, even one that has just
messaged the bot — that lookup is only reliable for channels/supergroups/
bots. See app/handlers.py's _resolve_forwarded_sender docstring.

Everything here is exercised with fakes/mocks: no real Telegram API call and
no real Bot ever gets constructed, so this is safe to run without a test bot
token or a live chat.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import MessageOriginChannel, MessageOriginHiddenUser, MessageOriginUser, User

import app.handlers as handlers
from app.db.models import Subscription, SubscriptionStatus

NON_ADMIN_ID = handlers.ADMIN_ID + 1  # synthetic id, never a real account
CLIENT_TELEGRAM_ID = 1590739481  # developer test id, per project convention


def _make_message(user_id: int, bot, reply_to_message=None) -> SimpleNamespace:
    message = SimpleNamespace()
    message.from_user = SimpleNamespace(id=user_id)
    message.bot = bot
    message.reply_to_message = reply_to_message
    message.answer = AsyncMock()
    return message


def _make_forwarded(origin) -> SimpleNamespace:
    forwarded = SimpleNamespace()
    forwarded.forward_origin = origin
    return forwarded


def _user_origin(user_id: int, username: str | None, full_name: str = "Test User"):
    sender = User(id=user_id, is_bot=False, first_name=full_name, username=username)
    return MessageOriginUser(date=datetime.now(timezone.utc), sender_user=sender)


async def test_non_admin_is_silently_ignored(monkeypatch):
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)

    forwarded = _make_forwarded(_user_origin(CLIENT_TELEGRAM_ID, "someone"))
    message = _make_message(NON_ADMIN_ID, bot=SimpleNamespace(), reply_to_message=forwarded)

    await handlers.admin_activate(message)

    activate_mock.assert_not_called()
    message.answer.assert_not_called()


async def test_no_reply_prompts_usage(monkeypatch):
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)

    message = _make_message(handlers.ADMIN_ID, bot=SimpleNamespace(), reply_to_message=None)

    await handlers.admin_activate(message)

    activate_mock.assert_not_called()
    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "/activate" in text


async def test_reply_to_a_non_forwarded_message_reports_clear_error(monkeypatch):
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)

    not_forwarded = _make_forwarded(None)
    message = _make_message(handlers.ADMIN_ID, bot=SimpleNamespace(), reply_to_message=not_forwarded)

    await handlers.admin_activate(message)

    activate_mock.assert_not_called()
    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "не пересланное" in text


async def test_hidden_forward_attribution_reports_clear_error_without_crashing(monkeypatch):
    """Confirmed live: getChat can't resolve a private @username at all, so
    when the client has additionally hidden forward attribution, there is no
    way left to recover their id — this must degrade to a clear message, not
    a crash or a silent no-op."""
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)

    origin = MessageOriginHiddenUser(
        date=datetime.now(timezone.utc), sender_user_name="Скрытый Пользователь"
    )
    forwarded = _make_forwarded(origin)
    message = _make_message(handlers.ADMIN_ID, bot=SimpleNamespace(), reply_to_message=forwarded)

    await handlers.admin_activate(message)

    activate_mock.assert_not_called()
    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "Скрытый Пользователь" in text


async def test_forward_from_a_channel_reports_clear_error(monkeypatch):
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)

    from aiogram.types import Chat

    origin = MessageOriginChannel(
        date=datetime.now(timezone.utc),
        chat=Chat(id=-100123456, type="channel"),
        message_id=1,
    )
    forwarded = _make_forwarded(origin)
    message = _make_message(handlers.ADMIN_ID, bot=SimpleNamespace(), reply_to_message=forwarded)

    await handlers.admin_activate(message)

    activate_mock.assert_not_called()
    message.answer.assert_awaited_once()


async def test_admin_activate_success_reuses_shared_activation_and_grants_access(
    monkeypatch,
):
    subscription = Subscription(
        id=1,
        user_id=1,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
    )
    activate_mock = AsyncMock(return_value=subscription)
    grant_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)
    monkeypatch.setattr(handlers, "confirm_and_grant_access", grant_mock)

    bot = SimpleNamespace()
    forwarded = _make_forwarded(_user_origin(CLIENT_TELEGRAM_ID, "realuser"))
    message = _make_message(handlers.ADMIN_ID, bot=bot, reply_to_message=forwarded)

    await handlers.admin_activate(message)

    activate_mock.assert_awaited_once_with(CLIENT_TELEGRAM_ID)
    grant_mock.assert_awaited_once_with(bot, CLIENT_TELEGRAM_ID)

    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert str(CLIENT_TELEGRAM_ID) in text
    assert "realuser" in text
    assert "2026-10-27" in text


async def test_admin_activate_falls_back_to_full_name_when_sender_has_no_username(
    monkeypatch,
):
    subscription = Subscription(
        id=1, user_id=1, status=SubscriptionStatus.ACTIVE, expires_at=datetime.now(timezone.utc)
    )
    monkeypatch.setattr(
        handlers, "activate_manual_admin_grant", AsyncMock(return_value=subscription)
    )
    monkeypatch.setattr(handlers, "confirm_and_grant_access", AsyncMock(return_value=True))

    forwarded = _make_forwarded(
        _user_origin(CLIENT_TELEGRAM_ID, username=None, full_name="Иван Петров")
    )
    message = _make_message(handlers.ADMIN_ID, bot=SimpleNamespace(), reply_to_message=forwarded)

    await handlers.admin_activate(message)

    (text,), _ = message.answer.call_args
    assert "Иван Петров" in text


async def test_concurrent_activation_reports_already_activated(monkeypatch):
    """activate_manual_admin_grant returning None means a concurrent call
    already recorded it — must not crash and must not send a duplicate DM."""
    activate_mock = AsyncMock(return_value=None)
    grant_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)
    monkeypatch.setattr(handlers, "confirm_and_grant_access", grant_mock)

    forwarded = _make_forwarded(_user_origin(CLIENT_TELEGRAM_ID, "realuser"))
    message = _make_message(handlers.ADMIN_ID, bot=SimpleNamespace(), reply_to_message=forwarded)

    await handlers.admin_activate(message)

    grant_mock.assert_not_called()
    message.answer.assert_awaited_once()
