"""add subscription last_warning_days_left

Revision ID: 11dc15c10ee4
Revises: 2c8fa22fa248
Create Date: 2026-10-03 12:48:13.866032

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11dc15c10ee4'
down_revision: Union[str, Sequence[str], None] = '2c8fa22fa248'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Backfills last_warning_days_left for existing EXPIRING rows with their
    current days-remaining, using the same max(1, ceil(...)) rounding as
    app.domain.time.days_remaining() — otherwise every subscriber who
    already got the old single reminder would get a duplicate right after
    this deploys. ACTIVE/EXPIRED/KICKED/PENDING rows are left NULL: they
    haven't been warned this cycle (or at all) yet.

    The enum column stores the Python Enum *names* ('EXPIRING'), not their
    lowercase .value ('expiring') — confirmed against the initial migration,
    which declares the Postgres enum type with the uppercase names.

    Autogenerate also proposed dropping 'apscheduler_jobs' here — that table
    belongs to APScheduler's own SQLAlchemyJobStore (main.py), not to our
    models, so it's expected to be invisible to Base.metadata. Dropping it
    would destroy the scheduler's persisted job state; left untouched.
    """
    op.add_column(
        "subscriptions", sa.Column("last_warning_days_left", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE subscriptions
        SET last_warning_days_left = GREATEST(
            1, CEIL(EXTRACT(EPOCH FROM (expires_at - now())) / 86400.0)
        )::integer
        WHERE status = 'EXPIRING'
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("subscriptions", "last_warning_days_left")
