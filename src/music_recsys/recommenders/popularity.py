"""学習インタラクションの出現回数を用いる決定的なPopularity推薦。"""

from collections import Counter
from collections.abc import Iterable

from music_recsys.data import Interaction
from music_recsys.types import RecommendationRequest, RecommendationResponse


class PopularityRecommender:
    def __init__(self, interactions: Iterable[Interaction]) -> None:
        self._counts = Counter(interaction.item_id for interaction in interactions)

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        candidates = set(request.candidate_ids).difference(request.history)
        ranked = sorted(candidates, key=lambda item_id: (-self._counts[item_id], item_id))
        return RecommendationResponse(request.request_id, tuple(ranked[: request.top_k]))
