import math
from datetime import datetime, timezone


def utcnow() -> datetime:
    """The single source of "now" for every subscription-timing computation.

    Timezone-aware UTC. Every timing-relevant column (expires_at, created_at)
    is TIMESTAMPTZ, so returning a naive value here would raise on comparison
    against rows read back from the DB — this must never be swapped for a
    bare datetime.now().
    """
    return datetime.now(timezone.utc)


_MONTHS_GENITIVE_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def days_remaining(expires_at: datetime) -> int:
    """Whole days left until expires_at, rounded up so "less than a day left"
    still reads as 1 day rather than 0 — matches the rounding already used by
    the expiration-warning job (app/jobs/expiration_warnings.py)."""
    return max(1, math.ceil((expires_at - utcnow()).total_seconds() / 86400))


def format_date_ru(dt: datetime) -> str:
    """"27 сентября" style date, for user-facing subscription messages."""
    return f"{dt.day} {_MONTHS_GENITIVE_RU[dt.month]}"


def pluralize_days_ru(n: int) -> str:
    """Correct Russian plural form of "день" for a given count."""
    if 11 <= n % 100 <= 14:
        return "дней"
    last_digit = n % 10
    if last_digit == 1:
        return "день"
    if last_digit in (2, 3, 4):
        return "дня"
    return "дней"
