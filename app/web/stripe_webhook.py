"""HTTP entry point for Stripe webhooks.

Stripe confirms payments by calling us, not by us polling it, so the bot
process serves this tiny aiohttp app next to the Telegram long polling loop
(see main.py). Only the single Stripe route lives here — nothing else should
be exposed on this port.
"""
import asyncio
import logging

import stripe
from aiogram import Bot
from aiohttp import web

from app.config import settings
from app.db.repositories import PaymentRepository

logger = logging.getLogger(__name__)

STRIPE_WEBHOOK_PATH = "/webhook/stripe"

# Value stored in payments.provider; also the idempotency key namespace.
PROVIDER = "stripe"

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

    session = event["data"]["object"]
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
