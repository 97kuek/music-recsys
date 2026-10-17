"""学習インタラクションの出現回数を用いる決定的なPopularity推薦。"""

from collections import Counter
from collections.abc import Iterable

from src.domain.evaluation.dataset import Interaction
from src.domain.recommendation.candidates import eligible_candidates
from src.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse


class PopularityRecommender:
    def __init__(self, interactions: Iterable[Interaction], *, basis="play_count") -> None:
        validate_basis(basis)
        if basis == "user_count":
            pairs = {(event.user_id, event.item_id) for event in interactions}
            self._counts = Counter(item for _, item in pairs)
        else:
            self._counts = Counter(interaction.item_id for interaction in interactions)

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        candidates = eligible_candidates(request)
        ranked = sorted(candidates, key=lambda item_id: (-self._counts[item_id], item_id))
        return RecommendationResponse(request.request_id, tuple(ranked[: request.top_k]))


def validate_basis(basis):
    if basis not in ("play_count", "user_count"):
        raise ValueError("popularity basis must be play_count or user_count")
    return basis
