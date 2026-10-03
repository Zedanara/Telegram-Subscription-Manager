"""What a confirmed Stripe payment means for the domain.

Deliberately free of aiogram and aiohttp imports: the webhook route owns the
HTTP concerns, this module owns the user/payment/subscription side so the same
logic can be driven from anywhere later (retry job, admin tooling).
"""
import logging
from datetime import datetime, timedelta
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
    """Reuse the user's live (ACTIVE or EXPIRING) subscription if there is
    one, otherwise start a fresh PENDING one — the same shape the screenshot
    flow creates, so activation always runs through the state machine rather
    than around it.

    Reusing EXPIRING (not just ACTIVE) matters: without it, a payer who
    renews while their subscription is already in the warning window gets a
    second, brand-new subscription row instead of their existing one being
    extended — leaving the old row behind to eventually get auto-kicked out
    of the channel despite having just paid. EXPIRING -> ACTIVE and
    ACTIVE -> ACTIVE are both already-allowed transitions
    (app/domain/subscription.py), so this needs no state-machine change.
    """
    live = await SubscriptionRepository.get_active_or_expiring_for_user(
        user_id, session=session
    )
    if live is not None:
        return live
    return await SubscriptionRepository.create(
        user_id=user_id, expires_at=None, session=session
    )


def _compute_renewed_expires_at(current_expires_at: datetime | None) -> datetime:
    """New expires_at for an activation: current_expires_at (if the
    subscription being renewed is live and still in the future) plus
    SUBSCRIPTION_DAYS, otherwise just now plus SUBSCRIPTION_DAYS.

    This is genuinely additive for an on-time renewal of a live
    subscription — paying 3 days early does not cost those 3 days — while
    still falling back to a fresh 30-day grant for a brand-new subscription
    (current_expires_at is None) or a stale one whose expires_at has already
    passed.
    """
    now = utcnow()
    base = max(now, current_expires_at) if current_expires_at is not None else now
    return base + timedelta(days=SUBSCRIPTION_DAYS)


async def _activate(subscription: Subscription, session: AsyncSession) -> Subscription:
    """Move `subscription` to ACTIVE with a renewed expiry and a cleared
    reminder-tracking field.

    The one place every activation path — Stripe, the admin's manual-grant
    command, and the manual-screenshot confirm flow — converges, after
    whatever located/created the Payment and the target Subscription row, so
    they can never drift apart on how expires_at or last_warning_days_left
    get updated.
    """
    new_expires_at = _compute_renewed_expires_at(subscription.expires_at)
    activated = await SubscriptionRepository.update_status(
        subscription.id,
        SubscriptionStatus.ACTIVE,
        expires_at=new_expires_at,
        session=session,
    )
    # A fresh reminder cycle starts here, whether or not a reminder was
    # already sent in the previous one — see app/jobs/expiration_warnings.py.
    await SubscriptionRepository.set_last_warning_days_left(
        subscription.id, None, session=session
    )
    return activated


async def _activate_subscription(
    telegram_id: int,
    provider: str,
    provider_ref: str,
    amount: Decimal,
    currency: str,
) -> Subscription | None:
    """Record a payment and activate the payer's subscription.

    The single place the Stripe webhook and the admin's manual-grant command
    go through, so they can never drift apart. See confirm_manual_payment
    below for the third activation path (a manual payment confirmed from a
    pre-existing Payment row), which shares _activate()'s renewal logic but
    not this Payment-creation wrapper.

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

                subscription = await _activate(subscription, session=db)
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
        subscription.expires_at,
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


async def confirm_manual_payment(pending_subscription_id: int) -> Subscription:
    """Activate a manual (bank-transfer-screenshot) payment once the admin
    confirms it — app/handlers.py's confirm_payment callback.

    The screenshot flow (app/handlers.py's receive_screenshot) always
    creates a brand-new PENDING Subscription + Payment the moment the
    screenshot arrives, with no way to know yet whether the payer already
    has a live subscription. So at confirm time: if the payer now has a
    live (ACTIVE/EXPIRING) one, THAT row is the one that gets renewed — the
    Payment is re-pointed onto it, and the PENDING placeholder is left
    exactly as PENDING. It is never activated and never deleted: PENDING is
    not a status app/jobs/auto_kick.py, app/jobs/expiration_warnings.py or
    the /subscribers report ever query for, so an inert PENDING row is
    invisible to all of them — the least invasive way to neutralise it
    without a state-machine change (PENDING's only allowed transition is to
    ACTIVE) or a new delete code path. Otherwise the placeholder itself is
    activated, same as before this existed.

    Raises ValueError if pending_subscription_id does not exist, and
    InvalidTransitionError (app.domain.subscription) if the target
    subscription can no longer move to ACTIVE — both pre-existing
    possibilities the caller already handles.
    """
    async with get_session() as db:
        async with db.begin():
            pending = await SubscriptionRepository.get_by_id(
                pending_subscription_id, session=db
            )
            if pending is None:
                raise ValueError(f"Subscription {pending_subscription_id} not found")

            live = await SubscriptionRepository.get_active_or_expiring_for_user(
                pending.user_id, session=db
            )

            if live is not None and live.id != pending.id:
                await PaymentRepository.repoint_subscription(
                    old_subscription_id=pending.id,
                    new_subscription_id=live.id,
                    session=db,
                )
                logger.info(
                    "Manual payment on placeholder subscription %s re-pointed to "
                    "live subscription %s for user %s",
                    pending.id,
                    live.id,
                    pending.user_id,
                )

            target = live if live is not None else pending
            activated = await _activate(target, session=db)

        return activated
