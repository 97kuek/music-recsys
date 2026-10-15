"""二値関連度のNDCG / Recallと、共通の出力正規化。"""

import math
from collections.abc import Collection, Sequence

from music_recsys.candidates import eligible_candidates
from music_recsys.types import (
    EvaluationResult,
    GroundTruth,
    RecommendationRequest,
    RecommendationResponse,
    validate_top_k,
)


def _validate_ranking(item_ids: Sequence[str], k: int) -> None:
    validate_top_k(k)
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("duplicate recommendations; normalize before scoring")


def ndcg_at_k(item_ids: Sequence[str], relevant_ids: Collection[str], k: int) -> float | None:
    _validate_ranking(item_ids, k)
    relevant = set(relevant_ids)
    if not relevant:
        return None
    dcg = sum(
        1 / math.log2(rank + 1)
        for rank, item_id in enumerate(item_ids[:k], 1)
        if item_id in relevant
    )
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    return dcg / ideal


def recall_at_k(item_ids: Sequence[str], relevant_ids: Collection[str], k: int) -> float | None:
    _validate_ranking(item_ids, k)
    relevant = set(relevant_ids)
    if not relevant:
        return None
    return len(set(item_ids[:k]).intersection(relevant)) / len(relevant)


def evaluate_response(
    request: RecommendationRequest,
    response: RecommendationResponse,
    truth: GroundTruth,
) -> EvaluationResult:
    if request.request_id != response.request_id or request.request_id != truth.request_id:
        raise ValueError("request_id mismatch")
    candidates = set(eligible_candidates(request))
    relevant = candidates.intersection(truth.relevant_item_ids)
    valid: list[str] = []
    seen: set[str] = set()
    invalid_count = duplicate_count = 0
    for item_id in response.item_ids:
        if item_id not in candidates:
            invalid_count += 1
        elif item_id in seen:
            duplicate_count += 1
        else:
            seen.add(item_id)
            valid.append(item_id)
    ranked = valid[: request.top_k]
    return EvaluationResult(
        request_id=request.request_id,
        ndcg=ndcg_at_k(ranked, relevant, request.top_k),
        recall=recall_at_k(ranked, relevant, request.top_k),
        relevant_count=len(relevant),
        returned_count=len(ranked),
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
    )
