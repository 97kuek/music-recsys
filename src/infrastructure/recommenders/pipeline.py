"""Bounded NumPy/CuPy union, RRF and sparse MMR; no future observations enter this module."""

import numpy as np

from src.domain.recommendation.pipeline import FusionSpec
from src.infrastructure.evaluation.features import FeaturePrefixes


class BatchedPipeline:
    def __init__(self, features, items, plays, spec=FusionSpec(), *, xp=np):
        self.xp, self.spec = xp, spec
        self.features = FeaturePrefixes(features, items, xp)
        self.plays = xp.asarray(plays)

    def fuse(self, left, right):
        xp, spec = self.xp, self.spec
        left, right = xp.asarray(left[:, : spec.top_m]), xp.asarray(right[:, : spec.top_m])
        if left.shape != right.shape or left.ndim != 2:
            raise ValueError("Candidate matrices must have identical padded shapes")
        for source in (left, right):
            ordered = xp.sort(source, axis=1)
            if bool(((ordered[:, 1:] == ordered[:, :-1]) & (ordered[:, 1:] >= 0)).any()):
                raise ValueError("Duplicate candidate within one source")
        ids = xp.concatenate((left, right), axis=1)
        if bool(((ids < -1) | (ids >= len(self.plays))).any()):
            raise ValueError("Candidate index outside the learning catalog")
        contributions = xp.concatenate(
            [
                xp.broadcast_to(weight / (spec.c + xp.arange(1, left.shape[1] + 1)), left.shape)
                for weight in spec.weights
            ],
            axis=1,
        )
        order = xp.argsort(xp.where(ids >= 0, ids, len(self.plays)), axis=1, kind="stable")
        ids = xp.take_along_axis(ids, order, axis=1)
        contributions = xp.take_along_axis(contributions, order, axis=1)
        next_same = xp.concatenate(
            (ids[:, :-1] == ids[:, 1:], xp.zeros((len(ids), 1), dtype=bool)), axis=1
        ) & (ids >= 0)
        previous_same = xp.concatenate(
            (xp.zeros((len(ids), 1), dtype=bool), next_same[:, :-1]), axis=1
        )
        scores = contributions + xp.where(
            next_same, xp.concatenate((contributions[:, 1:], xp.zeros((len(ids), 1))), axis=1), 0
        )
        valid = (ids >= 0) & ~previous_same
        scores = xp.where(valid, scores, -xp.inf)
        # Stable sorts preserve item-ID ties, then training plays, then RRF relevance.
        order = xp.argsort(-self.plays[xp.maximum(ids, 0)], axis=1, kind="stable")
        ids, scores = (
            xp.take_along_axis(ids, order, axis=1),
            xp.take_along_axis(scores, order, axis=1),
        )
        order = xp.argsort(-scores, axis=1, kind="stable")
        ids, scores = (
            xp.take_along_axis(ids, order, axis=1),
            xp.take_along_axis(scores, order, axis=1),
        )
        valid = xp.isfinite(scores)
        return xp.where(valid, ids, -1), xp.where(valid, scores, 0.0), valid.sum(axis=1)

    def rerank(self, pool, scores, *, lambda_, k=20):
        xp = self.xp
        if not 0 <= lambda_ <= 1 or type(k) is not int or not 1 <= k <= 20:
            raise ValueError("Invalid MMR lambda or final cutoff")
        width = pool.shape[1]
        result = xp.full((len(pool), k), -1, dtype=xp.int32)
        count = min(k, width)
        if lambda_ == 1:
            result[:, :count] = pool[:, :count]
            return result, {}
        safe = xp.maximum(pool, 0)
        feature_valid = (pool >= 0) & self.features.valid[safe]
        available = feature_valid.copy()
        vectors = self.features.matrix[safe.reshape(-1)]
        maximum = xp.zeros(pool.shape, dtype=xp.float64)
        has_selected = xp.zeros(len(pool), dtype=bool)
        rows = xp.arange(len(pool))
        relevance = (self.spec.c + 1) * scores / sum(self.spec.weights)
        selected_cosine = xp.full((len(pool), k), xp.nan)
        relevance_term = xp.full_like(selected_cosine, xp.nan)
        diversity_term = xp.full_like(selected_cosine, xp.nan)
        for position in range(count):
            active = feature_valid[:, position]
            objective = lambda_ * relevance - (1 - lambda_) * maximum
            objective = xp.where(available, objective, -xp.inf)
            first = xp.argmax(available, axis=1)
            best = objective.max(axis=1)
            tied = available & (objective >= best[:, None] - self.spec.tie_tolerance)
            choice = xp.where(has_selected, xp.argmax(tied, axis=1), first)
            selected = pool[rows, choice]
            result[:, position] = xp.where(active, selected, pool[:, position])
            selected_cosine[:, position] = xp.where(
                active & has_selected, maximum[rows, choice], xp.nan
            )
            relevance_term[:, position] = xp.where(
                active, lambda_ * relevance[rows, choice], xp.nan
            )
            diversity_term[:, position] = xp.where(
                active, (1 - lambda_) * maximum[rows, choice], xp.nan
            )
            available[rows, choice] &= ~active
            repeated = xp.repeat(xp.maximum(selected, 0), width)
            similar = xp.asarray(
                vectors.multiply(self.features.matrix[repeated]).sum(axis=1)
            ).reshape(pool.shape)
            maximum = xp.where(
                active[:, None], xp.maximum(maximum, xp.clip(similar, 0, 1)), maximum
            )
            has_selected |= active
        return result, dict(
            selected_cosine=selected_cosine,
            relevance_term=relevance_term,
            diversity_term=diversity_term,
            feature_valid_count=feature_valid.sum(axis=1),
            pinned_missing=((pool[:, :count] >= 0) & ~feature_valid[:, :count]).sum(axis=1),
        )
