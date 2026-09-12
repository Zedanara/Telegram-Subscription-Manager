"""HTTP entry point for Stripe webhooks.

Stripe confirms payments by calling us, not by us polling it, so the bot
process serves this tiny aiohttp app next to the Telegram long polling loop
(see main.py). Only the single Stripe route lives here — nothing else should
be exposed on this port.
"""
import asyncio
import logging
from decimal import Decimal

import stripe
from aiogram import Bot
from aiohttp import web

from app.config import settings
from app.db.repositories import PaymentRepository
from app.domain.pricing import get_current_price
from app.services.channel_access import confirm_and_grant_access
from app.services.payment_service import PROVIDER, activate_paid_checkout

logger = logging.getLogger(__name__)

STRIPE_WEBHOOK_PATH = "/webhook/stripe"

# Everything else Stripe may send is acknowledged and ignored.
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
    }
)

# Handlers reach the Bot instance through the app, so the webhook never has to
# build a second Bot (and a second aiohttp session) of its own.
BOT_KEY = web.AppKey("bot", Bot)


def _extract_telegram_id(session: dict) -> int | None:
    """The payer's telegram_id, set as client_reference_id when the Checkout
    Session was created (see app/services/stripe_service.py)."""
    raw = session.get("client_reference_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _extract_amount(session: dict) -> tuple[Decimal, str]:
    """Stripe reports totals in minor units (grosze for PLN)."""
    currency = (session.get("currency") or "pln").upper()
    amount_total = session.get("amount_total")
    if amount_total is None:
        logger.warning(
            "Checkout session %s has no amount_total — falling back to the current price",
            session.get("id"),
        )
        return Decimal(get_current_price()), currency
    return Decimal(amount_total) / 100, currency


def _verify_event(payload: bytes, signature: str) -> stripe.Event:
    """Return the verified Stripe event, or raise. There is deliberately no
    branch here that accepts an unverified payload — anything reaching the
    caller has been signed with our webhook secret."""
    return stripe.Webhook.construct_event(
        payload, signature, settings.stripe_webhook_secret
    )


async def handle_stripe_webhook(request: web.Request) -> web.Response:
    if not settings.stripe_webhook_secret:
        # Misconfiguration, not a bad request: 500 makes Stripe retry once the
        # secret is in place instead of silently dropping a real payment.
        logger.critical(
            "STRIPE_WEBHOOK_SECRET is not configured — refusing to handle webhooks"
        )
        return web.Response(status=500, text="webhook secret not configured")

    payload = await request.read()
    signature = request.headers.get("Stripe-Signature", "")

    try:
        event = _verify_event(payload, signature)
    except ValueError:
        logger.warning("Rejected Stripe webhook: payload is not valid JSON")
        return web.Response(status=400, text="invalid payload")
    except stripe.SignatureVerificationError as exc:
        logger.warning("Rejected Stripe webhook: signature verification failed (%s)", exc)
        return web.Response(status=400, text="invalid signature")

    event_id = event["id"]
    event_type = event["type"]
    logger.info("Verified Stripe event %s (%s)", event_id, event_type)

    if event_type not in HANDLED_EVENTS:
        logger.info("Ignoring unhandled Stripe event type %s", event_type)
        return web.Response(status=200, text="ignored")

    # construct_event hands back StripeObjects, which are not dicts and raise
    # AttributeError on .get() — convert once so absent keys read as None.
    session = event["data"]["object"].to_dict()
    session_id = session["id"]

    # Stripe retries on any non-2xx and may also deliver the same event twice,
    # so the checkout session id is the idempotency key: one Payment row per
    # session, ever. Acknowledge duplicates with 200 so retries stop.
    existing = await PaymentRepository.get_by_provider_ref(PROVIDER, session_id)
    if existing is not None:
        logger.info(
            "Checkout session %s already recorded as payment %s — "
            "skipping duplicate event %s (%s)",
            session_id,
            existing.id,
            event_id,
            event_type,
        )
        return web.Response(status=200, text="already processed")

    if event_type == "checkout.session.async_payment_failed":
        logger.warning(
            "Async payment failed for checkout session %s (client_reference_id=%s) — "
            "nothing activated",
            session_id,
            session.get("client_reference_id"),
        )
        return web.Response(status=200, text="payment failed")

    payment_status = session.get("payment_status")
    if event_type == "checkout.session.completed" and payment_status != "paid":
        # BLIK completes the session before the bank confirms: payment_status is
        # "unpaid" here and the real outcome arrives later as
        # async_payment_succeeded / async_payment_failed. Card sessions arrive
        # already "paid". Never treat "completed" as "paid" on its own.
        logger.info(
            "Checkout session %s completed with payment_status=%s — "
            "waiting for the async result",
            session_id,
            payment_status,
        )
        return web.Response(status=200, text="awaiting payment")

    telegram_id = _extract_telegram_id(session)
    if telegram_id is None:
        # Retrying will never produce the id, so acknowledge and shout in the log.
        logger.error(
            "Checkout session %s has client_reference_id=%r — cannot identify the payer",
            session_id,
            session.get("client_reference_id"),
        )
        return web.Response(status=200, text="unidentified payer")

    amount, currency = _extract_amount(session)

    try:
        subscription = await activate_paid_checkout(
            telegram_id, session_id, amount, currency
        )
    except Exception:
        # The whole unit of work rolled back, so nothing was half-written and
        # Stripe's retry can start cleanly from scratch. 500 makes it retry.
        logger.error(
            "Failed to activate subscription for checkout session %s (telegram_id=%s) — "
            "rolled back, awaiting Stripe retry",
            session_id,
            telegram_id,
            exc_info=True,
        )
        return web.Response(status=500, text="activation failed")

    if subscription is None:
        # A concurrent delivery won the race and is sending the confirmation.
        return web.Response(status=200, text="already processed")

    # Best effort by design: the payment is already committed, so a failed
    # confirmation is logged rather than reported back to Stripe.
    await confirm_and_grant_access(request.app[BOT_KEY], telegram_id)

    return web.Response(status=200, text="ok")


def create_webhook_app(bot: Bot) -> web.Application:
    app = web.Application()
    app[BOT_KEY] = bot
    app.router.add_post(STRIPE_WEBHOOK_PATH, handle_stripe_webhook)
    return app


async def run_webhook_server(bot: Bot) -> None:
    """Serve the webhook app until cancelled. Runs concurrently with polling."""
    runner = web.AppRunner(create_webhook_app(bot))
    await runner.setup()
    site = web.TCPSite(runner, settings.webhook_host, settings.webhook_port)
    await site.start()
    logger.info(
        "Stripe webhook listening on %s:%s%s",
        settings.webhook_host,
        settings.webhook_port,
        STRIPE_WEBHOOK_PATH,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
