"""What a confirmed Stripe payment means for the domain.

Deliberately free of aiogram and aiohttp imports: the webhook route owns the
HTTP concerns, this module owns the user/payment/subscription side so the same
logic can be driven from anywhere later (retry job, admin tooling).
"""
import logging
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription, SubscriptionStatus
from app.db.repositories import (
    DuplicatePaymentError,
    PaymentRepository,
    SubscriptionRepository,
    UserRepository,
)
from app.db.session import get_session
from app.domain.pricing import get_current_price
from app.domain.time import utcnow

logger = logging.getLogger(__name__)

# Value stored in payments.provider; also the idempotency key namespace.
PROVIDER = "stripe"

# Same namespace, for activations an admin grants by hand (bank transfer,
# non-EU payment rails Stripe can't support, etc.) instead of through Stripe.
PROVIDER_MANUAL_ADMIN = "manual_admin"

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


async def _activate_subscription(
    telegram_id: int,
    provider: str,
    provider_ref: str,
    amount: Decimal,
    currency: str,
) -> Subscription | None:
    """Record a payment and activate the payer's subscription for 30 days.

    The single place every activation path — the Stripe webhook, the admin's
    manual-grant command — goes through, so they can never drift apart.

    Everything happens in one transaction: the user, the subscription, the
    Payment row and the ACTIVE transition either all commit or none do, so a
    failure can never leave a recorded payment with no access behind it.

    Returns the activated Subscription, or None when another concurrent
    delivery of the same (provider, provider_ref) already recorded it — the
    unique constraint on that pair is what makes this safe.
    """
    async with get_session() as db:
        try:
            async with db.begin():
                user = await UserRepository.get_or_create(telegram_id, session=db)
                subscription = await _find_or_create_subscription(user.id, session=db)

                payment = await PaymentRepository.create(
                    subscription_id=subscription.id,
                    provider=provider,
                    provider_ref=provider_ref,
                    amount=amount,
                    currency=currency,
                    session=db,
                )

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
                "Payment %s:%s was recorded concurrently — this call activated nothing",
                provider,
                provider_ref,
            )
            return None

    logger.info(
        "Payment %s (%s %s, %s:%s) activated subscription %s for telegram_id %s until %s",
        payment_id,
        amount,
        currency,
        provider,
        provider_ref,
        subscription_id,
        telegram_id,
        expires_at,
    )
    return subscription


async def activate_paid_checkout(
    telegram_id: int,
    session_id: str,
    amount: Decimal,
    currency: str,
) -> Subscription | None:
    """Activate a subscription paid for through Stripe Checkout.

    Returns the activated Subscription, or None when another concurrent
    delivery of the same checkout session already recorded it.
    """
    return await _activate_subscription(telegram_id, PROVIDER, session_id, amount, currency)


async def activate_manual_admin_grant(telegram_id: int) -> Subscription | None:
    """Activate a subscription for a client who paid outside Stripe entirely
    (bank transfer, a payment rail Stripe can't support) and whom an admin is
    granting access to by hand — see app/handlers.py's /activate command.

    provider_ref is a fresh uuid4 per call, so repeated activations for the
    same client never collide against the (provider, provider_ref) unique
    constraint the way replaying the same Stripe session id would.
    """
    provider_ref = f"manual-admin-{uuid4()}"
    amount = Decimal(get_current_price())
    return await _activate_subscription(
        telegram_id, PROVIDER_MANUAL_ADMIN, provider_ref, amount, "PLN"
    )
