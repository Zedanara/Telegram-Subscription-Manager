"""Daily job: warn users whose subscription is about to expire.

Sends up to three reminders per cycle — at 3, 2 and 1 days left — tracked via
Subscription.last_warning_days_left rather than the ACTIVE -> EXPIRING status
change alone, since that transition only happens once but reminders fire on
three separate days. Deliberately stops at warning + that one transition:
removing someone from the channel (app/jobs/auto_kick.py) is a
higher-consequence action with its own careful review, and this job never
touches channel membership.
"""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import app.keyboards as kb
from app.config import settings
from app.db.models import Subscription, SubscriptionStatus
from app.db.repositories import SubscriptionRepository, UserRepository
from app.domain.subscription import InvalidTransitionError
from app.domain.time import days_remaining, format_date_ru, pluralize_days_ru

logger = logging.getLogger(__name__)

JOB_ID = "expiration_warnings"

# 10:00 UTC is late morning in Central Europe (the subscriber base's
# timezone, UTC+1/+2) — comfortably after people are awake, and clear of
# midnight UTC where unrelated batch/cron jobs conventionally cluster.
WARNING_JOB_HOUR_UTC = 10

WARNING_WINDOW_DAYS = 3

_MULTI_DAY_WARNING_TEXT = (
    "⏳ Подписка закончится через {days} {day_word} ({date}).\n\n"
    "Продли доступ через «💳 Оформить подписку» — чтобы не потерять канал 💫"
)

_LAST_DAY_WARNING_TEXT = (
    "⚠️ Завтра последний день подписки ({date}).\n\n"
    "Продли сегодня через «💳 Оформить подписку», иначе доступ к каналу закроется 💫"
)


def _warning_text(days_left: int, expires_at) -> str:
    date_str = format_date_ru(expires_at)
    if days_left <= 1:
        return _LAST_DAY_WARNING_TEXT.format(date=date_str)
    return _MULTI_DAY_WARNING_TEXT.format(
        days=days_left, day_word=pluralize_days_ru(days_left), date=date_str
    )


async def _warn_one(bot: Bot, subscription: Subscription) -> None:
    if subscription.status == SubscriptionStatus.ACTIVE:
        try:
            subscription = await SubscriptionRepository.update_status(
                subscription.id, SubscriptionStatus.EXPIRING
            )
        except InvalidTransitionError:
            # Lost a race with some other transition (e.g. a renewal) between
            # the list query and here — nothing left to warn about.
            logger.info(
                "Subscription %s was no longer ACTIVE by the time the warning "
                "job reached it — skipping",
                subscription.id,
            )
            return

    days_left = days_remaining(subscription.expires_at)
    if not 1 <= days_left <= WARNING_WINDOW_DAYS:
        # Can happen right after the ACTIVE -> EXPIRING transition above if
        # expires_at is further out than the window — list_expiring_within
        # already filters for this, so this is just a defensive re-check.
        return

    already_warned_for_this_or_a_later_day = (
        subscription.last_warning_days_left is not None
        and subscription.last_warning_days_left <= days_left
    )
    if already_warned_for_this_or_a_later_day:
        return

    user = await UserRepository.get_by_id(subscription.user_id)
    if user is None:
        logger.error(
            "Subscription %s is due a reminder but its user %s is missing",
            subscription.id,
            subscription.user_id,
        )
        return

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=_warning_text(days_left, subscription.expires_at),
            reply_markup=kb.renewal_reminder_menu,
        )
    except TelegramAPIError as exc:
        # The DM failed (e.g. the user blocked the bot) — last_warning_days_left
        # is left untouched, so this same reminder is retried on the next run
        # instead of being silently skipped forever.
        logger.error(
            "Could not deliver the expiration warning to telegram_id %s: %s",
            user.telegram_id,
            exc,
        )
        return

    await SubscriptionRepository.set_last_warning_days_left(subscription.id, days_left)


async def run_expiration_warnings(bot: Bot) -> None:
    """Warn every ACTIVE/EXPIRING subscription with 1-3 days left, moving
    ACTIVE ones to EXPIRING via the domain state machine.

    Idempotent per day via last_warning_days_left: a subscription already
    warned for a given days-left count (or a smaller one) this cycle is
    skipped, so a second run the same day — or a run that jumps straight from
    3 days left to 1 after missing a day — never sends a duplicate or a
    backlog of reminders.
    """
    candidates = await SubscriptionRepository.list_expiring_within(WARNING_WINDOW_DAYS)

    if not candidates:
        logger.info("Expiration warning job: nothing to warn")
        return

    for subscription in candidates:
        await _warn_one(bot, subscription)

    logger.info("Expiration warning job: processed %s subscription(s)", len(candidates))


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
