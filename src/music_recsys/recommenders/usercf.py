"""ユニーク既聴集合の類似度を使う、疎なユーザー近傍推薦。"""

import heapq
import math
from collections import Counter, defaultdict
from collections.abc import Iterable

from music_recsys.candidates import eligible_candidates
from music_recsys.data import Interaction
from music_recsys.types import RecommendationRequest, RecommendationResponse


class UserCFRecommender:
    def __init__(
        self,
        interactions: Iterable[Interaction],
        *,
        neighbors: int = 50,
        similarity: str = "cosine",
    ) -> None:
        if type(neighbors) is not int or neighbors < 1:
            raise ValueError("neighbors must be a positive integer")
        if similarity not in {"cosine", "jaccard"}:
            raise ValueError("similarity must be cosine or jaccard")
        self.neighbors, self.similarity = neighbors, similarity
        self._counts = Counter()
        self._items = defaultdict(set)
        item_users = defaultdict(set)
        for event in interactions:
            self._counts[event.item_id] += 1
            self._items[event.user_id].add(event.item_id)
            item_users[event.item_id].add(event.user_id)
        self._neighbors = {}
        for user in sorted(self._items):
            items = self._items[user]
            shared = Counter(other for item in items for other in item_users[item] if other != user)

            def weight(other, overlap):
                denominator = (
                    math.sqrt(len(items) * len(self._items[other]))
                    if similarity == "cosine"
                    else len(items) + len(self._items[other]) - overlap
                )
                return overlap / denominator

            self._neighbors[user] = dict(
                heapq.nsmallest(
                    neighbors,
                    ((other, weight(other, n)) for other, n in shared.items()),
                    key=lambda pair: (-pair[1], pair[0]),
                )
            )

    def _scores(self, request: RecommendationRequest) -> Counter:
        scores = Counter()
        for other, similarity in self._neighbors.get(request.user_id, {}).items():
            for item in sorted(self._items[other]):
                scores[item] += similarity
        return scores

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        scores = self._scores(request)
        ranked = sorted(
            eligible_candidates(request),
            key=lambda item: (-scores[item], -self._counts[item], item),
        )
        return RecommendationResponse(request.request_id, tuple(ranked[: request.top_k]))

    def explain(self, request: RecommendationRequest, item_id: str) -> dict:
        if item_id not in eligible_candidates(request):
            raise ValueError("explanation item must be eligible")
        contributors = [
            {"user_id": user, "similarity": weight}
            for user, weight in self._neighbors.get(request.user_id, {}).items()
            if item_id in self._items[user]
        ]
        return {
            "score": sum(row["similarity"] for row in contributors),
            "contributors": contributors,
            "fallback": None if contributors else "popularity",
            "reason": f"上位{self.neighbors}近傍のうち、この曲を聴いた{len(contributors)}人の{self.similarity}類似度合計。"
            if contributors
            else f"近傍の根拠なし。学習再生{self._counts[item_id]}回のPopularity補完。",
        }
