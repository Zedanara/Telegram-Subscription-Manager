"""Proves the db_session fixture itself actually works.

Nothing else in this sprint's suite needs a database — the state machine
tests operate on plain in-memory objects — but shipping fixture scaffolding
with no test at all exercising it risks it silently rotting. This is that
one exercise.
"""
from sqlalchemy import select

from app.db.models import User


async def test_db_session_roundtrip(db_session):
    db_session.add(User(telegram_id=123456789))
    await db_session.commit()

    result = await db_session.execute(select(User).where(User.telegram_id == 123456789))
    user = result.scalar_one()

    assert user.id is not None
    assert user.telegram_id == 123456789
