"""Online list metrics from observed events; never infer missing observations as zero."""

from collections import defaultdict
from statistics import mean

THRESHOLDS = (5, 10, 15, 20)


def fraction(numerator, denominator, missing=0):
    return {
        "value": numerator / denominator if denominator and not missing else None,
        "numerator": numerator,
        "denominator": denominator,
        "missing": missing,
    }


def summarize_online(payload):
    payload = payload.get("payload", payload)
    if payload.get("schema_version") not in (1, 2):
        raise ValueError("unsupported session log")
    events = {}
    for event in payload["events"]:
        previous = events.get(event["event_id"])
        if previous is not None and previous != event:
            raise ValueError("conflicting event IDs")
        events[event["event_id"]] = event
    grouped = defaultdict(list)
    for event in events.values():
        grouped[event["request_id"]].append(event)
    lists = []
    for request in payload["requests"]:
        request_id = request["request_id"]
        goal = payload.get("goals", {}).get(request_id, {})
        row = list_metrics(request, grouped[request_id], goal)
        row.update(
            participant_id=payload["session"]["participant_id"],
            session_id=payload["session"]["session_id"],
            request_id=request_id,
            condition_id=request.get("condition_id")
            or (goal.get("condition") or {}).get("condition_id"),
            list_number=request.get("list_number", 1),
            adjustment_index=request.get("adjustment_index", 0),
            parent_request_id=request.get("parent_request_id"),
            period=goal.get("period"),
        )
        lists.append(row)
    ratings = [row["satisfaction"] for row in lists if row["satisfaction"] is not None]
    offered = [row for row in lists if row["discovery_offered"]]
    answered = [row for row in offered if row["discovery_status"] in {"none", "selected"}]
    return {
        "format": "music-recsys-online-metrics-v1",
        "lists": lists,
        "satisfaction": {
            "value": mean(ratings) if ratings else None,
            "sum": sum(ratings),
            "answers": len(ratings),
            "missing": len(lists) - len(ratings),
        },
        "discovery_success_tracks": sum(row["discovery_success_tracks"] or 0 for row in answered)
        if answered
        else None,
        "discovery_list_success_rate": fraction(
            sum(bool(row["discovery_success_tracks"]) for row in answered), len(answered)
        ),
        "discovery_response_rate": fraction(len(answered), len(offered)),
    }


def list_metrics(request, events, goal):
    items = set(request["response"]["item_ids"])
    shown = {
        e["item_id"] for e in events if e["kind"] == "impression" and e.get("item_id") in items
    }
    liked = {e["item_id"] for e in events if e["kind"] == "like" and e.get("item_id") in items}
    evaluations = [e for e in events if e["kind"] == "list_evaluation"]
    evaluation = evaluations[0] if evaluations else {}
    complete = bool(evaluation.get("observation_complete")) and not any(
        e["kind"] == "observation_gap" for e in events
    )
    playability = goal.get("playability", {})
    playable = {i for i in shown if playability.get(i) == "available"}
    unknown_playability = {
        i for i in shown if playability.get(i) not in {"available", "unavailable", "expired"}
    }
    plays = defaultdict(list)
    for e in events:
        if e.get("playback_id") and e.get("item_id") in items:
            plays[e["playback_id"]].append(e)
    started, seconds, errors = set(), defaultdict(float), set()
    untimed = set()
    unconfirmed_starts = set()
    switches, early, uncertain = 0, 0, 0
    started_playbacks = 0
    directions = dict.fromkeys(("next", "previous", "direct"), 0)
    for playback_events in plays.values():
        item = playback_events[0]["item_id"]
        measurements = [
            e["listened_seconds"] for e in playback_events if e.get("listened_seconds") is not None
        ]
        if measurements:
            seconds[item] += max(measurements)
        else:
            untimed.add(item)
        if any(e["kind"] == "error" for e in playback_events):
            errors.add(item)
        if not any(e["kind"] == "play" for e in playback_events):
            unconfirmed_starts.add(item)
            continue
        started.add(item)
        started_playbacks += 1
        moved = next(
            (
                e
                for e in playback_events
                if e["kind"] == "track_switch" and e.get("was_playing") is True
            ),
            None,
        )
        if moved:
            switches += 1
            early += moved["listened_seconds"] < 10
            directions[moved["movement"]] += 1
        elif not complete and not any(
            e["kind"] in {"ended", "error", "track_switch"} for e in playback_events
        ):
            uncertain += 1

    def track_rate(positives, denominator, *, engagement=False):
        negative = denominator - positives
        missing = len(
            negative
            if not complete
            else (negative & (errors | untimed | unconfirmed_starts) if engagement else set())
        )
        return fraction(
            len(positives & denominator),
            len(denominator) + len(unknown_playability),
            missing + len(unknown_playability),
        )

    like_rate = fraction(len(liked & shown), len(shown), len(shown - liked) if not complete else 0)
    legacy_skips = sum(e["kind"] == "skip" for e in events)
    if legacy_skips and started_playbacks:
        uncertain = max(uncertain, 1)
    thresholds = {
        str(t): track_rate({i for i, s in seconds.items() if s >= t}, playable, engagement=True)
        for t in THRESHOLDS
    }
    shown_events = [e for e in events if e["kind"] == "evaluation_shown"]
    offered = any(
        any(e["kind"] == "like" and e["occurred_at"] <= q["occurred_at"] for e in events)
        for q in shown_events
    )
    discovery_status = evaluation.get("discovery_status", "unanswered")
    discovery = evaluation.get("discovery_item_ids", ())
    if set(discovery) - liked:
        raise ValueError("discovery success requires a like")
    # Old item-specific ratings are not reinterpreted as list satisfaction.
    ratings = [e["rating"] for e in events if e["kind"] == "rating" and e.get("item_id") is None]
    satisfaction = (
        evaluation.get("rating") if evaluations else (ratings[0] if len(ratings) == 1 else None)
    )
    response_ms = [
        e["response_latency_ms"]
        for e in events
        if e["kind"] == "impression" and e.get("response_latency_ms") is not None
    ]
    display_ms = [
        e["display_latency_ms"]
        for e in events
        if e["kind"] == "impression" and e.get("display_latency_ms") is not None
    ]
    return {
        "satisfaction": satisfaction,
        "like_rate": like_rate,
        "play_start_rate": track_rate(started, playable, engagement=True),
        "engaged_listen_rate": thresholds["10"],
        "engaged_sensitivity": thresholds,
        "track_switch_rate": fraction(switches, started_playbacks, uncertain),
        "early_skip_rate": fraction(early, started_playbacks, uncertain),
        "movement_counts": directions,
        "listened_seconds": dict(seconds),
        "discovery_offered": offered,
        "discovery_status": discovery_status,
        "discovery_success_tracks": len(set(discovery))
        if discovery_status != "unanswered"
        else None,
        "observation_complete": complete,
        "diagnostics": {
            "shown": len(shown),
            "not_shown": len(items - shown),
            "liked_without_impression": len(liked - shown),
            "unavailable": len(shown - playable - unknown_playability),
            "unknown_playability": len(unknown_playability),
            "playback_errors": len(errors),
            "unmeasured_playback_time": len(untimed),
            "missing_play_start": len(unconfirmed_starts),
            "legacy_skip_events": legacy_skips,
        },
        "latency": {
            "selection_seconds": request.get("selection_seconds"),
            "inference_seconds": request.get("inference_seconds"),
            "response_latency_ms": min(response_ms) if response_ms else None,
            "first_display_latency_ms": min(display_ms) if display_ms else None,
        },
        "usage": request["response"].get("usage"),
    }
