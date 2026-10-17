import numpy as np
import pytest


@pytest.mark.parametrize("use_idf", [False, True])
@pytest.mark.parametrize("aggregation", ["binary", "log_count"])
def test_sparse_content_matches_reference(tmp_path, use_idf, aggregation):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.data import Interaction
    from src.recommenders.content import ContentRecommender
    from src.recommenders.full_content import FullContent
    from src.types import RecommendationRequest

    records = [
        ("u", "a", 3),
        ("u", "b", 1),
        ("v", "b", 2),
        ("v", "c", 1),
        ("w", "d", 5),
        ("w", "e", 1),
    ]
    features = {
        "a": {"rock": 2},
        "b": {"rock": 1, "jazz": 3},
        "c": {"jazz": 1},
        "d": {},
        "e": {"electronic": 1},
        "future-only": {"future-tag": 100},
    }
    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist([dict(user_id=u, item_id=i, play_count=n) for u, i, n in records]),
        path,
    )
    actual = FullContent(
        path, features=features, use_idf=use_idf, aggregation=aggregation, progress=lambda _: None
    )
    expected = ContentRecommender(
        [Interaction(u, i) for u, i, n in records for _ in range(n)],
        features=features,
        use_idf=use_idf,
        aggregation=aggregation,
    )
    assert actual.vectors.shape == (5, 3)  # Future-only tags never enter fitting.
    assert not hasattr(actual, "user_factors")
    for user in actual.users:
        history = tuple(i for u, i, _ in records if u == user)
        for task in ("new", "repeat"):
            request = RecommendationRequest("r", user, history, tuple(actual.items), 20, task)
            ranked = actual.rank(user, history, task)
            reference = list(expected.recommend(request).item_ids)
            assert set(ranked) == set(reference)
            # Sparse reduction and math.fsum can order mathematically tied scores
            # differently by a few ulps; every non-tied order must agree.
            scores = [expected.explain(request, i)["score"] for i in ranked]
            assert all(a >= b - 1e-14 for a, b in zip(scores, scores[1:]))


def test_content_empty_tags_are_popularity_fallback(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.recommenders.full_content import FullContent

    path = tmp_path / "learning.parquet"
    pq.write_table(pa.table(dict(user_id=["u", "v"], item_id=["a", "b"], play_count=[1, 3])), path)
    model = FullContent(path, features={}, progress=lambda _: None)
    assert model.rank("u", {"a"}, "new") == ["b"]
    assert np.isfinite(model.vectors.data).all()
