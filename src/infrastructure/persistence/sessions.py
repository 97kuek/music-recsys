"""開発用の同意済みセッションだけを保存するSQLite。生トークンを保存しない。"""

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID


class LocalStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS participants (participant_id TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL, expires TEXT NOT NULL, profile TEXT NOT NULL)"
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

    def get(self, token_hash, now):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM sessions WHERE token_hash=? AND expires>?",
                (token_hash, now.isoformat()),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def active_session(self, participant_id, context, now):
        with self.connect() as db:
            rows = db.execute(
                "SELECT token_hash, payload FROM sessions WHERE expires>? ORDER BY rowid DESC",
                (now.isoformat(),),
            ).fetchall()
        for key, raw in rows:
            payload = json.loads(raw)
            if (
                payload["session"]["participant_id"] == participant_id
                and not payload.get("finished")
                and payload.get("resume", {}).get("context") == context
            ):
                return key, payload
        return None

    def rekey(self, previous, replacement):
        with self.connect() as db:
            if (
                db.execute(
                    "UPDATE sessions SET token_hash=? WHERE token_hash=?", (replacement, previous)
                ).rowcount
                != 1
            ):
                raise PermissionError("セッションが変更されました。再接続してください")

    def delete(self, token_hash):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM sessions WHERE token_hash=?", (token_hash,)
            ).fetchone()
            if row:
                self._export_path(json.loads(row[0])["session"]["session_id"]).unlink(
                    missing_ok=True
                )
            db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def purge(self, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for (payload,) in db.execute(
                "SELECT payload FROM sessions WHERE expires<=?", (now.isoformat(),)
            ):
                self._export_path(json.loads(payload)["session"]["session_id"]).unlink(
                    missing_ok=True
                )
            db.execute("DELETE FROM sessions WHERE expires<=?", (now.isoformat(),))
            db.execute("DELETE FROM participants WHERE expires<=?", (now.isoformat(),))

    def delete_participant(self, participant_id):
        """本人のCookieで所有確認した仮名IDの全セッション・管理exportを削除する。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, raw in db.execute("SELECT token_hash, payload FROM sessions").fetchall():
                payload = json.loads(raw)
                if payload["session"]["participant_id"] == participant_id:
                    self._export_path(payload["session"]["session_id"]).unlink(missing_ok=True)
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (key,))
            db.execute("DELETE FROM participants WHERE participant_id=?", (participant_id,))

    def _export_path(self, session_id):
        return self.path.parent / "exports" / f"{UUID(session_id)}.json"

    def summaries(self, now):
        with self.connect() as db:
            return [
                {
                    **{
                        key: p["session"][key]
                        for key in ("session_id", "started_at", "retain_until", "consent_version")
                    },
                    "requests": len(p["requests"]),
                    "events": len(p["events"]),
                    "finished": p.get("finished", False),
                }
                for (raw,) in db.execute(
                    "SELECT payload FROM sessions WHERE expires>? ORDER BY expires",
                    (now.isoformat(),),
                )
                for p in (json.loads(raw),)
            ]

    def export_session(self, session_id, now):
        target = self._export_path(session_id)
        with self.connect() as db:
            # Serialize with participant deletion, so a concurrent export cannot restore deleted data.
            db.execute("BEGIN IMMEDIATE")
            payload = next(
                (
                    p
                    for (raw,) in db.execute(
                        "SELECT payload FROM sessions WHERE expires>?", (now.isoformat(),)
                    )
                    for p in (json.loads(raw),)
                    if p["session"]["session_id"] == session_id
                ),
                None,
            )
            if payload is None:
                raise ValueError("unexpired consented session not found")
            if datetime.fromisoformat(payload["session"]["retain_until"]) <= now:
                raise ValueError("session expired")
            target.parent.mkdir(mode=0o700, exist_ok=True)
            if target.parent.is_symlink():
                raise ValueError("export directory must not be a symlink")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", dir=target.parent, prefix=".export-", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    json.dump(
                        {
                            "format": "music-recsys-log-v1",
                            "exported_at": now.isoformat(),
                            "payload": payload,
                        },
                        stream,
                        ensure_ascii=False,
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(
                    temporary, target
                )  # Exclusive publication; existing exports are never overwritten.
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        return target

    def add_participant(self, participant_id, code_hash, expires_at):
        with self.connect() as db:
            db.execute(
                "INSERT INTO participants VALUES (?,?,?,?)",
                (participant_id, code_hash, expires_at.isoformat(), "{}"),
            )

    def participant(self, participant_id, now):
        with self.connect() as db:
            row = db.execute(
                "SELECT participant_id, expires, profile FROM participants WHERE participant_id=? AND expires>?",
                (participant_id, now.isoformat()),
            ).fetchone()
        return (
            dict(participant_id=row[0], expires_at=row[1], profile=json.loads(row[2]))
            if row
            else None
        )

    def participant_by_code(self, code_hash, now):
        with self.connect() as db:
            row = db.execute(
                "SELECT participant_id FROM participants WHERE code_hash=? AND expires>?",
                (code_hash, now.isoformat()),
            ).fetchone()
        return row[0] if row else None

    def update_participant_code(self, participant_id, code_hash, now):
        with self.connect() as db:
            db.execute(
                "UPDATE participants SET code_hash=? WHERE participant_id=? AND expires>?",
                (code_hash, participant_id, now.isoformat()),
            )

    def update_participant_profile(self, participant_id, profile, now):
        with self.connect() as db:
            db.execute(
                "UPDATE participants SET profile=? WHERE participant_id=? AND expires>?",
                (json.dumps(profile), participant_id, now.isoformat()),
            )
