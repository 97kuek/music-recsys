import numpy as np
import pytest


@pytest.mark.parametrize("similarity", ["cosine", "jaccard", "pearson"])
@pytest.mark.parametrize("neighbors", [1, 3, 50])
def test_full_itemcf_matches_reference(tmp_path, similarity, neighbors):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.domain.evaluation.dataset import Interaction
    from src.domain.recommendation.contracts import RecommendationRequest
    from src.infrastructure.recommenders.full_itemcf import FullItemCF
    from src.infrastructure.recommenders.itemcf import ItemCFRecommender

    rng = np.random.default_rng(7)
    items = tuple(f"i{i:02}" for i in range(20))
    records, events, histories = [], [], {}
    for u in range(30):
        user = f"u{u:02}"
        history = sorted(rng.choice(items, int(rng.integers(1, 15)), replace=False))
        histories[user] = history
        for item in history:
            count = int(rng.integers(1, 10))
            records.append(dict(user_id=user, item_id=item, play_count=count, active_days=1))
            events.extend([Interaction(user, item)] * count)
    path = tmp_path / "learning.parquet"
    pq.write_table(pa.Table.from_pylist(records), path)
    expected = ItemCFRecommender(events, neighbors=neighbors, similarity=similarity)
    actual = FullItemCF(
        path,
        neighbors=neighbors,
        similarity=similarity,
        progress=lambda _: None,
        cache_dir=tmp_path / "cache",
    )
    for user, history in histories.items():
        for task in ("new", "repeat"):
            request = RecommendationRequest("r", user, tuple(history), items, 20, task)
            assert actual.rank(history, task) == list(expected.recommend(request).item_ids)


def test_itemcf_cache_uses_requested_neighbor_prefix(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.infrastructure.recommenders.full_itemcf import FullItemCF

    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.table(
            {
                "user_id": ["a", "a", "a", "b", "b"],
                "item_id": ["x", "y", "z", "x", "y"],
                "play_count": [1, 2, 1, 1, 1],
            }
        ),
        path,
    )
    first = FullItemCF(path, neighbors=2, cache_dir=tmp_path / "cache", progress=lambda _: None)
    cached = FullItemCF(path, neighbors=1, cache_dir=tmp_path / "cache", progress=lambda _: None)
    direct = FullItemCF(path, neighbors=1, progress=lambda _: None)
    assert not first.cache_hit and cached.cache_hit
    assert cached.targets.shape[1] == 1
    assert cached.rank(["x"], "new") == direct.rank(["x"], "new")
