from sqlalchemy import select

from app.db.models import User
from app.db.session import get_session


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
