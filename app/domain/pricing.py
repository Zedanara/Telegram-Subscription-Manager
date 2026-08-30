from datetime import datetime

# hotfix: temporary September pricing, Epic 5 will replace with real pricing service
_PRICE_CUTOFF = datetime(2026, 10, 1)
_PRICE_BEFORE_CUTOFF = 45
_PRICE_AFTER_CUTOFF = 55


def get_current_price() -> int:
    return _PRICE_BEFORE_CUTOFF if datetime.now() < _PRICE_CUTOFF else _PRICE_AFTER_CUTOFF
