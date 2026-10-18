"""保存済みセッションの符号化・復元。保存先へはアクセスしない。"""

from dataclasses import asdict

from src.domain.recommendation.profiles import InitialProfile
from src.domain.study.events import SessionLog
from src.domain.study.session import Session


def restore_session(payload, *, context, study, now):
    saved = payload["resume"]
    if saved["format"] != "music-recsys-resume-v1" or saved["context"] != context:
        raise ValueError("saved model, catalog, audience or study changed")
    log = SessionLog.from_export(payload, now=now)
    profile = payload["initial_profile"]
    if profile["user_id"] != log.session.participant_id:
        raise ValueError("saved participant mismatch")
    session = Session(
        log,
        True,
        InitialProfile(
            profile["user_id"],
            tuple(profile["selected_item_ids"]),
            tuple(profile["selected_artist_ids"]),
        ),
        registered=payload.get("registered", False),
        long_term_consent=payload.get("long_term_consent", False),
        profile_snapshot=payload.get("profile_snapshot", {}),
        context=payload.get("session_context", {}),
        assignment=payload.get("assignment", {}),
    )
    session.heard = set(saved["heard"])
    session.recommendations = {k: tuple(v) for k, v in saved["recommendations"].items()}
    session.replies = {int(k): v for k, v in saved["replies"].items()}
    session.playbacks = saved["playbacks"]
    session.playback_bindings = {k: tuple(v) for k, v in saved["playback_bindings"].items()}
    session.preview_bindings = saved["preview_bindings"]
    session.period, session.phase = saved["period"], saved["phase"]
    session.order, session.goals = payload["order"], payload["goals"]
    session.finished = payload["finished"]
    session.termination_reason = payload["termination_reason"]
    session.funnel = payload["funnel"]
    if session.phase not in {"profile", "listen", "evaluate"} or not (
        0 <= session.period < (len(session.order) or 1)
    ):
        raise ValueError("invalid saved phase or period")
    if study:
        expected = (
            study.schedule()[session.assignment["slot"]]
            if study.design
            else study.order(log.session.participant_id)
        )
        if session.order != expected or payload["plan_sha256"] != study.digest:
            raise ValueError("saved assignment changed")
    return session


def session_snapshot(session, *, context, mode, study, now):
    payload = session.log.export(now=now)
    payload.update(
        mode=mode,
        registered=session.registered,
        long_term_consent=session.long_term_consent,
        profile_snapshot=session.profile_snapshot,
        session_context=session.context,
        assignment=session.assignment,
        initial_profile=asdict(session.profile),
        goals=session.goals,
        recommendation_count=len(session.recommendations),
        finished=session.finished,
        termination_reason=session.termination_reason,
        funnel=session.funnel,
        study=study.model_dump() if study else None,
        plan_sha256=study.digest if study else None,
        order=session.order,
        resume={
            "format": "music-recsys-resume-v1",
            "context": context,
            "heard": sorted(session.heard),
            "recommendations": session.recommendations,
            "replies": session.replies,
            "playbacks": session.playbacks,
            "playback_bindings": session.playback_bindings,
            "preview_bindings": session.preview_bindings,
            "period": session.period,
            "phase": session.phase,
        },
    )
    return payload
