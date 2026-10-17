"""起動時ロード、セッション状態、既存推薦・ログ契約への接続。"""

import hashlib
import secrets
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

from src.application.ports.recommendation import ServedRecommender
from src.application.ports.study import Playback, SessionRepository
from src.application.recommendation import RecommendationService
from src.application.study.participants import Participants
from src.domain.catalog import CatalogTrack, PlaybackContext, TrackCatalog, TrackRef, TrackSource
from src.domain.recommendation.contracts import RecommendationRequest
from src.domain.recommendation.models import ComparisonStrategy, ModelSpec
from src.domain.recommendation.profiles import InitialProfile
from src.domain.study.events import InteractionEvent, SessionLog, StudySession
from src.domain.study.plan import StudyPlan
from src.domain.study.session import Session


def now():
    return datetime.now(UTC)


def token_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Runtime:
    def __init__(
        self,
        *,
        config: dict,
        config_sha256: str,
        models: dict[str, tuple[ModelSpec, ServedRecommender]],
        records: list,
        playback: Playback,
        store: SessionRepository,
        study: StudyPlan | None = None,
        scope=None,
    ):
        self.study = study
        self.config = config
        self.resume_context = {
            "config_sha256": config_sha256,
            "plan_sha256": study.digest if study else None,
            "scope": scope,
        }
        if config.get("mode") not in ("demo", "music4all", "jamendo"):
            raise ValueError("mode must explicitly be demo, music4all or jamendo")
        self.mode = config["mode"]
        self.models = models
        self.service = RecommendationService(tuple(models.values()))
        self.tracks = {row["item_id"]: row for row in records}
        if len(self.tracks) != len(records):
            raise ValueError("duplicate catalog item")
        self.candidates = {
            version: tuple(item for item in model.index if item in self.tracks)
            for version, (_, model) in self.models.items()
        }
        provider = "sample" if self.mode == "demo" else self.mode
        self.catalog = TrackCatalog(
            tuple(
                CatalogTrack(
                    row["item_id"],
                    (
                        TrackSource(
                            TrackRef(provider, row.get("source_id", row["item_id"])),
                            "app-v1",
                            row.get("title"),
                            tuple(row.get("artists", [])),
                        ),
                    ),
                )
                for row in records
            )
        )
        self.artists = {}
        for row in records:
            for artist in row.get("artists", []):
                self.artists.setdefault(artist, []).append(row["item_id"])
        self.artists = {key: tuple(value) for key, value in self.artists.items()}
        self.playback = playback
        self.study_candidates = None
        if study:
            versions = [condition.version for condition in study.conditions]
            if any(version not in self.models for version in versions):
                raise ValueError("study model artifacts are not ready")
            shared = set.intersection(*(set(self.candidates[v]) for v in versions))
            self.study_candidates = tuple(
                sorted(
                    item
                    for item in shared
                    if self.tracks[item].get("title")
                    and self.tracks[item].get("artists")
                    and self.playback.availability(self.context(item, "preflight"), now()).status
                    == "available"
                )
            )
            if len(self.study_candidates) < study.top_k:
                raise ValueError("study requires enough verified metadata and playable tracks")
        self.store = store
        self.participants = Participants(self.store)
        self.sessions = {}
        self.lock = RLock()
        self.store.purge(now())

    def context(self, item, participant):
        return PlaybackContext(
            self.catalog.get(item).sources[0].ref, "local", participant, "preview"
        )

    def track(self, item, participant="catalog"):
        source = self.tracks[item]
        context = self.context(item, participant)
        availability = self.playback.availability(context, now())
        presentation = self.catalog.presentation(item, context, availability, at=now())
        return {
            "item_id": item,
            "title": source.get("title") or f"楽曲名未取得 · {item}",
            "artists": source.get("artists") or ["アーティスト未取得"],
            "artwork": source.get("artwork"),
            "provider": self.mode,
            "license_url": source.get("license_url"),
            "sample": self.mode == "demo",
            "playback_status": presentation.playback_status,
        }

    def start(self, persist, allow_text, *, consent_version=None, participant=None):
        current = now()
        self.expire()
        if len(self.sessions) >= 100:
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
        )
        if self.study:
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
        self.sessions[token_key(token)] = session
        self.save(token, session)
        return token, session

    def expire(self):
        current = now()
        self.store.purge(current)
        for key, session in list(self.sessions.items()):
            if session.log.session.retain_until <= current:
                session.log.withdraw()
                del self.sessions[key]

    def get(self, token):
        self.expire()
        key = token_key(token)
        session = self.sessions.get(key)
        if session is None and token:
            payload = self.store.get(key, now())
            if payload is not None:
                try:
                    session = self.restore(payload)
                except (KeyError, TypeError, ValueError, IndexError) as error:
                    raise PermissionError(
                        "保存済みセッションを再開できません。実施者に確認するか、削除して開始してください"
                    ) from error
                if len(self.sessions) >= 100:
                    raise ValueError("開発用セッション数の上限です")
                self.sessions[key] = session
        if session is None:
            raise PermissionError("セッションがありません。もう一度開始してください")
        return session

    def restore(self, payload):
        saved = payload["resume"]
        if saved["format"] != "music-recsys-resume-v1" or saved["context"] != self.resume_context:
            raise ValueError("saved model, catalog, audience or study changed")
        log = SessionLog.from_export(payload, now=now())
        profile = payload["initial_profile"]
        if profile["user_id"] != log.session.participant_id:
            raise ValueError("saved participant mismatch")
        session = Session(
            log,
            True,
            InitialProfile(
                profile["user_id"],
                tuple(profile["selected_item_ids"]),
                tuple(profile["selected_artist_ids"]),
            ),
            registered=payload.get("registered", False),
        )
        session.heard = set(saved["heard"])
        session.recommendations = {k: tuple(v) for k, v in saved["recommendations"].items()}
        session.replies = {int(k): v for k, v in saved["replies"].items()}
        session.playbacks = saved["playbacks"]
        session.playback_bindings = {k: tuple(v) for k, v in saved["playback_bindings"].items()}
        session.preview_bindings = saved["preview_bindings"]
        session.period, session.phase = saved["period"], saved["phase"]
        session.order, session.goals = payload["order"], payload["goals"]
        session.finished = payload["finished"]
        session.termination_reason = payload["termination_reason"]
        session.funnel = payload["funnel"]
        if session.phase not in {"profile", "listen", "evaluate"} or not (
            0 <= session.period < (len(session.order) or 1)
        ):
            raise ValueError("invalid saved phase or period")
        if self.study and (
            session.order != self.study.order(log.session.participant_id)
            or payload["plan_sha256"] != self.study.digest
        ):
            raise ValueError("saved assignment changed")
        return session

    def resume_participant(self, participant_id):
        saved = self.store.active_session(participant_id, self.resume_context, now())
        if saved is None:
            return None
        old_key, payload = saved
        session = self.restore(payload)
        token = secrets.token_urlsafe(32)
        self.store.rekey(old_key, token_key(token))
        self.sessions.pop(old_key, None)
        self.sessions[token_key(token)] = session
        return token, session

    def save(self, token, session):
        if session.persist:
            payload = session.log.export(now=now())
            payload.update(
                mode=self.mode,
                registered=session.registered,
                initial_profile=asdict(session.profile),
                goals=session.goals,
                recommendation_count=len(session.recommendations),
                finished=session.finished,
                termination_reason=session.termination_reason,
                funnel=session.funnel,
                study=self.study.model_dump() if self.study else None,
                plan_sha256=self.study.digest if self.study else None,
                order=session.order,
                resume={
                    "format": "music-recsys-resume-v1",
                    "context": self.resume_context,
                    "heard": sorted(session.heard),
                    "recommendations": session.recommendations,
                    "replies": session.replies,
                    "playbacks": session.playbacks,
                    "playback_bindings": session.playback_bindings,
                    "preview_bindings": session.preview_bindings,
                    "period": session.period,
                    "phase": session.phase,
                },
            )
            self.store.put(token_key(token), payload)

    def recommend(self, token, version, goal, top_k, text):
        session = self.get(token)
        if session.finished:
            raise ValueError("終了済みです。新しいセッションを開始してください")
        if version not in self.models:
            raise ValueError("指定モデルは未準備です")
        if len(session.recommendations) >= 100:
            raise ValueError("1セッションの推薦回数上限です")
        spec, model = self.models[version]
        candidates = self.candidates[version]
        if self.study:
            candidates = self.study_candidates
        request_id = str(uuid4())
        if goal == "familiar" and session.heard and not self.study:
            request = RecommendationRequest(
                request_id,
                session.profile.user_id,
                tuple(sorted(session.heard)),
                candidates,
                top_k,
                "repeat",
            )
        else:
            request = session.profile.to_request(
                request_id, candidates, artist_tracks=self.artists, top_k=top_k
            )
            if session.heard and not self.study:
                request = replace(
                    request, history=tuple(sorted(set(request.history) | session.heard))
                )
        if self.study and len(set(candidates) - set(request.history)) < top_k:
            raise ValueError("比較に必要な未聴候補が不足しています。初期選択を減らしてください")
        execution = self.service.recommend(request, ComparisonStrategy(version))
        if self.study:
            condition = self.study.conditions[session.order[session.period]]
            if condition.version != version:
                raise ValueError("割り当てられた実験条件とモデルが一致しません")
            if condition.required_route and execution.inference_route != condition.required_route:
                raise ValueError("実験条件に必要な入力が不足しています。実施者に確認してください")
        session.log.record(execution, now=now(), request_text=text or None)
        session.recommendations[request_id] = execution.response.item_ids
        session.goals[request_id] = {
            "goal": goal,
            "effective_task": request.task,
            "text_interpreted": False,
            "input_kind": getattr(request, "input_kind", "listening_history"),
            "initial_profile": asdict(session.profile),
            "heard_item_ids": sorted(session.heard),
            "top_k": top_k,
            "period": session.period + 1,
            "condition": self.study.conditions[session.order[session.period]].model_dump()
            if self.study
            else None,
        }
        reply = {
            "request_id": request_id,
            "tracks": [
                self.track(item, session.profile.user_id) for item in execution.response.item_ids
            ],
            "model_version": spec.version_id,
            "algorithm": spec.algorithm,
            "route": execution.inference_route,
            "latency_ms": execution.inference_seconds * 1000,
            "goal": goal,
            "effective_task": request.task,
            "notice": (
                "好きな曲・アーティストを選び直してください。共通する特徴のある候補がありません"
                if not execution.response.item_ids and spec.algorithm == "content-tags"
                else "まだ再生履歴がないため、初期プロフィールから候補を提案します"
                if goal == "familiar" and not session.heard
                else "おまかせは暫定的に未聴候補を提示します。既聴・未聴混合の総合推薦は未対応です"
                if goal == "auto"
                else None
            ),
            "mode": self.mode,
        }
        session.replies[session.period] = reply
        session.goals[request_id]["playability"] = {
            track["item_id"]: track["playback_status"] for track in reply["tracks"]
        }
        self.save(token, session)
        return reply

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
            {"kind": kind, "phase": phase, "period": session.period + 1, "at": now().isoformat()}
        )
        self.save(token, session)

    def event(self, token, data):
        session = self.get(token)
        if session.finished:
            raise ValueError("終了済みです")
        self.require_current_request(session, data["request_id"])
        if len(session.playbacks) >= 5000:
            raise ValueError("イベント上限です")
        if data["kind"] in ("play", "pause", "ended", "error"):
            binding = session.playback_bindings.get(data.get("playback_id"))
            if binding != (data["request_id"], data.get("item_id")):
                raise ValueError("再生IDと推薦曲が一致しません")
        event = InteractionEvent(
            data["event_id"],
            session.log.session.session_id,
            data["request_id"],
            data["kind"],
            now(),
            item_id=data.get("item_id"),
            rating=data.get("rating"),
            instrument_id="satisfaction-v1" if data["kind"] == "rating" else None,
            scale_min=1 if data["kind"] == "rating" else None,
            scale_max=5 if data["kind"] == "rating" else None,
            playback_id=data.get("playback_id"),
            position_seconds=data.get("position_seconds"),
            listened_seconds=data.get("listened_seconds"),
            response_latency_ms=data.get("response_latency_ms"),
            display_latency_ms=data.get("display_latency_ms"),
        )
        # イベントの重複再送は初回のサーバー時刻で扱う。
        previous = session.playbacks.get(event.event_id)
        if previous is not None:
            if previous != data:
                raise ValueError("イベントIDの内容が一致しません")
            return
        if event.kind == "play":
            context = self.context(event.item_id, session.profile.user_id)
            if self.mode != "jamendo":
                self.playback.file(context, now())
        session.log.append(event, now=now())
        session.playbacks[event.event_id] = data
        if event.kind == "play":
            session.heard.add(event.item_id)
        self.save(token, session)

    def require_current_request(self, session, request_id):
        if self.study and session.replies.get(session.period, {}).get("request_id") != request_id:
            raise ValueError("現在の体験に属する推薦ではありません")

    def delete(self, token):
        key = token_key(token)
        payload = self.store.get(key, now())
        if payload and payload.get("registered"):
            participant_id = payload["session"]["participant_id"]
            self.store.delete_participant(participant_id)
            for other, session in list(self.sessions.items()):
                if session.log.session.participant_id == participant_id:
                    session.log.withdraw()
                    del self.sessions[other]
            return
        session = self.sessions.pop(key, None)
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
    ):
        old = old_token
        participant, code = None, None
        if consent_version == "local-ui-v4":
            if not persist:
                raise ValueError("この説明版ではログ保存への同意が必要です")
            if participant_code:
                participant = self.participants.resolve(participant_code, now())
            elif old:
                stored = self.store.get(token_key(old), now())
                if stored and stored.get("registered"):
                    participant = self.participants.get(stored["session"]["participant_id"], now())
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
                    expires_at=now() + timedelta(days=retention_days), now=now()
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
        return token, current, code

    def set_profile(self, token, items, artists):
        current = self.get(token)
        if current.finished:
            raise ValueError("終了済みです")
        if self.study and current.recommendations:
            raise ValueError("比較中の初期プロフィールは固定されています")
        if set(items) - self.tracks.keys() or set(artists) - self.artists.keys():
            raise ValueError("選択した曲・アーティストがありません")
        current.profile = InitialProfile(current.profile.user_id, tuple(items), tuple(artists))
        self.save(token, current)
        if current.registered:
            self.participants.save_profile(
                current.profile.user_id,
                {"selected_item_ids": items, "selected_artist_ids": artists},
                self.resume_context["config_sha256"],
                now(),
            )
        return {"selected_count": len(items), "artist_count": len(artists)}

    def assigned_recommendation(self, token, version, goal, top_k, text):
        current = self.get(token)
        if current.finished:
            raise ValueError("終了済みです")
        if self.study:
            if current.phase != "listen":
                raise ValueError("試聴画面で実行してください")
            if current.period in current.replies:
                return current.replies[current.period]
            condition = self.study.conditions[current.order[current.period]]
            reply = self.recommend(token, condition.version, "discover", self.study.top_k, "")
        else:
            version = version
            reply = self.recommend(token, version, goal, top_k, text)
        return reply

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

    def bind_preview(self, token, item_id):
        current = self.get(token)
        if current.finished or current.phase != "profile" or current.recommendations:
            raise ValueError("好みを選ぶ画面でのみ試聴できます")
        if item_id not in self.tracks:
            raise ValueError("楽曲がありません")
        if len(current.preview_bindings) >= 500:
            raise ValueError("試聴回数上限です")
        context = self.context(item_id, current.profile.user_id)
        external = self.playback.resolve(context, now())
        if external is None:
            self.playback.file(context, now())
        preview_id = str(uuid4())
        current.preview_bindings[preview_id] = item_id
        self.save(token, current)
        return preview_id, external

    def bind_playback(self, token, item_id, request_id):
        current = self.get(token)
        self.require_current_request(current, request_id)
        if current.finished or item_id not in current.recommendations.get(request_id, ()):
            raise ValueError("このセッションの推薦曲ではありません")
        context = self.context(item_id, current.profile.user_id)
        external = self.playback.resolve(context, now())
        if len(current.playback_bindings) >= 500:
            raise ValueError("試聴回数上限です")
        playback_id = str(uuid4())
        current.playback_bindings[playback_id] = (request_id, item_id)
        self.save(token, current)
        return playback_id, external
