"""app.jobs.expiration_warnings.run_expiration_warnings — the daily job that
now sends up to three reminders per cycle (3/2/1 days left), tracked via
Subscription.last_warning_days_left since the ACTIVE->EXPIRING transition
alone can only distinguish "warned" from "not warned" once per cycle.

SubscriptionRepository/UserRepository are monkeypatched with simple
in-memory fakes (mutating the same Subscription objects the test holds,
same as the real repository does within one transaction) rather than a real
session, since this job's own calls don't thread a session through and a
real `get_session()` would reach for the configured app database. Time is
mocked via app.domain.time.utcnow so "days left" is exact and controllable
across simulated runs. No real Telegram call is ever made.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError

import app.domain.time as time_module
import app.jobs.expiration_warnings as warnings_job
from app.db.models import Subscription, SubscriptionStatus, User
from app.domain.subscription import transition


class _FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> _FakeClock:
    fake = _FakeClock(datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(time_module, "utcnow", fake)
    return fake


def _make_subscription(
    sub_id: int,
    telegram_id: int,
    status: SubscriptionStatus,
    expires_at: datetime,
    last_warning_days_left: int | None = None,
) -> Subscription:
    return Subscription(
        id=sub_id,
        user_id=sub_id,
        status=status,
        expires_at=expires_at,
        last_warning_days_left=last_warning_days_left,
    )


@pytest.fixture
def repo_fakes(monkeypatch):
    """Wires SubscriptionRepository/UserRepository to the given subscription
    list, mutating the same objects the test holds — set it per-test via
    `repo_fakes.subscriptions = [...]`."""
    state = SimpleNamespace(subscriptions=[])

    async def list_expiring_within(days, session=None):
        return list(state.subscriptions)

    async def update_status(subscription_id, new_status, expires_at=None, session=None):
        sub = next(s for s in state.subscriptions if s.id == subscription_id)
        transition(sub, new_status)
        if expires_at is not None:
            sub.expires_at = expires_at
        return sub

    async def set_last_warning_days_left(subscription_id, days_left, session=None):
        sub = next(s for s in state.subscriptions if s.id == subscription_id)
        sub.last_warning_days_left = days_left
        return sub

    async def get_by_id(user_id, session=None):
        return next((User(id=s.id, telegram_id=s.user_id * 1000) for s in state.subscriptions if s.id == user_id), None)

    monkeypatch.setattr(warnings_job.SubscriptionRepository, "list_expiring_within", list_expiring_within)
    monkeypatch.setattr(warnings_job.SubscriptionRepository, "update_status", update_status)
    monkeypatch.setattr(
        warnings_job.SubscriptionRepository, "set_last_warning_days_left", set_last_warning_days_left
    )
    monkeypatch.setattr(warnings_job.UserRepository, "get_by_id", get_by_id)

    return state


def _make_bot() -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.send_message = AsyncMock()
    return bot


def _telegram_id_for(subscription: Subscription) -> int:
    return subscription.user_id * 1000


async def test_day_3_sends_once_and_transitions_to_expiring(clock, repo_fakes):
    sub = _make_subscription(1, 1, SubscriptionStatus.ACTIVE, clock.now + timedelta(days=3))
    repo_fakes.subscriptions = [sub]
    bot = _make_bot()

    await warnings_job.run_expiration_warnings(bot)

    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == _telegram_id_for(sub)
    assert "через 3 дня" in kwargs["text"]
    assert sub.status == SubscriptionStatus.EXPIRING
    assert sub.last_warning_days_left == 3


async def test_rerun_same_day_sends_nothing(clock, repo_fakes):
    sub = _make_subscription(1, 1, SubscriptionStatus.ACTIVE, clock.now + timedelta(days=3))
    repo_fakes.subscriptions = [sub]
    bot = _make_bot()

    await warnings_job.run_expiration_warnings(bot)
    await warnings_job.run_expiration_warnings(bot)

    bot.send_message.assert_awaited_once()


async def test_day_2_and_day_1_each_send_once_with_the_right_text(clock, repo_fakes):
    sub = _make_subscription(1, 1, SubscriptionStatus.ACTIVE, clock.now + timedelta(days=3))
    repo_fakes.subscriptions = [sub]
    bot = _make_bot()

    await warnings_job.run_expiration_warnings(bot)  # day 3
    assert bot.send_message.await_count == 1
    assert sub.last_warning_days_left == 3

    clock.now += timedelta(days=1)  # now 2 days left
    await warnings_job.run_expiration_warnings(bot)
    assert bot.send_message.await_count == 2
    text_day2 = bot.send_message.call_args.kwargs["text"]
    assert "через 2 дня" in text_day2
    assert sub.last_warning_days_left == 2

    clock.now += timedelta(days=1)  # now 1 day left
    await warnings_job.run_expiration_warnings(bot)
    assert bot.send_message.await_count == 3
    text_day1 = bot.send_message.call_args.kwargs["text"]
    assert "Завтра последний день подписки" in text_day1
    assert sub.last_warning_days_left == 1


async def test_jump_from_3_to_1_sends_exactly_one_message(clock, repo_fakes):
    """The job missed a day (e.g. downtime): already warned at 3, next run
    lands straight on 1 day left. Only the current (1-day) reminder goes
    out, never a backlog of the skipped 2-day one."""
    sub = _make_subscription(
        1, 1, SubscriptionStatus.EXPIRING, clock.now + timedelta(days=3), last_warning_days_left=3
    )
    repo_fakes.subscriptions = [sub]
    bot = _make_bot()

    clock.now += timedelta(days=2)  # jump straight to 1 day left
    await warnings_job.run_expiration_warnings(bot)

    bot.send_message.assert_awaited_once()
    assert "Завтра последний день подписки" in bot.send_message.call_args.kwargs["text"]
    assert sub.last_warning_days_left == 1


async def test_failed_dm_is_retried_next_run(clock, repo_fakes):
    sub = _make_subscription(1, 1, SubscriptionStatus.ACTIVE, clock.now + timedelta(days=3))
    repo_fakes.subscriptions = [sub]
    bot = _make_bot()
    bot.send_message.side_effect = TelegramAPIError(method=None, message="Forbidden: bot was blocked")

    await warnings_job.run_expiration_warnings(bot)

    assert sub.status == SubscriptionStatus.EXPIRING  # transition still happens
    assert sub.last_warning_days_left is None  # not recorded -- failed delivery
    bot.send_message.assert_awaited_once()

    bot.send_message.side_effect = None
    await warnings_job.run_expiration_warnings(bot)

    assert bot.send_message.await_count == 2
    assert sub.last_warning_days_left == 3


async def test_subscription_with_more_than_3_days_left_is_untouched(clock, repo_fakes):
    """Defensive re-check inside the job: even if a far-future subscription
    somehow showed up as a candidate, it must not be warned or mutated."""
    sub = _make_subscription(1, 1, SubscriptionStatus.ACTIVE, clock.now + timedelta(days=10))
    repo_fakes.subscriptions = [sub]
    bot = _make_bot()

    await warnings_job.run_expiration_warnings(bot)

    bot.send_message.assert_not_called()
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.last_warning_days_left is None
