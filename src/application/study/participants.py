"""仮名参加者の発行・本人確認・再開コード変更。"""

from uuid import uuid4

from src.application.ports.study import ParticipantRepository
from src.domain.study.participants import code_key, new_code


class Participants:
    def __init__(self, store: ParticipantRepository):
        self.store = store

    def issue(self, *, expires_at, now):
        if expires_at <= now:
            raise ValueError("参加コードの期限が不正です")
        participant_id, code = f"app:{uuid4()}", new_code()
        self.store.add_participant(participant_id, code_key(code), expires_at)
        return self.get(participant_id, now), code

    def get(self, participant_id, now):
        participant = self.store.participant(participant_id, now)
        if participant is None:
            raise PermissionError("参加コードの期限が切れているか、データが削除されています")
        return participant

    def resolve(self, code, now):
        participant_id = self.store.participant_by_code(code_key(code), now)
        if participant_id is None:
            raise PermissionError("参加コードを確認してください")
        return self.get(participant_id, now)

    def rotate(self, participant_id, now):
        self.get(participant_id, now)
        code = new_code()
        self.store.update_participant_code(participant_id, code_key(code), now)
        return code

    def save_profile(self, participant_id, profile, catalog_key, now):
        self.get(participant_id, now)
        self.store.update_participant_profile(
            participant_id, {"catalog_key": catalog_key, **profile}, now
        )
