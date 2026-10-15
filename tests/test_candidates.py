import pytest

from music_recsys.data import Interaction
from music_recsys.recommenders import create_recommender
from music_recsys.types import RecommendationRequest


@pytest.mark.parametrize("model", ["popularity", "itemcf"])
def test_repeat_candidates_keep_only_history(model):
    recommender = create_recommender(model, (Interaction("u", "a"), Interaction("v", "b")))
    request = RecommendationRequest("r", "u", ("a",), ("a", "b"), 20, task="repeat")
    assert recommender.recommend(request).item_ids == ("a",)
    assert recommender.recommend(
        RecommendationRequest("n", "u", ("a",), ("a", "b"), 20)
    ).item_ids == ("b",)


def test_candidate_policy_preserves_order_and_empty_set():
    from music_recsys.candidates import eligible_candidates

    request = RecommendationRequest("r", "u", ("a", "c"), ("c", "b", "a"), 20, task="repeat")
    assert eligible_candidates(request) == ("c", "a")
    assert eligible_candidates(RecommendationRequest("empty", "u", (), (), 20, task="repeat")) == ()
    with pytest.raises(ValueError):
        RecommendationRequest("bad", "u", (), ("a",), 20, task="unknown")
