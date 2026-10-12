"""推薦関数と評価器で共有する、JSONへ保存可能なデータ形式。"""

from dataclasses import dataclass
from typing import Literal


def validate_top_k(top_k: int) -> None:
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")


def validate_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("IDs must be non-empty strings")


def validate_ids(values: tuple[str, ...], *, unique: bool = True) -> None:
    if not isinstance(values, tuple):
        raise ValueError("IDs must be an immutable tuple")
    for value in values:
        validate_id(value)
    if unique and len(values) != len(set(values)):
        raise ValueError("duplicate IDs are not allowed")


@dataclass(frozen=True)
class RecommendationRequest:
    request_id: str
    user_id: str
    history: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    top_k: int

    def __post_init__(self) -> None:
        validate_id(self.request_id)
        validate_id(self.user_id)
        validate_ids(self.history)
        validate_ids(self.candidate_ids)
        validate_top_k(self.top_k)


@dataclass(frozen=True)
class RecommendationResponse:
    request_id: str
    item_ids: tuple[str, ...]
    status: Literal["ok", "error"] = "ok"
    error_type: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.request_id)
        validate_ids(self.item_ids, unique=False)
        if self.status not in ("ok", "error"):
            raise ValueError("status must be ok or error")
        if self.status == "ok" and self.error_type is not None:
            raise ValueError("successful responses cannot have error_type")
        if self.status == "error":
            validate_id(self.error_type)
            if self.item_ids:
                raise ValueError("failed responses cannot contain recommendations")


@dataclass(frozen=True)
class GroundTruth:
    request_id: str
    relevant_item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_id(self.request_id)
        validate_ids(self.relevant_item_ids)


@dataclass(frozen=True)
class EvaluationResult:
    request_id: str
    ndcg: float | None
    recall: float | None
    relevant_count: int
    returned_count: int
    invalid_count: int
    duplicate_count: int
