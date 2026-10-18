"""Mac管理者用のローカルログ操作。HTTP APIへ公開しない。"""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.application.study.participants import Participants
from src.infrastructure.persistence.sessions import LocalStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    issue = sub.add_parser("issue-codes", help="参加者へ個別に渡す4桁コードを発行")
    issue.add_argument("--count", type=int, required=True)
    issue.add_argument("--retention-days", type=int, required=True)
    export = sub.add_parser("export")
    export.add_argument("session_id")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("既存のsessions.sqliteを指定してください")
    if args.command == "issue-codes" and (
        not 1 <= args.count <= 5 or not 1 <= args.retention_days <= 366
    ):
        parser.error("人数は1〜5人、保存期間は1〜366日を指定してください")
    store = LocalStore(args.database)
    now = datetime.now(UTC)
    store.purge(now)
    if args.command == "issue-codes":
        participants = Participants(store)
        rows = []
        for _ in range(args.count):
            participant, code = participants.issue(
                expires_at=now + timedelta(days=args.retention_days), now=now
            )
            rows.append(
                {
                    "participant_id": participant["participant_id"],
                    "participant_code": code,
                    "expires_at": participant["expires_at"],
                }
            )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.command == "list":
        print(json.dumps(store.summaries(now), ensure_ascii=False, indent=2))
    else:
        print(store.export_session(args.session_id, now))


if __name__ == "__main__":
    main()
