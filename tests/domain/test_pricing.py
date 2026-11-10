"""app.domain.pricing — the cutoff is midnight Europe/Warsaw, independent of
the server's local timezone, and only ever compared as an aware instant."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain import pricing
from app.domain.pricing import PRICING_TZ, format_price, get_current_price

BEFORE = pricing._PRICE_BEFORE_CUTOFF
AFTER = pricing._PRICE_AFTER_CUTOFF


def test_cutoff_is_midnight_warsaw_not_utc():
    # Oct 1 00:00 Warsaw (CEST, UTC+2) is Sep 30 22:00 UTC.
    switch = datetime(2026, 9, 30, 22, 0, tzinfo=timezone.utc)
    assert get_current_price(switch - timedelta(microseconds=1)) == BEFORE
    assert get_current_price(switch) == AFTER
    # Under the old naive/UTC comparison these two hours still showed BEFORE.
    assert get_current_price(datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)) == AFTER


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        (datetime(2026, 9, 30, 23, 59, 59, tzinfo=PRICING_TZ), BEFORE),
        (datetime(2026, 10, 1, 0, 0, tzinfo=PRICING_TZ), AFTER),
        (datetime(2026, 11, 2, 12, 0, tzinfo=PRICING_TZ), AFTER),
    ],
)
def test_price_by_warsaw_wall_clock(local, expected):
    assert get_current_price(local) == expected


def test_default_reads_the_shared_utc_clock(monkeypatch):
    monkeypatch.setattr(pricing, "utcnow", lambda: datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert get_current_price() == BEFORE
    monkeypatch.setattr(pricing, "utcnow", lambda: datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert get_current_price() == AFTER


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        get_current_price(datetime(2026, 11, 2))


def test_format_price():
    assert format_price(AFTER) == f"{AFTER} zł"
