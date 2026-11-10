"""Regression for the 45-vs-55 report: the payment screen text, the payment
keyboard labels, the checkout-link message and the amount passed to Stripe
must all show the same price at every instant around the cutoff.

The clock is frozen at app.domain.pricing's date source. Every module under
test is imported long before the frozen instants (at collection, on the real
date), so a price cached at import time would show up as a mismatch on the
pre-cutoff case.

The stale-screen test reproduces the actual root cause: a payment screen
rendered before the cutoff, whose button is tapped after it.
"""
import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

import app.handlers as handlers
from app.domain import pricing
from app.domain.pricing import PRICING_TZ
from app.services import stripe_service

DEV_TELEGRAM_ID = 1590739481  # developer's own test id, never ADMIN_ID
BEFORE = pricing._PRICE_BEFORE_CUTOFF
AFTER = pricing._PRICE_AFTER_CUTOFF

INSTANTS = [
    pytest.param(datetime(2026, 9, 30, 23, 59, tzinfo=PRICING_TZ), BEFORE, id="sep30-23:59-warsaw"),
    pytest.param(datetime(2026, 10, 1, 0, 0, tzinfo=PRICING_TZ), AFTER, id="oct01-00:00-warsaw"),
    pytest.param(datetime(2026, 11, 2, 12, 0, tzinfo=PRICING_TZ), AFTER, id="nov02"),
]

_PRICE_IN_TEXT = re.compile(r"(\d+)\s*zł")


def _prices_in(*strings: str) -> set[int]:
    return {int(m) for s in strings for m in _PRICE_IN_TEXT.findall(s)}


@pytest.fixture
def freeze(monkeypatch):
    def _freeze(at: datetime) -> None:
        monkeypatch.setattr(pricing, "utcnow", lambda: at)
    return _freeze


@pytest.fixture
def stripe_amounts(monkeypatch) -> list[int]:
    """unit_amount of every Checkout Session the code tries to create; no
    network call is ever made."""
    amounts: list[int] = []

    def fake_create(**kwargs):
        amounts.append(kwargs["line_items"][0]["price_data"]["unit_amount"])
        return SimpleNamespace(url="https://checkout.stripe.test/cs_test")

    monkeypatch.setattr(stripe_service.stripe.checkout.Session, "create", fake_create)
    return amounts


async def _render_payment_screen() -> tuple[str, list[str]]:
    callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
    await handlers.show_payment(callback)
    (text,), kwargs = callback.message.edit_text.call_args
    labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    return text, labels


def _payment_message(text: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = text
    message.edit_text = AsyncMock()
    message.answer = AsyncMock()
    return message


async def _tap_stripe_button(message: MagicMock) -> str:
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=DEV_TELEGRAM_ID), message=message, answer=AsyncMock()
    )
    await handlers.stripe_checkout(callback)
    return message.answer.call_args.args[0]


@pytest.mark.parametrize(("at", "expected"), INSTANTS)
async def test_screen_keyboard_and_stripe_agree(at, expected, freeze, stripe_amounts):
    freeze(at)

    text, labels = await _render_payment_screen()
    reply = await _tap_stripe_button(_payment_message(text))

    assert _prices_in(text) == {expected}
    stripe_label = next(label for label in labels if "BLIK" in label)
    assert _prices_in(stripe_label) == {expected}
    assert _prices_in(*labels) == {expected}
    assert _prices_in(reply) == {expected}
    assert stripe_amounts == [expected * 100]


async def test_stale_pre_cutoff_screen_is_corrected_when_its_button_is_tapped(freeze, stripe_amounts):
    freeze(datetime(2026, 9, 30, 23, 59, tzinfo=PRICING_TZ))
    old_text, old_labels = await _render_payment_screen()
    assert _prices_in(old_text, *old_labels) == {BEFORE}

    # Weeks later the user taps the button on that same, never-updated message.
    freeze(datetime(2026, 11, 2, 12, 0, tzinfo=PRICING_TZ))
    message = _payment_message(old_text)
    reply = await _tap_stripe_button(message)

    message.edit_text.assert_awaited_once()
    (new_text,), kwargs = message.edit_text.call_args
    new_labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert _prices_in(new_text, *new_labels) == {AFTER}
    assert _prices_in(reply) == {AFTER}
    assert stripe_amounts == [AFTER * 100]


async def test_current_screen_is_not_re_edited(freeze, stripe_amounts):
    freeze(datetime(2026, 11, 2, 12, 0, tzinfo=PRICING_TZ))
    text, _ = await _render_payment_screen()
    message = _payment_message(text)

    await _tap_stripe_button(message)

    message.edit_text.assert_not_awaited()  # avoids Telegram's "message is not modified"
