"""起動時ロード、セッション状態、既存推薦・ログ契約への接続。"""

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4

from music_recsys.catalog import CatalogTrack, PlaybackContext, TrackCatalog, TrackRef, TrackSource
from music_recsys.events import InteractionEvent, SessionLog, StudySession
from music_recsys.jamendo import JamendoPlayback
from music_recsys.listening_data import file_sha256
from music_recsys.model_artifacts import load_model
from music_recsys.profiles import InitialProfile
from music_recsys.recommenders.tag_seed import TagSeedRecommender
from music_recsys.serving import ModelSpec, RecommendationService, Selection
from music_recsys.types import RecommendationRequest
from music_recsys.webapp.playback import AudioAsset, LocalPlayback
from music_recsys.webapp.storage import LocalStore


def now():
    return datetime.now(UTC)


def token_key(token):
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class ComparisonStrategy:
    version_id: str

    def select(self, request, models):
        return Selection("manual-comparison", "1", self.version_id)


@dataclass
class Session:
    log: SessionLog
    persist: bool
    profile: InitialProfile
    heard: set = field(default_factory=set)
    recommendations: dict = field(default_factory=dict)
    playbacks: dict = field(default_factory=dict)
    playback_bindings: dict = field(default_factory=dict)
    preview_bindings: dict = field(default_factory=dict)
    goals: dict = field(default_factory=dict)
    finished: bool = False
    termination_reason: str | None = None
    order: list = field(default_factory=list)
    period: int = 0
    phase: str = "profile"
    funnel: list = field(default_factory=list)
    replies: dict = field(default_factory=dict)


class Runtime:
    def __init__(self, config_path, *, database, study=None):
        self.study = study
        path = Path(config_path).resolve()
        self.config = json.loads(path.read_text())
        if self.config.get("mode") not in ("demo", "music4all", "jamendo"):
            raise ValueError("mode must explicitly be demo, music4all or jamendo")
        self.mode = self.config["mode"]
        self.models = {}
        for entry in self.config["models"]:
            if entry.get("format") == "content-tags":
                feature_path = path.parent / entry["path"]
                spec_path = path.parent / entry["spec"]
                if (
                    file_sha256(feature_path) != entry["sha256"]
                    or file_sha256(spec_path) != entry["spec_sha256"]
                ):
                    raise ValueError("content model checksum mismatch")
                spec = ModelSpec(**json.loads(spec_path.read_text()))
                if (
                    spec.training_ref != entry["sha256"]
                    or spec.catalog_ref != self.config["catalog"]["sha256"]
                ):
                    raise ValueError("content model provenance mismatch")
                model = TagSeedRecommender(json.loads(feature_path.read_text()))
            else:
                spec, model = load_model(
                    path.parent / entry["path"],
                    expected_manifest_sha256=entry["sha256"],
                    als_fold_in=entry.get("als_fold_in", False),
                )
            if spec.version_id in self.models:
                raise ValueError("duplicate model version")
            self.models[spec.version_id] = (spec, model)
        self.service = RecommendationService(tuple(self.models.values()))
        catalog_path = path.parent / self.config["catalog"]["path"]
        if file_sha256(catalog_path) != self.config["catalog"]["sha256"]:
            raise ValueError("catalog checksum mismatch")
        records = json.loads(catalog_path.read_text())
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
        assets = {}
        for item, meta in self.config.get("audio", {}).items():
            audio_path = (path.parent / meta["path"]).resolve()
            if (
                not audio_path.is_relative_to(path.parent)
                or file_sha256(audio_path) != meta["sha256"]
            ):
                raise ValueError("invalid audio file")
            if item not in self.tracks or not meta["license_ref"]:
                raise ValueError("unlicensed or unmatched audio")
            if self.mode == "music4all" and meta["sample"]:
                raise ValueError("sample audio cannot masquerade as a Music4All recording")
            assets[item] = AudioAsset(
                audio_path,
                meta["license_ref"],
                datetime.fromisoformat(meta["expires_at"]),
                meta["sample"],
            )
        self.playback = (
            JamendoPlayback(records) if self.mode == "jamendo" else LocalPlayback(assets)
        )
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
        self.store = LocalStore(database)
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

    def start(self, persist, allow_text, *, consent_version=None):
        current = now()
        self.expire()
        if len(self.sessions) >= 100:
            raise ValueError("開発用セッション数の上限です")
        token = secrets.token_urlsafe(32)
        participant = f"app:{uuid4()}"
        log = SessionLog(
            StudySession(
                str(uuid4()),
                participant,
                "local-development",
                "mvp-v1",
                "comparison-only",
                "manual",
                1,
                consent_version or ("local-save-v1" if persist else "memory-only"),
                current,
                current + timedelta(hours=24),
                persist and allow_text,
            )
        )
        session = Session(log, persist, InitialProfile(participant))
        if self.study:
            session.order = self.study.order(participant)
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
        session = self.sessions.get(token_key(token))
        if session is None:
            raise PermissionError("セッションがありません。もう一度開始してください")
        return session

    def save(self, token, session):
        if session.persist:
            payload = session.log.export(now=now())
            payload.update(
                mode=self.mode,
                initial_profile=asdict(session.profile),
                goals=session.goals,
                recommendation_count=len(session.recommendations),
                finished=session.finished,
                termination_reason=session.termination_reason,
                funnel=session.funnel,
                study=self.study.model_dump() if self.study else None,
                plan_sha256=self.study.digest if self.study else None,
                order=session.order,
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
        session = self.sessions.pop(key, None)
        if session is not None:
            session.log.withdraw()
        self.store.delete(key)
