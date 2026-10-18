"""Preview orders, or explicitly reserve one slot for an existing consented participant."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.config.study import read_study_plan
from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.persistence.sessions import LocalStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--participant-id")
    args = parser.parse_args()
    study = read_study_plan(args.study)
    if not study.design:
        parser.error("an exploratory design is required")
    if args.participant_id:
        if not args.database or not args.database.is_file() or not args.config:
            parser.error("enrollment requires an existing --database and --config")
        store = LocalStore(args.database)
        now = datetime.now(UTC)
        store.purge(now)
        result = store.assign(
            study, args.participant_id, {"config_sha256": file_sha256(args.config)}, now
        )
        result = {k: v for k, v in result.items() if k != "input"}
    else:
        if args.database or args.config:
            parser.error("omit database/config for a read-only preview")
        result = dict(
            protocol_id=study.protocol_id, plan_sha256=study.digest, schedule=study.schedule()
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
