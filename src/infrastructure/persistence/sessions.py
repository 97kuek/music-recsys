"""開発用の同意済みセッションだけを保存するSQLite。生トークンを保存しない。"""

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

from src.domain.study.participants import IdentityCollisionError


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
            db.execute(
                "CREATE TABLE IF NOT EXISTS experiments (protocol_id TEXT PRIMARY KEY, digest TEXT NOT NULL, context TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS assignments (protocol_id TEXT NOT NULL, participant_id TEXT NOT NULL, slot INTEGER NOT NULL, expires TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(protocol_id,participant_id), UNIQUE(protocol_id,slot))"
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
            db.execute("BEGIN IMMEDIATE")
            if (
                payload.get("registered")
                and not db.execute(
                    "SELECT 1 FROM participants WHERE participant_id=?",
                    (payload["session"]["participant_id"],),
                ).fetchone()
            ):
                raise PermissionError("参加データは削除されています")
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
            db.execute("DELETE FROM assignments WHERE expires<=?", (now.isoformat(),))

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
            db.execute("DELETE FROM assignments WHERE participant_id=?", (participant_id,))

    def assign(self, plan, participant_id, context, now):
        """Atomic enrollment; deleted slots may be reused, surviving assignments never move."""
        context = json.dumps(context, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            participant = db.execute(
                "SELECT expires FROM participants WHERE participant_id=? AND expires>?",
                (participant_id, now.isoformat()),
            ).fetchone()
            if participant is None:
                raise PermissionError("比較割り当てには保存同意済みの参加者が必要です")
            experiment = db.execute(
                "SELECT digest, context FROM experiments WHERE protocol_id=?", (plan.protocol_id,)
            ).fetchone()
            if experiment and experiment != (plan.digest, context):
                raise ValueError(
                    "実験IDの計画・カタログを変更できません。別の実験IDを指定してください"
                )
            db.execute(
                "INSERT OR IGNORE INTO experiments VALUES (?,?,?)",
                (plan.protocol_id, plan.digest, context),
            )
            previous = db.execute(
                "SELECT payload FROM assignments WHERE protocol_id=? AND participant_id=?",
                (plan.protocol_id, participant_id),
            ).fetchone()
            if previous:
                return json.loads(previous[0])
            used = {
                row[0]
                for row in db.execute(
                    "SELECT slot FROM assignments WHERE protocol_id=?", (plan.protocol_id,)
                )
            }
            available = set(range(5)) - used
            if not available:
                raise ValueError("探索的比較の参加者枠は5人までです")
            slot = min(available)
            payload = {
                "protocol_id": plan.protocol_id,
                "plan_sha256": plan.digest,
                "config_sha256": json.loads(context).get("config_sha256"),
                "participant_id": participant_id,
                "slot": slot,
                "order": plan.schedule()[slot],
                "assigned_at": now.isoformat(),
                "retain_until": participant[0],
                "input": None,
            }
            db.execute(
                "INSERT INTO assignments VALUES (?,?,?,?,?)",
                (plan.protocol_id, participant_id, slot, participant[0], json.dumps(payload)),
            )
            return payload

    def freeze_assignment(self, plan, participant_id, inputs, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM assignments WHERE protocol_id=? AND participant_id=? AND expires>?",
                (plan.protocol_id, participant_id, now.isoformat()),
            ).fetchone()
            if row is None:
                raise PermissionError("割り当てが削除または期限切れです")
            payload = json.loads(row[0])
            if payload["plan_sha256"] != plan.digest:
                raise ValueError("saved assignment changed")
            if payload["input"] is None:
                payload["input"] = inputs
                db.execute(
                    "UPDATE assignments SET payload=? WHERE protocol_id=? AND participant_id=?",
                    (json.dumps(payload), plan.protocol_id, participant_id),
                )
            return payload

    def analysis_snapshot(self, now):
        """One read transaction, retention filtered; never expose token/code hashes."""
        with self.connect() as db:
            db.execute("BEGIN")
            sessions = [
                json.loads(raw)
                for (raw,) in db.execute(
                    "SELECT payload FROM sessions WHERE expires>?", (now.isoformat(),)
                )
            ]
            assignments = [
                json.loads(raw)
                for (raw,) in db.execute(
                    "SELECT payload FROM assignments WHERE expires>?", (now.isoformat(),)
                )
            ]
        return sessions, assignments

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

    def add_participant(self, participant_id, identity_key, expires_at):
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO participants VALUES (?,?,?,?)",
                    (participant_id, identity_key, expires_at.isoformat(), "{}"),
                )
            except sqlite3.IntegrityError as error:
                raise IdentityCollisionError("認証IDが既に登録されています") from error

    def participant(self, participant_id, now):
        with self.connect() as db:
            row = db.execute(
                "SELECT participant_id, expires FROM participants WHERE participant_id=? AND expires>?",
                (participant_id, now.isoformat()),
            ).fetchone()
        return dict(participant_id=row[0], expires_at=row[1]) if row else None

    def participant_by_identity(self, identity_key, now):
        with self.connect() as db:
            row = db.execute(
                "SELECT participant_id FROM participants WHERE code_hash=? AND expires>?",
                (identity_key, now.isoformat()),
            ).fetchone()
        return row[0] if row else None

    def bind_identity(self, participant_id, identity_key, now):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT code_hash FROM participants WHERE participant_id=? AND expires>?",
                (participant_id, now.isoformat()),
            ).fetchone()
            if row is None or (row[0].startswith("access:") and row[0] != identity_key):
                return False
            existing = db.execute(
                "SELECT participant_id FROM participants WHERE code_hash=?", (identity_key,)
            ).fetchone()
            if existing and existing[0] != participant_id:
                return False
            db.execute(
                "UPDATE participants SET code_hash=? WHERE participant_id=?",
                (identity_key, participant_id),
            )
            return True
