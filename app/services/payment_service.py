"""What a confirmed Stripe payment means for the domain.

Deliberately free of aiogram and aiohttp imports: the webhook route owns the
HTTP concerns, this module owns the user/payment/subscription side so the same
logic can be driven from anywhere later (retry job, admin tooling).
"""
import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription, SubscriptionStatus
from app.db.repositories import (
    DuplicatePaymentError,
    PaymentRepository,
    SubscriptionRepository,
    UserRepository,
)
from app.db.session import get_session
from app.domain.time import utcnow

logger = logging.getLogger(__name__)

# Value stored in payments.provider; also the idempotency key namespace.
PROVIDER = "stripe"

SUBSCRIPTION_DAYS = 30


async def _find_or_create_subscription(
    user_id: int, session: AsyncSession
) -> Subscription:
    """Renew the user's active subscription if there is one, otherwise start a
    fresh PENDING one — the same shape the screenshot flow creates, so
    activation always runs through the state machine rather than around it."""
    active = await SubscriptionRepository.get_active_for_user(user_id, session=session)
    if active is not None:
        return active
    return await SubscriptionRepository.create(
        user_id=user_id, expires_at=None, session=session
    )


async def activate_paid_checkout(
    telegram_id: int,
    session_id: str,
    amount: Decimal,
    currency: str,
) -> Subscription | None:
    """Record the payment and activate the payer's subscription for 30 days.

    Everything happens in one transaction: the user, the subscription, the
    Payment row and the ACTIVE transition either all commit or none do, so a
    failure can never leave a recorded payment with no access behind it.

    Returns the activated Subscription, or None when another concurrent
    delivery of the same checkout session already recorded it — the unique
    constraint on (provider, provider_ref) is what makes that safe.
    """
    async with get_session() as db:
        try:
            async with db.begin():
                user = await UserRepository.get_or_create(telegram_id, session=db)
                subscription = await _find_or_create_subscription(user.id, session=db)

                payment = await PaymentRepository.create(
                    subscription_id=subscription.id,
                    provider=PROVIDER,
                    provider_ref=session_id,
                    amount=amount,
                    currency=currency,
                    session=db,
                )

                # Exactly the call the admin manual-confirm flow makes, so
                # app/domain/subscription.py stays the single place
                # transitions are decided.
                expires_at = utcnow() + timedelta(days=SUBSCRIPTION_DAYS)
                await SubscriptionRepository.update_status(
                    subscription.id,
                    SubscriptionStatus.ACTIVE,
                    expires_at=expires_at,
                    session=db,
                )
                payment_id = payment.id
                subscription_id = subscription.id
        except DuplicatePaymentError:
            # A concurrent delivery inserted first; its transaction owns the
            # activation and ours rolled back cleanly. Nothing to do.
            logger.info(
                "Checkout session %s was recorded concurrently — "
                "this delivery activated nothing",
                session_id,
            )
            return None

    logger.info(
        "Payment %s (%s %s, session %s) activated subscription %s for telegram_id %s until %s",
        payment_id,
        amount,
        currency,
        session_id,
        subscription_id,
        telegram_id,
        expires_at,
    )
    return subscription
