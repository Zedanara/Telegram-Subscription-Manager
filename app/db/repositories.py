from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, Subscription, SubscriptionStatus, User
from app.db.session import get_session
from app.domain.subscription import transition
from app.domain.time import utcnow

# Name of the constraint declared on Payment; see the model for why it exists.
PAYMENT_PROVIDER_REF_CONSTRAINT = "uq_payments_provider_provider_ref"


class DuplicatePaymentError(Exception):
    """A Payment with this (provider, provider_ref) already exists.

    Raised in place of the driver's IntegrityError so callers can treat a lost
    race as "already processed" without reaching into DB internals.
    """

    def __init__(self, provider: str, provider_ref: str) -> None:
        self.provider = provider
        self.provider_ref = provider_ref
        super().__init__(f"Payment for {provider}:{provider_ref} already exists")


def _is_duplicate_provider_ref(exc: IntegrityError) -> bool:
    constraint = getattr(exc.orig, "constraint_name", None)
    if constraint is not None:
        return constraint == PAYMENT_PROVIDER_REF_CONSTRAINT
    return PAYMENT_PROVIDER_REF_CONSTRAINT in str(exc.orig)


@asynccontextmanager
async def _session_scope(
    session: AsyncSession | None,
) -> AsyncIterator[tuple[AsyncSession, bool]]:
    """Yield (session, owned).

    When the caller supplies a session we join their transaction and leave
    commit/rollback entirely to them, so several repository calls can make up a
    single unit of work. With no session we open and commit our own, which is
    the original per-call behaviour every existing caller relies on.
    """
    if session is not None:
        yield session, False
    else:
        async with get_session() as own_session:
            yield own_session, True


class UserRepository:
    @staticmethod
    async def get_by_telegram_id(
        telegram_id: int, session: AsyncSession | None = None
    ) -> User | None:
        async with _session_scope(session) as (db, _):
            result = await db.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def create(telegram_id: int, session: AsyncSession | None = None) -> User:
        async with _session_scope(session) as (db, owned):
            user = User(telegram_id=telegram_id)
            db.add(user)
            if owned:
                await db.commit()
                await db.refresh(user)
            else:
                await db.flush()  # assigns the PK without ending the transaction
            return user

    @staticmethod
    async def get_or_create(
        telegram_id: int, session: AsyncSession | None = None
    ) -> User:
        user = await UserRepository.get_by_telegram_id(telegram_id, session=session)
        if user is not None:
            return user

        async with _session_scope(session) as (db, owned):
            # Two concurrent webhooks for the same new payer both reach this
            # point and both INSERT; one loses on users.telegram_id. Doing it
            # inside a SAVEPOINT means the loser's failed INSERT does not
            # poison the caller's transaction — it just reads the winner's row.
            try:
                async with db.begin_nested():
                    new_user = User(telegram_id=telegram_id)
                    db.add(new_user)
                    await db.flush()
            except IntegrityError:
                result = await db.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                winner = result.scalars().first()
                if winner is None:
                    raise
                return winner

            if owned:
                await db.commit()
                await db.refresh(new_user)
            return new_user

    @staticmethod
    async def get_by_id(
        user_id: int, session: AsyncSession | None = None
    ) -> User | None:
        async with _session_scope(session) as (db, _):
            return await db.get(User, user_id)


class SubscriptionRepository:
    @staticmethod
    async def get_active_for_user(
        user_id: int, session: AsyncSession | None = None
    ) -> Subscription | None:
        async with _session_scope(session) as (db, _):
            result = await db.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
                .order_by(Subscription.created_at.desc())
            )
            return result.scalars().first()

    @staticmethod
    async def get_active_or_expiring_for_user(
        user_id: int, session: AsyncSession | None = None
    ) -> Subscription | None:
        async with _session_scope(session) as (db, _):
            result = await db.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING]
                    ),
                )
                .order_by(Subscription.created_at.desc())
            )
            return result.scalars().first()

    @staticmethod
    async def create(
        user_id: int,
        expires_at: datetime | None,
        status: SubscriptionStatus | str = SubscriptionStatus.PENDING,
        session: AsyncSession | None = None,
    ) -> Subscription:
        if isinstance(status, str):
            status = SubscriptionStatus(status)
        async with _session_scope(session) as (db, owned):
            subscription = Subscription(
                user_id=user_id, expires_at=expires_at, status=status
            )
            db.add(subscription)
            if owned:
                await db.commit()
                await db.refresh(subscription)
            else:
                await db.flush()
            return subscription

    @staticmethod
    async def update_status(
        subscription_id: int,
        new_status: SubscriptionStatus | str,
        expires_at: datetime | None = None,
        session: AsyncSession | None = None,
    ) -> Subscription:
        if isinstance(new_status, str):
            new_status = SubscriptionStatus(new_status)
        async with _session_scope(session) as (db, owned):
            subscription = await db.get(Subscription, subscription_id)
            if subscription is None:
                raise ValueError(f"Subscription {subscription_id} not found")
            transition(subscription, new_status)
            if expires_at is not None:
                subscription.expires_at = expires_at
            if owned:
                await db.commit()
                await db.refresh(subscription)
            else:
                await db.flush()
            return subscription

    @staticmethod
    async def list_expiring_within(
        days: int, session: AsyncSession | None = None
    ) -> list[Subscription]:
        now = utcnow()
        threshold = now + timedelta(days=days)
        async with _session_scope(session) as (db, _):
            result = await db.execute(
                select(Subscription).where(
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING]
                    ),
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at >= now,
                    Subscription.expires_at <= threshold,
                )
            )
            return list(result.scalars().all())

    @staticmethod
    async def list_expired(session: AsyncSession | None = None) -> list[Subscription]:
        now = utcnow()
        async with _session_scope(session) as (db, _):
            result = await db.execute(
                select(Subscription).where(
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING]
                    ),
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at < now,
                )
            )
            return list(result.scalars().all())


class PaymentRepository:
    @staticmethod
    async def create(
        subscription_id: int,
        provider: str,
        provider_ref: str,
        amount: Decimal,
        currency: str,
        session: AsyncSession | None = None,
    ) -> Payment:
        """Insert a Payment.

        Raises DuplicatePaymentError if one already exists for this
        (provider, provider_ref). In a caller-owned transaction the session is
        left needing a rollback — the caller owns that, and for the webhook it
        is exactly the right outcome: nothing from the duplicate delivery lands.
        """
        async with _session_scope(session) as (db, owned):
            payment = Payment(
                subscription_id=subscription_id,
                provider=provider,
                provider_ref=provider_ref,
                amount=amount,
                currency=currency,
            )
            db.add(payment)
            try:
                if owned:
                    await db.commit()
                else:
                    await db.flush()
            except IntegrityError as exc:
                if not _is_duplicate_provider_ref(exc):
                    raise
                if owned:
                    await db.rollback()
                raise DuplicatePaymentError(provider, provider_ref) from exc
            if owned:
                await db.refresh(payment)
            return payment

    @staticmethod
    async def get_by_provider_ref(
        provider: str, provider_ref: str, session: AsyncSession | None = None
    ) -> Payment | None:
        async with _session_scope(session) as (db, _):
            result = await db.execute(
                select(Payment)
                .where(
                    Payment.provider == provider,
                    Payment.provider_ref == provider_ref,
                )
                .order_by(Payment.id)
            )
            # .first(), not .scalar_one_or_none(): the unique constraint stops
            # new duplicates, but a pair predating it must not turn every
            # delivery of that session into a permanent 500.
            return result.scalars().first()
