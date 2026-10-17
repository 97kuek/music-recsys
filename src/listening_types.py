"""listening-v2の観測事実・評価設定。gainは保存せずEvaluatorで計算する。"""

import math
from dataclasses import dataclass
from pathlib import Path

from src.data import Interaction
from src.types import RecommendationRequest, validate_id


def positive_integer(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def day_weight(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("day weight must be finite and non-negative")


@dataclass(frozen=True)
class ListeningConfig:
    window_days: tuple[int, ...] = (30, 90, 180)
    primary_window_days: int = 90
    ks: tuple[int, ...] = tuple(range(2, 21))
    primary_k: int = 10
    day_weights: tuple[float, ...] = (1.0,)
    primary_day_weight: float = 1.0

    def __post_init__(self):
        for name in ("window_days", "ks", "day_weights"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
                raise ValueError(f"{name} must be a nonempty unique tuple")
        for value in self.window_days:
            positive_integer(value, "window_days")
        for k in self.ks:
            positive_integer(k, "K")
            if k > 20:
                raise ValueError("K must not exceed 20")
        for value in self.day_weights:
            day_weight(value)
        positive_integer(self.primary_window_days, "primary_window_days")
        positive_integer(self.primary_k, "primary_k")
        day_weight(self.primary_day_weight)
        if (
            self.primary_window_days not in self.window_days
            or self.primary_k not in self.ks
            or self.primary_day_weight not in self.day_weights
        ):
            raise ValueError("primary conditions must be included in evaluation conditions")


@dataclass(frozen=True)
class PlaybackObservation:
    user_id: str
    item_id: str
    window_days: int
    learning_play_count: int
    learning_active_days: int
    evaluation_play_count: int
    evaluation_active_days: int

    def __post_init__(self):
        validate_id(self.user_id)
        validate_id(self.item_id)
        positive_integer(self.window_days, "window_days")
        for plays, days in (
            (self.learning_play_count, self.learning_active_days),
            (self.evaluation_play_count, self.evaluation_active_days),
        ):
            if (
                type(plays) is not int
                or type(days) is not int
                or not 0 <= days <= plays
                or (plays == 0) != (days == 0)
            ):
                raise ValueError("invalid play count / active days")
        if self.evaluation_play_count <= 0:
            raise ValueError("observation must have positive evaluation plays")


@dataclass(frozen=True)
class ListeningDataset:
    dataset_id: str
    catalog_ids: tuple[str, ...]
    train_interactions: tuple[Interaction, ...]
    requests: tuple[RecommendationRequest, ...]
    observations_path: Path
    manifest: dict
    sha256: str

    @property
    def provenance(self):
        phase = self.manifest["phase"]
        return {
            **self.manifest.get("source", {}),
            "phase": phase,
            "training_periods": ["train"] if phase == "validation" else ["train", "validation"],
        }


@dataclass(frozen=True)
class ListeningScore:
    binary_ndcg: float | None
    graded_ndcg: float | None
    recall: float | None
    relevant_count: int
    returned_count: int
    valid_count: int
    invalid_count: int
    duplicate_count: int
    missing_count: int
    overflow_count: int
