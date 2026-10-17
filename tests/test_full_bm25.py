import numpy as np
import pytest


def create_learning(path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = np.random.default_rng(9)
    rows = []
    for u in range(15):
        for i in sorted(rng.choice(11, size=4, replace=False)):
            rows.append(
                dict(user_id=f"u{u:02}", item_id=f"i{i:02}", play_count=int(rng.integers(1, 8)))
            )
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_bm25_matches_implicit_weighting_and_export(tmp_path):
    from implicit.nearest_neighbours import bm25_weight

    from src.domain.recommendation.contracts import RecommendationRequest
    from src.infrastructure.models.model_artifacts import export_model, load_model
    from src.infrastructure.recommenders.full_bm25 import FullBM25

    path = tmp_path / "learning.parquet"
    create_learning(path)
    model = FullBM25(path, neighbors=3, progress=lambda _: None)
    raw = model.load_learning(path)
    weighted = bm25_weight(raw.T, K1=1.2, B=0.75).tocsr()
    expected = (weighted @ weighted.T).toarray()
    np.fill_diagonal(expected, -np.inf)
    order = np.argsort(-expected, axis=1, kind="stable")[:, :3]
    np.testing.assert_array_equal(model.targets, order)
    np.testing.assert_allclose(model.weights, np.take_along_axis(expected, order, axis=1))
    export_model(model, tmp_path / "serving", version_id="bm25-test")
    spec, deployed = load_model(tmp_path / "serving")
    assert spec.algorithm == "itemcf"
    for user, row in model.users.items():
        history = tuple(model.items[raw[row].indices])
        for task in ("new", "repeat"):
            request = RecommendationRequest("r", user, history, tuple(model.items), 20, task)
            assert tuple(model.rank(user, history, task)) == deployed.recommend(request).item_ids


@pytest.mark.parametrize("parameters", [{"neighbors": 0}, {"k1": 0}, {"b": 1.1}, {"use_gpu": 1}])
def test_bm25_invalid_parameters_before_reading(parameters):
    from src.infrastructure.recommenders.full_bm25 import FullBM25

    with pytest.raises(ValueError):
        FullBM25("not-read.parquet", **parameters)


def test_gpu_bm25_neighbors_match_cpu(tmp_path):
    import os

    if os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1":
        pytest.skip("explicit GPU test required")
    from src.infrastructure.recommenders.full_bm25 import FullBM25

    path = tmp_path / "learning.parquet"
    create_learning(path)
    cpu = FullBM25(path, neighbors=5, progress=lambda _: None)
    gpu = FullBM25(path, neighbors=5, use_gpu=True, progress=lambda _: None)
    np.testing.assert_array_equal(gpu.targets, cpu.targets)
    np.testing.assert_allclose(gpu.weights, cpu.weights, atol=1e-12, rtol=1e-12)
