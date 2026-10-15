"""CFと内容ベースの候補順位を、重み付きRRFで統合する。"""

from dataclasses import replace

from music_recsys.candidates import eligible_candidates
from music_recsys.recommenders.content import ContentRecommender
from music_recsys.recommenders.factors import integer, number
from music_recsys.recommenders.itemcf import ItemCFRecommender
from music_recsys.recommenders.usercf import UserCFRecommender
from music_recsys.types import RecommendationRequest, RecommendationResponse


class HybridRecommender:
    def __init__(
        self,
        interactions,
        *,
        features: dict,
        cf_model: str = "itemcf",
        neighbors: int = 50,
        similarity: str = "cosine",
        cf_weight: float = 0.5,
        rrf_constant: float = 60,
        pool_size: int = 100,
        use_idf: bool = True,
        aggregation: str = "binary",
    ) -> None:
        if cf_model not in ("itemcf", "usercf"):
            raise ValueError("cf_model must be itemcf or usercf")
        number(cf_weight, "cf_weight")
        if cf_weight > 1:
            raise ValueError("cf_weight must be within [0,1]")
        number(rrf_constant, "rrf_constant")
        integer(pool_size, "pool_size")
        if pool_size < 20:
            raise ValueError("pool_size must be >=20")
        interactions = tuple(interactions)
        cls = ItemCFRecommender if cf_model == "itemcf" else UserCFRecommender
        self.cf = cls(interactions, neighbors=neighbors, similarity=similarity)
        self.content = ContentRecommender(
            interactions, features=features, use_idf=use_idf, aggregation=aggregation
        )
        self._counts = self.content._counts
        self.cf_weight, self.rrf_constant, self.pool_size = cf_weight, rrf_constant, pool_size
        self._last_request, self._last_parts = None, None

    def _parts(self, request):
        if self._last_request == request:
            return self._last_parts
        retrieved = replace(request, top_k=max(self.pool_size, request.top_k))
        parts = []
        for name, model, weight in (
            ("cf", self.cf, self.cf_weight),
            ("content", self.content, 1 - self.cf_weight),
        ):
            if weight:
                ranks = {
                    item: rank for rank, item in enumerate(model.recommend(retrieved).item_ids, 1)
                }
                parts.append((name, model, weight, ranks))
        self._last_request, self._last_parts = request, parts
        return parts

    def _score(self, item_id, parts):
        return sum(
            weight / (self.rrf_constant + ranks[item_id])
            for _, _, weight, ranks in parts
            if item_id in ranks
        )

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        parts = self._parts(request)
        ranked = sorted(
            eligible_candidates(request),
            key=lambda item: (-self._score(item, parts), -self._counts[item], item),
        )
        return RecommendationResponse(request.request_id, tuple(ranked[: request.top_k]))

    def explain(self, request: RecommendationRequest, item_id: str) -> dict:
        if item_id not in eligible_candidates(request):
            raise ValueError("explanation item must be eligible")
        parts = self._parts(request)
        contributors = [
            {
                "model": name,
                "rank": ranks[item_id],
                "weight": weight,
                "contribution": weight / (self.rrf_constant + ranks[item_id]),
                "base_fallback": model.explain(request, item_id)["fallback"],
            }
            for name, model, weight, ranks in parts
            if item_id in ranks
        ]
        return {
            "score": self._score(item_id, parts),
            "contributors": contributors,
            "fallback": None if contributors else "popularity",
            "reason": "CF・内容ベースの取得順位を重み付きRRFで統合。各寄与と基底の補完使用を併記。"
            if contributors
            else "取得プールにない曲。Popularity補完。",
        }
