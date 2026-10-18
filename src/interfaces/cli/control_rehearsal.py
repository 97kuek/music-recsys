"""Run synthetic, network-free fixed / single-decision / reactive contract rehearsals."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

from src.application.recommendation import RecommendationService
from src.application.strategy_control import StrategyControl
from src.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse
from src.domain.recommendation.control import ControlBudget, ControlFeedback
from src.domain.recommendation.models import ModelSpec
from src.domain.study.events import SessionLog, StudySession
from src.infrastructure.persistence.private_export import write_private_exclusive
from src.infrastructure.research.decision_providers import ScriptedDecisions


class _SyntheticRanker:
    def __init__(self, reverse):
        self.reverse = reverse

    def recommend(self, request):
        available = sorted(set(request.candidate_ids) - set(request.history), reverse=self.reverse)
        return RecommendationResponse(request.request_id, tuple(available[: request.top_k]))


def rehearse(*, engine: str) -> dict:
    """Fixtures never represent model quality, real listening or participant responses."""
    service = RecommendationService(
        tuple(
            (
                ModelSpec(target, "synthetic", "{}", "fixture", "fixture", "control-v1", 0),
                _SyntheticRanker(reverse),
            )
            for target, reverse in (("fixture-a", False), ("fixture-b", True))
        )
    )
    now = datetime.now(UTC)
    log = SessionLog(
        StudySession(
            "synthetic-session",
            "synthetic-participant",
            "synthetic-control-rehearsal",
            "control-v1",
            "within-subject",
            "fixed,llm_once,reactive",
            1,
            "synthetic-only",
            now,
            now + timedelta(hours=1),
        )
    )
    audits = []
    for mode in ("fixed", "llm_once", "reactive"):
        control = StrategyControl(
            service,
            session_id="synthetic-session",
            condition_id=mode,
            mode=mode,
            targets=(
                ("fixture-a", "Synthetic ascending order"),
                ("fixture-b", "Synthetic descending order"),
            ),
            feedback_choices=("different",),
            budget=ControlBudget(3, 5),
            provider=ScriptedDecisions(("fixture-a", "fixture-b", "fixture-a")),
            fixed_target="fixture-a",
        )
        executor = control
        if engine == "langgraph":
            from src.infrastructure.research.control_graph import LangGraphControl

            executor = LangGraphControl(control)
        parent = None
        for index in range(3):
            step = executor.run(
                RecommendationRequest(
                    f"{mode}-{index}",
                    "synthetic-participant",
                    (),
                    tuple(f"track-{i}" for i in range(10)),
                    5,
                ),
                explicit_adjustment=index > 0,
                parent_request_id=parent,
                feedback=ControlFeedback(("different",)) if index else ControlFeedback(),
                log=log,
                now=now,
            )
            if step.status != "ok":
                raise RuntimeError(f"synthetic control rehearsal failed: {step.reason}")
            audits.append(step.audit())
            parent = step.request_id
    return {
        "format": "music-recsys-control-rehearsal-v1",
        "synthetic": True,
        "engine": engine,
        "created_at": now.isoformat(),
        "packages": {name: version(name) for name in ("openai", "langgraph", "langsmith")}
        if engine == "langgraph"
        else {},
        "steps": audits,
        "session": log.export(now=now),
        "limits": ["No LLM inference", "No participant ratings", "No model quality evaluation"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic", action="store_true", required=True)
    parser.add_argument("--engine", choices=("python", "langgraph"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = rehearse(engine=args.engine)
    write_private_exclusive(
        args.output, json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    )


if __name__ == "__main__":
    main()
