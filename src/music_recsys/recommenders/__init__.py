"""Notebook・Benchmark共通のモデル選択。"""

from collections.abc import Iterable
from typing import Protocol

from music_recsys.data import Interaction
from music_recsys.recommenders.itemcf import ItemCFRecommender
from music_recsys.recommenders.popularity import PopularityRecommender
from music_recsys.recommenders.usercf import UserCFRecommender
from music_recsys.types import RecommendationRequest, RecommendationResponse


class Recommender(Protocol):
    def recommend(self, request: RecommendationRequest) -> RecommendationResponse: ...


AVAILABLE_MODELS = ("popularity", "itemcf", "usercf", "als", "bpr")


def create_recommender(
    model: str,
    interactions: Iterable[Interaction],
    *,
    parameters: dict | None = None,
    seed: int = 0,
    features: dict | None = None,
) -> Recommender:
    if model == "popularity":
        if parameters:
            raise ValueError("popularity has no tunable parameters")
        return PopularityRecommender(interactions)
    if model in {"itemcf", "usercf"}:
        parameters = parameters or {}
        if set(parameters) - {"neighbors", "similarity"}:
            raise ValueError(f"unknown {model} parameters")
        return (ItemCFRecommender if model == "itemcf" else UserCFRecommender)(
            interactions, **parameters
        )
    if model in {"als", "bpr"}:
        parameters = parameters or {}
        allowed = {"factors", "iterations", "regularization", "memory_budget_mb"}
        allowed |= {"alpha"} if model == "als" else {"learning_rate"}
        if set(parameters) - allowed:
            raise ValueError(f"unknown {model} parameters")
        if model == "als":
            from music_recsys.recommenders.als import ALSRecommender

            return ALSRecommender(interactions, seed=seed, **parameters)
        from music_recsys.recommenders.bpr import BPRRecommender

        return BPRRecommender(interactions, seed=seed, **parameters)
    raise ValueError(
        f"model '{model}' is not implemented; available: {', '.join(AVAILABLE_MODELS)}"
    )
