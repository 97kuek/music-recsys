"""Read-only SQLite analysis, including databases predating assignment tables."""

import json
import sqlite3
from pathlib import Path


class OnlineStudyReader:
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)

    def analysis_snapshot(self, now):
        db = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        try:
            db.execute("BEGIN")
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "sessions" not in tables:
                raise ValueError("not an online session database")
            values = []
            for table in ("sessions", "assignments"):
                values.append(
                    [
                        json.loads(raw)
                        for (raw,) in db.execute(
                            f"SELECT payload FROM {table} WHERE expires>?", (now.isoformat(),)
                        )
                    ]
                    if table in tables
                    else []
                )
            return tuple(values)
        finally:
            db.close()
