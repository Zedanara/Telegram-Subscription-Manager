"""Daily job: remove channel access for subscriptions past their expiry.

Higher consequence than the warning job (Issue #21) — this one actually
removes real people from a real Telegram channel — so every transition goes
through the domain state machine explicitly (never a direct jump to
KICKED), and a ban is always immediately followed by an unban so
KICKED -> ACTIVE (resubscribing later) stays possible. Nobody is left
permanently banned.
"""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db.models import Subscription, SubscriptionStatus
from app.db.repositories import SubscriptionRepository, UserRepository
from app.domain.subscription import InvalidTransitionError

logger = logging.getLogger(__name__)

JOB_ID = "auto_kick"

# 30 minutes after the expiration-warning job (10:00 UTC — see
# app/jobs/expiration_warnings.py), so on any given day warnings for
# subscriptions about to expire always go out before anything actually past
# its expires_at gets kicked, not the other way round.
KICK_JOB_HOUR_UTC = 10
KICK_JOB_MINUTE_UTC = 30

_KICKED_TEXT = (
    "🚫 Доступ к каналу закрыт — подписка истекла.\n\n"
    "Чтобы вернуться, оформи новую подписку через бота 💫"
)


async def _advance_to_kicked(
    subscription_id: int, current_status: SubscriptionStatus
) -> Subscription:
    """Step a subscription to KICKED one valid hop at a time.

    The warning job is expected to have already moved ACTIVE subscriptions
    to EXPIRING before they truly expire, but this must not assume that
    always happened (the warning job could have been down, or missed one) —
    so an ACTIVE subscription is walked EXPIRING -> EXPIRED -> KICKED here
    instead of skipping straight to KICKED. Each hop is its own
    update_status() call — the existing transition() function is what
    validates it, every time.
    """
    status = current_status
    if status == SubscriptionStatus.ACTIVE:
        await SubscriptionRepository.update_status(
            subscription_id, SubscriptionStatus.EXPIRING
        )
        status = SubscriptionStatus.EXPIRING
    if status == SubscriptionStatus.EXPIRING:
        await SubscriptionRepository.update_status(
            subscription_id, SubscriptionStatus.EXPIRED
        )
    return await SubscriptionRepository.update_status(
        subscription_id, SubscriptionStatus.KICKED
    )


async def _remove_from_channel(bot: Bot, telegram_id: int) -> None:
    if not settings.channel_id:
        logger.warning(
            "CHANNEL_ID is not configured — skipping the channel removal for "
            "telegram_id %s. The subscription is KICKED in the DB; remove "
            "this user from the channel manually.",
            telegram_id,
        )
        return

    try:
        # ban immediately followed by unban(only_if_banned=True): this
        # removes them now without a permanent ban, so a future invite link
        # can let them back in on KICKED -> ACTIVE.
        await bot.ban_chat_member(chat_id=settings.channel_id, user_id=telegram_id)
        await bot.unban_chat_member(
            chat_id=settings.channel_id, user_id=telegram_id, only_if_banned=True
        )
    except TelegramAPIError as exc:
        # Most commonly: they already left the channel manually. Their
        # access is already gone either way, so this is logged and treated
        # as non-fatal — it must never block the rest of the batch.
        logger.error(
            "Could not remove telegram_id %s from the channel (may have "
            "already left): %s",
            telegram_id,
            exc,
        )


async def _kick_one(bot: Bot, subscription: Subscription) -> None:
    try:
        kicked = await _advance_to_kicked(subscription.id, subscription.status)
    except InvalidTransitionError:
        # Lost a race with some other transition (e.g. a renewal) between
        # the list query and here — nothing left to kick.
        logger.info(
            "Subscription %s was no longer eligible for auto-kick by the "
            "time the job reached it — skipping",
            subscription.id,
        )
        return

    user = await UserRepository.get_by_id(kicked.user_id)
    if user is None:
        logger.error(
            "Subscription %s transitioned to KICKED but its user %s is missing",
            kicked.id,
            kicked.user_id,
        )
        return

    await _remove_from_channel(bot, user.telegram_id)

    try:
        await bot.send_message(chat_id=user.telegram_id, text=_KICKED_TEXT)
    except TelegramAPIError as exc:
        logger.error(
            "Could not deliver the access-removed notice to telegram_id %s: %s",
            user.telegram_id,
            exc,
        )


async def run_auto_kick(bot: Bot) -> None:
    """Kick every ACTIVE/EXPIRING subscription whose expires_at has passed.

    Idempotent by construction: SubscriptionRepository.list_expired() only
    ever returns ACTIVE/EXPIRING rows, so an already-KICKED subscription is
    never picked up again on a later run.
    """
    candidates = await SubscriptionRepository.list_expired()

    if not candidates:
        logger.info("Auto-kick job: nothing to kick")
        return

    for subscription in candidates:
        await _kick_one(bot, subscription)

    logger.info("Auto-kick job: processed %s subscription(s)", len(candidates))


async def _scheduled_run() -> None:
    """What the persisted job actually calls.

    Same reasoning as app/jobs/expiration_warnings.py: a live Bot can't be a
    job argument (the Postgres jobstore pickles `args`, and Bot's open
    aiohttp session doesn't survive pickling), so the job is registered with
    no args and builds/tears down its own Bot each run.
    """
    async with Bot(token=settings.bot_token) as bot:
        await run_auto_kick(bot)


def register(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(
        _scheduled_run,
        trigger="cron",
        hour=KICK_JOB_HOUR_UTC,
        minute=KICK_JOB_MINUTE_UTC,
        id=JOB_ID,
        replace_existing=True,
    )
