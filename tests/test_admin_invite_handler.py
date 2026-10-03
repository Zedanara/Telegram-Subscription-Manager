"""app.handlers.admin_invite — the admin-only /invite <telegram_id> command
that re-sends a fresh single-use channel invite link to an already-paying
subscriber, without touching Payment or Subscription rows at all.

Everything here is exercised with fakes/mocks: no real Telegram API call and
no real Bot ever gets constructed, so this is safe to run without a test bot
token or a live chat.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramAPIError

import app.handlers as handlers
from app.db.models import Subscription, SubscriptionStatus, User

NON_ADMIN_ID = handlers.ADMIN_ID + 1  # synthetic id, never a real account
CLIENT_TELEGRAM_ID = 1590739481  # developer test id, per project convention


def _make_message(user_id: int, bot) -> SimpleNamespace:
    message = SimpleNamespace()
    message.from_user = SimpleNamespace(id=user_id)
    message.bot = bot
    message.answer = AsyncMock()
    return message


def _make_command(args: str | None) -> SimpleNamespace:
    return SimpleNamespace(args=args)


def _make_bot() -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.send_message = AsyncMock()
    return bot


def _active_subscription() -> Subscription:
    return Subscription(
        id=1,
        user_id=1,
        status=SubscriptionStatus.ACTIVE,
        expires_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
    )


async def test_non_admin_is_silently_ignored(monkeypatch):
    create_link_mock = AsyncMock()
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)

    message = _make_message(NON_ADMIN_ID, _make_bot())

    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))

    create_link_mock.assert_not_called()
    message.answer.assert_not_called()


async def test_non_numeric_args_reports_usage_error(monkeypatch):
    create_link_mock = AsyncMock()
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)

    message = _make_message(handlers.ADMIN_ID, _make_bot())

    await handlers.admin_invite(message, _make_command("@not_a_number"))

    create_link_mock.assert_not_called()
    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "/invite" in text


async def test_missing_args_reports_usage_error(monkeypatch):
    create_link_mock = AsyncMock()
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)

    message = _make_message(handlers.ADMIN_ID, _make_bot())

    await handlers.admin_invite(message, _make_command(None))

    create_link_mock.assert_not_called()
    message.answer.assert_awaited_once()


async def test_user_with_no_subscription_is_refused(monkeypatch):
    """No User row at all for this telegram_id — must refuse, not crash."""
    create_link_mock = AsyncMock()
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)
    monkeypatch.setattr(
        handlers.UserRepository, "get_by_telegram_id", AsyncMock(return_value=None)
    )
    get_sub_mock = AsyncMock()
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "get_active_or_expiring_for_user", get_sub_mock
    )

    bot = _make_bot()
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))

    get_sub_mock.assert_not_called()
    create_link_mock.assert_not_called()
    bot.send_message.assert_not_called()
    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "нет активной подписки" in text


async def test_user_exists_but_has_no_active_or_expiring_subscription_is_refused(monkeypatch):
    create_link_mock = AsyncMock()
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)
    monkeypatch.setattr(
        handlers.UserRepository,
        "get_by_telegram_id",
        AsyncMock(return_value=User(id=1, telegram_id=CLIENT_TELEGRAM_ID)),
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository,
        "get_active_or_expiring_for_user",
        AsyncMock(return_value=None),
    )

    bot = _make_bot()
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))

    create_link_mock.assert_not_called()
    bot.send_message.assert_not_called()
    (text,), _ = message.answer.call_args
    assert "нет активной подписки" in text


async def test_channel_not_configured_reports_and_refuses(monkeypatch):
    create_link_mock = AsyncMock()
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)
    monkeypatch.setattr(handlers.settings, "channel_id", None)
    monkeypatch.setattr(
        handlers.UserRepository,
        "get_by_telegram_id",
        AsyncMock(return_value=User(id=1, telegram_id=CLIENT_TELEGRAM_ID)),
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository,
        "get_active_or_expiring_for_user",
        AsyncMock(return_value=_active_subscription()),
    )

    bot = _make_bot()
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))

    create_link_mock.assert_not_called()
    (text,), _ = message.answer.call_args
    assert "CHANNEL_ID не настроен" in text


async def test_invite_link_creation_failure_is_reported(monkeypatch):
    monkeypatch.setattr(handlers, "_create_invite_link", AsyncMock(return_value=None))
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)
    monkeypatch.setattr(
        handlers.UserRepository,
        "get_by_telegram_id",
        AsyncMock(return_value=User(id=1, telegram_id=CLIENT_TELEGRAM_ID)),
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository,
        "get_active_or_expiring_for_user",
        AsyncMock(return_value=_active_subscription()),
    )

    bot = _make_bot()
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))

    bot.send_message.assert_not_called()
    (text,), _ = message.answer.call_args
    assert "Не удалось создать ссылку" in text


async def test_successful_invite_sends_dm_and_confirms_to_admin(monkeypatch):
    create_link_mock = AsyncMock(return_value="https://t.me/+testlink")
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)
    monkeypatch.setattr(
        handlers.UserRepository,
        "get_by_telegram_id",
        AsyncMock(return_value=User(id=1, telegram_id=CLIENT_TELEGRAM_ID)),
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository,
        "get_active_or_expiring_for_user",
        AsyncMock(return_value=_active_subscription()),
    )

    bot = _make_bot()
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))

    create_link_mock.assert_awaited_once_with(bot, CLIENT_TELEGRAM_ID)
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == CLIENT_TELEGRAM_ID
    assert "https://t.me/+testlink" in kwargs["text"]

    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "отправлена" in text


async def test_dm_failure_falls_back_to_printing_link_for_admin(monkeypatch):
    """The subscriber never started the bot (or blocked it) — the admin must
    still get the link so Irina can forward it manually."""
    create_link_mock = AsyncMock(return_value="https://t.me/+testlink")
    monkeypatch.setattr(handlers, "_create_invite_link", create_link_mock)
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)
    monkeypatch.setattr(
        handlers.UserRepository,
        "get_by_telegram_id",
        AsyncMock(return_value=User(id=1, telegram_id=CLIENT_TELEGRAM_ID)),
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository,
        "get_active_or_expiring_for_user",
        AsyncMock(return_value=_active_subscription()),
    )

    bot = _make_bot()
    bot.send_message.side_effect = TelegramAPIError(method=None, message="Forbidden: bot can't initiate conversation")
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))

    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "не запускал бота" in text
    assert "https://t.me/+testlink" in text


async def test_invite_creates_no_payment_and_does_not_touch_subscription(monkeypatch):
    """Guard against a future change accidentally recording a Payment or
    advancing the Subscription — /invite must only ever create a link."""
    monkeypatch.setattr(handlers, "_create_invite_link", AsyncMock(return_value="https://t.me/+testlink"))
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)
    monkeypatch.setattr(
        handlers.UserRepository,
        "get_by_telegram_id",
        AsyncMock(return_value=User(id=1, telegram_id=CLIENT_TELEGRAM_ID)),
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository,
        "get_active_or_expiring_for_user",
        AsyncMock(return_value=_active_subscription()),
    )
    monkeypatch.setattr(
        handlers.PaymentRepository,
        "create",
        AsyncMock(side_effect=AssertionError("must not call PaymentRepository.create")),
    )
    for forbidden in ("create", "update_status"):
        monkeypatch.setattr(
            handlers.SubscriptionRepository,
            forbidden,
            AsyncMock(side_effect=AssertionError(f"must not call SubscriptionRepository.{forbidden}")),
        )

    message = _make_message(handlers.ADMIN_ID, _make_bot())

    # Would raise if any write path were hit.
    await handlers.admin_invite(message, _make_command(str(CLIENT_TELEGRAM_ID)))
