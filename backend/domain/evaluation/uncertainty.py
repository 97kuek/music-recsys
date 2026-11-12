"""User-cluster uncertainty for saved request-level metrics; no I/O or model access."""

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, stdev


@dataclass(frozen=True)
class BootstrapConfig:
    confidence: float = 0.95
    resamples: int = 2000
    seed: int = 42

    def __post_init__(self):
        if not 0 < self.confidence < 1:
            raise ValueError("confidence must be between 0 and 1")
        if type(self.resamples) is not int or self.resamples < 100:
            raise ValueError("resamples must be an integer >= 100")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")


def quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def cluster_statistics(
    values: Sequence[tuple[str, float]], config: BootstrapConfig, *, compute_ci: bool = True
) -> dict:
    """要求平均を維持し、ユーザーの全要求をまとめて再標本化する。"""
    groups = defaultdict(list)
    for user, value in values:
        if not math.isfinite(value):
            raise ValueError("non-finite metric value")
        groups[user].append(value)
    clusters = [(sum(groups[user]), len(groups[user])) for user in sorted(groups)]
    means = [total / count for total, count in clusters]
    result = {
        "mean": fmean(value for _, value in values) if values else None,
        "n_evaluated": len(values),
        "n_users": len(clusters),
        "user_std": stdev(means) if len(means) > 1 else None,
        "user_q25": quantile(means, 0.25),
        "user_median": quantile(means, 0.5),
        "user_q75": quantile(means, 0.75),
        "ci_low": None,
        "ci_high": None,
        "confidence": config.confidence,
        "resamples": config.resamples,
        "bootstrap_seed": config.seed,
        "ci_method": "user_cluster_percentile",
    }
    if not compute_ci:
        result["ci_method"] = "not_requested"
    if len(clusters) >= 2 and compute_ci:
        rng = random.Random(config.seed)
        sampled = []
        for _ in range(config.resamples):
            draw = rng.choices(clusters, k=len(clusters))
            sampled.append(sum(total for total, _ in draw) / sum(count for _, count in draw))
        tail = (1 - config.confidence) / 2
        result.update(ci_low=quantile(sampled, tail), ci_high=quantile(sampled, 1 - tail))
    return result
