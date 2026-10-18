"""Future list lineage is bounded per session, without implementing an Agent."""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse
from src.domain.recommendation.models import ModelSpec, RecommendationExecution, Selection
from src.domain.study.events import SessionLog, StudySession


def test_parent_conditions_and_session_adjustment_limit():
    now = datetime.now(UTC)
    session = StudySession(
        "session",
        "participant",
        "study",
        "protocol",
        "condition",
        "sequence",
        1,
        "local-ui-v5",
        now,
        now + timedelta(days=1),
    )
    spec = ModelSpec("v1", "popularity", "{}", "train", "catalog", "source", 42)

    def execution(request_id):
        return RecommendationExecution(
            RecommendationRequest(request_id, "participant", (), ("a",), 1),
            RecommendationResponse(request_id, ("a",)),
            spec,
            Selection("fixed", "1", "v1"),
            0,
            0,
        )

    log = SessionLog(session)
    log.record(execution("r1"), now=now, condition_id="a")
    with pytest.raises(ValueError, match="same session and condition"):
        log.record(execution("other-condition"), now=now, condition_id="b", parent_request_id="r1")
    log.record(execution("r2"), now=now, condition_id="a", parent_request_id="r1")
    log.record(execution("r3"), now=now, condition_id="a", parent_request_id="r2")
    assert [r["list_number"] for r in log.export(now=now)["requests"]] == [1, 2, 3]
    with pytest.raises(ValueError, match="two adjustments"):
        log.record(execution("r4"), now=now, condition_id="a", parent_request_id="r3")
    later = SessionLog(
        StudySession(
            "session-2",
            "participant",
            "study",
            "protocol",
            "condition",
            "sequence",
            1,
            "local-ui-v5",
            now,
            now + timedelta(days=1),
        )
    )
    later.record(execution("next-initial"), now=now, condition_id="a")
    later.record(
        execution("next-adjustment"), now=now, condition_id="a", parent_request_id="next-initial"
    )
    assert later.export(now=now)["requests"][-1]["adjustment_index"] == 1
    # The new two-condition protocol has two opportunities per condition; legacy stays global.
    scoped = SessionLog(session)
    for condition in ("a", "b"):
        parent = None
        for index in range(3):
            rid = f"{condition}-{index}"
            scoped.record(
                execution(rid),
                now=now,
                condition_id=condition,
                parent_request_id=parent,
                adjustment_scope="condition",
            )
            parent = rid
        with pytest.raises(ValueError, match="two adjustments per condition"):
            scoped.record(
                execution(f"{condition}-too-many"),
                now=now,
                condition_id=condition,
                parent_request_id=parent,
                adjustment_scope="condition",
            )
    assert len(scoped.export(now=now)["requests"]) == 6
    with pytest.raises(ValueError, match="one initial list"):
        scoped.record(
            execution("extra-root"), now=now, condition_id="a", adjustment_scope="condition"
        )
