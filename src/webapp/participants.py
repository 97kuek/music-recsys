"""仮名参加者と再開コード。氏名・メール・生コードをDBへ保存しない。"""

import hashlib
import json
import re
import secrets
from uuid import uuid4


def code_key(code):
    normalized = re.sub(r"[\s-]", "", code).upper()
    if not re.fullmatch(r"[0-9A-F]{24}", normalized):
        raise PermissionError("参加コードを確認してください")
    return hashlib.sha256(normalized.encode()).hexdigest()


def new_code():
    value = secrets.token_hex(12).upper()
    return "-".join(value[i : i + 4] for i in range(0, len(value), 4))


class Participants:
    def __init__(self, store):
        self.store = store

    def issue(self, *, expires_at, now):
        if expires_at <= now:
            raise ValueError("参加コードの期限が不正です")
        participant_id, code = f"app:{uuid4()}", new_code()
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO participants VALUES (?,?,?,?)",
                (participant_id, code_key(code), expires_at.isoformat(), "{}"),
            )
        return self.get(participant_id, now), code

    def get(self, participant_id, now):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT participant_id, expires, profile FROM participants WHERE participant_id=? AND expires>?",
                (participant_id, now.isoformat()),
            ).fetchone()
        if row is None:
            raise PermissionError("参加コードの期限が切れているか、データが削除されています")
        return dict(participant_id=row[0], expires_at=row[1], profile=json.loads(row[2]))

    def resolve(self, code, now):
        with self.store.connect() as db:
            row = db.execute(
                "SELECT participant_id FROM participants WHERE code_hash=? AND expires>?",
                (code_key(code), now.isoformat()),
            ).fetchone()
        if row is None:
            raise PermissionError("参加コードを確認してください")
        return self.get(row[0], now)

    def rotate(self, participant_id, now):
        self.get(participant_id, now)
        code = new_code()
        with self.store.connect() as db:
            db.execute(
                "UPDATE participants SET code_hash=? WHERE participant_id=? AND expires>?",
                (code_key(code), participant_id, now.isoformat()),
            )
        return code

    def save_profile(self, participant_id, profile, catalog_key, now):
        self.get(participant_id, now)
        payload = {"catalog_key": catalog_key, **profile}
        with self.store.connect() as db:
            db.execute(
                "UPDATE participants SET profile=? WHERE participant_id=? AND expires>?",
                (json.dumps(payload), participant_id, now.isoformat()),
            )
