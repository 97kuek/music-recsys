"""明示的に分割された検証データを読み込み、IDと履歴の整合性を検証する。"""

import hashlib
import json
from pathlib import Path
from typing import Any

from src.domain.evaluation.dataset import BenchmarkDataset, Interaction
from src.domain.recommendation.contracts import (
    GroundTruth,
    RecommendationRequest,
    validate_ids,
    validate_top_k,
)


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
