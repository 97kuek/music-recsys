import json
from dataclasses import dataclass
from itertools import combinations
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_selection_candidates import candidates  # noqa: F401

from backend.application.local_selection import select_all
from backend.domain.recommendation.local_selector import TARGETS, comparison_policies
from backend.domain.recommendation.selection_context import SelectionContext
from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.research import local_selection
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.hybrid_config import reference
from backend.infrastructure.research.results import verify_artifacts
from backend.infrastructure.research.selection_candidates import bind_candidates
from backend.infrastructure.research.selector_report import replay_pairs, replay_rows
from backend.infrastructure.research.store import atomic_json


def test_local_policies_receive_only_context_and_use_no_id_or_truth():
    requests = [("private", SelectionContext(t, 2, 7, 4, 2, 0.5, 1)) for t in ("new", "repeat")]
    rows = list(select_all(requests, comparison_policies("ease", "ease-bpr-rrf")))
    assert len(rows) == 10
    assert [r["target_id"] for r in rows if r["condition"] == "task-rule"] == [
        "ease",
        "ease-bpr-rrf",
    ]
    assert all(r["selection_seconds"] >= 0 for r in rows)
    with pytest.raises(ValueError):
        comparison_policies(None, "ease")


def metric_frame():
    rows = []
    for i, target in enumerate(TARGETS):
        for task in ("discovery", "repeat"):
            rows.append(
                dict(
                    condition=target,
                    aspect="accuracy",
                    task=task,
                    metric="recall" if task == "discovery" else "repeat_recall",
                    k=20,
                    window_days=90,
                    day_weight=None,
                    mean=i / 10,
                    n_evaluated=3,
                    n_excluded=1,
                    n_seeds=1 if i == 0 else 3,
                    ci_low=i / 10 - 0.01,
                    ci_high=i / 10 + 0.01,
                )
            )
    return pd.DataFrame(rows)


def paired_frame():
    rows = []
    for a, b in combinations(TARGETS, 2):
        for task in ("new", "repeat"):
            rows.append(
                dict(
                    model_left=a,
                    model_right=b,
                    task=task,
                    window_days=90,
                    metric="paired_difference_recall"
                    if task == "new"
                    else "paired_difference_repeat_recall",
                    k=20,
                    mean=0.1,
                    ci_low=0.05,
                    ci_high=0.15,
                    n_evaluated=3,
                    n_excluded=1,
                )
            )
    return pd.DataFrame(rows)


def test_task_replay_retains_seed_counts_and_reverses_paired_bounds():
    policies = comparison_policies("ease", "ease-bpr-rrf")
    rows, _ = replay_rows(metric_frame(), pd.DataFrame(), policies)
    rule = [r for r in rows if r["condition"] == "task-rule"]
    assert [(r["mean"], r["n_seeds"]) for r in rule] == [(0, 1), (0.1, 3)]
    pairs = replay_pairs(metric_frame(), paired_frame(), policies)
    zero = next(
        r
        for r in pairs
        if r["model_left"] == "ease" and r["model_right"] == "task-rule" and r["task"] == "new"
    )
    assert zero["mean"] == zero["ci_low"] == zero["ci_high"] == 0
    reversed_ = next(
        r
        for r in pairs
        if r["model_left"] == "ease-bpr-mmr0.5"
        and r["model_right"] == "task-rule"
        and r["task"] == "new"
    )
    assert (reversed_["mean"], reversed_["ci_low"], reversed_["ci_high"]) == (-0.1, -0.15, -0.05)


@pytest.fixture
def prepared_local(candidates, monkeypatch):  # noqa: F811
    root, saved, result = candidates
    result["observations"] = []
    write_csv(root / "metrics.csv", metric_frame().to_dict(orient="records"))
    common = [
        dict(pair="ease-bpr", condition=t, task=task, k=20, mean=0.5, n_evaluated=3, n_excluded=1)
        for t in TARGETS
        for task in ("new", "repeat")
    ]
    write_csv(root / "ild.csv", common)
    write_csv(root / "paired.csv", paired_frame().to_dict(orient="records"))
    result["artifacts"] = {
        name: dict(path=name + ".csv", sha256=file_sha256(root / (name + ".csv")))
        for name in ("metrics", "ild", "paired")
    }
    atomic_json(root / "config.json", saved)
    atomic_json(root / "result.json", result)
    versions, refs = bind_candidates(root, saved, result)
    inputs = root / "selection-context"
    inputs.mkdir()
    contexts = [
        dict(
            user_id=f"u{i}",
            task=t,
            history_count=2,
            candidate_count=7,
            total_plays=4,
            item_active_days=2,
            top_track_play_share=0.5,
            feature_fraction=1.0,
        )
        for i in range(4)
        for t in ("new", "repeat")
    ]
    pq.write_table(pa.Table.from_pylist(contexts), inputs / "contexts.parquet")
    atomic_json(
        inputs / "config.json", dict(candidates=versions, evidence={**reference(root), **refs})
    )
    atomic_json(
        inputs / "result.json",
        dict(
            status="succeeded",
            phase="validation",
            users=4,
            requests=8,
            artifacts={p.name: dict(path=p.name, sha256=file_sha256(p)) for p in inputs.iterdir()},
        ),
    )
    monkeypatch.setattr(local_selection, "read_result", lambda c: result)

    @dataclass
    class Evaluation:
        ks: tuple = (2, 20)

    return SimpleNamespace(output=root, evaluation=Evaluation())


def test_full_local_run_keeps_every_request_and_never_overwrites(prepared_local):
    config = prepared_local
    original = (config.output / "result.json").read_bytes()
    output = local_selection.run(
        config, new_target="ease", repeat_target="ease-bpr-rrf", render=lambda *a: []
    )
    result = json.loads((output / "result.json").read_text())
    assert result["selection_rows"] == 40 and result["requests_per_condition"] == 8
    assert result["llm_evaluation"] == "not_run" and result["failures"] == 0
    verify_artifacts(output, result["artifacts"])
    frame = pq.read_table(output / "selections.parquet").to_pandas()
    assert frame.groupby(["condition", "task"]).size().eq(4).all()
    assert (config.output / "result.json").read_bytes() == original
    with pytest.raises(FileExistsError):
        local_selection.run(
            config, new_target="ease", repeat_target="ease-bpr-rrf", render=lambda *a: []
        )


def test_changed_context_never_publishes(prepared_local):
    config = prepared_local
    (config.output / "selection-context" / "contexts.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        local_selection.run(
            config, new_target="ease", repeat_target="ease-bpr-rrf", render=lambda *a: []
        )
    assert not (config.output / "selector-local").exists()


def test_render_failure_leaves_only_partial_output(prepared_local):
    def fail(*args):
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        local_selection.run(
            prepared_local, new_target="ease", repeat_target="ease-bpr-rrf", render=fail
        )
    assert not (prepared_local.output / "selector-local").exists()
    assert not (prepared_local.output / "selector-local.partial" / "result.json").exists()
