"""add style_vectors

Revision ID: 7b2e4c9d1a05
Revises: 3c9f1a2b7d4e
Create Date: 2026-10-27 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b2e4c9d1a05"
down_revision: str | None = "3c9f1a2b7d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "style_vectors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "key", name="uq_style_vectors_kind_key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("style_vectors")
