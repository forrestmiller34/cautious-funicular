"""add ingestion_status table

Revision ID: 94c6456d2877
Revises: 4fb89f8a8f6c
Create Date: 2024-05-01 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "94c6456d2877"
down_revision: Union[str, Sequence[str], None] = "4fb89f8a8f6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ingestion_status table."""
    op.create_table(
        "ingestion_status",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(length=50), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "season",
            "data_type",
            name="uq_ingestion_status_source_season_type",
        ),
    )


def downgrade() -> None:
    """Drop ingestion_status table."""
    op.drop_table("ingestion_status")
