"""The single source of the subscription price.

Every price a user or Stripe sees — payment screen text, button labels, the
Checkout amount, manual payment records — must come from get_current_price()
called at the moment of the user's action, and be displayed via
format_price(). Nothing outside this module may hold a price literal
(enforced by tests/test_no_price_literals.py).

A rendered Telegram message is a snapshot: a payment screen shown before the
cutoff keeps its old text and button labels forever. Handlers that act on a
tap from such a message must re-read the price rather than trust what the
message says (see app/handlers.py's stripe_checkout).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.time import utcnow

# hotfix: temporary September pricing, Epic 5 will replace with real pricing service
PRICING_TZ = ZoneInfo("Europe/Warsaw")
# Midnight Polish time, not server-local or UTC: the bot container runs in
# UTC, where a naive cutoff only switched at 02:00 Warsaw time (CEST).
_PRICE_CUTOFF = datetime(2026, 10, 1, tzinfo=PRICING_TZ)
_PRICE_BEFORE_CUTOFF = 45
_PRICE_AFTER_CUTOFF = 55
_CURRENCY_SYMBOL = "zł"


def get_current_price(now: datetime | None = None) -> int:
    """Subscription price in whole złoty at `now` (default: utcnow()).

    `now` must be timezone-aware; the comparison is between instants, so the
    server's local timezone has no influence on when the price switches.
    """
    if now is None:
        now = utcnow()
    if now.tzinfo is None:
        raise ValueError("get_current_price() needs a timezone-aware datetime")
    return _PRICE_BEFORE_CUTOFF if now < _PRICE_CUTOFF else _PRICE_AFTER_CUTOFF


def format_price(price: int) -> str:
    """User-facing rendering of a price, e.g. "55 zł"."""
    return f"{price} {_CURRENCY_SYMBOL}"
