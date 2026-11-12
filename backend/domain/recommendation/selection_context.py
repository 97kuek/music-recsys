"""Train-only aggregate input shared by rule and language-model selectors."""

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SelectionContext:
    task: str
    history_count: int
    candidate_count: int
    total_plays: int
    item_active_days: int
    top_track_play_share: float
    feature_fraction: float

    def __post_init__(self):
        if self.task not in {"new", "repeat"}:
            raise ValueError("Unknown selection task")
        for name in ("history_count", "candidate_count", "total_plays", "item_active_days"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError("Selection counts must be nonnegative integers")
        if (
            not self.history_count
            or min(self.total_plays, self.item_active_days) < self.history_count
        ):
            raise ValueError("Warm selection context requires positive learning observations")
        for name in ("top_track_play_share", "feature_fraction"):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Selection fractions must be finite and within [0,1]")

    def payload(self):
        """No IDs, titles, raw histories, future truths or evaluation metrics."""
        return asdict(self)
