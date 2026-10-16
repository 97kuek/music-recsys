"""開発用の同意済みセッションだけを保存するSQLite。生トークンを保存しない。"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class LocalStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires TEXT NOT NULL, payload TEXT NOT NULL)"
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, token_hash, payload):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?,?,?)",
                (
                    token_hash,
                    payload["session"]["retain_until"],
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def delete(self, token_hash):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def purge(self, now):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<=?", (now.isoformat(),))
