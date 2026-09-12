"""Daily job: warn users whose subscription is about to expire.

Deliberately stops at warning + the ACTIVE -> EXPIRING transition. Removing
someone from the channel (Issue #22) is a higher-consequence action that gets
its own sprint and its own careful review; this job never touches channel
membership.
"""
import logging
import math

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db.models import Subscription, SubscriptionStatus
from app.db.repositories import SubscriptionRepository, UserRepository
from app.domain.subscription import InvalidTransitionError
from app.domain.time import utcnow

logger = logging.getLogger(__name__)

JOB_ID = "expiration_warnings"

# 10:00 UTC is late morning in Central Europe (the subscriber base's
# timezone, UTC+1/+2) — comfortably after people are awake, and clear of
# midnight UTC where unrelated batch/cron jobs conventionally cluster.
WARNING_JOB_HOUR_UTC = 10

WARNING_WINDOW_DAYS = 3

_WARNING_TEXT = (
    "⏳ Подписка истекает через {days} дн.\n\n"
    "Чтобы не потерять доступ к каналу, продли подписку через бота 💫"
)


async def _warn_one(bot: Bot, subscription: Subscription) -> None:
    try:
        await SubscriptionRepository.update_status(
            subscription.id, SubscriptionStatus.EXPIRING
        )
    except InvalidTransitionError:
        # Lost a race with some other transition (e.g. a renewal) between the
        # list query and here — nothing left to warn about.
        logger.info(
            "Subscription %s was no longer ACTIVE by the time the warning "
            "job reached it — skipping",
            subscription.id,
        )
        return

    user = await UserRepository.get_by_id(subscription.user_id)
    if user is None:
        logger.error(
            "Subscription %s transitioned to EXPIRING but its user %s is missing",
            subscription.id,
            subscription.user_id,
        )
        return

    days_left = max(
        1, math.ceil((subscription.expires_at - utcnow()).total_seconds() / 86400)
    )
    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=_WARNING_TEXT.format(days=days_left),
        )
    except TelegramAPIError as exc:
        logger.error(
            "Could not deliver the expiration warning to telegram_id %s: %s",
            user.telegram_id,
            exc,
        )


async def run_expiration_warnings(bot: Bot) -> None:
    """Warn every ACTIVE subscription expiring within WARNING_WINDOW_DAYS and
    move it to EXPIRING via the domain state machine.

    Idempotent by construction, no last_warned_at column needed: only
    subscriptions still in ACTIVE status are picked up, and this is the only
    place anything moves ACTIVE -> EXPIRING. Once warned, a subscription is
    EXPIRING, so a second run the same day — or any later day, until a future
    sprint's auto-kick job or a renewal moves it elsewhere — skips it.
    """
    candidates = await SubscriptionRepository.list_expiring_within(WARNING_WINDOW_DAYS)
    active = [sub for sub in candidates if sub.status == SubscriptionStatus.ACTIVE]

    if not active:
        logger.info("Expiration warning job: nothing to warn")
        return

    for subscription in active:
        await _warn_one(bot, subscription)

    logger.info("Expiration warning job: warned %s subscription(s)", len(active))


async def _scheduled_run() -> None:
    """What the persisted job actually calls.

    A live Bot can't be a job argument: the Postgres jobstore pickles
    everything passed via `args`, and Bot holds an open aiohttp session that
    doesn't survive pickling — so the job is registered with no args, and
    instead builds and tears down its own Bot each run.
    """
    async with Bot(token=settings.bot_token) as bot:
        await run_expiration_warnings(bot)


def register(scheduler: AsyncIOScheduler) -> None:
    scheduler.add_job(
        _scheduled_run,
        trigger="cron",
        hour=WARNING_JOB_HOUR_UTC,
        minute=0,
        id=JOB_ID,
        replace_existing=True,
    )
