"""Every valid transition succeeds, every invalid one raises.

This is the core safety mechanism of the whole payment/kick system — the
warning job and the auto-kick job both rely on transition() to refuse an
invalid hop rather than silently corrupting a subscription's status, so
this file is deliberately exhaustive: all 5x5 = 25 (from, to) status pairs
are exercised, not just a handful of examples.

VALID_TRANSITIONS below is written independently of
app.domain.subscription.TRANSITIONS (rather than derived from it), so that
an accidental change to the state machine itself is caught as a test
failure instead of silently redefining what "valid" means.
"""
import pytest

from app.db.models import Subscription, SubscriptionStatus
from app.domain.subscription import (
    TRANSITIONS,
    InvalidTransitionError,
    can_transition,
    transition,
)

ALL_STATUSES = list(SubscriptionStatus)

# The intended business rules:
#   PENDING  -> ACTIVE     first payment confirmed
#   ACTIVE   -> ACTIVE     renewing while still active just extends it
#   ACTIVE   -> EXPIRING   the warning job, a few days before expiry
#   EXPIRING -> ACTIVE     renewed before it fully expired
#   EXPIRING -> EXPIRED    the auto-kick job, past expires_at
#   EXPIRED  -> ACTIVE     renewed after expiry but before being kicked
#   EXPIRED  -> KICKED     the auto-kick job, removed from the channel
#   KICKED   -> ACTIVE     resubscribing after being kicked
VALID_TRANSITIONS = {
    (SubscriptionStatus.PENDING, SubscriptionStatus.ACTIVE),
    (SubscriptionStatus.ACTIVE, SubscriptionStatus.ACTIVE),
    (SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING),
    (SubscriptionStatus.EXPIRING, SubscriptionStatus.ACTIVE),
    (SubscriptionStatus.EXPIRING, SubscriptionStatus.EXPIRED),
    (SubscriptionStatus.EXPIRED, SubscriptionStatus.ACTIVE),
    (SubscriptionStatus.EXPIRED, SubscriptionStatus.KICKED),
    (SubscriptionStatus.KICKED, SubscriptionStatus.ACTIVE),
}

ALL_PAIRS = [(frm, to) for frm in ALL_STATUSES for to in ALL_STATUSES]
INVALID_PAIRS = [pair for pair in ALL_PAIRS if pair not in VALID_TRANSITIONS]


def _pair_id(pair: tuple[SubscriptionStatus, SubscriptionStatus]) -> str:
    frm, to = pair
    return f"{frm.value}->{to.value}"


def _make_subscription(status: SubscriptionStatus) -> Subscription:
    return Subscription(user_id=1, status=status, expires_at=None)


def test_transitions_dict_matches_documented_business_rules():
    """Regression alarm: if TRANSITIONS in the domain module ever changes,
    this fails and forces a conscious update of the rules documented above
    (and of the two parametrized tests below) rather than an unnoticed
    drift between "what the code does" and "what was intended"."""
    actual = {(frm, to) for frm, tos in TRANSITIONS.items() for to in tos}
    assert actual == VALID_TRANSITIONS


def test_every_status_has_at_least_one_valid_outgoing_transition():
    """No status should be a dead end with no way to ever become ACTIVE
    (or move forward) again — that would be a bug, not a feature."""
    for status in ALL_STATUSES:
        outgoing = {to for (frm, to) in VALID_TRANSITIONS if frm == status}
        assert outgoing, f"{status} has no valid outgoing transition"


_SORTED_VALID = sorted(VALID_TRANSITIONS, key=str)


@pytest.mark.parametrize(
    "from_status, to_status", _SORTED_VALID, ids=[_pair_id(p) for p in _SORTED_VALID]
)
def test_valid_transition_succeeds(from_status, to_status):
    assert can_transition(from_status, to_status) is True

    sub = _make_subscription(from_status)
    result = transition(sub, to_status)

    assert sub.status == to_status
    assert result is sub


_SORTED_INVALID = sorted(INVALID_PAIRS, key=str)


@pytest.mark.parametrize(
    "from_status, to_status", _SORTED_INVALID, ids=[_pair_id(p) for p in _SORTED_INVALID]
)
def test_invalid_transition_raises(from_status, to_status):
    assert can_transition(from_status, to_status) is False

    sub = _make_subscription(from_status)
    with pytest.raises(InvalidTransitionError) as exc_info:
        transition(sub, to_status)

    assert sub.status == from_status, "status must be unchanged after a rejected transition"
    assert exc_info.value.from_status == from_status
    assert exc_info.value.to_status == to_status
