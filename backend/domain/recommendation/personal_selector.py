"""A frozen one-split policy using only training-history aggregates."""

from dataclasses import dataclass
from math import isfinite

from backend.domain.recommendation.local_selector import TARGETS
from backend.domain.recommendation.selection_context import SelectionContext

FEATURES = (
    "history_count",
    "candidate_count",
    "total_plays",
    "item_active_days",
    "top_track_play_share",
    "feature_fraction",
)


@dataclass(frozen=True)
class PersonalPolicy:
    feature: str | None
    threshold: float | None
    left: str
    right: str

    def __post_init__(self):
        if self.left not in TARGETS or self.right not in TARGETS:
            raise ValueError("Unknown target")
        if self.feature is None:
            if self.threshold is not None or self.left != self.right:
                raise ValueError("Constant policy requires identical targets")
        elif self.feature not in FEATURES or self.threshold is None or not isfinite(self.threshold):
            raise ValueError("Invalid split")

    def choose(self, context: SelectionContext) -> str:
        return (
            self.left
            if self.feature is None or getattr(context, self.feature) <= self.threshold
            else self.right
        )
