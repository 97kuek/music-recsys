"""公開済み履歴snapshotの分割と、小規模Benchmark入力の書き出し。"""

import argparse
import hashlib
import json
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from src.domain.datasets import Artifact, ValidationError
from src.infrastructure.datasets.temporal import TemporalConfig, export_phase, prepare_temporal
from src.infrastructure.ingestion.artifacts import ArtifactStore
from src.infrastructure.ingestion.pipeline import load_snapshot
from src.infrastructure.ingestion.postgres import engine_from_environment, execute
from src.infrastructure.ingestion.splits import load_split, publish_split
from src.infrastructure.provenance import source_version
from src.paths import SOURCE_ROOT


def split_snapshot(
    engine,
    storage_root: Path,
    snapshot_id: UUID,
    *,
    lockfile: Path,
    config: TemporalConfig = TemporalConfig(),
    progress: Callable[[str], None] | None = None,
) -> UUID:
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    catalog = load_snapshot(engine, storage_root, snapshot_id)
    with engine.connect() as c:
        snapshot = (
            execute(
                c,
                """SELECT d.key,r.config,s.dataset_version_id
            FROM metadata_snapshots s JOIN datasets d ON d.id=s.dataset_id
            JOIN import_runs r ON r.id=s.created_by_import_run_id
            WHERE s.id=:s AND s.published_at IS NOT NULL""",
                s=snapshot_id,
            )
            .mappings()
            .one()
        )
        history = snapshot["config"].get("history_events")
        if history is None:
            raise ValidationError("snapshot does not contain playback history provenance")
        row = (
            execute(
                c,
                """SELECT a.* FROM data_artifacts a JOIN datasets d ON d.id=a.dataset_id
            WHERE d.key=:key AND a.sha256=:sha AND a.storage_key=:storage""",
                key=snapshot["key"],
                sha=history["sha256"],
                storage=history["storage_key"],
            )
            .mappings()
            .one()
        )
    store = ArtifactStore(storage_root, snapshot["key"])
    events = Artifact(
        row["storage_key"],
        store.root / row["storage_key"],
        row["format"],
        row["sha256"],
        row["byte_size"],
        row["row_count"],
        row["verified_at"],
    )
    store.verify(events)
    if events.row_count != history["row_count"]:
        raise ValidationError("history row count mismatch")
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as c:
        c.register("catalog_tracks", pa.table({"item_id": [t.source_id for t in catalog.tracks]}))
        c.register("catalog_users", pa.table({"user_id": [u.source_id for u in catalog.users]}))
        for table, column in [("catalog_tracks", "item_id"), ("catalog_users", "user_id")]:
            if c.execute(
                f"SELECT count(*) FROM (SELECT DISTINCT {column} FROM read_parquet(?) EXCEPT SELECT {column} FROM {table})",
                [str(events.path)],
            ).fetchone()[0]:
                raise ValidationError("history contains IDs outside snapshot")
    identity = {
        **asdict(config),
        "protocol": "temporal-warm-unseen-v1",
        "boundary_inclusion": {
            "train": "event_time < train_end",
            "validation": "train_end <= event_time < validation_end",
            "test": "validation_end <= event_time",
        },
        "preprocessing_version": source_version(
            SOURCE_ROOT,
            (
                "infrastructure/provenance.py",
                "domain/datasets.py",
                "infrastructure/datasets/temporal.py",
                "interfaces/cli/temporal_cli.py",
                "infrastructure/ingestion/splits.py",
                "infrastructure/ingestion/split_validation.py",
                "infrastructure/ingestion/event_validation.py",
            ),
        ),
        "dependency_lock_sha256": hashlib.sha256(Path(lockfile).read_bytes()).hexdigest(),
        "selection": snapshot["config"],
    }
    with tempfile.TemporaryDirectory(dir=store.root, prefix="temporal-") as directory:
        result = prepare_temporal(
            events.path, Path(directory) / "split", config, provenance={}, progress=progress
        )
        artifacts = {"events": events}
        for role in ("train", "validation", "test"):
            path = result.files[role]
            artifacts[role] = store.put(
                path, format="parquet", row_count=pq.ParquetFile(path).metadata.num_rows
            )
        return publish_split(engine, storage_root, snapshot_id, identity, artifacts, result.summary)


def export_split(
    engine,
    storage_root: Path,
    split_id: UUID,
    output: Path,
    *,
    phase: str,
    protocol: str = "binary-v1",
    windows: tuple[int, ...] = (30, 90, 180),
) -> None:
    saved = load_split(engine, storage_root, split_id)
    if saved["dataset_key"] != "music4all-onion":
        raise ValidationError("this export supports Music4All-Onion only")
    config = TemporalConfig(**{k: saved["config"][k] for k in asdict(TemporalConfig())})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    provenance = {
        "dataset_key": saved["dataset_key"],
        "dataset_version_id": str(saved["dataset_version_id"]),
        "version_label": saved["version_label"],
        "snapshot_id": str(saved["snapshot_id"]),
        "split_id": str(split_id),
        "preprocessing": saved["config"],
    }
    if protocol == "listening-v2":
        from src.interfaces.cli.listening_export import export_listening_phase

        roles = (
            ("train", "validation") if phase == "validation" else ("train", "validation", "test")
        )
        provenance["source_artifacts"] = {
            role: {
                "storage_key": saved["artifacts"][role].storage_key,
                "sha256": saved["artifacts"][role].sha256,
                "row_count": saved["artifacts"][role].row_count,
            }
            for role in roles
        }
        export_listening_phase(
            {k: a.path for k, a in saved["artifacts"].items()},
            output,
            config,
            phase=phase,
            windows=windows,
            provenance=provenance,
        )
        return
    if protocol != "binary-v1":
        raise ValueError("unknown evaluation protocol")
    export_phase(
        {k: a.path for k, a in saved["artifacts"].items()},
        output,
        config,
        phase=phase,
        provenance=provenance,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish temporal split or export a small benchmark"
    )
    parser.add_argument("--storage-root", type=Path, default=Path("data/imports"))
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--snapshot-id", type=UUID, required=True)
    prepare.add_argument("--lockfile", type=Path, default=Path("uv.lock"))
    export = sub.add_parser("export")
    export.add_argument("--split-id", type=UUID, required=True)
    export.add_argument("--phase", choices=("validation", "test"), required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--protocol", choices=("binary-v1", "listening-v2"), default="binary-v1")
    export.add_argument("--window-days", type=int, nargs="+", default=[30, 90, 180])
    args = parser.parse_args()
    engine = None
    try:
        engine = engine_from_environment()
        if args.command == "prepare":
            split = split_snapshot(
                engine,
                args.storage_root,
                args.snapshot_id,
                lockfile=args.lockfile,
                progress=lambda message: print(message, file=sys.stderr, flush=True),
            )
            print(json.dumps({"split_id": str(split)}))
        else:
            export_split(
                engine,
                args.storage_root,
                args.split_id,
                args.output,
                phase=args.phase,
                protocol=args.protocol,
                windows=tuple(args.window_days),
            )
            print(json.dumps({"status": "exported", "phase": args.phase}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
