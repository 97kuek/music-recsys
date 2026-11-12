"""Strict HTTP request schemas, independent of route registration and runtime state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Start(Body):
    consent_version: (
        Literal[
            "local-ui-v2",
            "local-ui-v3",
            "local-ui-v4",
            "local-ui-v5",
            "local-ui-v6",
            "local-ui-v7",
            "local-ui-v8",
        ]
        | None
    ) = None
    persist: bool = False
    allow_text: bool = False


class Profile(Body):
    items: list[str] = Field(default_factory=list, max_length=30)
    artists: list[str] = Field(default_factory=list, max_length=10)


class Recommend(Body):
    version: str | None = Field(default=None, max_length=200)
    goal: Literal["discover", "familiar", "auto"] = "auto"
    top_k: int = Field(default=10, ge=2, le=20)
    text: str = Field(default="", max_length=500)


class Event(Body):
    event_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
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
    item_id: str | None = Field(default=None, max_length=200)
    rating: int | None = Field(default=None, ge=1, le=5)
    playback_id: str | None = Field(default=None, max_length=100)
    position_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)

    listened_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    response_latency_ms: float | None = Field(default=None, ge=0, le=3600000, allow_inf_nan=False)
    display_latency_ms: float | None = Field(default=None, ge=0, le=3600000, allow_inf_nan=False)
    event_version: Literal[1, 2] = 1
    movement: Literal["next", "previous", "direct"] | None = None
    target_item_id: str | None = Field(default=None, max_length=200)
    was_playing: bool | None = None
    discovery_status: Literal["unanswered", "none", "selected"] | None = None
    discovery_item_ids: list[str] = Field(default_factory=list, max_length=20)
    observation_complete: bool | None = None


class Preview(Body):
    item_id: str = Field(min_length=1, max_length=200)


class Playback(Body):
    request_id: str = Field(max_length=100)
    item_id: str = Field(max_length=200)


class Progress(Body):
    phase: Literal["profile", "listen", "evaluate"]
    kind: Literal["enter", "hidden", "visible", "exit"] = "enter"


class Advance(Body):
    period: int = Field(ge=1)
