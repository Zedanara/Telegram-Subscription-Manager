from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import Subscription, SubscriptionStatus, User
from app.db.session import get_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UserRepository:
    @staticmethod
    async def get_by_telegram_id(telegram_id: int) -> User | None:
        async with get_session() as session:
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def create(telegram_id: int) -> User:
        async with get_session() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    @staticmethod
    async def get_or_create(telegram_id: int) -> User:
        user = await UserRepository.get_by_telegram_id(telegram_id)
        if user is not None:
            return user
        return await UserRepository.create(telegram_id)


class SubscriptionRepository:
    @staticmethod
    async def get_active_for_user(user_id: int) -> Subscription | None:
        async with get_session() as session:
            result = await session.execute(
                select(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == SubscriptionStatus.ACTIVE,
                )
                .order_by(Subscription.created_at.desc())
            )
            return result.scalars().first()

    @staticmethod
    async def create(
        user_id: int,
        expires_at: datetime | None,
        status: SubscriptionStatus | str = SubscriptionStatus.PENDING,
    ) -> Subscription:
        if isinstance(status, str):
            status = SubscriptionStatus(status)
        async with get_session() as session:
            subscription = Subscription(
                user_id=user_id, expires_at=expires_at, status=status
            )
            session.add(subscription)
            await session.commit()
            await session.refresh(subscription)
            return subscription

    @staticmethod
    async def update_status(
        subscription_id: int, new_status: SubscriptionStatus | str
    ) -> Subscription:
        if isinstance(new_status, str):
            new_status = SubscriptionStatus(new_status)
        async with get_session() as session:
            subscription = await session.get(Subscription, subscription_id)
            if subscription is None:
                raise ValueError(f"Subscription {subscription_id} not found")
            subscription.status = new_status
            await session.commit()
            await session.refresh(subscription)
            return subscription

    @staticmethod
    async def list_expiring_within(days: int) -> list[Subscription]:
        now = _utcnow()
        threshold = now + timedelta(days=days)
        async with get_session() as session:
            result = await session.execute(
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
    async def list_expired() -> list[Subscription]:
        now = _utcnow()
        async with get_session() as session:
            result = await session.execute(
                select(Subscription).where(
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRING]
                    ),
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at < now,
                )
            )
            return list(result.scalars().all())
