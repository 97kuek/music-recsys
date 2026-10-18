"""試聴権限・再生IDの結合・操作イベントの検証と記録。"""

from uuid import uuid4

from src.application.ports.study import Clock
from src.application.study.catalog import StudyCatalog
from src.application.study.sessions import SessionService
from src.domain.study.events import InteractionEvent


class PlaybackService:
    def __init__(self, sessions: SessionService, catalog: StudyCatalog, clock: Clock):
        self.sessions, self.catalog, self.clock = sessions, catalog, clock

    def event(self, token, data):
        session = self.sessions.get(token)
        # イベントの重複再送は初回のサーバー時刻で扱う。
        previous = session.playbacks.get(data["event_id"])
        if previous is not None:
            if previous != data:
                raise ValueError("イベントIDの内容が一致しません")
            # A previous attempt may have updated memory before the repository write failed.
            self.sessions.save(token, session)
            return
        if session.finished:
            raise ValueError("終了済みです")
        self.sessions.require_current_request(session, data["request_id"])
        if len(session.playbacks) >= 5000:
            raise ValueError("イベント上限です")
        if data["kind"] in ("play", "pause", "playback_progress", "ended", "error", "track_switch"):
            binding = session.playback_bindings.get(data.get("playback_id"))
            if binding != (data["request_id"], data.get("item_id")):
                raise ValueError("再生IDと推薦曲が一致しません")
        event = InteractionEvent(
            data["event_id"],
            session.log.session.session_id,
            data["request_id"],
            data["kind"],
            self.clock(),
            item_id=data.get("item_id"),
            rating=data.get("rating"),
            instrument_id=(
                "list-satisfaction-v2" if data["kind"] == "list_evaluation" else "satisfaction-v1"
            )
            if data.get("rating") is not None
            else None,
            scale_min=1 if data.get("rating") is not None else None,
            scale_max=5 if data.get("rating") is not None else None,
            playback_id=data.get("playback_id"),
            position_seconds=data.get("position_seconds"),
            listened_seconds=data.get("listened_seconds"),
            response_latency_ms=data.get("response_latency_ms"),
            display_latency_ms=data.get("display_latency_ms"),
            event_version=data.get("event_version", 1),
            movement=data.get("movement"),
            target_item_id=data.get("target_item_id"),
            was_playing=data.get("was_playing"),
            discovery_status=data.get("discovery_status"),
            discovery_item_ids=tuple(data.get("discovery_item_ids", ())),
            observation_complete=data.get("observation_complete"),
        )
        if event.kind == "play":
            context = self.catalog.context(event.item_id, session.profile.user_id)
            self.catalog.playback.file(context, self.clock())
        session.log.append(event, now=self.clock())
        session.playbacks[event.event_id] = data
        if event.kind == "play":
            session.heard.add(event.item_id)
        self.sessions.save(token, session)
        if event.kind == "like" and session.long_term_consent:
            from src.application.study.preferences import PreferenceService

            PreferenceService(self.sessions).for_participant(session.profile.user_id)

    def bind_preview(self, token, item_id):
        current = self.sessions.get(token)
        if current.finished or current.phase != "profile" or current.recommendations:
            raise ValueError("好みを選ぶ画面でのみ試聴できます")
        if item_id not in self.catalog.tracks:
            raise ValueError("楽曲がありません")
        if len(current.preview_bindings) >= 500:
            raise ValueError("試聴回数上限です")
        context = self.catalog.context(item_id, current.profile.user_id)
        external = self.catalog.playback.resolve(context, self.clock())
        if external is None:
            self.catalog.playback.file(context, self.clock())
        preview_id = str(uuid4())
        current.preview_bindings[preview_id] = item_id
        self.sessions.save(token, current)
        return preview_id, external

    def bind_playback(self, token, item_id, request_id):
        current = self.sessions.get(token)
        self.sessions.require_current_request(current, request_id)
        if current.finished or item_id not in current.recommendations.get(request_id, ()):
            raise ValueError("このセッションの推薦曲ではありません")
        context = self.catalog.context(item_id, current.profile.user_id)
        external = self.catalog.playback.resolve(context, self.clock())
        if len(current.playback_bindings) >= 500:
            raise ValueError("試聴回数上限です")
        playback_id = str(uuid4())
        current.playback_bindings[playback_id] = (request_id, item_id)
        self.sessions.save(token, current)
        return playback_id, external

    def preview_file(self, token, preview_id):
        current = self.sessions.get(token)
        item = current.preview_bindings.get(preview_id)
        if (
            current.finished
            or current.phase != "profile"
            or current.recommendations
            or item is None
        ):
            raise LookupError("試聴が見つかりません")
        return self.catalog.playback.file(
            self.catalog.context(item, current.profile.user_id), self.clock()
        )

    def playback_file(self, token, playback_id):
        current = self.sessions.get(token)
        binding = current.playback_bindings.get(playback_id)
        if current.finished or binding is None:
            raise LookupError("試聴が見つかりません")
        self.sessions.require_current_request(current, binding[0])
        return self.catalog.playback.file(
            self.catalog.context(binding[1], current.profile.user_id), self.clock()
        )
