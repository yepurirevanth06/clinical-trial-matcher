"""convert conditions and locations to jsonb

Revision ID: b80721178fbd
Revises: 5b72dceb5d13
Create Date: 2026-08-16 04:29:21.438552
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b80721178fbd'
down_revision: str | None = '5b72dceb5d13'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # op.alter_column emits a bare ALTER TYPE, which Postgres rejects on a
    # non-empty table: there is no implicit json -> jsonb cast. The USING
    # clause is required, so this is raw SQL rather than the Alembic helper.
    op.execute("ALTER TABLE trials ALTER COLUMN conditions TYPE jsonb USING conditions::jsonb")
    op.execute("ALTER TABLE trials ALTER COLUMN locations TYPE jsonb USING locations::jsonb")


def downgrade() -> None:
    # Reversible, but lossy in principle: jsonb has already normalised key
    # order and whitespace, so the original bytes are not recoverable.
    op.execute("ALTER TABLE trials ALTER COLUMN conditions TYPE json USING conditions::json")
    op.execute("ALTER TABLE trials ALTER COLUMN locations TYPE json USING locations::json")
