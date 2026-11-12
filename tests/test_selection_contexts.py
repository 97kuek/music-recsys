import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.domain.recommendation.selection_context import SelectionContext


def test_payload_has_only_training_aggregates():
    context = SelectionContext("new", 3, 7, 10, 5, 0.5, 2 / 3)
    assert set(context.payload()) == {
        "task",
        "history_count",
        "candidate_count",
        "total_plays",
        "item_active_days",
        "top_track_play_share",
        "feature_fraction",
    }
    with pytest.raises(TypeError):
        SelectionContext(**context.payload(), validation_ndcg=1)
    with pytest.raises(ValueError):
        SelectionContext("new", 3, 7, 10, 5, float("nan"), 1)


def test_prepare_keeps_all_users_and_never_reads_future_rows(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    from backend.infrastructure.datasets.listening_data import read_bundle
    from backend.infrastructure.research import selection_contexts
    from backend.infrastructure.research.results import verify_artifacts

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    manifest, paths = read_bundle(bundle / "validation")
    paths["observations"] = tmp_path / "MUST_NOT_READ_FUTURE"
    root = tmp_path / "hybrid"
    root.mkdir()
    groups = ("ease", "ease-bpr-rrf", "ease-bpr-mmr0.8", "ease-bpr-mmr0.5")
    (root / "config.json").write_text(json.dumps(dict(conditions=[dict(group=g) for g in groups])))
    result = dict(status="succeeded", phase="validation", population=dict(users=4))
    (root / "result.json").write_text(json.dumps(result))
    monkeypatch.setattr(selection_contexts, "read_result", lambda c: result)
    monkeypatch.setattr(selection_contexts, "read_bundle", lambda p: (manifest, paths))
    monkeypatch.setattr(selection_contexts, "bind_candidates", lambda *args: ({}, {}))
    config = SimpleNamespace(
        output=root, validation_dataset=bundle / "validation", features=bundle / "features.json"
    )
    original = {p: p.read_bytes() for p in root.iterdir()}
    output = selection_contexts.prepare(config)
    rows = pq.read_table(output / "contexts.parquet").to_pylist()
    assert len(rows) == 8 and len({r["user_id"] for r in rows}) == 4
    assert {r["task"] for r in rows} == {"new", "repeat"}
    for row in rows:
        SelectionContext(**{k: v for k, v in row.items() if k != "user_id"})
    saved = json.loads((output / "result.json").read_text())
    assert saved["llm_evaluation"] == "not_run" and saved["selection"] == "not_selected"
    verify_artifacts(output, saved["artifacts"])
    assert all(p.read_bytes() == content for p, content in original.items())
    with pytest.raises(FileExistsError):
        selection_contexts.prepare(config)
