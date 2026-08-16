"""add search_vector and gin indexes

Revision ID: 7fe4e2a87e91
Revises: 22ed3afbee2e
Create Date: 2026-08-16 04:33:52.150477
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '7fe4e2a87e91'
down_revision: str | None = 'b80721178fbd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A STORED generated column: Postgres recomputes search_vector on every
    # INSERT/UPDATE. No trigger to maintain, no application code to forget.
    # setweight tags each source so ts_rank can score a title hit above a
    # summary hit -- without it every match ranks identically and results
    # come back effectively unordered.
    # conditions::text renders the jsonb array with its brackets and quotes;
    # to_tsvector discards those as non-words. Crude, but immutable, which
    # a generated column requires.
    op.execute(
        """
        ALTER TABLE trials ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(brief_summary, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(conditions::text, '')), 'C')
        ) STORED
        """
    )
    # GIN over GiST: GIN is slower to build and larger on disk, but faster to
    # query. This table is read-heavy -- writes happen only on sync.
    op.execute("CREATE INDEX ix_trials_search_vector ON trials USING GIN (search_vector)")
    # jsonb_path_ops is smaller and faster than the default jsonb_ops, at the
    # cost of supporting only @>. Containment is the only query we run here.
    op.execute(
        "CREATE INDEX ix_trials_conditions ON trials USING GIN (conditions jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trials_conditions")
    op.execute("DROP INDEX IF EXISTS ix_trials_search_vector")
    # Dropping the column drops its dependent index too, but being explicit
    # keeps the downgrade readable.
    op.execute("ALTER TABLE trials DROP COLUMN IF EXISTS search_vector")
