import hashlib
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from listening_support import write_bundle

from src.infrastructure.research.bpr_audit import audit_ranking, population_audit, revision_audit
from src.interfaces.notebook.bpr_epoch_views import best_epoch, make_figures
from src.interfaces.notebook.bpr_epochs import summarize


def test_primary_metric_selects_observed_best_epoch_not_largest_budget():
    summary = pd.DataFrame(
        [
            dict(epochs=100, binary_ndcg_mean=0.02, training_seconds_mean=80),
            dict(epochs=200, binary_ndcg_mean=0.03, training_seconds_mean=120),
            dict(epochs=500, binary_ndcg_mean=0.025, training_seconds_mean=220),
        ]
    )
    assert best_epoch(summary) == 200


def test_random_expectation_macro_average_and_full_ranking_leak_check(tmp_path):
    learning = [
        dict(user_id=u, item_id=i, play_count=1, active_days=1)
        for u, i in [("u", "a"), ("u", "b"), ("v", "c"), ("v", "d"), ("w", "a")]
    ]
    truth = [
        dict(
            user_id=u,
            item_id=i,
            window_days=90,
            learning_play_count=0,
            learning_active_days=0,
            evaluation_play_count=1,
            evaluation_active_days=1,
        )
        for u, i in [("u", "c"), ("u", "d"), ("v", "a")]
    ]
    bundle = write_bundle(tmp_path / "bundle", learning=learning, observations=truth)
    conn, _, data = population_audit(bundle, window_days=90, k=1)
    try:
        assert data["users"] == 3 and data["eligible_users"] == 2
        assert data["random_ndcg"] == pytest.approx(0.75)
        assert data["random_recall"] == pytest.approx(0.5)
        output = tmp_path / "saved"
        output.mkdir()
        recs = [
            dict(user_id=u, task="new", item_ids=items, candidate_count=len(items))
            for u, items in [("u", ["c", "d"]), ("v", ["b", "a"]), ("w", ["b", "c", "d"])]
        ]
        pq.write_table(pa.Table.from_pylist(recs), output / "recommendations.parquet")
        measured = audit_ranking(conn, SimpleNamespace(output=output), items=4, k=1)
        assert measured["binary_ndcg"] == pytest.approx(0.5)
        assert measured["recall"] == pytest.approx(0.25)
        recs[0]["item_ids"] = ["a", "d"]
        pq.write_table(pa.Table.from_pylist(recs), output / "recommendations.parquet")
        with pytest.raises(ValueError, match="contain history"):
            audit_ranking(conn, SimpleNamespace(output=output), items=4, k=1)
    finally:
        conn.close()


def test_summary_uses_seed_sd_and_does_not_turn_unmeasured_into_zero():
    import matplotlib.pyplot as plt

    points = pd.DataFrame(
        [
            dict(epochs=100, factors=128, seed=s, binary_ndcg=n, recall=n / 2, training_seconds=t)
            for s, n, t in zip([42, 43, 44], [0, 0.01, 0.02], [10, 20, 30], strict=True)
        ]
    )
    curves = []
    for r in points.itertuples():
        curves.extend(
            [
                dict(
                    epochs=100,
                    seed=r.seed,
                    task="discovery",
                    metric="binary_ndcg",
                    k=20,
                    mean=r.binary_ndcg,
                ),
                dict(
                    epochs=100, seed=r.seed, task="discovery", metric="recall", k=20, mean=r.recall
                ),
                dict(epochs=100, seed=r.seed, task="new", metric="ild", k=20, mean=np.nan),
                dict(epochs=100, seed=r.seed, task="all", metric="api_cost", k=None, mean=np.nan),
            ]
        )
    curves = pd.DataFrame(curves)
    summary = summarize(points, curves, 20)
    assert summary.binary_ndcg_mean.iloc[0] == pytest.approx(0.01)
    assert summary.binary_ndcg_std.iloc[0] == pytest.approx(0.01)
    assert pd.isna(summary.api_cost_mean.iloc[0])
    images = make_figures(points, curves, summary, factors=128, window_days=90, k=20)
    try:
        assert not any("ild" in name or "loss" in name for name in images)
        assert all(len(fig.axes) == 1 for fig in images.values())
    finally:
        for fig in images.values():
            plt.close(fig)


def test_revision_audit_accepts_import_move_but_rejects_calculation_change(monkeypatch):
    import subprocess

    names = [
        "recommenders/full_bpr.py",
        "recommenders/full_factors.py",
        "recommenders/factors.py",
        "recommenders/gpu_runtime.py",
        "gpu_evaluation.py",
        "gpu_metrics.py",
        "streaming_metrics.py",
        "full_io.py",
        "full_scoring.py",
        "evaluate.py",
        "listening_data.py",
        "configuration.py",
        "__init__.py",
    ]
    snapshots = {}
    for revision, package in [("a", "src/music_recsys/"), ("b", "src/")]:
        snapshots[revision] = {
            package + n: (
                "from music_recsys.foo import x\nvalue=1\n"
                if revision == "a"
                else "from src.foo import x\nvalue=1\n"
            ).encode()
            for n in names
        }

    def fake_git(args, **kwargs):
        if args[1] == "ls-tree":
            return ("\n".join(sorted(snapshots[args[4]])) + "\n").encode()
        revision, path = args[2].split(":", 1)
        return snapshots[revision][path]

    monkeypatch.setattr(subprocess, "check_output", fake_git)

    def hashes():
        result = {}
        for revision, prefix in [("a", "src/music_recsys/"), ("b", "src/")]:
            h = hashlib.sha256()
            for path, content in sorted(snapshots[revision].items()):
                h.update(path[len(prefix) :].encode() + b"\0" + content + b"\0")
            result[revision] = h.hexdigest()
        return result

    assert revision_audit(".", hashes())["equivalent"]
    snapshots["b"]["src/gpu_metrics.py"] = b"from src.foo import x\nvalue=2\n"
    with pytest.raises(ValueError, match="implementation differs"):
        revision_audit(".", hashes())
