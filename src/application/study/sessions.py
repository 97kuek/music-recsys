"""参加者の開始・再開・保持期限・プロフィール・比較条件の進行。"""

import hashlib
import secrets
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from uuid import uuid4

from src.application.ports.study import Clock, SessionRepository
from src.application.study.catalog import StudyCatalog
from src.application.study.participants import Participants
from src.application.study.snapshots import restore_session, session_snapshot
from src.domain.recommendation.profiles import InitialProfile
from src.domain.study.events import SessionLog, StudySession
from src.domain.study.plan import StudyPlan
from src.domain.study.session import Session


def token_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


class SessionService:
    def __init__(
        self,
        *,
        mode: str,
        context: dict,
        store: SessionRepository,
        catalog: StudyCatalog,
        study: StudyPlan | None,
        clock: Clock,
    ):
        self.mode, self.resume_context, self.store = mode, context, store
        self.catalog, self.study, self.clock = catalog, study, clock
        self.participants = Participants(store)
        self.active = {}
        self.store.purge(self.clock())

    def restore(self, payload):
        return restore_session(
            payload, context=self.resume_context, study=self.study, now=self.clock()
        )

    def save(self, token, session):
        if session.persist:
            payload = session_snapshot(
                session,
                context=self.resume_context,
                mode=self.mode,
                study=self.study,
                now=self.clock(),
            )
            self.store.put(token_key(token), payload)

    def start(
        self,
        persist,
        allow_text,
        *,
        consent_version=None,
        participant=None,
    ):
        current = self.clock()
        self.expire()
        if len(self.active) >= 100:
            raise ValueError("開発用セッション数の上限です")
        token = secrets.token_urlsafe(32)
        participant_id = participant["participant_id"] if participant else f"app:{uuid4()}"
        log = SessionLog(
            StudySession(
                str(uuid4()),
                participant_id,
                "local-development",
                "mvp-v1",
                "comparison-only",
                "manual",
                1,
                consent_version or ("local-save-v1" if persist else "memory-only"),
                current,
                datetime.fromisoformat(participant["expires_at"])
                if participant
                else current + timedelta(hours=24),
                persist and allow_text,
            )
        )
        session = Session(
            log,
            persist,
            InitialProfile(participant_id),
            registered=participant is not None,
        )
        if self.study:
            if self.study.design:
                if not persist or participant is None:
                    raise ValueError("探索的比較には参加者登録と保存同意が必要です")
                session.assignment = self.store.assign(
                    self.study,
                    participant_id,
                    {"config_sha256": self.resume_context["config_sha256"]},
                    current,
                )
                session.order = session.assignment["order"]
                self._use_frozen_input(session)
            else:
                session.order = self.study.order(participant_id)
            session.log.session = replace(
                session.log.session,
                study_id=self.study.protocol_id,
                protocol_version=self.study.digest,
                condition_id="within-subject",
                sequence_id="-".join(map(str, session.order)),
            )
        session.funnel.append(
            {"kind": "enter", "phase": "profile", "period": 1, "at": current.isoformat()}
        )
        self.active[token_key(token)] = session
        self.save(token, session)
        return token, session

    def _use_frozen_input(self, session):
        inputs = session.assignment.get("input")
        if inputs is not None:
            profile = inputs["profile"]
            session.profile = InitialProfile(
                session.profile.user_id,
                tuple(profile["selected_item_ids"]),
                tuple(profile["selected_artist_ids"]),
            )

    def freeze_comparison_input(self, session):
        if self.study and self.study.design:
            session.assignment = self.store.freeze_assignment(
                self.study,
                session.profile.user_id,
                {
                    "profile": asdict(session.profile),
                },
                self.clock(),
            )
            self._use_frozen_input(session)

    def expire(self):
        current = self.clock()
        self.store.purge(current)
        for key, session in list(self.active.items()):
            if session.log.session.retain_until <= current:
                session.log.withdraw()
                del self.active[key]

    def get(self, token):
        self.expire()
        key = token_key(token)
        session = self.active.get(key)
        if session is None and token:
            payload = self.store.get(key, self.clock())
            if payload is not None:
                try:
                    session = self.restore(payload)
                except (KeyError, TypeError, ValueError, IndexError) as error:
                    raise PermissionError(
                        "保存済みセッションを再開できません。実施者に確認するか、削除して開始してください"
                    ) from error
                if len(self.active) >= 100:
                    raise ValueError("開発用セッション数の上限です")
                self.active[key] = session
        if session is None:
            raise PermissionError("セッションがありません。もう一度開始してください")
        if session.registered:
            try:
                self.participants.get(session.profile.user_id, self.clock())
                if self.store.get(key, self.clock()) is None:
                    raise PermissionError("セッションが変更または削除されています")
            except PermissionError:
                session.log.withdraw()
                self.active.pop(key, None)
                raise
        return session

    def resume_participant(self, participant_id):
        saved = self.store.active_session(participant_id, self.resume_context, self.clock())
        if saved is None:
            return None
        old_key, payload = saved
        session = self.restore(payload)
        token = secrets.token_urlsafe(32)
        self.store.rekey(old_key, token_key(token))
        self.active.pop(old_key, None)
        self.active[token_key(token)] = session
        return token, session

    def delete(self, token):
        key = token_key(token)
        payload = self.store.get(key, self.clock())
        if payload and payload.get("registered"):
            participant_id = payload["session"]["participant_id"]
            self.store.delete_participant(participant_id)
            for other, session in list(self.active.items()):
                if session.log.session.participant_id == participant_id:
                    session.log.withdraw()
                    del self.active[other]
            return
        session = self.active.pop(key, None)
        if session is not None:
            session.log.withdraw()
        self.store.delete(key)

    def open_session(
        self,
        *,
        old_token=None,
        persist=False,
        allow_text=False,
        consent_version=None,
        retention_days=None,
        identity_key=None,
    ):
        old = old_token
        participant = None
        self.expire()
        if consent_version in {
            "local-ui-v4",
            "local-ui-v5",
            "local-ui-v6",
            "local-ui-v7",
            "local-ui-v8",
        }:
            if not persist:
                raise ValueError("この説明版ではログ保存への同意が必要です")
            if old:
                try:
                    current = self.get(old)
                except PermissionError:
                    pass
                else:
                    if current.registered:
                        if identity_key:
                            try:
                                self.participants.bind(
                                    current.profile.user_id, identity_key, self.clock()
                                )
                            except PermissionError:
                                pass
                            else:
                                participant = self.participants.get(
                                    current.profile.user_id, self.clock()
                                )
                        else:
                            participant = self.participants.get(
                                current.profile.user_id, self.clock()
                            )
                        if participant and not current.finished:
                            return old, current
            if participant is None and identity_key:
                participant = self.participants.for_identity(identity_key, self.clock())
            if participant is None:
                if retention_days is None:
                    raise ValueError("サーバー管理者が保存期間を設定してから開始してください")
                participant = self.participants.register(
                    expires_at=self.clock() + timedelta(days=retention_days),
                    now=self.clock(),
                    identity_key=identity_key,
                )
        elif old:
            self.delete(old)
        resumed = self.resume_participant(participant["participant_id"]) if participant else None
        if resumed:
            token, current = resumed
        else:
            token, current = self.start(
                persist,
                allow_text,
                consent_version=consent_version,
                participant=participant,
            )
        return token, current

    def set_profile(self, token, items, artists):
        current = self.get(token)
        if current.finished:
            raise ValueError("終了済みです")
        if self.study and current.recommendations:
            raise ValueError("比較中の初期プロフィールは固定されています")
        if current.assignment.get("input") is not None:
            if (
                tuple(items) != current.profile.selected_item_ids
                or tuple(artists) != current.profile.selected_artist_ids
            ):
                raise ValueError("この実験の初期プロフィールは固定されています")
            return {"selected_count": len(items), "artist_count": len(artists)}
        if set(items) - self.catalog.tracks.keys() or set(artists) - self.catalog.artists.keys():
            raise ValueError("選択した曲・アーティストがありません")
        current.profile = InitialProfile(current.profile.user_id, tuple(items), tuple(artists))
        self.save(token, current)
        return {"selected_count": len(items), "artist_count": len(artists)}

    def set_context(self, token, choices, text):
        session = self.get(token)
        if session.finished:
            raise ValueError("終了済みです")
        if len(set(choices)) != len(choices) or set(choices) - {
            "calmer",
            "more_energy",
            "different_style",
        }:
            raise ValueError("unknown session request")
        if text and not session.log.session.allow_request_text:
            raise PermissionError("自由文保存への別同意が必要です")
        session.context = {
            "choices": choices,
            "text": text or None,
            "recorded_at": self.clock().isoformat(),
            "interpreted": False,
        }
        self.save(token, session)
        return session.context

    def progress(self, token, phase, kind="enter"):
        session = self.get(token)
        if session.finished:
            raise ValueError("終了済みです")
        if len(session.funnel) >= 1000:
            raise ValueError("操作記録の上限です")
        if kind == "enter":
            allowed = {
                "profile": {"profile", "listen"},
                "listen": {"listen", "evaluate"},
                "evaluate": {"evaluate", "listen"},
            }
            if not session.recommendations:
                allowed["listen"].add("profile")
            if phase not in allowed.get(session.phase, set()):
                raise ValueError("画面の遷移が不正です")
            if phase == "evaluate" and session.period not in session.replies:
                raise ValueError("先に推薦を受けてください")
            session.phase = phase
        elif phase != session.phase:
            raise ValueError("現在の画面と一致しません")
        session.funnel.append(
            {
                "kind": kind,
                "phase": phase,
                "period": session.period + 1,
                "at": self.clock().isoformat(),
            }
        )
        self.save(token, session)

    def finish(self, token):
        current = self.get(token)
        if self.study and current.period != len(current.order) - 1:
            raise ValueError("途中終了は離脱操作を使用してください")
        if self.study and current.phase != "evaluate":
            raise ValueError("評価画面で終了してください")
        self.progress(token, current.phase, "exit")
        current.finished = True
        current.termination_reason = "completed"
        self.save(token, current)

    def next_period(self, token, period):
        current = self.get(token)
        if not self.study or current.finished or period != current.period + 1:
            raise ValueError("条件の遷移が不正です")
        if current.phase != "evaluate" or current.period not in current.replies:
            raise ValueError("現在の試聴を終えてから進んでください")
        if current.period + 1 >= len(current.order):
            raise ValueError("最後の条件です")
        current.period += 1
        self.progress(token, "listen")
        return current

    def leave(self, token):
        current = self.get(token)
        self.progress(token, current.phase, "exit")
        current.finished = True
        current.termination_reason = "abandoned"
        self.save(token, current)

    def require_current_request(self, session, request_id):
        if self.study and session.replies.get(session.period, {}).get("request_id") != request_id:
            raise ValueError("現在の体験に属する推薦ではありません")
