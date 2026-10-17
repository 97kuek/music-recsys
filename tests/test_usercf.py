import math

import pytest

from src.data import Interaction
from src.recommenders import create_recommender
from src.types import RecommendationRequest


def events():
    return tuple(
        Interaction(u, i) for u, items in [("u", "a"), ("v", "ab"), ("w", "acd")] for i in items
    )


def test_usercf_has_hand_computed_neighbors_and_excludes_self():
    model = create_recommender("usercf", events(), parameters={"neighbors": 2})
    request = RecommendationRequest("r", "u", ("a",), ("a", "b", "c", "d"), 20)
    assert model.recommend(request).item_ids == ("b", "c", "d")
    assert model.explain(request, "b")["score"] == pytest.approx(1 / math.sqrt(2))
    assert model.explain(request, "c")["score"] == pytest.approx(1 / math.sqrt(3))
    assert {x["user_id"] for x in model.explain(request, "b")["contributors"]} == {"v"}
    repeat = RecommendationRequest("repeat", "u", ("a",), ("a", "b"), 20, task="repeat")
    assert model.recommend(repeat).item_ids == ("a",)


def test_jaccard_is_binary_and_training_order_does_not_change_ranking():
    r = RecommendationRequest("r", "u", ("a",), ("d", "c", "b"), 20)
    a = create_recommender("usercf", events(), parameters={"similarity": "jaccard", "neighbors": 1})
    b = create_recommender(
        "usercf",
        reversed(events() + (Interaction("v", "a"),) * 10),
        parameters={"similarity": "jaccard", "neighbors": 1},
        seed=42,
    )
    assert a.recommend(r).item_ids == b.recommend(r).item_ids == ("b", "c", "d")
    assert b.explain(r, "b")["score"] == 0.5
    assert b.explain(r, "c")["fallback"] == "popularity"


def test_cold_user_and_empty_candidates_are_explicit():
    model = create_recommender("usercf", events())
    r = RecommendationRequest("r", "cold", (), ("z", "b", "a"), 20)
    assert model.recommend(r).item_ids == ("a", "b", "z")
    assert model.explain(r, "a")["fallback"] == "popularity"
    empty = RecommendationRequest("e", "u", ("a",), ("a",), 20)
    assert model.recommend(empty).item_ids == ()
    with pytest.raises(ValueError):
        model.explain(empty, "a")


@pytest.mark.parametrize(
    "params",
    [
        {"neighbors": True},
        {"neighbors": 0},
        {"neighbors": 1.2},
        {"similarity": "pearson"},
        {"extra": 1},
    ],
)
def test_usercf_rejects_invalid_settings(params):
    with pytest.raises(ValueError):
        create_recommender("usercf", events(), parameters=params)
