import os
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)


def test_gpu_rank_accuracy_diversity_matches_cpu():
    import cupy as cp

    from src.infrastructure.evaluation.gpu_evaluation import GPURanker
    from src.infrastructure.evaluation.gpu_metrics import GPUAccuracy, GPUDiversity
    from src.infrastructure.evaluation.streaming_metrics import (
        DiversityAccumulator,
        accuracy_vectors,
    )
    from src.infrastructure.recommenders.full_factors import FullFactorModel

    model = FullFactorModel()
    model.items = np.array(["a", "b", "c", "d", "e"])
    model.users = {"u": 0, "v": 1, "w": 2}
    model.index = {i: n for n, i in enumerate(model.items)}
    model.plays = np.array([1, 3, 2, 2, 5])
    model.user_factors = np.array([[1, 0], [0, 1], [1, 1]], dtype=np.float32)
    model.item_factors = np.array([[1, 0], [1, 1], [1, 1], [0, 1], [0, 0]], dtype=np.float32)
    users = list(model.users)
    histories = [{"a", "b"}, {"c"}, set(model.items)]
    rankings, counts, _ = GPURanker(model).rank(users, histories)
    config = SimpleNamespace(
        ks=tuple(range(2, 21)),
        window_days=(30, 90, 180),
        day_weights=(0.0, 0.5, 1.0, 2.0),
        primary_window_days=90,
        primary_day_weight=1.0,
    )
    observations = [
        [("u", "b", w, 1, 0, 5, 2) for w in (30, 90, 180)] + [("u", "d", 90, 0, 0, 3, 2)],
        [("v", "a", 90, 0, 0, 2, 1), ("v", "c", 180, 1, 0, 1, 1)],
        [],
    ]
    features = {"a": {"rock": 1}, "b": {"rock": 1, "jazz": 1}, "c": {}, "e": {"jazz": 1}}
    item_users = dict(a=1, b=2, c=1, d=1, e=1)
    gpu_accuracy = GPUAccuracy(config, model.index)
    gpu_diversity = GPUDiversity(features, model.items, item_users, len(users), config.ks)
    cpu_diversity = DiversityAccumulator(features, item_users, len(users), config.ks)
    for task in ("new", "repeat"):
        expected = []
        for row, user in enumerate(users):
            ranking = model.rank(user, histories[row], task)
            actual = [model.items[i] for i in cp.asnumpy(rankings[task][row]) if i >= 0]
            assert actual == ranking
            truth = [
                (r[1], r[2], r[5], r[6])
                for r in observations[row]
                if (r[3] > 0) == (task == "repeat")
            ]
            expected.append(dict(accuracy_vectors(ranking, truth, task=task, config=config)))
            cpu_diversity.add(task, ranking, int(counts[task][row]))
        for key, (values, valid) in gpu_accuracy.add(task, rankings[task], observations):
            for row in range(len(users)):
                assert bool(valid[row]) == (expected[row][key] is not None)
                if valid[row]:
                    np.testing.assert_allclose(
                        values[row], expected[row][key], atol=1e-12, rtol=1e-12
                    )
        gpu_diversity.add(task, rankings[task], counts[task])
    universe = dict(new=5, repeat=5)
    for actual, expected in zip(
        gpu_diversity.finish(universe), cpu_diversity.finish(universe), strict=True
    ):
        assert actual.keys() == expected.keys()
        for key in actual:
            if isinstance(expected[key], float):
                assert actual[key] == pytest.approx(expected[key], abs=1e-12)
            else:
                assert actual[key] == expected[key]
    rows = gpu_accuracy.finish(len(users))
    assert all(r["mean"] is None or np.isfinite(r["mean"]) for r in rows)
    assert all(
        r["ci_low"] is not None
        for r in rows
        if r["window_days"] == 90 and r["n_evaluated"] and r["day_weight"] in (None, 1.0)
    )


@pytest.mark.parametrize("model_name", ["als", "bpr", "content", "bm25"])
def test_full_gpu_pipeline_matches_cpu_scoring(tmp_path, monkeypatch, model_name):
    import json
    import runpy
    from dataclasses import replace
    from pathlib import Path

    import duckdb

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.datasets.listening_data import validate_bundle
    from src.infrastructure.evaluation.full_scoring import score_cpu
    from src.infrastructure.recommenders import full_als, full_bm25, full_bpr, full_content
    from src.infrastructure.research.configuration import load_settings
    from src.interfaces.cli.experiment_search import load_study_config
    from src.interfaces.cli.full_evaluation import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    config_path = (
        "configs/bpr-30epoch/seed0.json" if model_name == "bpr" else f"configs/{model_name}.json"
    )
    raw = load_settings(Path(config_path))
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "never-test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "gpu"),
    )
    if model_name in {"als", "bpr"}:
        raw["model"]["parameters"].update(factors=4, iterations=2, use_gpu=True)
    raw["search"] = dict(stage="A", seeds=[0], max_trials=1)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    module, name = {
        "als": (full_als, "FullALS"),
        "bpr": (full_bpr, "FullBPR"),
        "content": (full_content, "FullContent"),
        "bm25": (full_bm25, "FullBM25"),
    }[model_name]
    original = getattr(module, name)
    captured = []

    def train(*args, **kwargs):
        model = original(*args, **kwargs)
        captured.append(model)
        return model

    monkeypatch.setattr(module, name, train)
    output = run(path)
    actual = json.loads((output / "result.json").read_text(encoding="utf-8"))
    saved = json.loads((output / "config.json").read_text(encoding="utf-8"))
    assert saved["execution"]["evaluation_device"] == "cuda"
    config = load_study_config(path)
    manifest, paths, counts = validate_bundle(config.validation_dataset)
    features = load_features(
        config.features,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    with duckdb.connect() as c:
        ranked = c.execute(
            "SELECT item_id,sum(play_count),count(*) FROM read_parquet(?) GROUP BY item_id ORDER BY sum(play_count) DESC,item_id",
            [str(paths["learning"])],
        ).fetchall()
    popularity = [r[0] for r in ranked]
    cpu = tmp_path / "cpu"
    cpu.mkdir()
    rows, diversity, _ = score_cpu(
        replace(config, output=cpu),
        paths,
        counts,
        features,
        {r[0]: r[2] for r in ranked},
        popularity,
        {i: j for j, i in enumerate(popularity)},
        captured[0],
        None,
        lambda _: None,
    )
    for gpu_rows, cpu_rows in [(actual["accuracy"], rows), (actual["diversity"], diversity)]:
        assert len(gpu_rows) == len(cpu_rows)
        for a, b in zip(gpu_rows, cpu_rows, strict=True):
            assert a.keys() == b.keys()
            for key in a:
                if isinstance(b[key], float):
                    assert a[key] == pytest.approx(b[key], abs=1e-10)
                else:
                    assert a[key] == b[key]
