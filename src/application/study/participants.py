"""認証済みIDとローカルcookieによる仮名参加者登録。"""

import secrets
from uuid import uuid4

from src.application.ports.study import ParticipantRepository
from src.domain.study.participants import IdentityCollisionError


class Participants:
    def __init__(self, store: ParticipantRepository):
        self.store = store

    def register(self, *, expires_at, now, identity_key=None):
        if expires_at <= now:
            raise ValueError("保存期限が不正です")
        if identity_key:
            participant = self.for_identity(identity_key, now)
            if participant:
                return participant
        participant_id = f"app:{uuid4()}"
        key = identity_key or f"local:{secrets.token_hex(32)}"
        try:
            self.store.add_participant(participant_id, key, expires_at)
        except IdentityCollisionError:
            if identity_key:
                participant = self.for_identity(identity_key, now)
                if participant:
                    return participant
            raise ValueError("参加者を登録できません。再試行してください") from None
        return self.get(participant_id, now)

    def get(self, participant_id, now):
        participant = self.store.participant(participant_id, now)
        if participant is None:
            raise PermissionError("保存期限が切れているか、データが削除されています")
        return participant

    def for_identity(self, identity_key, now):
        participant_id = self.store.participant_by_identity(identity_key, now)
        return self.get(participant_id, now) if participant_id else None

    def bind(self, participant_id, identity_key, now):
        if not self.store.bind_identity(participant_id, identity_key, now):
            raise PermissionError("認証した利用者のセッションではありません")
