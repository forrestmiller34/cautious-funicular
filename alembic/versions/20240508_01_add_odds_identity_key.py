"""add identity key to odds markets"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20240508_01_add_odds_identity_key"
down_revision = "20240505_01_odds_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "odds_markets",
        sa.Column("identity_key", sa.String(length=255), nullable=True),
    )
    op.execute(
        """
        UPDATE odds_markets
        SET identity_key = CONCAT_WS(
            '|',
            event_id::text,
            provider,
            bookmaker_id::text,
            market_type,
            scope,
            participant_type,
            COALESCE(participant_id::text, 'null'),
            COALESCE(LOWER(side), 'null'),
            COALESCE(TO_CHAR(line, 'FM999999990.000'), 'null')
        )
        """
    )
    op.alter_column("odds_markets", "identity_key", nullable=False)
    op.drop_constraint("uq_odds_markets_snapshot", "odds_markets", type_="unique")
    op.create_unique_constraint(
        "uq_odds_markets_identity", "odds_markets", ["identity_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_odds_markets_identity", "odds_markets", type_="unique")
    op.create_unique_constraint(
        "uq_odds_markets_snapshot",
        "odds_markets",
        [
            "event_id",
            "provider",
            "bookmaker_id",
            "market_type",
            "participant_type",
            "participant_id",
            "side",
            "line",
            "as_of",
        ],
    )
    op.drop_column("odds_markets", "identity_key")
