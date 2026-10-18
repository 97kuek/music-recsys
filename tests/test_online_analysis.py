"""Exploratory comparisons retain pairing, lineage, missingness and source provenance."""

import copy
import csv
import io
import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.study.online_analysis import analyze_online
from src.domain.study.plan import Condition, StudyPlan
from src.infrastructure.persistence.sessions import LocalStore
from src.infrastructure.research.online import OnlineStudyReader
from src.interfaces.online_export import TABLES, analysis_csv

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def plan(design="exploratory_standard"):
    return StudyPlan(
        "pilot",
        42,
        5,
        tuple(
            Condition(
                c,
                f"pending-{c}",
                controller=None
                if design.endswith("standard")
                else ("llm_single" if c == "A" else "reactive_agent"),
            )
            for c in ("ABC" if design.endswith("standard") else "AB")
        ),
        design,
    )


def session_payload(participant, session_id, ratings, *, study=None):
    study = study or plan()
    requests, events, goals = [], [], {}
    for condition, values in ratings.items():
        parent = None
        for adjustment, rating in enumerate(values):
            rid = f"{condition}{adjustment}"
            requests.append(
                dict(
                    request_id=rid,
                    condition_id=condition,
                    adjustment_index=adjustment,
                    list_number=adjustment + 1,
                    parent_request_id=parent,
                    selection_seconds=0,
                    inference_seconds=0.1,
                    response=dict(item_ids=list("abcde"), usage=None),
                )
            )
            goals[rid] = dict(
                period=[c.condition_id for c in study.conditions].index(condition) + 1,
                initial_profile=dict(
                    user_id=participant, selected_item_ids=["seed"], selected_artist_ids=[]
                ),
                playability=dict.fromkeys("abcde", "available"),
            )
            for item in "abcde":
                events.append(
                    dict(event_id=f"{rid}:{item}", request_id=rid, kind="impression", item_id=item)
                )
            events.append(
                dict(
                    event_id=f"{rid}:rating",
                    request_id=rid,
                    kind="list_evaluation",
                    rating=rating,
                    discovery_status="unanswered",
                    discovery_item_ids=[],
                    observation_complete=True,
                )
            )
            parent = rid
    return dict(
        schema_version=2,
        session=dict(
            participant_id=participant,
            session_id=session_id,
            study_id=study.protocol_id,
            started_at=NOW.isoformat(),
            retain_until=(NOW + timedelta(days=1)).isoformat(),
            consent_version="local-ui-v5",
        ),
        study=study.model_dump(),
        plan_sha256=study.digest,
        order=list(range(len(study.conditions))),
        requests=requests,
        events=events,
        goals=goals,
        termination_reason="completed",
    )


def experiment(*payloads, **kwargs):
    return analyze_online(payloads, now=NOW, **kwargs)["experiments"][0]


def test_repeated_sessions_are_one_participant_and_differences_are_paired():
    p1 = session_payload("p1", "s1", dict(A=[1], B=[4], C=[None]))
    p2 = session_payload("p1", "s2", dict(A=[3], B=[2], C=[5]))
    p3 = session_payload("p2", "s3", dict(A=[5], B=[None]))
    report = experiment(p1, p2, p3, copy.deepcopy(p1))
    assert report["participants"] == 2 and report["session_count"] == 3
    a, b, c = report["conditions"]
    assert a["initial"]["mean"] == 3.5  # mean(mean(1,3), 5), not mean(1,3,5)
    assert a["initial"]["median"] == 3.5
    assert a["initial"]["distribution"] == {"1": 1, "2": 0, "3": 1, "4": 0, "5": 1}
    assert c["initial"]["answers"] == 1 and c["initial"]["missing"] == 2
    pair = next(
        p
        for p in report["paired_differences"]
        if p["participant_id"] == "p1" and p["stage"] == "initial" and p["condition_b"] == "B"
    )
    assert pair["difference_b_minus_a"] == 1 and pair["session_ids"] == ["s1", "s2"]
    assert b["behavior"]["like_rate"]["mean"] == 0  # observed zero
    assert len(report["lists"]) == 8
    assert all(r["event_ids"] and len(r["source_sha256"]) == 64 for r in report["lists"])


def test_no_adjustment_missing_answers_and_measured_improvement_are_distinct():
    study = plan("exploratory_strategy")
    report = experiment(
        session_payload("p1", "s1", dict(A=[2, None, 5], B=[4]), study=study),
        session_payload("p2", "s2", dict(A=[None, 3], B=[None]), study=study),
    )
    outcomes = {(o["participant_id"], o["condition_id"]): o for o in report["outcomes"]}
    a = outcomes["p1", "A"]
    assert a["adjustment_count"] == 2 and a["improvement"] == 3
    assert a["transitions"][0]["satisfaction_change"] is None
    assert a["transitions"][1]["satisfaction_change"] is None
    assert outcomes["p1", "B"]["improvement"] is None
    assert outcomes["p1", "B"]["improvement_status"] == "not_adjusted"
    assert outcomes["p2", "A"]["improvement_status"] == "missing_answer"
    assert outcomes["p2", "B"]["stages"]["0"]["status"] == "missing"
    assert outcomes["p2", "B"]["stages"]["1"]["status"] == "not_adjusted"
    assert report["conditions"][0]["stages"]["1"]["missing"] == 1
    assert report["conditions"][1]["unadjusted"] == 2


def test_cost_and_time_require_complete_measurement_and_never_fill_zero():
    study = plan("exploratory_strategy")
    payload = session_payload("p", "s", dict(A=[3, 4], B=[3]), study=study)
    payload["requests"][0]["response"]["usage"] = dict(api_cost_usd=0, llm_calls=0)
    payload["requests"][1]["inference_seconds"] = None
    a = experiment(payload)["conditions"][0]
    assert a["usage"]["api_cost_usd"] == dict(
        total=None, measured_subtotal=0, measured=1, missing=1
    )
    assert a["latency"]["inference_seconds"] == dict(
        total=None, measured_subtotal=0.1, measured=1, missing=1
    )
    assert a["latency"]["selection_seconds"]["total"] == 0


def test_order_dropout_legacy_and_conflicting_export_handling():
    payload = session_payload("p", "s", dict(A=[3]))
    payload["termination_reason"] = "abandoned"
    report = experiment(payload)
    assert report["diagnostics"]["abandoned_sessions"] == 1
    assert report["outcomes"][1]["condition_status"] == "not_started"
    assert report["conditions"][1]["not_started"] == 1
    assert report["order_effects"][0]["mean"] == 3
    old = copy.deepcopy(payload)
    old["schema_version"] = 1
    old["goals"] = {}
    old["events"] = [dict(event_id="old", request_id="A0", kind="rating", rating=2)]
    assert experiment(old)["lists"][0]["satisfaction"] == 2
    assert experiment(old)["diagnostics"]["unknown_input_sessions"] == 1
    with pytest.raises(ValueError, match="conflicting snapshots"):
        experiment(payload, old)
    payload["session"]["retain_until"] = NOW.isoformat()
    assert analyze_online([payload], now=NOW)["experiments"] == []


def test_bad_lineage_and_profile_leaks_are_retained_but_excluded():
    payload = session_payload("p", "s", dict(A=[1], B=[5]))
    payload["goals"]["B0"]["initial_profile"]["selected_item_ids"].append("leaked-like")
    report = experiment(payload)
    assert report["conditions"][0]["initial"]["mean"] is None
    assert report["diagnostics"]["invalid_lists"] == 2
    assert "comparison_input_changed" in report["lists"][0]["integrity_issues"]
    payload = session_payload("p", "s", dict(A=[1, 2]), study=plan("exploratory_strategy"))
    payload["requests"][1]["parent_request_id"] = "unknown"
    assert experiment(payload)["diagnostics"]["invalid_lists"] == 2


def test_catalog_revisions_never_pool_into_the_same_comparison():
    first = session_payload("p", "s1", dict(A=[1]))
    second = session_payload("p", "s2", dict(A=[5]))
    first["resume"] = dict(context=dict(config_sha256="catalog-one"))
    second["resume"] = dict(context=dict(config_sha256="catalog-two"))
    report = analyze_online([first, second], plans=[plan()], now=NOW)
    assert len(report["experiments"]) == 2
    assert {e["conditions"][0]["initial"]["mean"] for e in report["experiments"]} == {1, 5}
    assert {e["config_sha256"] for e in report["experiments"]} == {"catalog-one", "catalog-two"}


def test_csv_all_tables_and_privacy_and_empty_protocol():
    payload = session_payload("p", "s", dict(A=[None], B=[1]))
    payload["resume"] = dict(secret="cookie-must-not-escape")
    payload["session_context"] = dict(text="private-free-text")
    result = analyze_online([payload], now=NOW)
    encoded = json.dumps(result)
    assert "cookie-must-not-escape" not in encoded and "private-free-text" not in encoded
    for table in TABLES:
        text = analysis_csv(result, table)
        assert "format,generated_at,protocol_id,plan_sha256" in text
    rows = list(csv.DictReader(io.StringIO(analysis_csv(result, "lists"))))
    assert rows[0]["satisfaction"] == "" and rows[0]["like_rate.value"] == "0.0"
    result["experiments"][0]["assignments"] = [dict(participant_id="=cmd()")]
    assert "'=cmd()" in analysis_csv(result, "assignments")
    empty = analyze_online([], plans=[plan("exploratory_strategy")], now=NOW)["experiments"][0]
    assert empty["participants"] == 0 and empty["conditions"][0]["initial"]["mean"] is None
    assert empty["conditions"][0]["usage"]["api_cost_usd"]["total"] is None


def test_analysis_cli_read_only_private_exclusive(tmp_path, monkeypatch):
    from src.interfaces.cli.online_analysis import main

    source, target = tmp_path / "session.json", tmp_path / "analysis.json"
    source.write_text(json.dumps(session_payload("p", "s", dict(A=[4]))))
    before = source.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        [
            "online_analysis",
            "--input",
            str(source),
            "--output",
            str(target),
        ],
    )
    from unittest.mock import patch

    with patch("src.interfaces.cli.online_analysis.datetime") as clock:
        clock.now.return_value = NOW
        main()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text())["experiments"][0]["participants"] == 1
    with pytest.raises(FileExistsError):
        with patch("src.interfaces.cli.online_analysis.datetime") as clock:
            clock.now.return_value = NOW
            main()
    assert before == source.read_bytes()
    store = LocalStore(tmp_path / "sessions.sqlite")
    store.put("token", json.loads(source.read_text()))
    before = store.path.read_bytes()
    snapshots, assignments = OnlineStudyReader(store.path).analysis_snapshot(NOW)
    assert snapshots[0]["session"]["participant_id"] == "p" and assignments == []
    assert before == store.path.read_bytes()
