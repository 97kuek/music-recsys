"""内側のコードへ技術依存が戻らないことと、Adapter交換時の契約を確認する。"""

import ast
import hashlib
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.application.study.participants import Participants
from src.application.study.runtime import Runtime
from src.domain.catalog import PlaybackAvailability
from src.domain.recommendation.candidates import eligible_candidates
from src.domain.recommendation.contracts import RecommendationResponse
from src.domain.recommendation.models import ModelSpec
from src.domain.study.plan import Condition, StudyPlan
from src.infrastructure.config.study import ConditionPayload, StudyPlanPayload

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("layer", ["domain", "application", "infrastructure"])
def test_python_dependencies_point_inward(layer):
    for path in (ROOT / "src" / layer).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                if layer == "infrastructure":
                    assert not module.startswith(("src.interfaces", "src.bootstrap")), path
                elif module.startswith("src."):
                    allowed = (
                        ("src.domain",) if layer == "domain" else ("src.domain", "src.application")
                    )
                    assert module.startswith(allowed), (path, module)
                else:
                    assert module.split(".")[0] in sys.stdlib_module_names, (path, module)


def test_frontend_boundaries_keep_transport_and_react_outside_core():
    for layer in ("domain", "application", "infrastructure", "presentation"):
        for path in (ROOT / "frontend/src" / layer).rglob("*"):
            if path.suffix not in {".ts", ".tsx"}:
                continue
            source = path.read_text()
            imports = re.findall(r"(?:from\s+|import\s*\()['\"]([^'\"]+)", source)
            for module in imports:
                if layer in {"domain", "application"}:
                    assert module.startswith("."), (path, module)
                    resolved = (path.parent / module).resolve().relative_to(ROOT / "frontend/src")
                    assert resolved.parts[0] in {"domain", "application"}, (path, module)
                    if layer == "domain":
                        assert resolved.parts[0] == "domain", (path, module)
                elif layer == "infrastructure":
                    assert "presentation" not in module and module != "react", (path, module)
                else:
                    assert "infrastructure" not in module, (path, module)
            if layer in {"domain", "application", "presentation"}:
                assert not re.search(r"\bfetch\s*\(|import\.meta\.env|localStorage\.", source), path


def test_study_plan_retains_deployed_json_hash_and_assignment():
    schema = StudyPlanPayload(
        protocol_id="研究",
        seed=42,
        top_k=5,
        conditions=[
            ConditionPayload(condition_id="a", version="v1"),
            ConditionPayload(condition_id="b", version="v2", required_route="trained_user"),
        ],
    )
    plan = StudyPlan(
        schema.protocol_id,
        schema.seed,
        schema.top_k,
        tuple(Condition(**condition.model_dump()) for condition in schema.conditions),
    )
    assert plan.model_dump() == schema.model_dump()
    assert plan.digest == hashlib.sha256(schema.model_dump_json().encode()).hexdigest()
    assert plan.order("app:participant") == plan.order("app:participant")
    with pytest.raises(ValueError, match="duplicate"):
        StudyPlan("study", 42, 5, (Condition("a", "v1"), Condition("a", "v2")))


class MemoryParticipants:
    def __init__(self):
        self.rows = {}

    def add_participant(self, participant_id, code_hash, expires_at):
        self.rows[participant_id] = {
            "participant_id": participant_id,
            "expires_at": expires_at.isoformat(),
            "profile": {},
            "code_hash": code_hash,
        }

    def participant(self, participant_id, now):
        row = self.rows.get(participant_id)
        return row if row and datetime.fromisoformat(row["expires_at"]) > now else None

    def participant_by_code(self, code_hash, now):
        return next(
            (
                key
                for key in self.rows
                if self.participant(key, now) and self.rows[key]["code_hash"] == code_hash
            ),
            None,
        )

    def update_participant_code(self, participant_id, code_hash, now):
        self.rows[participant_id]["code_hash"] = code_hash

    def update_participant_profile(self, participant_id, profile, now):
        self.rows[participant_id]["profile"] = profile


def test_participant_actions_work_without_sqlite_or_http():
    store = MemoryParticipants()
    participants = Participants(store)
    now = datetime.now(UTC)
    participant, code = participants.issue(expires_at=now + timedelta(days=1), now=now)
    identity = participant["participant_id"]
    assert participants.resolve(code.lower().replace("-", " "), now)["participant_id"] == identity
    replacement = participants.rotate(identity, now)
    with pytest.raises(PermissionError):
        participants.resolve(code, now)
    assert participants.resolve(replacement, now)["participant_id"] == identity
    participants.save_profile(identity, {"selected_item_ids": ["a"]}, "catalog", now)
    assert participants.get(identity, now)["profile"] == {
        "catalog_key": "catalog",
        "selected_item_ids": ["a"],
    }
    with pytest.raises(PermissionError):
        participants.resolve(replacement, now + timedelta(days=2))


def test_recommendation_session_uses_injected_model_media_and_repository():
    class Store:
        def __init__(self):
            self.payloads = {}

        def purge(self, now):
            pass

        def put(self, key, payload):
            self.payloads[key] = payload

    class Media:
        def availability(self, context, now):
            return PlaybackAvailability(context, "available", now, now + timedelta(minutes=1))

    class Model:
        index = ("a", "b", "c")

        def recommend(self, request):
            return RecommendationResponse(
                request.request_id, tuple(sorted(eligible_candidates(request)))[: request.top_k]
            )

    spec = ModelSpec("v1", "popularity", "{}", "train", "catalog", "implementation", 42)
    store = Store()
    runtime = Runtime(
        config={"mode": "demo"},
        config_sha256="catalog",
        models={"v1": (spec, Model())},
        records=[{"item_id": item, "title": item, "artists": ["artist"]} for item in Model.index],
        playback=Media(),
        store=store,
    )
    token, session = runtime.start(True, False)
    runtime.set_profile(token, ["a"], [])
    runtime.progress(token, "listen")
    reply = runtime.assigned_recommendation(token, "v1", "discover", 2, "")
    assert [track["item_id"] for track in reply["tracks"]] == ["b", "c"]
    runtime.finish(token)
    assert (
        session.finished
        and next(iter(store.payloads.values()))["termination_reason"] == "completed"
    )
