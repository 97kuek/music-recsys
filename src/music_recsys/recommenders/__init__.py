"""Notebook・Benchmark共通のモデル選択。"""

from collections.abc import Iterable
from typing import Protocol

from music_recsys.data import Interaction
from music_recsys.recommenders.itemcf import ItemCFRecommender
from music_recsys.recommenders.popularity import PopularityRecommender
from music_recsys.types import RecommendationRequest, RecommendationResponse


class Recommender(Protocol):
    def recommend(self, request: RecommendationRequest) -> RecommendationResponse: ...


AVAILABLE_MODELS = ("popularity", "itemcf")


def create_recommender(
    model: str,
    interactions: Iterable[Interaction],
    *,
    parameters: dict | None = None,
    seed: int = 0,
) -> Recommender:
    if model == "popularity":
        if parameters:
            raise ValueError("popularity has no tunable parameters")
        return PopularityRecommender(interactions)
    if model == "itemcf":
        parameters = parameters or {}
        if set(parameters) - {"neighbors", "similarity"}:
            raise ValueError("unknown itemcf parameters")
        return ItemCFRecommender(interactions, **parameters)
    raise ValueError(
        f"model '{model}' is not implemented; available: {', '.join(AVAILABLE_MODELS)}"
    )
