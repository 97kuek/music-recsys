"""因子モデルの疎な学習index・候補・補完・メモリ予算。"""

import math
from collections import Counter
from collections.abc import Iterable

from music_recsys.candidates import eligible_candidates
from music_recsys.data import Interaction
from music_recsys.types import RecommendationRequest, RecommendationResponse


def integer(value, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def number(value, name: str, *, positive: bool = False) -> None:
    try:
        valid = (
            type(value) in (int, float)
            and math.isfinite(value)
            and (value > 0 if positive else value >= 0)
        )
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'non-negative'}")


class FactorModel:
    def __init__(
        self,
        interactions: Iterable[Interaction],
        *,
        factors: int,
        seed: int,
        memory_budget_mb: float,
    ) -> None:
        integer(factors, "factors")
        number(memory_budget_mb, "memory_budget_mb", positive=True)
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be non-negative integer")
        pairs = Counter((e.user_id, e.item_id) for e in interactions)
        self.users = {u: i for i, u in enumerate(sorted({u for u, _ in pairs}))}
        self.items = {item: i for i, item in enumerate(sorted({item for _, item in pairs}))}
        self._counts = Counter()
        self.user_rows = [[] for _ in self.users]
        self.item_rows = [[] for _ in self.items]
        for (u, item), count in sorted(pairs.items()):
            ui, ii = self.users[u], self.items[item]
            self.user_rows[ui].append((ii, count))
            self.item_rows[ii].append((ui, count))
            self._counts[item] += count
        degree = max((len(row) for row in self.user_rows + self.item_rows), default=0)
        # Dense factors, gathered rows and solve scratch only; not a claim about total process RSS.
        self.estimated_buffer_bytes = 8 * (
            2 * (len(self.users) + len(self.items)) * factors
            + 3 * factors * factors
            + 2 * degree * factors
        )
        if self.estimated_buffer_bytes > memory_budget_mb * 1024**2:
            raise ValueError("factor arrays exceed memory_budget_mb before allocation")
        try:
            import numpy as np
        except ImportError:
            raise ValueError("install models dependency group: uv sync --group models") from None
        self.rng = np.random.default_rng(seed)
        self.user_factors = self.rng.normal(0, 0.01, size=(len(self.users), factors))
        self.item_factors = self.rng.normal(0, 0.01, size=(len(self.items), factors))
        self.active_users = set(self.users)
        self.active_items = set(self.items)

    def score(self, user_id: str, item_id: str) -> float | None:
        if user_id not in self.active_users or item_id not in self.active_items:
            return None
        score = float(
            self.user_factors[self.users[user_id]] @ self.item_factors[self.items[item_id]]
        )
        if not math.isfinite(score):
            raise ValueError("non-finite factor score")
        return score

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        candidates = eligible_candidates(request)
        scores = {item: self.score(request.user_id, item) for item in candidates}
        ranked = sorted(
            candidates,
            key=lambda item: (
                scores[item] is None,
                -(scores[item] or 0),
                -self._counts[item],
                item,
            ),
        )
        return RecommendationResponse(request.request_id, tuple(ranked[: request.top_k]))

    def explain(self, request: RecommendationRequest, item_id: str) -> dict:
        if item_id not in eligible_candidates(request):
            raise ValueError("explanation item must be eligible")
        score = self.score(request.user_id, item_id)
        return {
            "score": score,
            "contributors": [],
            "fallback": "popularity" if score is None else None,
            "reason": "学習したユーザー因子と曲因子の内積。音楽的な因果理由を示す値ではありません。"
            if score is not None
            else f"学習可能な因子なし。学習再生{self._counts[item_id]}回によるPopularity補完。",
        }
