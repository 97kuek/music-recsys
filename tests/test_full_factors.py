import numpy as np
import pytest

from music_recsys.recommenders.full_factors import FullFactorModel


def test_shared_learning_sorts_and_aggregates_counts(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, play_count=n)
                for u, i, n in [("v", "c", 4), ("u", "b", 2), ("u", "a", 1), ("u", "b", 3)]
            ]
        ),
        path,
    )
    model = FullFactorModel()
    matrix = model.load_learning(path)
    assert model.users == {"u": 0, "v": 1}
    assert model.items.tolist() == ["a", "b", "c"]
    np.testing.assert_array_equal(matrix.toarray(), [[1, 5, 0], [0, 0, 4]])
    np.testing.assert_array_equal(model.plays, [1, 5, 4])
    assert matrix.has_sorted_indices


@pytest.mark.parametrize("count", [0, -1, float("nan"), float("inf")])
def test_shared_learning_rejects_invalid_counts(tmp_path, count):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "learning.parquet"
    pq.write_table(pa.Table.from_pylist([dict(user_id="u", item_id="a", play_count=count)]), path)
    with pytest.raises(ValueError, match="finite and positive"):
        FullFactorModel().load_learning(path)
