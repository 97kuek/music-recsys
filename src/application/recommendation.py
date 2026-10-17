"""学習済み推薦器の選択と推論。学習・正解集合・外部接続は扱わない。"""

from time import perf_counter

from src.application.ports.recommendation import Recommender, Strategy
from src.domain.recommendation.candidates import eligible_candidates
from src.domain.recommendation.contracts import RecommendationRequest
from src.domain.recommendation.models import ModelSpec, RecommendationExecution


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
