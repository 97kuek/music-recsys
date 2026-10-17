"""手計算した順位評価と、不正な入力の扱いを確認する。"""

import math

import pytest


def test_ndcg_matches_hand_calculation():
    from src.domain.evaluation.metrics import ndcg_at_k

    # 正解は1位と3位。DCG=1+0.5、IDCG=1+1/log2(3)。
    assert ndcg_at_k(("a", "b", "c"), {"a", "c"}, 3) == pytest.approx(1.5 / (1 + 1 / math.log2(3)))


def test_recall_uses_all_relevant_items_as_denominator():
    from src.domain.evaluation.metrics import recall_at_k

    assert recall_at_k(("a", "x", "b"), {"a", "b", "c"}, 2) == pytest.approx(1 / 3)


@pytest.mark.parametrize("function_name", ["ndcg_at_k", "recall_at_k"])
def test_no_relevant_items_is_undefined(function_name):
    from src.domain.evaluation import metrics

    assert getattr(metrics, function_name)(("a",), set(), 2) is None


@pytest.mark.parametrize("function_name", ["ndcg_at_k", "recall_at_k"])
def test_empty_recommendations_score_zero(function_name):
    from src.domain.evaluation import metrics

    assert getattr(metrics, function_name)((), {"a"}, 2) == 0.0


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_k_is_rejected(k):
    from src.domain.evaluation.metrics import ndcg_at_k, recall_at_k

    for function in (ndcg_at_k, recall_at_k):
        with pytest.raises(ValueError):
            function(("a",), {"a"}, k)


def test_duplicate_recommendations_are_rejected_by_raw_metrics():
    from src.domain.evaluation.metrics import ndcg_at_k, recall_at_k

    for function in (ndcg_at_k, recall_at_k):
        with pytest.raises(ValueError, match="duplicate"):
            function(("a", "a"), {"a"}, 2)


def test_evaluator_filters_invalid_and_duplicate_ids_before_truncation():
    from src.domain.evaluation.metrics import evaluate_response
    from src.domain.recommendation.contracts import (
        GroundTruth,
        RecommendationRequest,
        RecommendationResponse,
    )

    request = RecommendationRequest("r", "u", (), ("a", "b", "c"), 2)
    response = RecommendationResponse("r", ("outside", "a", "a", "b", "c"))
    result = evaluate_response(request, response, GroundTruth("r", ("a", "outside")))
    assert result.ndcg == 1.0
    assert result.recall == 1.0
    assert result.relevant_count == 1
    assert result.returned_count == 2
    assert result.invalid_count == 1
    assert result.duplicate_count == 1


def test_evaluator_id_mismatch_is_rejected():
    from src.domain.evaluation.metrics import evaluate_response
    from src.domain.recommendation.contracts import (
        GroundTruth,
        RecommendationRequest,
        RecommendationResponse,
    )

    request = RecommendationRequest("r", "u", (), ("a",), 2)
    with pytest.raises(ValueError, match="request_id"):
        evaluate_response(
            request, RecommendationResponse("wrong", ("a",)), GroundTruth("r", ("a",))
        )


def test_failed_response_with_relevant_truth_scores_zero():
    from src.domain.evaluation.metrics import evaluate_response
    from src.domain.recommendation.contracts import (
        GroundTruth,
        RecommendationRequest,
        RecommendationResponse,
    )

    request = RecommendationRequest("r", "u", (), ("a",), 2)
    response = RecommendationResponse("r", (), status="error", error_type="RuntimeError")
    result = evaluate_response(request, response, GroundTruth("r", ("a",)))
    assert result.ndcg == 0.0
    assert result.recall == 0.0
    assert result.returned_count == 0
