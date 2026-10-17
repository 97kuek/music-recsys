import numpy as np
import pytest

from src.infrastructure.recommenders.als import als_solve


def test_log_confidence_matches_dense_objective():
    other = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    confidence = np.diag(1 + 10 * np.log1p([2, 0, 30]))
    expected = np.linalg.solve(
        other.T @ confidence @ other + 0.1 * np.eye(2), other.T @ confidence @ np.array([1, 0, 1])
    )
    actual = als_solve(
        other, [(0, 2), (2, 30)], alpha=10, regularization=0.1, confidence_transform="log1p"
    )
    np.testing.assert_allclose(actual, expected)


@pytest.mark.parametrize("transform", ["linear", "log1p"])
def test_sparse_als_matches_event_model_and_is_order_independent(tmp_path, transform):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.domain.evaluation.dataset import Interaction
    from src.infrastructure.recommenders.als import ALSRecommender
    from src.infrastructure.recommenders.full_als import FullALS

    records = [
        dict(user_id=u, item_id=i, play_count=n)
        for u, i, n in [("u", "a", 10), ("u", "b", 1), ("v", "b", 2), ("v", "c", 5)]
    ]
    path = tmp_path / "learning.parquet"
    pq.write_table(pa.Table.from_pylist(records[::-1]), path)
    params = dict(
        factors=3,
        iterations=4,
        alpha=10,
        regularization=0.1,
        seed=42,
        confidence_transform=transform,
    )
    actual = FullALS(path, **params, progress=lambda _: None)
    expected = ALSRecommender(
        [Interaction(r["user_id"], r["item_id"]) for r in records for _ in range(r["play_count"])],
        **params,
    )
    np.testing.assert_allclose(actual.user_factors, expected.user_factors)
    np.testing.assert_allclose(actual.item_factors, expected.item_factors)
    assert actual.rank("u", {"a", "b"}, "new") == ["c"]
    assert set(actual.rank("u", {"a", "b"}, "repeat")) == {"a", "b"}
    repeat = FullALS(path, **params, progress=lambda _: None)
    np.testing.assert_array_equal(actual.item_factors, repeat.item_factors)
    with pytest.raises(ValueError, match="memory_budget"):
        FullALS(path, factors=100000, memory_budget_mb=1)
    with pytest.raises(ValueError, match="confidence_transform"):
        FullALS(path, confidence_transform="invalid")


def test_gpu_als_matches_small_dense_solution(tmp_path):
    import os

    if os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1":
        pytest.skip("GPU test requires explicit MUSIC_RECSYS_TEST_GPU=1")
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.infrastructure.recommenders.full_als import FullALS

    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, play_count=n)
                for u, i, n in [("u", "a", 10), ("u", "b", 1), ("v", "b", 2), ("v", "c", 5)]
            ]
        ),
        path,
    )
    params = dict(factors=3, iterations=2, seed=42, progress=lambda _: None)
    cpu = FullALS(path, **params)
    gpu = FullALS(path, **params, use_gpu=True, cg_steps=20)
    np.testing.assert_allclose(gpu.user_factors, cpu.user_factors, rtol=0.002, atol=0.002)
    np.testing.assert_allclose(gpu.item_factors, cpu.item_factors, rtol=0.002, atol=0.002)
    assert gpu.rank("u", {"a", "b"}, "new") == ["c"]
    assert set(gpu.rank("u", {"a", "b"}, "repeat")) == {"a", "b"}
    large = FullALS(path, factors=512, iterations=2, use_gpu=True, progress=lambda _: None)
    assert np.isfinite(large.item_factors).all()
    assert large.execution["training_device"] == "cuda"


@pytest.mark.parametrize("task", ["new", "repeat"])
def test_partial_ranking_matches_full_sort_including_ties(task):
    from src.infrastructure.recommenders.full_als import FullALS

    rng = np.random.default_rng(19)
    model = FullALS.__new__(FullALS)
    model.items = np.array([f"i{i:04}" for i in range(200)])
    model.index = {item: i for i, item in enumerate(model.items)}
    model.users = {"u": 0}
    model.user_factors = np.array([[1.0]])
    model.item_factors = rng.integers(0, 4, (200, 1)).astype(float)
    model.plays = rng.integers(1, 20, 200)
    history = set(model.items[:100])
    candidates = np.arange(100) if task == "repeat" else np.arange(100, 200)
    scores = model.item_factors[candidates, 0]
    expected = candidates[np.lexsort((candidates, -model.plays[candidates], -scores))[:20]]
    assert model.rank("u", history, task) == model.items[expected].tolist()
