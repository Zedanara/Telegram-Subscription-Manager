"""add user last_question_at

Revision ID: 2c8fa22fa248
Revises: 4fa5fb802141
Create Date: 2026-09-13 18:32:26.093089

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2c8fa22fa248'
down_revision: Union[str, Sequence[str], None] = '4fa5fb802141'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Autogenerate also proposed dropping 'apscheduler_jobs' — that table
    belongs to APScheduler's own SQLAlchemyJobStore (main.py), not to our
    models, so it's expected to be invisible to Base.metadata. Dropping it
    here would destroy the scheduler's persisted job state; left untouched.
    """
    op.add_column('users', sa.Column('last_question_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'last_question_at')
