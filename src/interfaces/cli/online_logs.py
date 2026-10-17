"""Mac管理者用のローカルログ操作。HTTP APIへ公開しない。"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.persistence.sessions import LocalStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    export = sub.add_parser("export")
    export.add_argument("session_id")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("既存のsessions.sqliteを指定してください")
    store = LocalStore(args.database)
    now = datetime.now(UTC)
    store.purge(now)
    if args.command == "list":
        print(json.dumps(store.summaries(now), ensure_ascii=False, indent=2))
    else:
        print(store.export_session(args.session_id, now))


if __name__ == "__main__":
    main()
