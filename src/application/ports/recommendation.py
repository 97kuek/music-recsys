"""推薦ユースケースが要求する推論とモデル選択のポート。"""

from collections.abc import Collection
from typing import Protocol

from src.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse
from src.domain.recommendation.models import ModelSpec, Selection


class Recommender(Protocol):
    def recommend(self, request: RecommendationRequest) -> RecommendationResponse: ...


class Strategy(Protocol):
    def select(
        self, request: RecommendationRequest, models: tuple[ModelSpec, ...]
    ) -> Selection: ...


class ServedRecommender(Recommender, Protocol):
    """配信可能な学習カタログを持つ推論器。"""

    index: Collection[str]
