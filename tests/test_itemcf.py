"""手計算した暗黙フィードバックで近傍・候補・根拠を検証する。"""

import math

import pytest

from src.domain.evaluation.dataset import Interaction
from src.domain.recommendation.contracts import RecommendationRequest
from src.infrastructure.recommenders import create_recommender


def interactions():
    # |A|=9, |B|=5, |C|=1, |A∩B|=2, |A∩C|=1。
    return tuple(
        [Interaction(f"u{i}", "a") for i in range(9)]
        + [Interaction(f"u{i}", "b") for i in (0, 1, 10, 11, 12)]
        + [Interaction("u2", "c")]
    )


def test_cosine_and_jaccard_produce_hand_computed_rankings():
    request = RecommendationRequest("r", "u8", ("a",), ("a", "b", "c"), 20)
    cosine = create_recommender(
        "itemcf", interactions(), parameters={"neighbors": 2, "similarity": "cosine"}
    )
    jaccard = create_recommender(
        "itemcf", interactions(), parameters={"neighbors": 2, "similarity": "jaccard"}
    )
    assert cosine.recommend(request).item_ids == ("c", "b")
    assert jaccard.recommend(request).item_ids == ("b", "c")
    assert cosine.explain(request, "b")["score"] == pytest.approx(2 / math.sqrt(45))
    assert cosine.explain(request, "c")["score"] == pytest.approx(1 / 3)
    assert jaccard.explain(request, "b")["score"] == pytest.approx(2 / 12)
    assert jaccard.explain(request, "c")["score"] == pytest.approx(1 / 9)


def test_neighbor_limit_applies_before_request_candidates_and_repeated_plays_do_not_change_similarity():
    request = RecommendationRequest("r", "u8", ("a",), ("b", "c"), 20)
    data = interactions() + (Interaction("u0", "b"),) * 100
    model = create_recommender("itemcf", data, parameters={"neighbors": 1, "similarity": "cosine"})
    assert model.explain(request, "b")["score"] == 0
    assert model.explain(request, "c")["score"] == pytest.approx(1 / 3)
    only_b = RecommendationRequest("restricted", "u8", ("a",), ("b",), 20)
    assert model.explain(only_b, "b")["score"] == 0
    assert model.recommend(request).item_ids == ("c", "b")


def test_cold_user_and_unknown_items_use_explicit_popularity_fallback():
    model = create_recommender("itemcf", interactions(), parameters={"neighbors": 2})
    request = RecommendationRequest("r", "cold", (), ("z", "c", "b"), 20)
    assert model.recommend(request).item_ids == ("b", "c", "z")
    detail = model.explain(request, "b")
    assert detail["score"] == 0
    assert detail["fallback"] == "popularity"
    assert detail["contributors"] == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"neighbors": 0},
        {"neighbors": True},
        {"neighbors": 1.5},
        {"similarity": "unknown"},
        {"unknown": 1},
    ],
)
def test_invalid_model_parameters_are_rejected(parameters):
    with pytest.raises(ValueError):
        create_recommender("itemcf", interactions(), parameters=parameters)


def test_training_order_and_seed_do_not_change_tied_rankings():
    data = (Interaction("u", "a"), Interaction("u", "b"), Interaction("u", "c"))
    request = RecommendationRequest("r", "u", ("a",), ("c", "b", "a"), 20)
    for seed, rows in ((0, data), (42, reversed(data))):
        model = create_recommender("itemcf", rows, parameters={"neighbors": 2}, seed=seed)
        assert model.recommend(request).item_ids == ("b", "c")
        assert model.explain(request, "b")["contributors"] == [{"item_id": "a", "similarity": 1.0}]


def test_existing_benchmark_cli_accepts_itemcf_parameters(tmp_path):
    from pathlib import Path

    from src.infrastructure.evaluation.benchmark import run_experiment

    dataset = Path(__file__).resolve().parents[1] / "tests/fixtures/examples/toy/dataset.json"
    config = tmp_path / "benchmark.toml"
    config.write_text(
        f'[benchmark]\ndataset="{dataset}"\nmodel="itemcf"\ntop_k=10\nseed=42\n[benchmark.parameters]\nneighbors=2\nsimilarity="jaccard"\n'
    )
    report = run_experiment(config)
    assert report.summary.n_failed == 0
    assert report.metadata.model_name == "itemcf"
    assert report.metadata.model_parameters == {"neighbors": 2, "similarity": "jaccard"}


def test_inspection_exposes_itemcf_scores_and_history_evidence():
    from pathlib import Path

    from src.infrastructure.datasets.json_dataset import load_dataset
    from src.interfaces.notebook.inspection import inspect_recommendation

    dataset = load_dataset(
        Path(__file__).resolve().parents[1] / "tests/fixtures/examples/toy/dataset.json", top_k=20
    )
    result = inspect_recommendation(
        dataset, model="itemcf", parameters={"neighbors": 2}, request_id="r1"
    )
    assert result.recommendations[0]["score"] > 0
    assert result.recommendations[0]["contributors"]
    assert result.recommendations[0]["reason"]


def test_real_itemcf_grid_selects_and_retrains_locked_parameters(study):
    from dataclasses import replace

    from src.infrastructure.research.search import (
        freeze_selection,
        inspect_selected,
        review_selection,
        run_final_test,
        run_search,
        select_best,
        smoke,
    )
    from src.interfaces.notebook.inspection import inspect_recommendation

    config = replace(
        study(),
        model="itemcf",
        grid={"neighbors": [1, 2, 3], "similarity": ["cosine", "jaccard"]},
        stage="B",
    )
    assert smoke(config)["status"] == "succeeded"
    searched = run_search(config)
    assert searched["status"] == "succeeded"
    assert searched["planned_trials"] == 12
    assert run_search(config)["reused_trials"] == 12
    selected = select_best(config)
    inspection = inspect_selected(config)
    result = inspect_recommendation(
        inspection["dataset"],
        model=inspection["model"],
        parameters=inspection["parameters"],
        seed=inspection["seed"],
        request_id="r1",
    )
    assert result.response.status == "ok"
    review_selection(config, accepted=True)
    freeze_selection(config)
    final = run_final_test(config)
    assert final["status"] == "succeeded"
    assert all(t["parameters"] == selected["parameters"] for t in final["trials"].values())
