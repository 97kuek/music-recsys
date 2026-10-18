"""Descriptive within-participant analysis. No model selection or significance tests."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime

from src.domain.study.online_metrics import summarize_online
from src.domain.study.online_summaries import describe_experiment


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def analyze_online(payloads, *, assignments=(), plans=(), now):
    """Each experiment revision is isolated; duplicate exports never create observations."""
    unique = {}
    for original in payloads:
        payload = original.get("payload", original)
        session = payload["session"]
        if datetime.fromisoformat(session["retain_until"]) <= now:
            continue
        key = session["session_id"]
        if key in unique and unique[key] != payload:
            raise ValueError("conflicting snapshots for the same session; select one explicitly")
        unique[key] = payload
    groups = {}

    def group(protocol, digest, plan=None, config=None):
        key = protocol, digest, config
        if key not in groups:
            groups[key] = dict(
                protocol_id=protocol,
                plan_sha256=digest,
                config_sha256=config,
                plan=plan,
                assignments=[],
                sessions=[],
                lists=[],
            )
        elif plan is not None:
            if groups[key]["plan"] not in (None, plan):
                raise ValueError("conflicting experiment plans")
            groups[key]["plan"] = plan
        return groups[key]

    for plan in plans:
        group(plan.protocol_id, plan.digest, plan.model_dump())
    saved_assignments = {}
    for assignment in [
        *assignments,
        *(p["assignment"] for p in unique.values() if p.get("assignment")),
    ]:
        key = assignment["protocol_id"], assignment["plan_sha256"], assignment["participant_id"]
        previous = saved_assignments.get(key)
        if previous:
            if previous["order"] != assignment["order"] or previous["slot"] != assignment["slot"]:
                raise ValueError("conflicting saved assignments")
            if (
                previous.get("input")
                and assignment.get("input")
                and previous["input"] != assignment["input"]
            ):
                raise ValueError("conflicting frozen comparison inputs")
            if previous.get("input"):
                continue
        saved_assignments[key] = assignment
    for assignment in saved_assignments.values():
        # Never expose the frozen preference contents in researcher analysis.
        public = {k: v for k, v in assignment.items() if k != "input"}
        public["input_sha256"] = (
            fingerprint(assignment["input"]) if assignment.get("input") else None
        )
        group(
            assignment["protocol_id"],
            assignment["plan_sha256"],
            config=assignment.get("config_sha256"),
        )["assignments"].append(public)
    for payload in sorted(
        unique.values(), key=lambda p: (p["session"]["started_at"], p["session"]["session_id"])
    ):
        session = payload["session"]
        study = payload.get("study")
        target = group(
            study["protocol_id"] if study else session["study_id"],
            payload.get("plan_sha256"),
            study,
            payload.get("resume", {}).get("context", {}).get("config_sha256"),
        )
        source_hash = fingerprint(payload)
        source_events = defaultdict(set)
        for event in payload["events"]:
            source_events[event["request_id"]].add(event["event_id"])
        conditions = {c["condition_id"] for c in study["conditions"]} if study else set()
        profile_hashes = set()
        assignment = saved_assignments.get(
            (target["protocol_id"], target["plan_sha256"], session["participant_id"]), {}
        )
        expected_profile = (assignment.get("input") or {}).get("profile")
        rows = summarize_online(payload)["lists"]
        for row in rows:
            goal = payload.get("goals", {}).get(row["request_id"], {})
            profile = goal.get("initial_profile")
            row["input_sha256"] = fingerprint(profile) if profile is not None else None
            if row["input_sha256"]:
                profile_hashes.add(row["input_sha256"])
            row.update(
                source_sha256=source_hash,
                retain_until=session["retain_until"],
                event_ids=sorted(source_events[row["request_id"]]),
                eligible=True,
                integrity_issues=[],
            )
            if expected_profile is not None and row["input_sha256"] != fingerprint(
                expected_profile
            ):
                row["integrity_issues"].append("frozen_input_mismatch")
            if study and study.get("design") and profile is None:
                row["integrity_issues"].append("missing_comparison_input")
            if conditions and row["condition_id"] not in conditions:
                row["integrity_issues"].append("unknown_condition")
            order = payload.get("order", [])
            period = row["period"]
            if (
                study
                and period is not None
                and (
                    type(period) is not int
                    or not 1 <= period <= len(order)
                    or not 0 <= order[period - 1] < len(study["conditions"])
                    or study["conditions"][order[period - 1]]["condition_id"] != row["condition_id"]
                )
            ):
                row["integrity_issues"].append("assignment_mismatch")
        if len(profile_hashes) > 1:
            for row in rows:
                row["integrity_issues"].append("comparison_input_changed")
        for condition in {r["condition_id"] for r in rows}:
            chain = sorted(
                (r for r in rows if r["condition_id"] == condition),
                key=lambda r: r["adjustment_index"],
            )
            valid = all(
                row["adjustment_index"] == i
                and row["list_number"] == i + 1
                and row["parent_request_id"] == (chain[i - 1]["request_id"] if i else None)
                for i, row in enumerate(chain)
            ) and len(chain) <= (
                1 if study and study.get("design") == "exploratory_standard" else 3
            )
            if not valid:
                for row in chain:
                    row["integrity_issues"].append("invalid_list_chain")
        for row in rows:
            row["eligible"] = not row["integrity_issues"]
        target["lists"].extend(rows)
        target["sessions"].append(
            dict(
                participant_id=session["participant_id"],
                session_id=session["session_id"],
                started_at=session["started_at"],
                retain_until=session["retain_until"],
                consent_version=session["consent_version"],
                mode=payload.get("mode", "unknown"),
                status="completed"
                if payload.get("termination_reason") == "completed"
                else (
                    "abandoned"
                    if payload.get("termination_reason") == "abandoned"
                    else "unfinished"
                ),
                order=payload.get("order", []),
                source_sha256=source_hash,
                profile_check="consistent"
                if len(profile_hashes) == 1
                else ("changed" if profile_hashes else "unavailable"),
            )
        )
    for key, value in list(groups.items()):
        if (
            key[2] is None
            and not value["sessions"]
            and not value["assignments"]
            and any(other[:2] == key[:2] and other[2] is not None for other in groups)
        ):
            del groups[key]
    experiments = [
        describe_experiment(g) for _, g in sorted(groups.items(), key=lambda pair: str(pair[0]))
    ]
    return dict(
        format="music-recsys-online-analysis-v1",
        generated_at=now.isoformat(),
        primary_metric="list_satisfaction_1_to_5",
        methods=dict(
            list_metrics="music-recsys-online-metrics-v1",
            summaries="valid_session_means_then_equal_participant_weight",
            distribution="observed_list_counts",
            improvement="adjusted_and_both_endpoints_observed",
        ),
        experiments=experiments,
        interpretation="Exploratory descriptive results; repeated sessions are not independent participants. No general superiority or significance claim.",
    )
