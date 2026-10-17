import math

import numpy as np
import pytest

from src.data import Interaction
from src.recommenders import create_recommender
from src.types import RecommendationRequest


def events():
    return [
        Interaction(u, i) for u, items in [("u", "ab"), ("v", "bc"), ("w", "ad")] for i in items
    ]


def test_als_normal_equation_includes_unobserved_preference_zero():
    from src.recommenders.als import als_solve

    other = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    confidence = np.diag([5.0, 1.0, 3.0])
    preference = np.array([1.0, 0.0, 1.0])
    expected = np.linalg.solve(
        other.T @ confidence @ other + 0.2 * np.eye(2), other.T @ confidence @ preference
    )
    actual = als_solve(other, [(0, 2), (2, 1)], alpha=2.0, regularization=0.2)
    np.testing.assert_allclose(actual, expected)


def test_bpr_step_increases_positive_negative_margin():
    from src.recommenders.bpr import bpr_step

    u, p, n = np.array([0.2, 0.4]), np.array([0.3, 0.2]), np.array([-0.1, 0.1])
    before = float(u @ (p - n))
    bpr_step(u, p, n, learning_rate=0.05, regularization=0.01)
    assert float(u @ (p - n)) > before
    assert all(np.isfinite(x).all() for x in (u, p, n))


def test_dense_negative_sampling_terminates_and_is_uniform_over_unseen_positions():
    from src.recommenders.bpr import unseen_at_rank

    assert unseen_at_rank(tuple(range(99999)), 100000, 0) == 99999
    assert [unseen_at_rank((0, 2, 4), 6, k) for k in range(3)] == [1, 3, 5]
    with pytest.raises(ValueError):
        unseen_at_rank((0, 1), 2, 0)


@pytest.mark.parametrize("model", ["als", "bpr"])
def test_factor_models_are_seeded_order_independent_and_support_repeat(model):
    params = {"factors": 3, "iterations": 4}
    a = create_recommender(model, events(), parameters=params, seed=42)
    b = create_recommender(model, reversed(events()), parameters=params, seed=42)
    r = RecommendationRequest("r", "u", ("a", "b"), ("a", "b", "c", "d"), 20)
    assert a.recommend(r).item_ids == b.recommend(r).item_ids
    assert set(a.recommend(r).item_ids) == {"c", "d"}
    for item in ("c", "d"):
        detail = a.explain(r, item)
        assert math.isfinite(detail["score"])
        assert detail["score"] == b.explain(r, item)["score"]
        assert "内積" in detail["reason"]
    repeat = RecommendationRequest("repeat", "u", ("a", "b"), ("a", "b", "c"), 20, task="repeat")
    assert set(a.recommend(repeat).item_ids) == {"a", "b"}
    cold = RecommendationRequest("cold", "unknown", (), ("z", "a", "b"), 20)
    assert a.recommend(cold).item_ids == ("a", "b", "z")
    assert a.explain(cold, "a")["fallback"] == "popularity"


def test_bpr_all_items_heard_uses_explicit_fallback_instead_of_random_untrained_factors():
    model = create_recommender(
        "bpr", [Interaction("u", "a"), Interaction("u", "b")], parameters={"iterations": 2}
    )
    r = RecommendationRequest("r", "u", ("a", "b"), ("a", "b"), 20, task="repeat")
    assert model.recommend(r).item_ids == ("a", "b")
    assert model.explain(r, "a")["fallback"] == "popularity"


@pytest.mark.parametrize("model", ["als", "bpr"])
@pytest.mark.parametrize(
    "params",
    [
        {"factors": True},
        {"factors": 0},
        {"iterations": 0},
        {"regularization": -1},
        {"regularization": float("nan")},
        {"factors": 100000, "memory_budget_mb": 1},
        {"unknown": 1},
    ],
)
def test_factor_models_reject_invalid_or_unaffordable_parameters(model, params):
    with pytest.raises(ValueError):
        create_recommender(model, events(), parameters=params)


@pytest.mark.parametrize(
    "model,params",
    [
        ("als", {"alpha": -1}),
        ("als", {"alpha": 1e308}),
        ("bpr", {"learning_rate": 0}),
        ("bpr", {"learning_rate": float("inf")}),
    ],
)
def test_extreme_confidence_and_learning_rate_are_not_successful_models(model, params):
    with pytest.raises(ValueError):
        create_recommender(
            model, events() * 3, parameters={"factors": 2, "iterations": 2, **params}
        )


def test_bpr_different_seeds_record_actual_run_variation():
    r = RecommendationRequest("r", "u", ("a", "b"), ("c", "d"), 20)
    a = create_recommender("bpr", events(), parameters={"factors": 3, "iterations": 2}, seed=0)
    b = create_recommender("bpr", events(), parameters={"factors": 3, "iterations": 2}, seed=42)
    assert a.explain(r, "c")["score"] != b.explain(r, "c")["score"]
