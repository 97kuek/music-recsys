"""Notebook・Benchmark共通のモデル選択。"""

from collections.abc import Iterable
from typing import Protocol

from music_recsys.data import Interaction
from music_recsys.recommenders.popularity import PopularityRecommender
from music_recsys.types import RecommendationRequest, RecommendationResponse


class Recommender(Protocol):
    def recommend(self, request: RecommendationRequest) -> RecommendationResponse: ...


def create_recommender(model: str, interactions: Iterable[Interaction]) -> Recommender:
    if model == "popularity":
        return PopularityRecommender(interactions)
    raise ValueError(f"model '{model}' is not implemented; available: popularity")
