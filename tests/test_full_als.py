import numpy as np
import pytest

from music_recsys.recommenders.als import als_solve


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

    from music_recsys.data import Interaction
    from music_recsys.recommenders.als import ALSRecommender
    from music_recsys.recommenders.full_als import FullALS

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
