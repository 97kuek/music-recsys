import numpy as np
import pytest

from music_recsys.recommenders.full_bpr import FullBPR


def test_bpr_train_only_reproducible_and_binary(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from implicit.cpu.bpr import BayesianPersonalizedRanking
    from scipy.sparse import csr_matrix

    path = tmp_path / "learning.parquet"
    records = [
        dict(user_id=u, item_id=i, play_count=n)
        for u, i, n in [("u", "a", 10), ("u", "b", 1), ("v", "b", 2), ("v", "c", 5)]
    ]
    pq.write_table(pa.Table.from_pylist(records[::-1]), path)
    params = dict(factors=4, iterations=30, seed=42, progress=lambda _: None)
    actual = FullBPR(path, **params)
    expected = BayesianPersonalizedRanking(
        factors=4,
        iterations=30,
        random_state=42,
        num_threads=1,
        learning_rate=0.01,
        regularization=0.01,
        verify_negative_samples=True,
    )
    expected.fit(csr_matrix([[1, 1, 0], [0, 1, 1]], dtype=np.float32), show_progress=False)
    np.testing.assert_array_equal(actual.user_factors, expected.user_factors)
    np.testing.assert_array_equal(actual.item_factors, expected.item_factors)
    assert actual.rank("u", {"a", "b"}, "new") == ["c"]
    assert set(actual.rank("u", {"a", "b"}, "repeat")) == {"a", "b"}
    for record in records:
        record["play_count"] = 1
    pq.write_table(pa.Table.from_pylist(records), path)
    repeat = FullBPR(path, **params)
    np.testing.assert_array_equal(actual.item_factors, repeat.item_factors)
    with pytest.raises(ValueError, match="memory_budget"):
        FullBPR(path, factors=100000, memory_budget_mb=1)
    with pytest.raises(ValueError, match="task"):
        actual.rank("u", {"a", "b"}, "invalid")


def test_all_observed_user_uses_popularity(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id="u", item_id="a", play_count=1),
                dict(user_id="u", item_id="b", play_count=3),
            ]
        ),
        path,
    )
    model = FullBPR(path, progress=lambda _: None)
    assert model.rank("u", {"a", "b"}, "new") == []
    assert model.rank("u", {"a", "b"}, "repeat") == ["b", "a"]


def test_gpu_bpr_explicit_opt_in(tmp_path):
    import os

    if os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1":
        pytest.skip("GPU test requires explicit MUSIC_RECSYS_TEST_GPU=1")
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, play_count=1)
                for u, i in [("u", "a"), ("u", "b"), ("v", "b"), ("v", "c")]
            ]
        ),
        path,
    )
    model = FullBPR(path, factors=32, iterations=3, use_gpu=True, progress=lambda _: None)
    assert model.execution["training_device"] == "cuda"
    assert not model.execution["bitwise_reproducible"]
    assert np.isfinite(model.item_factors).all()
    assert model.rank("u", {"a", "b"}, "new") == ["c"]
    assert set(model.rank("u", {"a", "b"}, "repeat")) == {"a", "b"}
