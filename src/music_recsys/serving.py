"""学習済み推薦器の選択と推論。学習・正解集合・外部接続は扱わない。"""

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from music_recsys.candidates import eligible_candidates
from music_recsys.ingestion.types import canonical_json
from music_recsys.recommenders import Recommender
from music_recsys.types import RecommendationRequest, RecommendationResponse, validate_id


@dataclass(frozen=True)
class ModelSpec:
    version_id: str
    algorithm: str
    parameters_json: str
    training_ref: str
    catalog_ref: str
    implementation_ref: str
    seed: int

    def __post_init__(self):
        for value in (
            self.version_id,
            self.algorithm,
            self.training_ref,
            self.catalog_ref,
            self.implementation_ref,
        ):
            validate_id(value)
        parameters = json.loads(self.parameters_json)
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be a JSON object")
        object.__setattr__(self, "parameters_json", canonical_json(parameters))
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")

    @property
    def parameters(self) -> dict:
        return json.loads(self.parameters_json)


@dataclass(frozen=True)
class Selection:
    strategy_id: str
    strategy_version: str
    model_version_id: str

    def __post_init__(self):
        for value in (self.strategy_id, self.strategy_version, self.model_version_id):
            validate_id(value)


class Strategy(Protocol):
    def select(
        self, request: RecommendationRequest, models: tuple[ModelSpec, ...]
    ) -> Selection: ...


@dataclass(frozen=True)
class FixedStrategy:
    model_version_id: str

    def select(self, request: RecommendationRequest, models: tuple[ModelSpec, ...]) -> Selection:
        return Selection("fixed", "1", self.model_version_id)


@dataclass(frozen=True)
class RecommendationExecution:
    request: RecommendationRequest
    response: RecommendationResponse
    model: ModelSpec
    selection: Selection
    selection_seconds: float
    inference_seconds: float
    inference_route: str | None = None


class RecommendationService:
    def __init__(self, models: tuple[tuple[ModelSpec, Recommender], ...]):
        self._models = {spec.version_id: (spec, model) for spec, model in models}
        if not models or len(self._models) != len(models):
            raise ValueError("registry must be nonempty with unique model versions")

    def recommend(
        self, request: RecommendationRequest, strategy: Strategy
    ) -> RecommendationExecution:
        start = perf_counter()
        selection = strategy.select(request, tuple(spec for spec, _ in self._models.values()))
        if selection.model_version_id not in self._models:
            raise ValueError("strategy selected an unregistered model")
        selected = perf_counter()
        spec, model = self._models[selection.model_version_id]
        response = model.recommend(request)
        finished = perf_counter()
        if response.request_id != request.request_id:
            raise ValueError("response request_id mismatch")
        items = response.item_ids
        if len(items) > request.top_k or len(items) != len(set(items)):
            raise ValueError("response has duplicates or exceeds top_k")
        if not set(items).issubset(eligible_candidates(request)):
            raise ValueError("response violates candidate/task restrictions")
        return RecommendationExecution(
            request,
            response,
            spec,
            selection,
            selected - start,
            finished - selected,
            model.route(request) if callable(getattr(model, "route", None)) else None,
        )
