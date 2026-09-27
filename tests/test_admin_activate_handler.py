"""app.handlers.admin_activate — the admin-only /activate command for
clients who paid outside Stripe entirely (see app/services/payment_service.py
for the shared activation transaction this reuses).

Everything here is exercised with fakes/mocks: no real Telegram API call and
no real Bot ever gets constructed, so this is safe to run without a test bot
token or a live chat.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat

import app.handlers as handlers
from app.db.models import Subscription, SubscriptionStatus
from app.domain.time import utcnow

NON_ADMIN_ID = handlers.ADMIN_ID + 1  # synthetic id, never a real account


def _make_message(user_id: int, bot) -> SimpleNamespace:
    message = SimpleNamespace()
    message.from_user = SimpleNamespace(id=user_id)
    message.bot = bot
    message.answer = AsyncMock()
    return message


def _make_command(args: str | None) -> SimpleNamespace:
    return SimpleNamespace(args=args)


async def test_non_admin_is_silently_ignored(monkeypatch):
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)
    bot = SimpleNamespace(get_chat=AsyncMock())

    message = _make_message(NON_ADMIN_ID, bot)
    await handlers.admin_activate(message, _make_command("@someone"))

    bot.get_chat.assert_not_called()
    activate_mock.assert_not_called()
    message.answer.assert_not_called()


async def test_missing_username_prompts_usage(monkeypatch):
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)
    bot = SimpleNamespace(get_chat=AsyncMock())

    message = _make_message(handlers.ADMIN_ID, bot)
    await handlers.admin_activate(message, _make_command(None))

    bot.get_chat.assert_not_called()
    activate_mock.assert_not_called()
    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "/activate" in text


async def test_unresolvable_username_reports_clear_error_without_crashing(monkeypatch):
    activate_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)

    async def _get_chat(chat_id):
        raise TelegramBadRequest(
            method=GetChat(chat_id=chat_id), message="Bad Request: chat not found"
        )

    bot = SimpleNamespace(get_chat=_get_chat)
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_activate(message, _make_command("@ghost_user"))

    activate_mock.assert_not_called()
    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "ghost_user" in text
    assert "chat not found" in text


async def test_admin_activate_success_reuses_shared_activation_and_grants_access(
    monkeypatch,
):
    subscription = Subscription(
        id=1, user_id=1, status=SubscriptionStatus.ACTIVE, expires_at=utcnow()
    )
    activate_mock = AsyncMock(return_value=subscription)
    grant_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)
    monkeypatch.setattr(handlers, "confirm_and_grant_access", grant_mock)

    chat = SimpleNamespace(id=1590739481)  # developer test id, per project convention
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=chat))
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_activate(message, _make_command("@dev_test_user"))

    bot.get_chat.assert_awaited_once_with("@dev_test_user")
    activate_mock.assert_awaited_once_with(1590739481)
    grant_mock.assert_awaited_once_with(bot, 1590739481)

    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "1590739481" in text
    assert "dev_test_user" in text


async def test_admin_activate_strips_leading_at_sign_from_args(monkeypatch):
    """/activate accepts both '@name' and 'name' as the argument."""
    subscription = Subscription(
        id=1, user_id=1, status=SubscriptionStatus.ACTIVE, expires_at=utcnow()
    )
    monkeypatch.setattr(
        handlers, "activate_manual_admin_grant", AsyncMock(return_value=subscription)
    )
    monkeypatch.setattr(handlers, "confirm_and_grant_access", AsyncMock(return_value=True))

    chat = SimpleNamespace(id=1590739481)
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=chat))
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_activate(message, _make_command("dev_test_user"))

    bot.get_chat.assert_awaited_once_with("@dev_test_user")


async def test_concurrent_activation_reports_already_activated(monkeypatch):
    """activate_manual_admin_grant returning None means a concurrent call
    already recorded it — must not crash and must not send a duplicate DM."""
    activate_mock = AsyncMock(return_value=None)
    grant_mock = AsyncMock()
    monkeypatch.setattr(handlers, "activate_manual_admin_grant", activate_mock)
    monkeypatch.setattr(handlers, "confirm_and_grant_access", grant_mock)

    chat = SimpleNamespace(id=1590739481)
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=chat))
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_activate(message, _make_command("@dev_test_user"))

    grant_mock.assert_not_called()
    message.answer.assert_awaited_once()
