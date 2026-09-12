from datetime import datetime, timezone


def utcnow() -> datetime:
    """The single source of "now" for every subscription-timing computation.

    Timezone-aware UTC. Every timing-relevant column (expires_at, created_at)
    is TIMESTAMPTZ, so returning a naive value here would raise on comparison
    against rows read back from the DB — this must never be swapped for a
    bare datetime.now().
    """
    return datetime.now(timezone.utc)
