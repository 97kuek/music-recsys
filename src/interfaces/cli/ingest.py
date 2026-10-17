"""公開Music4Allの取り込み。接続値と例外本文を出力しない。"""

import argparse
import json
import sys
from pathlib import Path

from src.domain.datasets import ImportFailed


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Music4All-Onion IDs and weighted tags")
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--event-cache", type=Path, required=True)
    parser.add_argument("--version-label", required=True)
    parser.add_argument("--selection", choices=("metadata", "history", "full"), default="metadata")
    parser.add_argument(
        "--train-end", help="Exclusive timezone-naive training cutoff for history selection"
    )
    parser.add_argument(
        "--selection-seed", type=int, help="Use deterministic seeded user selection"
    )
    parser.add_argument(
        "--tracks", type=int, default=100, help="Track limit for metadata selection"
    )
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--storage-root", type=Path, default=Path("data/imports"))
    parser.add_argument("--lockfile", type=Path, default=Path("uv.lock"))
    args = parser.parse_args()
    engine = None
    try:
        from src.infrastructure.datasets.music4all_adapter import Music4AllAdapter
        from src.infrastructure.ingestion.pipeline import import_dataset
        from src.infrastructure.ingestion.postgres import engine_from_environment

        def progress(message: str) -> None:
            print(message, file=sys.stderr, flush=True)

        adapter = Music4AllAdapter(
            record_path=args.record,
            tags_path=args.tags,
            events_path=args.events,
            event_cache=args.event_cache,
            version_label=args.version_label,
            track_limit=args.tracks,
            user_limit=args.users,
            selection=args.selection,
            train_end=args.train_end,
            selection_seed=args.selection_seed,
            progress=progress,
        )
        engine = engine_from_environment()
        result = import_dataset(
            adapter, engine, args.storage_root, args.lockfile, progress=progress
        )
        print(
            json.dumps(
                {
                    "status": "succeeded",
                    "run_id": str(result.run_id),
                    "snapshot_id": str(result.snapshot_id),
                    "tracks": result.tracks,
                    "users": result.users,
                    "reused": result.reused,
                    "input_fingerprint": result.input_fingerprint,
                    "content_fingerprint": result.content_fingerprint,
                }
            )
        )
        return 0
    except Exception as error:
        output = {"status": "rejected", "error_type": type(error).__name__}
        if isinstance(error, ImportFailed):
            output.update(status="failed", error_type=error.error_type, run_id=str(error.run_id))
        print(json.dumps(output), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
