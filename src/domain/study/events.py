"""オンライン研究のログ契約。永続化・認証・同意取得UIは呼び出し側で担う。"""

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from src.domain.catalog import aware_time
from src.domain.recommendation.contracts import validate_id
from src.domain.recommendation.models import RecommendationExecution


@dataclass(frozen=True)
class StudySession:
    session_id: str
    participant_id: str
    study_id: str
    protocol_version: str
    condition_id: str
    sequence_id: str
    period: int
    consent_version: str
    started_at: datetime
    retain_until: datetime
    allow_request_text: bool = False
    offline_run_ref: str | None = None

    def __post_init__(self):
        for name in (
            "session_id",
            "participant_id",
            "study_id",
            "protocol_version",
            "condition_id",
            "sequence_id",
            "consent_version",
        ):
            validate_id(getattr(self, name))
        if self.offline_run_ref is not None:
            validate_id(self.offline_run_ref)
        if type(self.period) is not int or self.period < 1:
            raise ValueError("period must be a positive integer")
        if type(self.allow_request_text) is not bool:
            raise ValueError("allow_request_text must be boolean")
        aware_time(self.started_at)
        aware_time(self.retain_until)
        if self.retain_until <= self.started_at:
            raise ValueError("retention must end after session starts")


@dataclass(frozen=True)
class InteractionEvent:
    event_id: str
    session_id: str
    request_id: str
    kind: Literal[
        "impression",
        "play",
        "pause",
        "playback_progress",
        "ended",
        "error",
        "skip",
        "save",
        "like",
        "rating",
        "track_switch",
        "evaluation_shown",
        "list_evaluation",
        "observation_gap",
    ]
    occurred_at: datetime
    item_id: str | None = None
    rating: float | None = None
    instrument_id: str | None = None
    scale_min: float | None = None
    scale_max: float | None = None
    playback_id: str | None = None
    position_seconds: float | None = None

    listened_seconds: float | None = None
    response_latency_ms: float | None = None
    display_latency_ms: float | None = None
    event_version: int = 1
    movement: str | None = None
    target_item_id: str | None = None
    was_playing: bool | None = None
    discovery_status: str | None = None
    discovery_item_ids: tuple[str, ...] = ()
    observation_complete: bool | None = None

    def __post_init__(self):
        from src.domain.study.online_events import validate_online_event

        validate_online_event(self)
        for field in ("listened_seconds", "response_latency_ms", "display_latency_ms"):
            value = getattr(self, field)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("client measurements must be finite and nonnegative")
        if self.kind == "playback_progress" and (
            self.playback_id is None
            or self.listened_seconds is None
            or self.position_seconds is None
        ):
            raise ValueError("playback progress requires playback ID, position and listening time")
        if self.listened_seconds is not None and self.playback_id is None:
            raise ValueError("listening time requires playback ID")
        for value in (self.event_id, self.session_id, self.request_id):
            validate_id(value)
        aware_time(self.occurred_at)
        if self.kind not in (
            "impression",
            "play",
            "pause",
            "playback_progress",
            "ended",
            "error",
            "skip",
            "save",
            "like",
            "rating",
            "track_switch",
            "evaluation_shown",
            "list_evaluation",
            "observation_gap",
        ):
            raise ValueError("unknown event kind")
        if self.playback_id is not None:
            validate_id(self.playback_id)
        if self.position_seconds is not None and (
            type(self.position_seconds) not in (int, float)
            or not math.isfinite(self.position_seconds)
            or self.position_seconds < 0
        ):
            raise ValueError("position_seconds must be finite nonnegative or None")
        if self.item_id is not None:
            validate_id(self.item_id)
        if self.kind == "rating" or (self.kind == "list_evaluation" and self.rating is not None):
            validate_id(self.instrument_id)
            values = (self.rating, self.scale_min, self.scale_max)
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError("rating and scale must be finite numbers")
            if (
                not self.scale_min < self.scale_max
                or not self.scale_min <= self.rating <= self.scale_max
            ):
                raise ValueError("rating is outside scale")
        else:
            if self.kind not in {"evaluation_shown", "list_evaluation", "observation_gap"}:
                validate_id(self.item_id)
            if any(
                v is not None
                for v in (self.rating, self.instrument_id, self.scale_min, self.scale_max)
            ):
                raise ValueError("rating fields are only valid for rating events")


class SessionLog:
    """同意済みセッション用のメモリ内参照実装。返却済みコピーは削除できない。"""

    def __init__(self, session: StudySession):
        self.session = session
        self._requests: dict[str, dict] = {}
        self._events: dict[str, dict] = {}
        self._withdrawn = False

    @classmethod
    def from_export(cls, payload: dict, *, now: datetime):
        """Restore a local, consented export while retaining original IDs and timestamps."""
        import copy

        if payload.get("schema_version") not in (1, 2):
            raise ValueError("unsupported session log schema")
        fields = dict(payload["session"])
        for key in ("started_at", "retain_until"):
            fields[key] = datetime.fromisoformat(fields[key])
        log = cls(StudySession(**fields))
        log._check(now)
        for row in payload["requests"]:
            request_id = row["request_id"]
            validate_id(request_id)
            recorded = datetime.fromisoformat(row["recorded_at"])
            aware_time(recorded)
            if (
                request_id in log._requests
                or row["response"]["request_id"] != request_id
                or not log.session.started_at <= recorded <= now
            ):
                raise ValueError("invalid saved request")
            log._requests[request_id] = copy.deepcopy(row)
        for raw in payload["events"]:
            row = {**raw, "occurred_at": datetime.fromisoformat(raw["occurred_at"])}
            row["discovery_item_ids"] = tuple(row.get("discovery_item_ids", ()))
            log.append(InteractionEvent(**row), now=now)
        return log

    def _check(self, now: datetime) -> None:
        aware_time(now)
        if now >= self.session.retain_until:
            self._requests.clear()
            self._events.clear()
            self._withdrawn = True
        if self._withdrawn or now < self.session.started_at:
            raise ValueError("session is withdrawn, expired, or not yet active")

    def record(
        self,
        execution: RecommendationExecution,
        *,
        now: datetime,
        request_text: str | None = None,
        condition_id: str | None = None,
        parent_request_id: str | None = None,
        adjustment_scope: str = "session",
    ) -> None:
        self._check(now)
        request = execution.request
        if request.request_id in self._requests:
            raise ValueError("request_id already recorded")
        # 別ユーザーの実行結果をセッションへ誤結合しない。
        if request.user_id != self.session.participant_id:
            raise ValueError("request user must be the session participant pseudonym")
        if execution.response.request_id != request.request_id:
            raise ValueError("response request_id mismatch")
        if execution.selection.model_version_id != execution.model.version_id:
            raise ValueError("selected model mismatch")
        for seconds in (execution.selection_seconds, execution.inference_seconds):
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError("latency must be finite and nonnegative")
        row = {
            "request_id": request.request_id,
            "recorded_at": now.isoformat(),
            "task": request.task,
            "input_kind": getattr(request, "input_kind", "listening_history"),
            "top_k": request.top_k,
            "candidate_count": len(request.candidate_ids),
            "history_count": len(request.history),
            "model": {**asdict(execution.model), "parameters": execution.model.parameters},
            "selection": asdict(execution.selection),
            "response": asdict(execution.response),
            "selection_seconds": execution.selection_seconds,
            "inference_seconds": execution.inference_seconds,
            "inference_route": execution.inference_route,
        }
        parent = self._requests.get(parent_request_id) if parent_request_id else None
        if parent_request_id and (parent is None or parent.get("condition_id") != condition_id):
            raise ValueError("parent request must belong to the same session and condition")
        adjustment = parent.get("adjustment_index", 0) + 1 if parent else 0
        if adjustment_scope not in {"session", "condition"}:
            raise ValueError("invalid adjustment scope")
        if adjustment_scope == "condition" and (
            condition_id is None
            or (
                parent is None
                and any(r.get("condition_id") == condition_id for r in self._requests.values())
            )
        ):
            raise ValueError("condition scope requires one initial list per condition")
        if (
            parent
            and sum(
                r.get("parent_request_id") is not None
                and (adjustment_scope == "session" or r.get("condition_id") == condition_id)
                for r in self._requests.values()
            )
            >= 2
        ):
            raise ValueError(f"at most two adjustments per {adjustment_scope}")
        if parent and any(
            r.get("parent_request_id") == parent_request_id for r in self._requests.values()
        ):
            raise ValueError("adjustments must form a single chain")
        row.update(
            condition_id=condition_id,
            parent_request_id=parent_request_id,
            adjustment_index=adjustment,
            list_number=adjustment + 1,
        )
        del row["model"]["parameters_json"]
        if request_text is not None and self.session.allow_request_text:
            validate_id(request_text)
            row["request_text"] = request_text
        self._requests[request.request_id] = row

    def append(self, event: InteractionEvent, *, now: datetime) -> None:
        self._check(now)
        if event.session_id != self.session.session_id or event.request_id not in self._requests:
            raise ValueError("event must reference a recorded request in this session")
        request = self._requests[event.request_id]
        if not datetime.fromisoformat(request["recorded_at"]) <= event.occurred_at <= now:
            raise ValueError("event must occur after the request and not in the future")
        if request["response"]["status"] != "ok":
            raise ValueError("cannot interact with a failed recommendation")
        if event.item_id is not None and event.item_id not in request["response"]["item_ids"]:
            raise ValueError("event item must be a recommended item")
        if event.event_id not in self._events and any(
            e["kind"] == "list_evaluation" and e["request_id"] == event.request_id
            for e in self._events.values()
        ):
            raise ValueError("a list can be evaluated only once; it is already closed")
        if (
            event.target_item_id is not None
            and event.target_item_id not in request["response"]["item_ids"]
        ):
            raise ValueError("switch target must be a recommended item")
        if event.kind == "list_evaluation":
            existing = [e for e in self._events.values() if e["request_id"] == event.request_id]
            if any(e["kind"] == "rating" and e.get("item_id") is None for e in existing):
                raise ValueError("list satisfaction was already recorded in the legacy format")
            if any(
                e["kind"] == "list_evaluation" and e["event_id"] != event.event_id for e in existing
            ):
                raise ValueError("a list can be evaluated only once")
            liked = {e["item_id"] for e in existing if e["kind"] == "like"}
            if set(event.discovery_item_ids) - liked:
                raise ValueError("discovery answers must reference liked tracks")
        row = {**asdict(event), "occurred_at": event.occurred_at.isoformat()}
        row["discovery_item_ids"] = list(event.discovery_item_ids)
        previous = self._events.get(event.event_id)
        if previous is not None and previous != row:
            raise ValueError("event_id collision")
        self._events[event.event_id] = row

    def withdraw(self) -> None:
        self._withdrawn = True
        self._requests.clear()
        self._events.clear()

    def export(self, *, now: datetime) -> dict:
        import copy

        self._check(now)
        session = asdict(self.session)
        for field in ("started_at", "retain_until"):
            session[field] = session[field].isoformat()
        return copy.deepcopy(
            {
                "schema_version": 2
                if self.session.consent_version in {"local-ui-v5", "local-ui-v6"}
                or any(e.get("event_version") == 2 for e in self._events.values())
                else 1,
                "session": session,
                "requests": list(self._requests.values()),
                "events": list(self._events.values()),
            }
        )
