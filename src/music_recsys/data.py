"""明示的に分割された検証データを読み込み、IDと履歴の整合性を検証する。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from music_recsys.types import (
    GroundTruth,
    RecommendationRequest,
    validate_id,
    validate_ids,
    validate_top_k,
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


def _rows(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = raw[key]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{key} must be a list of objects")
    return rows


def _ids(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError("item IDs must be a list")
    result = tuple(raw)
    validate_ids(result)
    return result


def load_dataset(path: Path, *, top_k: int) -> BenchmarkDataset:
    """履歴曲を候補から除き、正解は別のフィールドに保持する。"""
    validate_top_k(top_k)
    content = Path(path).read_bytes()
    raw = json.loads(content)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise ValueError("dataset schema_version must be 1")
    try:
        catalog = _ids(raw["catalog_ids"])
        interactions = tuple(
            Interaction(row["user_id"], row["item_id"]) for row in _rows(raw, "train_interactions")
        )
        requests = []
        for row in _rows(raw, "requests"):
            history = _ids(row["history"])
            heard = set(history)
            candidates = tuple(item_id for item_id in catalog if item_id not in heard)
            requests.append(
                RecommendationRequest(row["request_id"], row["user_id"], history, candidates, top_k)
            )
        truth = tuple(
            GroundTruth(row["request_id"], _ids(row["relevant_item_ids"]))
            for row in _rows(raw, "ground_truth")
        )
        return BenchmarkDataset(
            raw["dataset_id"],
            catalog,
            interactions,
            tuple(requests),
            truth,
            hashlib.sha256(content).hexdigest(),
            raw.get("provenance"),
        )
    except KeyError as error:
        raise ValueError(f"missing dataset field: {error.args[0]}") from error
