"""What a confirmed Stripe payment means for the domain.

Deliberately free of aiogram and aiohttp imports: the webhook route owns the
HTTP concerns, this module owns the user/payment/subscription side so the same
logic can be driven from anywhere later (retry job, admin tooling).
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from app.db.models import Subscription, SubscriptionStatus
from app.db.repositories import PaymentRepository, SubscriptionRepository, UserRepository

logger = logging.getLogger(__name__)

# Value stored in payments.provider; also the idempotency key namespace.
PROVIDER = "stripe"

SUBSCRIPTION_DAYS = 30


async def _find_or_create_subscription(user_id: int) -> Subscription:
    """Renew the user's active subscription if there is one, otherwise start a
    fresh PENDING one — the same shape the screenshot flow creates, so
    activation always runs through the state machine rather than around it."""
    active = await SubscriptionRepository.get_active_for_user(user_id)
    if active is not None:
        return active
    return await SubscriptionRepository.create(user_id=user_id, expires_at=None)


async def activate_paid_checkout(
    telegram_id: int,
    session_id: str,
    amount: Decimal,
    currency: str,
) -> Subscription:
    """Record the payment and activate the payer's subscription for 30 days.

    Callers must have checked PaymentRepository.get_by_provider_ref first —
    this function is not idempotent on its own.
    """
    user = await UserRepository.get_or_create(telegram_id)
    subscription = await _find_or_create_subscription(user.id)

    payment = await PaymentRepository.create(
        subscription_id=subscription.id,
        provider=PROVIDER,
        provider_ref=session_id,
        amount=amount,
        currency=currency,
    )

    # Exactly the call the admin manual-confirm flow makes, so
    # app/domain/subscription.py stays the single place transitions are decided.
    expires_at = datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)
    subscription = await SubscriptionRepository.update_status(
        subscription.id, SubscriptionStatus.ACTIVE, expires_at=expires_at
    )

    logger.info(
        "Payment %s (%s %s, session %s) activated subscription %s for telegram_id %s until %s",
        payment.id,
        amount,
        currency,
        session_id,
        subscription.id,
        telegram_id,
        expires_at,
    )
    return subscription
