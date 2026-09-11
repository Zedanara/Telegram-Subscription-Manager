"""HTTP entry point for Stripe webhooks.

Stripe confirms payments by calling us, not by us polling it, so the bot
process serves this tiny aiohttp app next to the Telegram long polling loop
(see main.py). Only the single Stripe route lives here — nothing else should
be exposed on this port.
"""
import asyncio
import logging

from aiogram import Bot
from aiohttp import web

from app.config import settings

logger = logging.getLogger(__name__)

STRIPE_WEBHOOK_PATH = "/webhook/stripe"

# Handlers reach the Bot instance through the app, so the webhook never has to
# build a second Bot (and a second aiohttp session) of its own.
BOT_KEY = web.AppKey("bot", Bot)


async def handle_stripe_webhook(request: web.Request) -> web.Response:
    payload = await request.read()
    logger.info("Received Stripe webhook (%d bytes)", len(payload))
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
