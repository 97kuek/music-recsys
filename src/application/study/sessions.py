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
        long_term_consent=False,
    ):
        if long_term_consent and (
            not persist or participant is None or consent_version != "local-ui-v5"
        ):
            raise ValueError("長期嗜好には参加者登録と追加同意が必要です")
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
        profile = participant["profile"] if participant else {}
        if consent_version == "local-ui-v5" and not long_term_consent:
            profile = {}
        if profile.get("catalog_key") != self.resume_context["config_sha256"]:
            profile = {}
        session = Session(
            log,
            persist,
            InitialProfile(
                participant_id,
                tuple(profile.get("selected_item_ids", ())),
                tuple(profile.get("selected_artist_ids", ())),
            ),
            registered=participant is not None,
            long_term_consent=long_term_consent,
        )
        if long_term_consent:
            from src.application.study.preferences import PreferenceService

            preferences = PreferenceService(self).for_participant(participant_id)
            liked = {row["item_id"] for row in preferences["inferred_tracks"]}
            seeds = set(session.profile.selected_item_ids) | liked
            # Preserve the existing UI limit without arbitrarily choosing a preference ranking.
            if len(seeds) <= 30:
                session.profile = replace(session.profile, selected_item_ids=tuple(sorted(seeds)))
            session.profile_snapshot = {
                "method": preferences["method"],
                "user_settings": preferences["user_settings"],
                "inferred_tracks": preferences["inferred_tracks"],
                "captured_at": current.isoformat(),
                "requires_selection": len(seeds) > 30,
            }
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
            if inputs.get("long_term_consent") and not session.long_term_consent:
                raise ValueError("固定した比較入力の再利用には長期嗜好の追加同意が必要です")
            profile = inputs["profile"]
            session.profile = InitialProfile(
                session.profile.user_id,
                tuple(profile["selected_item_ids"]),
                tuple(profile["selected_artist_ids"]),
            )
            session.profile_snapshot = inputs["profile_snapshot"]

    def freeze_comparison_input(self, session):
        if self.study and self.study.design:
            session.assignment = self.store.freeze_assignment(
                self.study,
                session.profile.user_id,
                {
                    "profile": asdict(session.profile),
                    "profile_snapshot": session.profile_snapshot,
                    "long_term_consent": session.long_term_consent,
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
        participant_code=None,
        retention_days=None,
        long_term_consent=False,
    ):
        old = old_token
        participant, code = None, None
        if long_term_consent and (consent_version != "local-ui-v5" or not persist):
            raise ValueError("長期嗜好には新しい説明版と保存同意が必要です")
        if consent_version in {"local-ui-v4", "local-ui-v5"}:
            if not persist:
                raise ValueError("この説明版ではログ保存への同意が必要です")
            if participant_code:
                participant = self.participants.resolve(participant_code, self.clock())
            elif old:
                stored = self.store.get(token_key(old), self.clock())
                if stored and stored.get("registered"):
                    participant = self.participants.get(
                        stored["session"]["participant_id"], self.clock()
                    )
            if old:
                try:
                    current = self.get(old)
                except PermissionError:
                    pass
                else:
                    if (
                        current.registered
                        and not current.finished
                        and participant
                        and participant["participant_id"] == current.log.session.participant_id
                    ):
                        return old, current, None
            if participant is None:
                if retention_days is None:
                    raise ValueError("サーバー管理者が保存期間を設定してから開始してください")
                participant, code = self.participants.issue(
                    expires_at=self.clock() + timedelta(days=retention_days), now=self.clock()
                )
            if consent_version == "local-ui-v5" and long_term_consent:
                previous = participant["profile"]
                permission = previous.get("long_term_permission") or {
                    "version": "long-term-v1",
                    "granted_at": self.clock().isoformat(),
                    "retain_until": participant["expires_at"],
                }
                self.store.update_participant_profile(
                    participant["participant_id"],
                    {**previous, "long_term_permission": permission},
                    self.clock(),
                )
                participant = self.participants.get(participant["participant_id"], self.clock())
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
                long_term_consent=long_term_consent,
            )
        return token, current, code

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
        if current.long_term_consent:
            current.profile_snapshot.update(confirmed_items=items, confirmed_artists=artists)
        self.save(token, current)
        if current.registered and (
            current.long_term_consent or current.log.session.consent_version != "local-ui-v5"
        ):
            self.participants.save_profile(
                current.profile.user_id,
                {"selected_item_ids": items, "selected_artist_ids": artists},
                self.resume_context["config_sha256"],
                self.clock(),
            )
        return {"selected_count": len(items), "artist_count": len(artists)}

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
