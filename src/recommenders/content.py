"""固定Snapshotの重み付きタグを、学習カタログだけで変換する内容ベース推薦。"""

import math
from collections import Counter, defaultdict

from src.candidates import eligible_candidates
from src.features import unit_vector, validate_features
from src.types import RecommendationRequest, RecommendationResponse


class ContentRecommender:
    def __init__(
        self, interactions, *, features: dict, use_idf: bool = True, aggregation: str = "binary"
    ) -> None:
        if features is None:
            raise ValueError("content requires explicit features")
        validate_features(features)
        if type(use_idf) is not bool:
            raise ValueError("use_idf must be boolean")
        if not isinstance(aggregation, str) or aggregation not in {"binary", "log_count"}:
            raise ValueError("aggregation must be binary or log_count")
        self.aggregation, self.use_idf = aggregation, use_idf
        self._counts = Counter()
        self._user_counts = defaultdict(Counter)
        for event in interactions:
            self._counts[event.item_id] += 1
            self._user_counts[event.user_id][event.item_id] += 1
        df = Counter(
            t
            for item in sorted(self._counts)
            for t, value in features.get(item, {}).items()
            if value > 0
        )
        n = len(self._counts)
        self._idf = {
            term: math.log((1 + n) / (1 + count)) + 1 if use_idf else 1.0
            for term, count in df.items()
        }
        self._vectors = {}
        for item in sorted(self._counts):
            vector = unit_vector(features.get(item, {}))
            self._vectors[item] = unit_vector(
                {term: value * self._idf[term] for term, value in vector.items()}
            )
        self._last_request = None
        self._last_profile = {}

    def _profile(self, request: RecommendationRequest) -> dict:
        if self._last_request == request:
            return self._last_profile
        profile = Counter()
        for item in sorted(request.history):
            weight = (
                1.0
                if self.aggregation == "binary"
                else math.log1p(self._user_counts[request.user_id][item])
            )
            for term, value in self._vectors.get(item, {}).items():
                profile[term] += weight * value
        self._last_request, self._last_profile = request, unit_vector(profile)
        return self._last_profile

    def _score(self, profile: dict, item_id: str) -> float:
        return math.fsum(
            value * profile.get(term, 0) for term, value in self._vectors.get(item_id, {}).items()
        )

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        profile = self._profile(request)
        ranked = sorted(
            eligible_candidates(request),
            key=lambda item: (-self._score(profile, item), -self._counts[item], item),
        )
        return RecommendationResponse(request.request_id, tuple(ranked[: request.top_k]))

    def explain(self, request: RecommendationRequest, item_id: str) -> dict:
        if item_id not in eligible_candidates(request):
            raise ValueError("explanation item must be eligible")
        profile = self._profile(request)
        contributors = [
            {"feature": term, "contribution": value * profile[term]}
            for term, value in self._vectors.get(item_id, {}).items()
            if profile.get(term, 0) > 0
        ]
        contributors.sort(key=lambda r: (-r["contribution"], r["feature"]))
        score = math.fsum(row["contribution"] for row in contributors)
        return {
            "score": score,
            "contributors": contributors,
            "fallback": None if score > 0 else "popularity",
            "reason": f"学習履歴の嗜好ベクトルと曲タグのCosine類似度。共通する非ゼロタグ{len(contributors)}個。"
            if score > 0
            else f"利用可能な共通タグなし。学習再生{self._counts[item_id]}回のPopularity補完。",
        }
