import math
from datetime import datetime, timedelta

from app.domain.time import utcnow

QUESTION_COOLDOWN = timedelta(hours=1)


def minutes_until_allowed(last_at: datetime | None, cooldown: timedelta) -> int:
    """Whole minutes remaining before another action is allowed, 0 if allowed
    now (never asked before, or the cooldown has already elapsed)."""
    if last_at is None:
        return 0
    remaining = cooldown - (utcnow() - last_at)
    return max(0, math.ceil(remaining.total_seconds() / 60))


def pluralize_minutes_ru(n: int) -> str:
    """Correct Russian plural form of "минута" for a given count — mirrors
    pluralize_days_ru (app/domain/time.py)."""
    if 11 <= n % 100 <= 14:
        return "минут"
    last_digit = n % 10
    if last_digit == 1:
        return "минуту"
    if last_digit in (2, 3, 4):
        return "минуты"
    return "минут"
