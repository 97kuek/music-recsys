"""Notebook・Benchmark共通のモデル選択。"""

from collections.abc import Iterable

from src.application.ports.recommendation import Recommender
from src.domain.evaluation.dataset import Interaction
from src.infrastructure.recommenders.itemcf import ItemCFRecommender
from src.infrastructure.recommenders.popularity import PopularityRecommender
from src.infrastructure.recommenders.usercf import UserCFRecommender

AVAILABLE_MODELS = ("popularity", "itemcf", "usercf", "als", "bpr", "content", "hybrid")


def create_recommender(
    model: str,
    interactions: Iterable[Interaction],
    *,
    parameters: dict | None = None,
    seed: int = 0,
    features: dict | None = None,
) -> Recommender:
    if model == "popularity":
        parameters = parameters or {}
        if parameters.keys() - {"basis"}:
            raise ValueError("unknown popularity parameters")
        return PopularityRecommender(interactions, **parameters)
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
        allowed |= {"alpha", "confidence_transform"} if model == "als" else {"learning_rate"}
        if set(parameters) - allowed:
            raise ValueError(f"unknown {model} parameters")
        if model == "als":
            from src.infrastructure.recommenders.als import ALSRecommender

            return ALSRecommender(interactions, seed=seed, **parameters)
        from src.infrastructure.recommenders.bpr import BPRRecommender

        return BPRRecommender(interactions, seed=seed, **parameters)
    if model in {"content", "hybrid"}:
        parameters = parameters or {}
        allowed = {"use_idf", "aggregation"}
        if model == "hybrid":
            allowed |= {
                "cf_model",
                "neighbors",
                "similarity",
                "cf_weight",
                "rrf_constant",
                "pool_size",
            }
        if set(parameters) - allowed:
            raise ValueError(f"unknown {model} parameters")
        if model == "content":
            from src.infrastructure.recommenders.content import ContentRecommender

            return ContentRecommender(interactions, features=features, **parameters)
        from src.infrastructure.recommenders.hybrid import HybridRecommender

        return HybridRecommender(interactions, features=features, **parameters)
    raise ValueError(
        f"model '{model}' is not implemented; available: {', '.join(AVAILABLE_MODELS)}"
    )
