import math

import pytest

from music_recsys.data import Interaction
from music_recsys.recommenders import create_recommender
from music_recsys.types import RecommendationRequest


def fixture():
    events = [Interaction("u", "a"), Interaction("v", "b"), Interaction("v", "c")]
    r = RecommendationRequest("r", "u", ("a",), ("a", "b", "c", "z"), 20)
    return events, r, {"a": {"rock": 1}, "b": {"rock": 0.8, "pop": 0.2}, "c": {"jazz": 1}}


def test_content_cosine_is_hand_computed_and_explains_missing_features():
    events, r, features = fixture()
    m = create_recommender("content", events, features=features, parameters={"use_idf": False})
    assert m.recommend(r).item_ids == ("b", "c", "z")
    assert m.explain(r, "b")["score"] == pytest.approx(0.8 / math.sqrt(0.68))
    assert m.explain(r, "b")["contributors"][0]["feature"] == "rock"
    assert m.explain(r, "z")["fallback"] == "popularity"
    repeat = RecommendationRequest("repeat", "u", ("a",), ("a", "b"), 20, task="repeat")
    assert m.recommend(repeat).item_ids == ("a",)


def test_content_idf_is_fitted_only_on_learning_catalog():
    events, r, features = fixture()
    m = create_recommender("content", events, features=features)
    features = {**features, **{f"future{i}": {"rock": 100} for i in range(100)}}
    other = create_recommender("content", reversed(events), features=features)
    assert m.explain(r, "b")["score"] == other.explain(r, "b")["score"]


def test_log_count_aggregation_changes_profile_without_future_observations():
    events = [Interaction("u", "a")] * 10 + [
        Interaction("u", "b"),
        Interaction("v", "c"),
        Interaction("v", "d"),
    ]
    features = {"a": {"rock": 1}, "b": {"jazz": 1}, "c": {"rock": 1}, "d": {"jazz": 1}}
    r = RecommendationRequest("r", "u", ("a", "b"), ("c", "d"), 20)
    binary = create_recommender("content", events, features=features, parameters={"use_idf": False})
    weighted = create_recommender(
        "content",
        events,
        features=features,
        parameters={"use_idf": False, "aggregation": "log_count"},
    )
    assert binary.explain(r, "c")["score"] == binary.explain(r, "d")["score"]
    assert weighted.explain(r, "c")["score"] > weighted.explain(r, "d")["score"]


@pytest.mark.parametrize("cf", ["itemcf", "usercf"])
def test_hybrid_endpoints_match_base_rankings_and_middle_rrf_is_measured(cf):
    events, r, features = fixture()
    cf_model = create_recommender(cf, events)
    content = create_recommender("content", events, features=features)
    for weight, base in [(0.0, content), (1.0, cf_model)]:
        m = create_recommender(
            "hybrid", events, features=features, parameters={"cf_model": cf, "cf_weight": weight}
        )
        assert m.recommend(r).item_ids == base.recommend(r).item_ids
    hybrid = create_recommender(
        "hybrid", events, features=features, parameters={"cf_weight": 0.5, "rrf_constant": 60}
    )
    detail = hybrid.explain(r, "b")
    assert detail["score"] == pytest.approx(sum(x["contribution"] for x in detail["contributors"]))
    assert detail["contributors"]


@pytest.mark.parametrize(
    "model,params",
    [
        ("content", {"use_idf": 1}),
        ("content", {"aggregation": "future"}),
        ("content", {"unknown": 1}),
        ("hybrid", {"cf_weight": -1}),
        ("hybrid", {"cf_weight": float("nan")}),
        ("hybrid", {"rrf_constant": -1}),
        ("hybrid", {"pool_size": 1}),
        ("hybrid", {"cf_model": "hybrid"}),
    ],
)
def test_content_hybrid_reject_invalid_parameters(model, params):
    events, _, features = fixture()
    with pytest.raises(ValueError):
        create_recommender(model, events, features=features, parameters=params)


@pytest.mark.parametrize("model", ["content", "hybrid"])
def test_content_models_require_explicit_features(model):
    events, _, _ = fixture()
    with pytest.raises(ValueError, match="features"):
        create_recommender(model, events)
