import math

import pytest

from src.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse


def test_idcg_includes_unreturned_high_gain_and_duplicate_positions_do_not_shift():
    from src.domain.evaluation.listening import PlaybackObservation
    from src.domain.evaluation.listening_metrics import evaluate_listening_response

    request = RecommendationRequest("r", "u", (), ("a", "b", "c"), 20)
    response = RecommendationResponse("r", ("a", "a", "outside", "b"))
    observations = tuple(
        PlaybackObservation("u", item, 90, 0, 0, n, d)
        for item, n, d in (("a", 1, 1), ("b", 3, 2), ("c", 9, 3))
    )
    score = evaluate_listening_response(
        request, response, observations, window_days=90, k=2, day_weight=1
    )
    ideal = (math.log2(10) + 2) + (2 + math.log2(3)) / math.log2(3)
    assert score.graded_ndcg == pytest.approx(2 / ideal)
    assert score.binary_ndcg == pytest.approx(1 / (1 + 1 / math.log2(3)))
    assert score.recall == pytest.approx(1 / 3)
    assert score.duplicate_count == 1
    at_four = evaluate_listening_response(
        request, response, observations, window_days=90, k=4, day_weight=0
    )
    assert at_four.invalid_count == 1
    assert at_four.recall == pytest.approx(2 / 3)


def test_repeat_days_weight_and_failure_are_distinct_from_empty_truth():
    from src.domain.evaluation.listening import PlaybackObservation
    from src.domain.evaluation.listening_metrics import evaluate_listening_response

    request = RecommendationRequest("r", "u", ("a", "b"), ("a", "b"), 20, task="repeat")
    observations = (
        PlaybackObservation("u", "a", 90, 2, 1, 3, 1),
        PlaybackObservation("u", "b", 90, 1, 1, 3, 3),
    )
    response = RecommendationResponse("r", ("a",))
    zero = evaluate_listening_response(
        request, response, observations, window_days=90, k=1, day_weight=0
    )
    weighted = evaluate_listening_response(
        request, response, observations, window_days=90, k=1, day_weight=1
    )
    assert zero.graded_ndcg == 1
    assert weighted.graded_ndcg == pytest.approx(3 / 4)
    error = RecommendationResponse("r", (), status="error", error_type="RuntimeError")
    assert (
        evaluate_listening_response(
            request, error, observations, window_days=90, k=10, day_weight=1
        ).graded_ndcg
        == 0
    )
    assert (
        evaluate_listening_response(
            request, error, (), window_days=90, k=10, day_weight=1
        ).graded_ndcg
        is None
    )


def test_overflow_does_not_fill_missing_prefix_truth():
    from src.domain.evaluation.listening import PlaybackObservation
    from src.domain.evaluation.listening_metrics import evaluate_listening_response

    request = RecommendationRequest("r", "u", (), ("a",), 20)
    score = evaluate_listening_response(
        request,
        RecommendationResponse("r", ("outside",) * 20 + ("a",)),
        (PlaybackObservation("u", "a", 90, 0, 0, 1, 1),),
        window_days=90,
        k=20,
        day_weight=1,
    )
    assert score.recall == 0
    assert score.overflow_count == 1
    assert score.valid_count == 0
