"""二値のユーザー×曲から学習する、疎なItemCFベースライン。"""

import heapq
import math
from collections import Counter, defaultdict
from collections.abc import Iterable

from src.domain.evaluation.dataset import Interaction
from src.domain.recommendation.candidates import eligible_candidates
from src.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse


class ItemCFRecommender:
    def __init__(
        self,
        interactions: Iterable[Interaction],
        *,
        neighbors: int = 50,
        similarity: str = "cosine",
    ) -> None:
        if type(neighbors) is not int or neighbors < 1:
            raise ValueError("neighbors must be a positive integer")
        if similarity not in {"cosine", "jaccard", "pearson"}:
            raise ValueError("similarity must be cosine, jaccard or pearson")
        self.neighbors, self.similarity = neighbors, similarity
        self._counts = Counter()
        user_items, item_users = defaultdict(set), defaultdict(set)
        for event in interactions:
            self._counts[event.item_id] += 1
            user_items[event.user_id].add(event.item_id)
            item_users[event.item_id].add(event.user_id)
        self._neighbors = {}
        # 1曲分の共起だけを展開し、全曲×全曲の密行列を保持しない。
        for item in sorted(item_users):
            users = item_users[item]
            shared = Counter(other for user in users for other in user_items[user] if other != item)

            def weight(other, overlap):
                if similarity == "pearson":
                    from src.infrastructure.recommenders.binary_similarity import pearson

                    return float(
                        pearson(overlap, len(users), len(item_users[other]), len(user_items))
                    )
                denominator = (
                    math.sqrt(len(users) * len(item_users[other]))
                    if similarity == "cosine"
                    else len(users) + len(item_users[other]) - overlap
                )
                return overlap / denominator

            candidates = ((other, weight(other, count)) for other, count in shared.items())
            nearest = heapq.nsmallest(
                neighbors,
                (pair for pair in candidates if pair[1] > 0),
                key=lambda pair: (-pair[1], pair[0]),
            )
            self._neighbors[item] = dict(nearest)

    def _scores(self, request):
        scores = Counter()
        for heard in sorted(request.history):
            scores.update(self._neighbors.get(heard, {}))
        return scores

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        scores = self._scores(request)
        candidates = eligible_candidates(request)
        ranked = sorted(candidates, key=lambda item: (-scores[item], -self._counts[item], item))
        return RecommendationResponse(request.request_id, tuple(ranked[: request.top_k]))

    def explain(self, request: RecommendationRequest, item_id: str) -> dict:
        if item_id not in eligible_candidates(request):
            raise ValueError("explanation item must be an eligible candidate")
        contributors = [
            {"item_id": heard, "similarity": self._neighbors[heard][item_id]}
            for heard in sorted(request.history)
            if item_id in self._neighbors.get(heard, {})
        ]
        score = sum(row["similarity"] for row in contributors)
        reason = (
            f"履歴曲{len(contributors)}曲からの{self.similarity}類似度の合計。各履歴曲の上位{self.neighbors}近傍を使用。"
            if contributors
            else f"類似度による根拠なし。学習全体の再生{self._counts[item_id]}回によるPopularity補完。"
        )
        return {
            "score": score,
            "reason": reason,
            "contributors": contributors,
            "fallback": None if contributors else "popularity",
        }
