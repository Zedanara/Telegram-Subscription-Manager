"""Telling a paying subscriber their payment landed, and letting them in.

Every failure here is logged and swallowed: by the time this runs the payment
is already confirmed and committed, so a missing CHANNEL_ID or a Telegram
outage must never turn into a failed webhook and a retry storm.
"""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.config import settings

logger = logging.getLogger(__name__)

_CONFIRMED_WITH_LINK = (
    "🎉 Оплата получена! Доступ в закрытый клуб активен 30 дней.\n\n"
    "🔗 Твоя персональная ссылка на канал:\n{invite_link}\n\n"
    "Ссылка одноразовая — не передавай её другим 💫"
)

# No link to give yet, but the payer must never be left wondering whether the
# money arrived.
_CONFIRMED_WITHOUT_LINK = (
    "🎉 Оплата получена! Доступ в закрытый клуб активен 30 дней.\n\n"
    "Ирина добавит тебя в канал в течение дня — если ссылка не придёт, "
    "напиши ей прямо здесь 💫"
)


async def _create_invite_link(bot: Bot, telegram_id: int) -> str | None:
    if not settings.channel_id:
        logger.warning(
            "CHANNEL_ID is not configured — skipping the channel invite for "
            "telegram_id %s. The subscription is active; invite this user manually.",
            telegram_id,
        )
        return None

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
        return None

    return invite.invite_link


async def confirm_and_grant_access(bot: Bot, telegram_id: int) -> bool:
    """DM the subscriber that their payment is confirmed, with a single-use
    invite link when one can be created.

    Returns True if the message was delivered (with or without a link).
    Never raises.
    """
    invite_link = await _create_invite_link(bot, telegram_id)
    text = (
        _CONFIRMED_WITH_LINK.format(invite_link=invite_link)
        if invite_link
        else _CONFIRMED_WITHOUT_LINK
    )

    try:
        await bot.send_message(chat_id=telegram_id, text=text)
    except TelegramAPIError as exc:
        logger.error(
            "Could not deliver the payment confirmation to telegram_id %s: %s",
            telegram_id,
            exc,
        )
        return False

    logger.info(
        "Confirmed payment to telegram_id %s (invite link: %s)",
        telegram_id,
        "sent" if invite_link else "not available",
    )
    return True
