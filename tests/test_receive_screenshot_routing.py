"""Regression test for a real bug: a genuine bank-transfer screenshot sent
as an uncompressed file (message.document with an image mime type, e.g. via
Telegram Desktop's "send without compression") was rejected with the
generic "please send an actual photo" error, because the handler's filter
only checked F.photo and never looked at message.document at all.

This bug lives in the ROUTER'S FILTER MATCHING, not in receive_screenshot's
function body — calling the handler directly (like the admin_activate
tests do) would never have caught it, since that bypasses the filter
entirely. So this exercises the real aiogram Dispatcher + the project's
real router, the same way a live update would be routed, with only the DB
repositories and the Telegram API call mocked out.

Confirmed live (see PR description / commit message) against a real
Dispatcher with a real Postgres-backed run before this fix existed, and
again after, using the exact same message shapes as here.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Document, Message, MessageOriginUser, PhotoSize, Update, User

import app.handlers as handlers

BOT_TOKEN = "123456789:AAEaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # syntactically valid, never called live
CHAT_ID = 555000111
FROM_USER = User(id=CHAT_ID, is_bot=False, first_name="Client")
CHAT = Chat(id=CHAT_ID, type="private")

_next_id = 1


def _next_message_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


@pytest_asyncio.fixture
async def dispatcher(monkeypatch):
    """A real Dispatcher with the project's real router, DB repositories
    mocked (no DB needed) and outgoing Telegram API calls captured instead
    of sent (no network needed)."""
    monkeypatch.setattr(
        handlers.UserRepository, "get_or_create", AsyncMock(return_value=SimpleNamespace(id=1))
    )
    monkeypatch.setattr(
        handlers.SubscriptionRepository, "create", AsyncMock(return_value=SimpleNamespace(id=1))
    )
    monkeypatch.setattr(handlers.PaymentRepository, "create", AsyncMock())

    calls = []

    async def fake_call(self, method, **kwargs):
        calls.append(method)
        return AsyncMock()

    monkeypatch.setattr(Bot, "__call__", fake_call)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    # handlers.router is a module-level singleton already attached to a
    # Dispatcher by an earlier test in this file — aiogram refuses to attach
    # an already-attached router, so detach it first. Test-only workaround;
    # production code only ever attaches it once (see main.py).
    handlers.router._parent_router = None
    dp.include_router(handlers.router)

    key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=CHAT_ID)
    await dp.storage.set_state(key, handlers.ScreenshotState.waiting_for_screenshot)

    yield SimpleNamespace(bot=bot, dp=dp, calls=calls)

    await bot.session.close()


async def _feed(dispatcher, **message_kwargs) -> list:
    message = Message(
        message_id=_next_message_id(),
        date=datetime.now(timezone.utc),
        chat=CHAT,
        from_user=FROM_USER,
        **message_kwargs,
    )
    update = Update(update_id=_next_message_id(), message=message)
    await dispatcher.dp.feed_update(dispatcher.bot, update)
    return dispatcher.calls


def _method_names(calls) -> list[str]:
    return [type(c).__name__ for c in calls]


async def test_compressed_photo_is_accepted(dispatcher):
    calls = await _feed(
        dispatcher,
        photo=[PhotoSize(file_id="p1", file_unique_id="p1u", width=100, height=100)],
    )
    assert _method_names(calls) == ["SendPhoto", "SendMessage"]


async def test_forwarded_compressed_photo_is_accepted(dispatcher):
    """Rules out "F.photo doesn't match forwards" as a contributing cause."""
    calls = await _feed(
        dispatcher,
        photo=[PhotoSize(file_id="p2", file_unique_id="p2u", width=100, height=100)],
        forward_origin=MessageOriginUser(
            date=datetime.now(timezone.utc),
            sender_user=User(id=999, is_bot=False, first_name="Original"),
        ),
    )
    assert _method_names(calls) == ["SendPhoto", "SendMessage"]


async def test_image_sent_as_uncompressed_document_is_accepted(dispatcher):
    """The actual bug: this used to fall through to the generic rejection
    message even though it's a genuine screenshot."""
    calls = await _feed(
        dispatcher,
        document=Document(
            file_id="d1", file_unique_id="d1u", file_name="screenshot.png", mime_type="image/png"
        ),
    )
    assert _method_names(calls) == ["SendDocument", "SendMessage"]
    reply = calls[-1]
    assert "Спасибо" in reply.text


async def test_non_image_document_is_still_rejected(dispatcher):
    """A genuinely wrong attachment (e.g. a PDF) must still get the clear
    "send a photo" message, not be silently accepted."""
    calls = await _feed(
        dispatcher,
        document=Document(
            file_id="d2", file_unique_id="d2u", file_name="statement.pdf", mime_type="application/pdf"
        ),
    )
    assert _method_names(calls) == ["SendMessage"]
    assert "именно фото" in calls[0].text


async def test_plain_text_is_still_rejected(dispatcher):
    calls = await _feed(dispatcher, text="Вот")
    assert _method_names(calls) == ["SendMessage"]
    assert "именно фото" in calls[0].text
