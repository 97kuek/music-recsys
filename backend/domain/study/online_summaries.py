"""Participant-weighted summaries and explicit missing / no-adjustment states."""

from itertools import combinations
from statistics import mean, median

RATES = (
    "like_rate",
    "play_start_rate",
    "engaged_listen_rate",
    "track_switch_rate",
    "early_skip_rate",
)
USAGE = ("input_tokens", "output_tokens", "api_cost_usd", "llm_calls", "tool_calls")


def measured_total(values):
    known = [v for v in values if v is not None]
    return dict(
        total=sum(known) if values and len(known) == len(values) else None,
        measured_subtotal=sum(known) if known else None,
        measured=len(known),
        missing=len(values) - len(known),
    )


def describe_values(rows, value):
    participants = sorted({r["participant_id"] for r in rows})
    by_participant = []
    observed = []
    for participant in participants:
        own = [r for r in rows if r["participant_id"] == participant]
        answers = [value(r) for r in own if value(r) is not None]
        observed.extend(answers)
        by_participant.append(
            dict(
                participant_id=participant,
                value=mean(answers) if answers else None,
                answers=len(answers),
                missing=len(own) - len(answers),
                session_ids=sorted({r["session_id"] for r in own}),
            )
        )
    values = [p["value"] for p in by_participant if p["value"] is not None]
    return dict(
        mean=mean(values) if values else None,
        median=median(values) if values else None,
        participants=len(participants),
        answered_participants=len(values),
        answers=len(observed),
        missing=len(rows) - len(observed),
        response_rate=len(observed) / len(rows) if rows else None,
        by_participant=by_participant,
    )


def satisfaction(rows, key):
    summary = describe_values(rows, lambda r: r[key])
    summary["distribution"] = {str(n): sum(r[key] == n for r in rows) for n in range(1, 6)}
    return summary


def outcome(session, condition, rows, position):
    chain = sorted(rows, key=lambda r: r["adjustment_index"])
    stages = {r["adjustment_index"]: r for r in chain}
    initial, final = stages.get(0), chain[-1] if chain else None

    def stage_value(stage):
        row = stages.get(stage)
        return dict(
            status=("not_started" if not chain else "not_adjusted")
            if row is None
            else ("missing" if row["satisfaction"] is None else "answered"),
            satisfaction=row["satisfaction"] if row else None,
            request_id=row["request_id"] if row else None,
        )

    initial_rating = initial["satisfaction"] if initial else None
    final_rating = final["satisfaction"] if final else None
    adjusted = len(chain) > 1
    change_status = (
        "not_started"
        if not chain
        else (
            "not_adjusted"
            if not adjusted
            else (
                "observed"
                if initial_rating is not None and final_rating is not None
                else "missing_answer"
            )
        )
    )
    transitions = []
    for before, after in zip(chain, chain[1:]):

        def difference(key):
            a, b = before[key], after[key]
            a = a["value"] if isinstance(a, dict) else a
            b = b["value"] if isinstance(b, dict) else b
            return b - a if a is not None and b is not None else None

        transitions.append(
            dict(
                from_request_id=before["request_id"],
                to_request_id=after["request_id"],
                adjustment_index=after["adjustment_index"],
                satisfaction_change=difference("satisfaction"),
                behavior_change={key: difference(key) for key in RATES},
            )
        )
    return dict(
        participant_id=session["participant_id"],
        session_id=session["session_id"],
        retain_until=session["retain_until"],
        condition_id=condition,
        position=position,
        session_status=session["status"],
        condition_status="not_started"
        if not chain
        else (
            "completed"
            if session["status"] == "completed"
            or (
                position is not None
                and any((r.get("period") or 0) > position for r in session.get("all_lists", []))
            )
            else session["status"]
        ),
        stages={str(n): stage_value(n) for n in range(3)},
        initial_satisfaction=initial_rating,
        final_satisfaction=final_rating,
        adjustment_count=len(chain) - 1 if chain else None,
        improvement=final_rating - initial_rating if change_status == "observed" else None,
        improvement_status=change_status,
        transitions=transitions,
        request_ids=[r["request_id"] for r in chain],
        latency={
            key: measured_total([r["latency"][key] for r in chain])
            for key in (
                "selection_seconds",
                "inference_seconds",
                "response_latency_ms",
                "first_display_latency_ms",
            )
        },
        usage={key: measured_total([(r["usage"] or {}).get(key) for r in chain]) for key in USAGE},
    )


def behavior(rows):
    result = {key: describe_values(rows, lambda r: r[key]["value"]) for key in RATES}
    result["engaged_sensitivity"] = {
        str(t): describe_values(rows, lambda r: r["engaged_sensitivity"][str(t)]["value"])
        for t in (5, 10, 15, 20)
    }
    offered = [r for r in rows if r["discovery_offered"]]
    answered = [r for r in offered if r["discovery_status"] in {"none", "selected"}]
    result["discovery"] = dict(
        success_tracks=sum(r["discovery_success_tracks"] for r in answered) if answered else None,
        success_lists=sum(r["discovery_success_tracks"] > 0 for r in answered)
        if answered
        else None,
        answered=len(answered),
        offered=len(offered),
        success_list_rate=len([r for r in answered if r["discovery_success_tracks"] > 0])
        / len(answered)
        if answered
        else None,
        response_rate=len(answered) / len(offered) if offered else None,
    )
    return result


def describe_experiment(group):
    rows = group["lists"]
    eligible = [r for r in rows if r["eligible"]]
    conditions = [c["condition_id"] for c in (group["plan"] or {}).get("conditions", [])]
    if not conditions:
        conditions = sorted({r["condition_id"] for r in rows if r["condition_id"] is not None})
    outcomes = []
    for session in group["sessions"]:
        own = [r for r in eligible if r["session_id"] == session["session_id"]]
        for condition in conditions:
            index = conditions.index(condition)
            position = session["order"].index(index) + 1 if index in session["order"] else None
            outcomes.append(
                outcome(
                    {**session, "all_lists": own},
                    condition,
                    [r for r in own if r["condition_id"] == condition],
                    position,
                )
            )
    summaries = []
    for condition in conditions:
        own = [o for o in outcomes if o["condition_id"] == condition]
        lists = [r for r in eligible if r["condition_id"] == condition]
        summaries.append(
            dict(
                condition_id=condition,
                initial=satisfaction(own, "initial_satisfaction"),
                final=satisfaction(own, "final_satisfaction"),
                stages={
                    str(n): satisfaction(
                        [
                            dict(o, rating=o["stages"][str(n)]["satisfaction"])
                            for o in own
                            if o["stages"][str(n)]["status"] not in {"not_adjusted", "not_started"}
                        ],
                        "rating",
                    )
                    for n in range(3)
                },
                improvement=describe_values(
                    [
                        o
                        for o in own
                        if o["improvement_status"] not in {"not_adjusted", "not_started"}
                    ],
                    lambda o: o["improvement"],
                ),
                unadjusted=sum(o["adjustment_count"] == 0 for o in own),
                not_started=sum(o["adjustment_count"] is None for o in own),
                abandoned=sum(o["condition_status"] == "abandoned" for o in own),
                unfinished=sum(o["condition_status"] == "unfinished" for o in own),
                behavior=behavior(lists),
                behavior_by_stage={
                    str(n): behavior([r for r in lists if r["adjustment_index"] == n])
                    for n in range(3)
                },
                latency={
                    key: measured_total([r["latency"][key] for r in lists])
                    for key in (
                        "selection_seconds",
                        "inference_seconds",
                        "response_latency_ms",
                        "first_display_latency_ms",
                    )
                },
                usage={
                    key: measured_total([(r["usage"] or {}).get(key) for r in lists])
                    for key in USAGE
                },
            )
        )
    pairs = []
    for stage in ("initial", "final"):
        for a, b in combinations(summaries, 2):
            av = {p["participant_id"]: p for p in a[stage]["by_participant"]}
            bv = {p["participant_id"]: p for p in b[stage]["by_participant"]}
            for participant in sorted(av.keys() | bv.keys()):
                left, right = (
                    av.get(participant, {}).get("value"),
                    bv.get(participant, {}).get("value"),
                )
                pairs.append(
                    dict(
                        participant_id=participant,
                        stage=stage,
                        condition_a=a["condition_id"],
                        condition_b=b["condition_id"],
                        a=left,
                        b=right,
                        difference_b_minus_a=right - left
                        if left is not None and right is not None
                        else None,
                        session_ids=sorted(
                            set(
                                av.get(participant, {}).get("session_ids", [])
                                + bv.get(participant, {}).get("session_ids", [])
                            )
                        ),
                    )
                )
    group.update(
        conditions=summaries,
        outcomes=outcomes,
        paired_differences=pairs,
        participants=len(
            {s["participant_id"] for s in group["sessions"]}
            | {a["participant_id"] for a in group["assignments"]}
        ),
        session_count=len(group["sessions"]),
        order_effects=[
            dict(
                condition_id=c,
                position=p,
                **satisfaction(
                    [o for o in outcomes if o["condition_id"] == c and o["position"] == p],
                    "initial_satisfaction",
                ),
            )
            for c in conditions
            for p in range(1, len(conditions) + 1)
        ],
        diagnostics=dict(
            invalid_lists=sum(not r["eligible"] for r in rows),
            playback_errors=sum(r["diagnostics"]["playback_errors"] for r in rows),
            observation_incomplete=sum(not r["observation_complete"] for r in rows),
            unknown_input_sessions=sum(
                s["profile_check"] == "unavailable" for s in group["sessions"]
            ),
            abandoned_sessions=sum(s["status"] == "abandoned" for s in group["sessions"]),
            unfinished_sessions=sum(s["status"] == "unfinished" for s in group["sessions"]),
        ),
    )
    return group
