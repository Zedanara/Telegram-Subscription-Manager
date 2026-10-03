"""app.jobs.auto_kick.run_auto_kick — the daily job that removes channel
access for subscriptions past their expiry. Covers only the new safety net
added by the renewal-reuses-live-subscription fix: a user who still has
another live (ACTIVE/EXPIRING, future-dated) subscription must not be
banned or DMed when a stale row of theirs gets closed out as KICKED. The
pre-existing kick/dry-run/admin-protection behavior is exercised only as a
baseline control here, not re-verified in full — it is unchanged by this
fix.

SubscriptionRepository/UserRepository are monkeypatched with simple
in-memory fakes mutating the same Subscription objects the test holds, same
approach as tests/test_expiration_warnings_job.py and for the same reason:
this job's repository calls don't thread a session through, so a real
get_session() would reach for the configured app database. No real
Telegram call is ever made.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.jobs.auto_kick as auto_kick_job
from app.db.models import Subscription, SubscriptionStatus, User
from app.domain.subscription import transition


def _make_subscription(
    sub_id: int, user_id: int, status: SubscriptionStatus, expires_at: datetime
) -> Subscription:
    return Subscription(id=sub_id, user_id=user_id, status=status, expires_at=expires_at)


@pytest.fixture
def repo_fakes(monkeypatch):
    """Wires SubscriptionRepository/UserRepository to the given state —
    set per-test via `repo_fakes.subscriptions`, `.expired`, `.users`."""
    state = SimpleNamespace(subscriptions=[], expired=[], users={})

    async def list_expired(session=None):
        return list(state.expired)

    async def update_status(subscription_id, new_status, expires_at=None, session=None):
        sub = next(s for s in state.subscriptions if s.id == subscription_id)
        transition(sub, new_status)
        if expires_at is not None:
            sub.expires_at = expires_at
        return sub

    async def get_by_id(user_id, session=None):
        return state.users.get(user_id)

    async def get_active_or_expiring_for_user(user_id, session=None):
        candidates = [
            s
            for s in state.subscriptions
            if s.user_id == user_id
            and s.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING)
        ]
        return candidates[0] if candidates else None

    monkeypatch.setattr(auto_kick_job.SubscriptionRepository, "list_expired", list_expired)
    monkeypatch.setattr(auto_kick_job.SubscriptionRepository, "update_status", update_status)
    monkeypatch.setattr(auto_kick_job.UserRepository, "get_by_id", get_by_id)
    monkeypatch.setattr(
        auto_kick_job.SubscriptionRepository,
        "get_active_or_expiring_for_user",
        get_active_or_expiring_for_user,
    )

    return state


def _make_bot() -> SimpleNamespace:
    bot = SimpleNamespace()
    bot.ban_chat_member = AsyncMock()
    bot.unban_chat_member = AsyncMock()
    bot.send_message = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="left"))
    return bot


async def test_normal_kick_bans_and_dms_when_no_other_live_subscription(
    monkeypatch, repo_fakes
):
    monkeypatch.setattr(auto_kick_job.settings, "channel_id", -100123456)
    monkeypatch.setattr(auto_kick_job.settings, "dry_run_kick", False)

    now = datetime.now(timezone.utc)
    stale = _make_subscription(1, 1, SubscriptionStatus.EXPIRING, now - timedelta(hours=1))
    repo_fakes.subscriptions = [stale]
    repo_fakes.expired = [stale]
    repo_fakes.users = {1: User(id=1, telegram_id=555)}

    bot = _make_bot()

    await auto_kick_job.run_auto_kick(bot)

    assert stale.status == SubscriptionStatus.KICKED
    bot.ban_chat_member.assert_awaited_once()
    bot.unban_chat_member.assert_awaited_once()
    bot.send_message.assert_awaited_once()


async def test_kick_skips_ban_and_dm_when_user_has_another_live_subscription(
    monkeypatch, repo_fakes
):
    monkeypatch.setattr(auto_kick_job.settings, "channel_id", -100123456)
    monkeypatch.setattr(auto_kick_job.settings, "dry_run_kick", False)

    now = datetime.now(timezone.utc)
    stale = _make_subscription(1, 1, SubscriptionStatus.EXPIRING, now - timedelta(hours=1))
    live = _make_subscription(2, 1, SubscriptionStatus.ACTIVE, now + timedelta(days=25))
    repo_fakes.subscriptions = [stale, live]
    repo_fakes.expired = [stale]
    repo_fakes.users = {1: User(id=1, telegram_id=555)}

    bot = _make_bot()

    await auto_kick_job.run_auto_kick(bot)

    # The stale row is still closed out through the state machine...
    assert stale.status == SubscriptionStatus.KICKED
    # ...but no Telegram side effect happens: the user is legitimately
    # still paying under their other (live) subscription.
    bot.ban_chat_member.assert_not_called()
    bot.unban_chat_member.assert_not_called()
    bot.send_message.assert_not_called()
    bot.get_chat_member.assert_not_called()  # never even reached the channel check


async def test_another_already_expired_subscription_does_not_block_the_kick(
    monkeypatch, repo_fakes
):
    """The safety net requires the other subscription's expires_at to be in
    the future — a second row that is ALSO stale is not a legitimate reason
    to skip the kick, just more damage from the same pre-fix bug."""
    monkeypatch.setattr(auto_kick_job.settings, "channel_id", -100123456)
    monkeypatch.setattr(auto_kick_job.settings, "dry_run_kick", False)

    now = datetime.now(timezone.utc)
    stale = _make_subscription(1, 1, SubscriptionStatus.EXPIRING, now - timedelta(hours=1))
    also_stale = _make_subscription(2, 1, SubscriptionStatus.ACTIVE, now - timedelta(minutes=5))
    repo_fakes.subscriptions = [stale, also_stale]
    repo_fakes.expired = [stale]
    repo_fakes.users = {1: User(id=1, telegram_id=555)}

    bot = _make_bot()

    await auto_kick_job.run_auto_kick(bot)

    bot.ban_chat_member.assert_awaited_once()
    bot.send_message.assert_awaited_once()


async def test_dry_run_kick_is_still_respected_when_no_other_live_subscription(
    monkeypatch, repo_fakes
):
    """Baseline control: the safety net must not change the pre-existing
    DRY_RUN_KICK behavior for the ordinary (no duplicate) case."""
    monkeypatch.setattr(auto_kick_job.settings, "channel_id", -100123456)
    monkeypatch.setattr(auto_kick_job.settings, "dry_run_kick", True)

    now = datetime.now(timezone.utc)
    stale = _make_subscription(1, 1, SubscriptionStatus.EXPIRING, now - timedelta(hours=1))
    repo_fakes.subscriptions = [stale]
    repo_fakes.expired = [stale]
    repo_fakes.users = {1: User(id=1, telegram_id=555)}

    bot = _make_bot()

    await auto_kick_job.run_auto_kick(bot)

    bot.ban_chat_member.assert_not_called()
    bot.send_message.assert_not_called()
