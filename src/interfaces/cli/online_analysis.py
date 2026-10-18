"""Read-only exploratory analysis of private online logs; never select recommendation models."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.application.study.analysis import analyze_repository
from src.domain.study.online_analysis import analyze_online
from src.infrastructure.config.study import read_study_plan
from src.infrastructure.persistence.private_export import write_private_exclusive
from src.infrastructure.research.online import OnlineStudyReader
from src.interfaces.online_export import TABLES, analysis_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path)
    source.add_argument("--input", type=Path, nargs="+")
    parser.add_argument("--study", type=Path, action="append", default=[])
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--table", choices=TABLES, default="lists")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    now = datetime.now(UTC)
    plans = [read_study_plan(path) for path in args.study]
    if args.database:
        result = analyze_repository(OnlineStudyReader(args.database), now=now, plans=plans)
    else:
        result = analyze_online(
            [json.loads(path.read_text()) for path in args.input], plans=plans, now=now
        )
    encoded = (
        analysis_csv(result, args.table)
        if args.format == "csv"
        else json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    )
    write_private_exclusive(args.output, encoded)


if __name__ == "__main__":
    main()
