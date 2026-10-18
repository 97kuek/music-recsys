"""Observed online numerators, denominators, censoring and legacy compatibility."""

import copy
import json
import stat

import pytest

from src.domain.study.online_metrics import summarize_online


@pytest.fixture
def payload():
    events = []

    def event(kind, item=None, **fields):
        events.append(
            dict(
                event_id=str(len(events)),
                request_id="r",
                kind=kind,
                item_id=item,
                occurred_at="2026-01-01T00:00:00+00:00",
                **fields,
            )
        )

    for item in "abcd":
        event("impression", item)
    event("impression", "a")
    event("like", "a")
    event("like", "a")
    event("play", "a", playback_id="p1", listened_seconds=0)
    event("play", "a", playback_id="p1", listened_seconds=1)
    event("playback_progress", "a", playback_id="p1", listened_seconds=5, position_seconds=25)
    event("ended", "a", playback_id="p1", listened_seconds=10, position_seconds=30)
    event("play", "b", playback_id="p2", listened_seconds=0)
    event(
        "track_switch",
        "b",
        playback_id="p2",
        listened_seconds=4.9,
        was_playing=True,
        movement="direct",
        target_item_id="c",
    )
    event("evaluation_shown")
    event(
        "list_evaluation",
        rating=4,
        discovery_status="selected",
        discovery_item_ids=["a"],
        observation_complete=True,
    )
    return dict(
        schema_version=2,
        session=dict(session_id="s", participant_id="u"),
        requests=[
            dict(
                request_id="r",
                response=dict(item_ids=list("abcd"), usage=None),
                condition_id="condition-a",
                selection_seconds=0.01,
                inference_seconds=0.02,
                list_number=1,
            )
        ],
        goals={
            "r": {"playability": dict(a="available", b="available", c="available", d="unavailable")}
        },
        events=events,
    )


def test_unique_counts_thresholds_switches_and_discovery(payload):
    # Duplicate delivery is ignored as well as duplicate impressions/likes with new IDs.
    payload["events"].append(copy.deepcopy(payload["events"][0]))
    result = summarize_online(payload)
    row = result["lists"][0]
    assert row["like_rate"] == dict(value=0.25, numerator=1, denominator=4, missing=0)
    assert row["play_start_rate"]["value"] == pytest.approx(2 / 3)
    assert row["engaged_listen_rate"]["value"] == pytest.approx(1 / 3)
    assert [row["engaged_sensitivity"][str(t)]["numerator"] for t in (5, 10, 15, 20)] == [
        1,
        1,
        0,
        0,
    ]
    assert row["listened_seconds"] == {"a": 10, "b": 4.9}
    assert row["track_switch_rate"]["value"] == 0.5
    assert row["early_skip_rate"]["value"] == 0.5
    assert row["movement_counts"] == dict(next=0, previous=0, direct=1)
    assert result["satisfaction"]["value"] == 4
    assert result["discovery_success_tracks"] == 1
    assert result["discovery_list_success_rate"]["value"] == 1
    assert result["discovery_response_rate"]["value"] == 1
    assert row["usage"] is None


def test_missing_none_and_measured_zero_are_distinct(payload):
    evaluation = payload["events"][-1]
    evaluation.update(rating=None, discovery_status="none", discovery_item_ids=[])
    result = summarize_online(payload)
    assert result["satisfaction"]["value"] is None
    assert result["discovery_success_tracks"] == 0
    assert result["discovery_list_success_rate"]["value"] == 0
    evaluation.update(discovery_status="unanswered")
    result = summarize_online(payload)
    assert result["discovery_success_tracks"] is None
    assert result["discovery_list_success_rate"]["value"] is None
    assert result["discovery_response_rate"]["value"] == 0


def test_disconnect_does_not_turn_unobserved_actions_into_zero(payload):
    payload["events"].insert(0, dict(event_id="gap", request_id="r", kind="observation_gap"))
    row = summarize_online(payload)["lists"][0]
    assert row["like_rate"]["value"] is None
    assert row["like_rate"]["missing"] == 3
    assert row["engaged_listen_rate"]["value"] is None
    assert row["engaged_listen_rate"]["numerator"] == 1


@pytest.mark.parametrize("kind", ["pause", "ended", "error"])
def test_pause_end_error_are_never_skips(payload, kind):
    payload["events"] = [e for e in payload["events"] if e["kind"] != "track_switch"]
    payload["events"].append(
        dict(
            event_id="end",
            request_id="r",
            item_id="b",
            playback_id="p2",
            kind=kind,
            listened_seconds=4.9,
        )
    )
    row = summarize_online(payload)["lists"][0]
    assert row["track_switch_rate"]["value"] == 0
    assert row["early_skip_rate"]["value"] == 0
    if kind == "error":
        assert row["engaged_listen_rate"]["value"] is None


def test_at_ten_seconds_switch_is_not_early_and_paused_move_is_not_switch(payload):
    move = next(e for e in payload["events"] if e["kind"] == "track_switch")
    move["listened_seconds"] = 10
    assert summarize_online(payload)["lists"][0]["early_skip_rate"]["value"] == 0
    move["was_playing"] = False
    assert summarize_online(payload)["lists"][0]["track_switch_rate"]["value"] == 0


def test_legacy_events_missing_playability_and_no_answers_remain_missing(payload):
    payload["schema_version"] = 1
    payload["goals"] = {}
    payload["events"] = [
        e
        for e in payload["events"]
        if e["kind"] not in {"list_evaluation", "evaluation_shown", "track_switch"}
    ]
    row = summarize_online(payload)["lists"][0]
    assert row["satisfaction"] is None and row["discovery_success_tracks"] is None
    assert row["play_start_rate"]["value"] is None
    assert row["diagnostics"]["unknown_playability"] == 4


def test_multiple_playback_ids_sum_actual_seconds_without_readding_checkpoints(payload):
    payload["events"].extend(
        [
            dict(
                event_id="a2",
                request_id="r",
                item_id="a",
                kind="play",
                playback_id="p3",
                listened_seconds=0,
            ),
            dict(
                event_id="a3",
                request_id="r",
                item_id="a",
                kind="pause",
                playback_id="p3",
                listened_seconds=5,
            ),
        ]
    )
    row = summarize_online(payload)["lists"][0]
    assert row["listened_seconds"]["a"] == 15
    assert row["engaged_sensitivity"]["15"]["numerator"] == 1
    assert row["engaged_sensitivity"]["20"]["numerator"] == 0


def test_unmeasured_playback_time_is_not_fabricated_as_zero(payload):
    for e in payload["events"]:
        if e.get("item_id") == "a":
            e.pop("listened_seconds", None)
    row = summarize_online(payload)["lists"][0]
    assert "a" not in row["listened_seconds"]
    assert row["engaged_listen_rate"]["value"] is None
    assert row["diagnostics"]["unmeasured_playback_time"] == 1


def test_missing_start_event_is_not_reported_as_unplayed(payload):
    payload["events"] = [
        e for e in payload["events"] if not (e["kind"] == "play" and e.get("item_id") == "a")
    ]
    row = summarize_online(payload)["lists"][0]
    assert row["play_start_rate"]["value"] is None
    assert row["play_start_rate"]["missing"] == 1
    assert row["engaged_listen_rate"]["numerator"] == 1


def test_legacy_skip_cannot_establish_zero_new_switches(payload):
    payload["events"] = [e for e in payload["events"] if e["kind"] != "track_switch"]
    payload["events"].append(dict(event_id="legacy-skip", request_id="r", kind="skip", item_id="b"))
    assert summarize_online(payload)["lists"][0]["track_switch_rate"]["value"] is None


def test_cli_is_private_exclusive_and_does_not_modify_input(payload, tmp_path, monkeypatch):
    from src.interfaces.cli.online_metrics import main

    source, target = tmp_path / "export.json", tmp_path / "metrics.json"
    original = json.dumps({"format": "music-recsys-log-v1", "payload": payload})
    source.write_text(original)
    monkeypatch.setattr(
        "sys.argv", ["online_metrics", "--input", str(source), "--output", str(target)]
    )
    main()
    assert json.loads(target.read_text())["lists"][0]["satisfaction"] == 4
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    previous = target.read_bytes()
    with pytest.raises(FileExistsError):
        main()
    assert target.read_bytes() == previous and source.read_text() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export.json", "metrics.json"]
