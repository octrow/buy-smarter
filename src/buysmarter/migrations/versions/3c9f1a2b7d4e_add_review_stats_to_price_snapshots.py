"""add review_count and rating to price_snapshots

Revision ID: 3c9f1a2b7d4e
Revises: 05dbcfa0e44d
Create Date: 2026-10-13 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c9f1a2b7d4e"
down_revision: str | None = "05dbcfa0e44d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("price_snapshots", sa.Column("review_count", sa.Integer(), nullable=True))
    op.add_column("price_snapshots", sa.Column("rating", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("price_snapshots", "rating")
    op.drop_column("price_snapshots", "review_count")
