"""Versioned online event invariants, independent of storage and HTTP."""

from src.domain.recommendation.contracts import validate_ids


def validate_online_event(event):
    if type(event.event_version) is not int or event.event_version not in (1, 2):
        raise ValueError("unsupported event version")
    added = {"track_switch", "evaluation_shown", "list_evaluation", "observation_gap"}
    if event.kind in added and event.event_version != 2:
        raise ValueError("new online events require version 2")
    if event.event_version == 2 and event.kind == "rating" and event.item_id is not None:
        raise ValueError("online satisfaction is list-level only")
    if event.kind == "track_switch":
        if (
            event.playback_id is None
            or event.listened_seconds is None
            or event.position_seconds is None
            or type(event.was_playing) is not bool
            or event.movement not in {"next", "previous", "direct"}
            or not event.target_item_id
            or event.target_item_id == event.item_id
        ):
            raise ValueError("track switch requires a bound playback, timing, direction and target")
    elif any(v is not None for v in (event.movement, event.target_item_id, event.was_playing)):
        raise ValueError("movement fields are only valid for track switches")
    validate_ids(event.discovery_item_ids)
    if event.kind == "list_evaluation":
        if event.discovery_status not in {"unanswered", "none", "selected"}:
            raise ValueError("explicit discovery answer state required")
        if (event.discovery_status == "selected") != bool(event.discovery_item_ids):
            raise ValueError("discovery selection does not match answer state")
        if type(event.observation_complete) is not bool:
            raise ValueError("list completion needs an observation status")
        if event.rating is not None and (
            type(event.rating) is not int or not 1 <= event.rating <= 5
        ):
            raise ValueError("list satisfaction must be an integer from 1 to 5")
    elif (
        event.discovery_status is not None
        or event.discovery_item_ids
        or event.observation_complete is not None
    ):
        raise ValueError("evaluation fields require a list evaluation")
    if (
        event.kind in {"evaluation_shown", "list_evaluation", "observation_gap"}
        and event.item_id is not None
    ):
        raise ValueError("list events cannot rate individual tracks")
