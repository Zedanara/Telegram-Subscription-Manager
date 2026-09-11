"""Granting a paying subscriber access to the closed Telegram channel.

Every failure here is logged and swallowed: by the time this runs the payment
is already confirmed and persisted, so a missing CHANNEL_ID or a Telegram
outage must never turn into a failed webhook and a retry storm.
"""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.config import settings

logger = logging.getLogger(__name__)


async def grant_channel_access(bot: Bot, telegram_id: int) -> bool:
    """Create a single-use invite link and DM it to the subscriber.

    Returns True when the link was delivered, False when it was skipped or
    failed. Never raises.
    """
    if not settings.channel_id:
        logger.warning(
            "CHANNEL_ID is not configured — skipping the channel invite for "
            "telegram_id %s. The subscription is active; invite this user manually.",
            telegram_id,
        )
        return False

    try:
        invite = await bot.create_chat_invite_link(
            chat_id=settings.channel_id,
            name=f"sub-{telegram_id}"[:32],
            member_limit=1,
        )
    except TelegramAPIError as exc:
        logger.error(
            "Could not create a channel invite link for telegram_id %s: %s",
            telegram_id,
            exc,
        )
        return False

    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=(
                "🎉 Оплата получена! Доступ в закрытый клуб активен 30 дней.\n\n"
                f"🔗 Твоя персональная ссылка на канал:\n{invite.invite_link}\n\n"
                "Ссылка одноразовая — не передавай её другим 💫"
            ),
        )
    except TelegramAPIError as exc:
        logger.error(
            "Created invite link for telegram_id %s but could not deliver it: %s",
            telegram_id,
            exc,
        )
        return False

    logger.info("Delivered single-use channel invite link to telegram_id %s", telegram_id)
    return True
