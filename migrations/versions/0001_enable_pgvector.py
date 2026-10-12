"""pgvectorを固定版で有効化する。

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH VERSION '0.8.7'")
    op.execute(
        """
        DO $$
        BEGIN
            IF (SELECT extversion FROM pg_extension WHERE extname = 'vector') <> '0.8.7' THEN
                RAISE EXCEPTION 'pgvector extension version must be 0.8.7';
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP EXTENSION vector")
