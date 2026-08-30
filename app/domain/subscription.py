# Reuses the DB layer's SubscriptionStatus enum directly (same values,
# nothing to keep in sync) — this module still has zero aiogram imports
# and zero direct DB session handling; it only operates on an
# already-loaded Subscription object, the caller persists any change.
from app.db.models import Subscription, SubscriptionStatus

TRANSITIONS: dict[SubscriptionStatus, frozenset[SubscriptionStatus]] = {
    SubscriptionStatus.PENDING: frozenset({SubscriptionStatus.ACTIVE}),
    SubscriptionStatus.ACTIVE: frozenset(
        {SubscriptionStatus.EXPIRING, SubscriptionStatus.ACTIVE}
    ),
    SubscriptionStatus.EXPIRING: frozenset(
        {SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRED}
    ),
    SubscriptionStatus.EXPIRED: frozenset(
        {SubscriptionStatus.ACTIVE, SubscriptionStatus.KICKED}
    ),
    SubscriptionStatus.KICKED: frozenset({SubscriptionStatus.ACTIVE}),
}


class InvalidTransitionError(Exception):
    def __init__(
        self, from_status: SubscriptionStatus, to_status: SubscriptionStatus
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Cannot transition subscription from {from_status.value} to {to_status.value}"
        )


def can_transition(from_status: SubscriptionStatus, to_status: SubscriptionStatus) -> bool:
    return to_status in TRANSITIONS.get(from_status, frozenset())


def transition(subscription: Subscription, to_status: SubscriptionStatus) -> Subscription:
    if not can_transition(subscription.status, to_status):
        raise InvalidTransitionError(subscription.status, to_status)
    subscription.status = to_status
    return subscription
