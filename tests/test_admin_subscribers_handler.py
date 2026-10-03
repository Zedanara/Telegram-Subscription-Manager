"""app.handlers.admin_subscribers — the admin-only /subscribers report that
compares "who paid" against "who is actually in the channel", for the
manual legacy-member cleanup described in the channel-reconcile task.

Everything here is exercised with fakes/mocks: no real Telegram API call and
no real Bot ever gets constructed, and SubscriptionRepository.list_active_or_expiring
is monkeypatched directly, so this is safe to run without a test bot token,
a live chat, or a real database.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

import app.handlers as handlers
from app.db.models import Payment, Subscription, SubscriptionStatus, User

NON_ADMIN_ID = handlers.ADMIN_ID + 1  # synthetic id, never a real account


def _make_message(user_id: int, bot) -> SimpleNamespace:
    message = SimpleNamespace()
    message.from_user = SimpleNamespace(id=user_id)
    message.bot = bot
    message.answer = AsyncMock()
    return message


def _make_bot(get_chat_impl=None, get_chat_member_impl=None) -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.get_chat = AsyncMock(side_effect=get_chat_impl)
    bot.get_chat_member = AsyncMock(side_effect=get_chat_member_impl)
    return bot


def _make_subscription(
    sub_id: int,
    telegram_id: int,
    provider: str,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    expires_at: datetime | None = None,
) -> Subscription:
    if expires_at is None:
        expires_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
    subscription = Subscription(
        id=sub_id, user_id=sub_id, status=status, expires_at=expires_at
    )
    subscription.user = User(id=sub_id, telegram_id=telegram_id)
    subscription.payments = [
        Payment(
            id=sub_id,
            subscription_id=sub_id,
            provider=provider,
            provider_ref=f"ref-{sub_id}",
            amount=Decimal("55"),
            currency="PLN",
        )
    ]
    return subscription


async def test_non_admin_is_silently_ignored(monkeypatch):
    list_mock = AsyncMock()
    monkeypatch.setattr(handlers.SubscriptionRepository, "list_active_or_expiring", list_mock)

    message = _make_message(NON_ADMIN_ID, _make_bot())

    await handlers.admin_subscribers(message)

    list_mock.assert_not_called()
    message.answer.assert_not_called()


async def test_member_and_left_rows_are_labelled_and_counted(monkeypatch):
    monkeypatch.setattr(handlers, "_SUBSCRIBERS_API_DELAY", 0)
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)

    subs = [
        _make_subscription(1, 1590739481, "stripe"),
        _make_subscription(2, 1590739482, "manual"),
    ]
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "list_active_or_expiring", AsyncMock(return_value=subs)
    )

    def get_chat_member_impl(chat_id, user_id):
        status = ChatMemberStatus.MEMBER if user_id == 1590739481 else ChatMemberStatus.LEFT
        return SimpleNamespace(status=status)

    bot = _make_bot(
        get_chat_impl=lambda telegram_id: SimpleNamespace(full_name=f"User {telegram_id}", username=None),
        get_chat_member_impl=get_chat_member_impl,
    )
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_subscribers(message)

    message.answer.assert_awaited_once()
    (text,), _ = message.answer.call_args
    assert "1590739481" in text and "канал: member" in text
    assert "1590739482" in text and "канал: left — NOT IN CHANNEL" in text
    assert "В канале: 1" in text
    assert "НЕ в канале: 1" in text
    assert "stripe: 1" in text
    assert "manual: 1" in text


async def test_kicked_status_is_also_marked_not_in_channel(monkeypatch):
    monkeypatch.setattr(handlers, "_SUBSCRIBERS_API_DELAY", 0)
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)

    subs = [_make_subscription(1, 1590739481, "manual_admin")]
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "list_active_or_expiring", AsyncMock(return_value=subs)
    )

    bot = _make_bot(
        get_chat_impl=lambda telegram_id: SimpleNamespace(full_name="User", username="user1"),
        get_chat_member_impl=lambda chat_id, user_id: SimpleNamespace(status=ChatMemberStatus.KICKED),
    )
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_subscribers(message)

    (text,), _ = message.answer.call_args
    assert "канал: kicked — NOT IN CHANNEL" in text
    assert "НЕ в канале: 1" in text


async def test_get_chat_member_api_error_is_reported_as_unknown_without_aborting(monkeypatch):
    """A per-user Telegram API failure must not blow up the whole report —
    the row is marked unknown and every other row still renders."""
    monkeypatch.setattr(handlers, "_SUBSCRIBERS_API_DELAY", 0)
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)

    subs = [
        _make_subscription(1, 1590739481, "stripe"),
        _make_subscription(2, 1590739482, "stripe"),
    ]
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "list_active_or_expiring", AsyncMock(return_value=subs)
    )

    def get_chat_member_impl(chat_id, user_id):
        if user_id == 1590739481:
            raise TelegramAPIError(method=None, message="boom")
        return SimpleNamespace(status=ChatMemberStatus.MEMBER)

    bot = _make_bot(
        get_chat_impl=lambda telegram_id: SimpleNamespace(full_name="User", username=None),
        get_chat_member_impl=get_chat_member_impl,
    )
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_subscribers(message)

    (text,), _ = message.answer.call_args
    assert "канал: неизвестно (ошибка API)" in text
    assert "канал: member" in text
    assert "В канале: 1" in text
    assert "НЕ в канале: 0" in text


async def test_get_chat_api_error_falls_back_to_numeric_id(monkeypatch):
    monkeypatch.setattr(handlers, "_SUBSCRIBERS_API_DELAY", 0)
    monkeypatch.setattr(handlers.settings, "channel_id", None)

    subs = [_make_subscription(1, 1590739481, "stripe")]
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "list_active_or_expiring", AsyncMock(return_value=subs)
    )

    bot = _make_bot(get_chat_impl=lambda telegram_id: (_ for _ in ()).throw(
        TelegramAPIError(method=None, message="user not found")
    ))
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_subscribers(message)

    (text,), _ = message.answer.call_args
    assert "1590739481" in text


async def test_channel_id_not_configured_skips_membership_column(monkeypatch):
    monkeypatch.setattr(handlers, "_SUBSCRIBERS_API_DELAY", 0)
    monkeypatch.setattr(handlers.settings, "channel_id", None)

    subs = [_make_subscription(1, 1590739481, "stripe")]
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "list_active_or_expiring", AsyncMock(return_value=subs)
    )

    bot = _make_bot(get_chat_impl=lambda telegram_id: SimpleNamespace(full_name="User", username=None))
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_subscribers(message)

    bot.get_chat_member.assert_not_called()
    (text,), _ = message.answer.call_args
    assert "CHANNEL_ID не настроен" in text
    assert "канал:" not in text
    assert "В канале:" not in text


async def test_report_makes_zero_db_writes(monkeypatch):
    """Guard against a future change accidentally adding a write: fail loudly
    if the handler ever touches a write-capable repository method."""
    monkeypatch.setattr(handlers, "_SUBSCRIBERS_API_DELAY", 0)
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)

    subs = [_make_subscription(1, 1590739481, "stripe")]
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "list_active_or_expiring", AsyncMock(return_value=subs)
    )
    for forbidden in ("create", "update_status"):
        monkeypatch.setattr(
            handlers.SubscriptionRepository,
            forbidden,
            AsyncMock(side_effect=AssertionError(f"must not call SubscriptionRepository.{forbidden}")),
        )
    monkeypatch.setattr(
        handlers.PaymentRepository,
        "create",
        AsyncMock(side_effect=AssertionError("must not call PaymentRepository.create")),
    )

    bot = _make_bot(
        get_chat_impl=lambda telegram_id: SimpleNamespace(full_name="User", username=None),
        get_chat_member_impl=lambda chat_id, user_id: SimpleNamespace(status=ChatMemberStatus.MEMBER),
    )
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_subscribers(message)  # would raise if any write path were hit


async def test_message_splitting_with_many_rows(monkeypatch):
    """With enough subscribers the report must exceed Telegram's 4096-char
    single-message limit and get sent as several messages, each within the
    limit, instead of one oversized call that Telegram would reject."""
    monkeypatch.setattr(handlers, "_SUBSCRIBERS_API_DELAY", 0)
    monkeypatch.setattr(handlers.settings, "channel_id", -100123456)

    subs = [
        _make_subscription(i, 1000000000 + i, "stripe" if i % 2 == 0 else "manual")
        for i in range(1, 61)
    ]
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "list_active_or_expiring", AsyncMock(return_value=subs)
    )

    bot = _make_bot(
        get_chat_impl=lambda telegram_id: SimpleNamespace(
            full_name=f"Subscriber Number {telegram_id}", username=f"user{telegram_id}"
        ),
        get_chat_member_impl=lambda chat_id, user_id: SimpleNamespace(status=ChatMemberStatus.MEMBER),
    )
    message = _make_message(handlers.ADMIN_ID, bot)

    await handlers.admin_subscribers(message)

    assert message.answer.await_count > 1
    for call in message.answer.call_args_list:
        (text,), _ = call
        assert len(text) <= handlers._TELEGRAM_MESSAGE_LIMIT

    # every subscriber's id must appear exactly once across all chunks
    full_text = "\n".join(call.args[0] for call in message.answer.call_args_list)
    for i in range(1, 61):
        assert str(1000000000 + i) in full_text


def test_split_into_chunks_never_exceeds_the_limit_or_drops_lines():
    lines = [f"line-{i}" for i in range(20)]
    chunks = handlers._split_into_chunks(lines, limit=25)

    assert all(len(chunk) <= 25 for chunk in chunks)
    assert "\n".join(chunks).split("\n") == lines
