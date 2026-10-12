"""学習データの分離、候補制約、入力整合性を確認する。"""

import json
from pathlib import Path

import pytest

TOY_PATH = Path(__file__).resolve().parents[1] / "examples/toy/dataset.json"


def test_popularity_counts_training_events_and_breaks_ties_by_id():
    from music_recsys.data import Interaction
    from music_recsys.recommenders.popularity import PopularityRecommender
    from music_recsys.types import RecommendationRequest

    model = PopularityRecommender(
        (Interaction("u1", "b"), Interaction("u2", "a"), Interaction("u1", "b"))
    )
    response = model.recommend(RecommendationRequest("r", "cold", (), ("z", "b", "a", "c"), 4))
    assert response.item_ids == ("b", "a", "c", "z")
    assert response.request_id == "r"


def test_popularity_excludes_history_even_if_it_is_in_candidates():
    from music_recsys.data import Interaction
    from music_recsys.recommenders.popularity import PopularityRecommender
    from music_recsys.types import RecommendationRequest

    model = PopularityRecommender((Interaction("u", "a"),))
    result = model.recommend(RecommendationRequest("r", "u", ("a",), ("a", "b"), 10))
    assert result.item_ids == ("b",)


def test_toy_dataset_has_separate_training_and_truth():
    from music_recsys.data import load_dataset
    from music_recsys.recommenders.popularity import PopularityRecommender

    dataset = load_dataset(TOY_PATH, top_k=2)
    assert len(dataset.requests) == 5
    assert len(dataset.catalog_ids) == 6
    model = PopularityRecommender(dataset.train_interactions)
    assert model.recommend(dataset.requests[0]).item_ids == ("track_c", "track_d")
    assert dataset.ground_truth[0].relevant_item_ids == ("track_c", "track_e")
    assert "track_e" not in {interaction.item_id for interaction in dataset.train_interactions}
    assert set(dataset.requests[0].history).isdisjoint(dataset.requests[0].candidate_ids)


def test_changing_test_truth_does_not_change_recommendations(tmp_path):
    from music_recsys.data import load_dataset
    from music_recsys.recommenders.popularity import PopularityRecommender

    raw = json.loads(TOY_PATH.read_text())
    raw["ground_truth"][0]["relevant_item_ids"] = ["track_f"]
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(raw))
    original = load_dataset(TOY_PATH, top_k=2)
    changed = load_dataset(changed_path, top_k=2)
    assert PopularityRecommender(original.train_interactions).recommend(original.requests[0]) == (
        PopularityRecommender(changed.train_interactions).recommend(changed.requests[0])
    )
    assert original.sha256 != changed.sha256


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_catalog",
        "unknown_train_item",
        "unseen_history",
        "missing_truth",
        "duplicate_request",
        "unknown_truth_item",
    ],
)
def test_loader_rejects_inconsistent_dataset(tmp_path, mutation):
    from music_recsys.data import load_dataset

    raw = json.loads(TOY_PATH.read_text())
    if mutation == "duplicate_catalog":
        raw["catalog_ids"].append("track_a")
    elif mutation == "unknown_train_item":
        raw["train_interactions"][0]["item_id"] = "outside"
    elif mutation == "unseen_history":
        raw["requests"][0]["history"].append("track_f")
    elif mutation == "missing_truth":
        raw["ground_truth"].pop()
    elif mutation == "duplicate_request":
        raw["requests"].append(raw["requests"][0])
    elif mutation == "unknown_truth_item":
        raw["ground_truth"][0]["relevant_item_ids"] = ["outside"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_dataset(path, top_k=2)


@pytest.mark.parametrize("k", [0, -1, True])
def test_request_rejects_invalid_top_k(k):
    from music_recsys.types import RecommendationRequest

    with pytest.raises(ValueError):
        RecommendationRequest("r", "u", (), ("a",), k)


def test_request_rejects_mutable_or_invalid_item_sequences():
    from music_recsys.types import RecommendationRequest

    for candidate_ids in (["a"], ("a", "a"), ("",), (3,)):
        with pytest.raises(ValueError):
            RecommendationRequest("r", "u", (), candidate_ids, 2)
