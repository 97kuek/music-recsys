"""Fixed candidate union, reciprocal-rank fusion and feature-aware MMR contracts."""

import math
from dataclasses import dataclass

from src.domain.recommendation.features import unit_vector


@dataclass(frozen=True)
class FusionSpec:
    top_m: int = 100
    c: float = 60.0
    weights: tuple[float, float] = (1.0, 1.0)
    tie_tolerance: float = 1e-12

    def __post_init__(self):
        if not math.isfinite(self.tie_tolerance) or self.tie_tolerance < 0:
            raise ValueError("Tie tolerance must be finite and nonnegative")
        if type(self.top_m) is not int or self.top_m < 1:
            raise ValueError("top_m must be a positive integer")
        if not math.isfinite(self.c) or self.c < 0:
            raise ValueError("RRF c must be finite and nonnegative")
        if (
            len(self.weights) != 2
            or any(not math.isfinite(w) or w < 0 for w in self.weights)
            or sum(self.weights) <= 0
        ):
            raise ValueError("RRF requires two nonnegative weights with a positive sum")


@dataclass(frozen=True)
class Candidate:
    item_id: str
    rank: int
    raw_score: float | None


@dataclass(frozen=True)
class PoolItem:
    item_id: str
    rrf_score: float
    source_ranks: tuple[int | None, ...]
    source_scores: tuple[float | None, ...]


@dataclass(frozen=True)
class HybridCondition:
    name: str
    group: str
    sources: tuple[str, ...]
    seed: int
    mmr_lambda: float = 1.0

    def __post_init__(self):
        if (
            not self.name
            or not self.group
            or len(self.sources) not in (1, 2)
            or len(set(self.sources)) != len(self.sources)
        ):
            raise ValueError("A condition requires a name and one or two distinct sources")
        if not math.isfinite(self.mmr_lambda) or not 0 <= self.mmr_lambda <= 1:
            raise ValueError("MMR lambda must be in [0,1]")
        if len(self.sources) == 1 and self.mmr_lambda != 1:
            raise ValueError("Single-source baselines do not apply MMR")


def rrf_pool(sources, spec, plays):
    """Reference union preserves original ranks and nullable raw scores without truncation."""
    if len(sources) != 2:
        raise ValueError("RRF requires two sources")
    union = {}
    for s, source in enumerate(sources):
        seen = set()
        for rank, candidate in enumerate(source[: spec.top_m], 1):
            if candidate.rank != rank or candidate.item_id in seen:
                raise ValueError("Source ranks must be contiguous and IDs unique")
            if candidate.raw_score is not None and not math.isfinite(candidate.raw_score):
                raise ValueError("Raw scores must be finite or null")
            seen.add(candidate.item_id)
            entry = union.setdefault(candidate.item_id, [0.0, [None, None], [None, None]])
            entry[0] += spec.weights[s] / (spec.c + rank)
            entry[1][s], entry[2][s] = rank, candidate.raw_score
    return [
        PoolItem(item, value, tuple(ranks), tuple(scores))
        for item, (value, ranks, scores) in sorted(
            union.items(), key=lambda pair: (-pair[1][0], -plays[pair[0]], pair[0])
        )
    ]


def mmr_reference(
    pool, features, *, lambda_, c=60.0, weights=(1.0, 1.0), k=20, tie_tolerance=1e-12
):
    """Reference MMR pins feature-missing positions; its first valid choice follows RRF."""
    if (
        not math.isfinite(lambda_)
        or not 0 <= lambda_ <= 1
        or type(k) is not int
        or not 1 <= k <= 20
    ):
        raise ValueError("Invalid MMR lambda or cutoff")
    if lambda_ == 1:
        return list(pool[:k])
    vectors = {row.item_id: unit_vector(features.get(row.item_id, {})) for row in pool}
    available = [i for i, row in enumerate(pool) if vectors[row.item_id]]
    selected, output = [], []
    for baseline in pool[:k]:
        if not vectors[baseline.item_id]:
            output.append(baseline)
            continue

        def objective(i):
            row = pool[i]
            similarity = max(
                (
                    math.fsum(
                        v * vectors[previous].get(t, 0) for t, v in vectors[row.item_id].items()
                    )
                    for previous in selected
                ),
                default=0.0,
            )
            return lambda_ * (c + 1) * row.rrf_score / sum(weights) - (1 - lambda_) * min(
                1.0, max(0.0, similarity)
            )

        maximum = max(map(objective, available)) if selected else None
        choice = (
            available[0]
            if not selected
            else next(i for i in available if objective(i) >= maximum - tie_tolerance)
        )
        available.remove(choice)
        selected.append(pool[choice].item_id)
        output.append(pool[choice])
    return output
