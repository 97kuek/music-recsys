"""履歴と正解を別々に保持する評価データセット。"""

from dataclasses import dataclass

from src.domain.recommendation.contracts import (
    GroundTruth,
    RecommendationRequest,
    validate_id,
    validate_ids,
)


@dataclass(frozen=True)
class Interaction:
    user_id: str
    item_id: str

    def __post_init__(self) -> None:
        validate_id(self.user_id)
        validate_id(self.item_id)


@dataclass(frozen=True)
class BenchmarkDataset:
    dataset_id: str
    catalog_ids: tuple[str, ...]
    train_interactions: tuple[Interaction, ...]
    requests: tuple[RecommendationRequest, ...]
    ground_truth: tuple[GroundTruth, ...]
    sha256: str
    provenance: dict | None = None

    def __post_init__(self) -> None:
        validate_id(self.dataset_id)
        validate_ids(self.catalog_ids)
        catalog = set(self.catalog_ids)
        if not catalog or not self.requests:
            raise ValueError("catalog and requests must not be empty")
        history_by_user: dict[str, set[str]] = {}
        for interaction in self.train_interactions:
            if interaction.item_id not in catalog:
                raise ValueError("training item is outside catalog")
            history_by_user.setdefault(interaction.user_id, set()).add(interaction.item_id)
        request_ids = [request.request_id for request in self.requests]
        truth_ids = [truth.request_id for truth in self.ground_truth]
        if len(set(request_ids)) != len(request_ids) or len(set(truth_ids)) != len(truth_ids):
            raise ValueError("duplicate request_id")
        if set(request_ids) != set(truth_ids):
            raise ValueError("ground_truth must match all request IDs")
        for request in self.requests:
            if not set(request.candidate_ids).issubset(catalog):
                raise ValueError("candidate item is outside catalog")
            if not set(request.history).issubset(history_by_user.get(request.user_id, set())):
                raise ValueError("request history must come from the user's training data")
        for truth in self.ground_truth:
            if not set(truth.relevant_item_ids).issubset(catalog):
                raise ValueError("ground_truth item is outside catalog")
