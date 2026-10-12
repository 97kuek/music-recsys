"""取り込み台帳と不変スナップショットを追加する。

Revision ID: 0002
Revises: 0001
"""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL_DIRECTORY = Path(__file__).resolve().parents[1] / "sql"


def upgrade() -> None:
    for filename in (
        "0002_functions.sql",
        "0002_tables.sql",
        "0002_guards.sql",
        "0002_publication.sql",
    ):
        op.execute((SQL_DIRECTORY / filename).read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("ALTER TABLE import_runs DROP CONSTRAINT fk_import_result")
    for table in (
        "snapshot_artifacts",
        "snapshot_users",
        "snapshot_tracks",
        "metadata_snapshots",
        "import_run_sources",
        "import_runs",
        "track_metadata",
        "users",
        "tracks",
        "source_files",
        "data_artifacts",
        "dataset_versions",
        "datasets",
    ):
        op.drop_table(table)
    for signature in (
        "recsys_validate_snapshot()",
        "recsys_validate_run()",
        "recsys_guard_snapshot_members()",
        "recsys_guard_snapshot()",
        "recsys_guard_run_sources()",
        "recsys_guard_run()",
        "recsys_snapshot_manifest(uuid)",
        "recsys_input_document(uuid)",
        "recsys_validate_source_file()",
        "recsys_reject_mutation()",
        "recsys_valid_source_manifest(jsonb)",
        "recsys_valid_tags(jsonb)",
        "recsys_json_sha256(jsonb)",
        "recsys_canonical_json(jsonb)",
    ):
        op.execute(f"DROP FUNCTION {signature}")
