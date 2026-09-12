"""Shared pytest fixtures.

`db_session` is here for any future test that needs a real database
round-trip (repository tests, etc.) — nothing in this sprint's suite uses it
yet, since the state machine tests operate on plain in-memory objects.

It runs against an in-memory SQLite database (via aiosqlite) so the suite
has zero external dependencies and stays fast in CI. Caveat worth knowing
before reaching for it: SQLite does not enforce or preserve timezone-aware
TIMESTAMPTZ semantics the way Postgres does (see app/domain/time.py and the
migration that made every timing column TIMESTAMPTZ). A naive/aware
datetime bug that only shows up against Postgres would NOT be caught by a
test using this fixture — that class of bug needs a real Postgres instance
to verify against (as has been done manually, per sprint, against the
docker-compose db service).
"""
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
