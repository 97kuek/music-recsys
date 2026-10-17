"""学習済みモデルの識別と、推薦実行の値オブジェクト。"""

import json
from dataclasses import dataclass

from src.domain.datasets import canonical_json
from src.domain.recommendation.contracts import (
    RecommendationRequest,
    RecommendationResponse,
    validate_id,
)


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


@dataclass(frozen=True)
class ComparisonStrategy:
    version_id: str

    def select(self, request, models):
        return Selection("manual-comparison", "1", self.version_id)
