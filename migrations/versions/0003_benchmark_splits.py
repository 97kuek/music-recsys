"""保存済み時系列分割の不変台帳を追加する。

Revision ID: 0003
Revises: 0002
"""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    path = Path(__file__).resolve().parents[1] / "sql" / "0003_benchmark_splits.sql"
    op.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.drop_table("benchmark_splits")
    op.execute("DROP FUNCTION recsys_validate_split()")
