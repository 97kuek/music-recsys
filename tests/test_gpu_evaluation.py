import os
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)


@pytest.mark.parametrize("basis", ["play_count", "user_count"])
def test_gpu_popularity_matches_reference_with_ties_and_empty_candidates(tmp_path, basis):
    import cupy as cp
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.infrastructure.evaluation.gpu_evaluation import GPURanker
    from src.infrastructure.recommenders.full_popularity import FullPopularity

    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id="u", item_id="a", play_count=10),
                dict(user_id="u", item_id="b", play_count=1),
                dict(user_id="v", item_id="b", play_count=1),
                dict(user_id="v", item_id="c", play_count=1),
                dict(user_id="w", item_id="c", play_count=1),
            ]
        ),
        path,
    )
    model = FullPopularity(path, basis=basis)
    histories = [set(), {"a"}, {"a", "b", "c"}]
    rankings, counts, _ = GPURanker(model).rank(["new", "u", "v"], histories)
    order = sorted(model.items, key=lambda i: (-model.scores[model.index[i]], i))
    for task in ("new", "repeat"):
        for row, history in enumerate(histories):
            expected = [i for i in order if (i in history) == (task == "repeat")]
            actual = [model.items[i] for i in cp.asnumpy(rankings[task][row]) if i >= 0]
            assert actual == expected
            assert counts[task][row] == len(expected)


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
    ranker = GPURanker(model)
    rankings, counts, _ = ranker.rank(users, histories)
    candidates, candidate_counts, raw, _ = ranker.candidates(users, histories, top_m=100)
    for task in ("new", "repeat"):
        np.testing.assert_array_equal(
            cp.asnumpy(candidates[task][:, :20]), cp.asnumpy(rankings[task])
        )
        np.testing.assert_array_equal(candidate_counts[task], counts[task])
        for u, indices, scores in zip(
            users, cp.asnumpy(candidates[task]), cp.asnumpy(raw[task]), strict=True
        ):
            valid = indices >= 0
            expected = model.item_factors[indices[valid]] @ model.user_factors[model.users[u]]
            np.testing.assert_allclose(scores[valid], expected, atol=1e-7)
            assert np.isnan(scores[~valid]).all()
    with pytest.raises(ValueError, match="top_m"):
        ranker.candidates(users, histories, top_m=0)
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


def test_gpu_ild_wide_sparse_features_match_independent_dense_pair_reference():
    import cupy as cp

    from src.domain.recommendation.features import unit_vector
    from src.infrastructure.evaluation.gpu_metrics import GPUDiversity

    rng = np.random.default_rng(5626)
    items = [f"i{i:04}" for i in range(300)]
    features = {
        item: {f"t{t}": float(rng.integers(1, 30)) for t in rng.choice(600, 8, replace=False)}
        for item in items
    }
    features["unranked"] = {f"t{i:06}": 1.0 for i in range(168048)}
    features[items[0]] = {}
    del features[items[1]]
    rankings = np.stack([rng.choice(len(items), 20, replace=False) for _ in range(128)])
    rankings[0, 2:] = -1
    rankings[1] = -1
    rankings[2, 1:] = -1
    ks = list(range(2, 21))
    gpu = GPUDiversity(features, items, dict.fromkeys(items, 1), 128, ks)
    gpu.add("new", cp.asarray(rankings), np.sum(rankings >= 0, axis=1))
    result = gpu.finish(dict(new=len(items)))
    vectors = np.zeros((len(items), 600), dtype=float)
    for i, item in enumerate(items):
        for tag, value in unit_vector(features.get(item, {})).items():
            vectors[i, int(tag[1:])] = value
    for row in result:
        values = []
        for ranked in rankings[:, : row["k"]]:
            ids = ranked[ranked >= 0]
            if len(ids) < 2 or not np.all(np.linalg.norm(vectors[ids], axis=1) > 0):
                continue
            gram = vectors[ids] @ vectors[ids].T
            values.append(
                np.triu(1 - np.clip(gram, 0, 1), k=1).sum() / (len(ids) * (len(ids) - 1) / 2)
            )
        assert row["ild_evaluated"] == len(values)
        assert row["ild"] == pytest.approx(np.mean(values), abs=1e-12)


@pytest.mark.parametrize("model_name", ["als", "bpr", "content", "bm25", "ease", "itemcf"])
def test_full_gpu_pipeline_matches_cpu_scoring(tmp_path, monkeypatch, model_name):
    import json
    import runpy
    from dataclasses import replace
    from pathlib import Path

    import duckdb

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.datasets.listening_data import validate_bundle
    from src.infrastructure.evaluation.full_scoring import score_cpu
    from src.infrastructure.recommenders import (
        full_als,
        full_bm25,
        full_bpr,
        full_content,
        full_ease,
        full_itemcf,
    )
    from src.infrastructure.research.configuration import load_settings
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.evaluate import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    config_path = (
        "configs/bpr-30epoch.json" if model_name == "bpr" else f"configs/{model_name}.json"
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
    if model_name == "itemcf":
        raw["model"]["parameters"] = dict(neighbors=3, similarity="cosine")
    raw["search"] = dict(stage="A", seeds=[0], max_trials=1)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    module, name = {
        "als": (full_als, "FullALS"),
        "bpr": (full_bpr, "FullBPR"),
        "content": (full_content, "FullContent"),
        "bm25": (full_bm25, "FullBM25"),
        "ease": (full_ease, "FullEASE"),
        "itemcf": (full_itemcf, "FullItemCF"),
    }[model_name]
    original = getattr(module, name)
    captured = []

    def train(*args, **kwargs):
        model = original(*args, **kwargs)
        captured.append(model)
        return model

    monkeypatch.setattr(module, name, train)
    output = run(path, gpu_itemcf=model_name == "itemcf")
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
        captured[0] if model_name != "itemcf" else None,
        captured[0] if model_name == "itemcf" else None,
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


@pytest.mark.parametrize("similarity", ["cosine", "jaccard", "pearson"])
def test_gpu_itemcf_neighbors_ranking_and_cache_match_cpu(tmp_path, similarity):
    import cupy as cp
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.infrastructure.evaluation.gpu_evaluation import GPURanker
    from src.infrastructure.recommenders.full_itemcf import FullItemCF

    rng = np.random.default_rng(73)
    records, histories = [], []
    for u in range(12):
        history = sorted(rng.choice([f"i{i:02}" for i in range(18)], 8, replace=False))
        histories.append(set(history))
        records.extend(
            dict(user_id=f"u{u:02}", item_id=i, play_count=int(rng.integers(1, 8))) for i in history
        )
    path = tmp_path / "learning.parquet"
    pq.write_table(pa.Table.from_pylist(records), path)
    cpu = FullItemCF(path, neighbors=3, similarity=similarity, progress=lambda _: None)
    gpu = FullItemCF(
        path,
        neighbors=3,
        similarity=similarity,
        use_gpu=True,
        cache_dir=tmp_path / "cache",
        progress=lambda _: None,
    )
    np.testing.assert_array_equal(gpu.targets, cpu.targets)
    np.testing.assert_allclose(gpu.weights, cpu.weights, atol=1e-15, rtol=1e-15)
    users = list(gpu.users)
    ranked, counts, _ = GPURanker(gpu).rank(users, histories)
    for task in ("new", "repeat"):
        for index, history in enumerate(histories):
            actual = [gpu.items[i] for i in cp.asnumpy(ranked[task][index]) if i >= 0]
            assert actual == cpu.rank(history, task)
            assert counts[task][index] == (
                len(history) if task == "repeat" else len(gpu.items) - len(history)
            )
    cached = FullItemCF(
        path,
        neighbors=1,
        similarity=similarity,
        use_gpu=True,
        cache_dir=tmp_path / "cache",
        progress=lambda _: None,
    )
    assert cached.cache_hit and not gpu.cache_hit
    np.testing.assert_array_equal(cached.targets, gpu.targets[:, :1])
    np.testing.assert_allclose(cached.weights, gpu.weights[:, :1])
