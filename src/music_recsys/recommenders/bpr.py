"""学習内の既聴/未聴ペアを、一様負例抽出して学習するBPR-MF。"""

import math
from bisect import bisect_right

from music_recsys.recommenders.factors import FactorModel, integer, number


def unseen_at_rank(observed_sorted: tuple[int, ...], n_items: int, rank: int) -> int:
    """ソート済みユニーク観測indexの補集合におけるrank番目。棄却ループは使わない。"""
    if type(rank) is not int or not 0 <= rank < n_items - len(observed_sorted):
        raise ValueError("unseen rank out of range")
    lo, hi = rank, rank + len(observed_sorted)
    while lo < hi:
        mid = (lo + hi) // 2
        if mid + 1 - bisect_right(observed_sorted, mid) <= rank:
            lo = mid + 1
        else:
            hi = mid
    return lo


def bpr_step(user, positive, negative, *, learning_rate: float, regularization: float) -> None:
    u, p, n = user.copy(), positive.copy(), negative.copy()
    margin = float(u @ (p - n))
    gradient = (
        math.exp(-margin) / (1 + math.exp(-margin)) if margin >= 0 else 1 / (1 + math.exp(margin))
    )
    user += learning_rate * (gradient * (p - n) - regularization * u)
    positive += learning_rate * (gradient * u - regularization * p)
    negative += learning_rate * (-gradient * u - regularization * n)


class BPRRecommender(FactorModel):
    def __init__(
        self,
        interactions,
        *,
        factors: int = 32,
        iterations: int = 20,
        learning_rate: float = 0.05,
        regularization: float = 0.01,
        seed: int = 0,
        memory_budget_mb: float = 512,
    ) -> None:
        integer(iterations, "iterations")
        number(learning_rate, "learning_rate", positive=True)
        number(regularization, "regularization")
        super().__init__(
            interactions, factors=factors, seed=seed, memory_budget_mb=memory_budget_mb
        )
        import numpy as np

        observed = [tuple(i for i, _ in row) for row in self.user_rows]
        self.active_users = {u for u, i in self.users.items() if len(observed[i]) < len(self.items)}
        pairs = [
            (ui, ii)
            for u, ui in self.users.items()
            if u in self.active_users
            for ii in observed[ui]
        ]
        self.training_updates = 0
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                for _ in range(iterations):
                    for index in self.rng.permutation(len(pairs)):
                        ui, ii = pairs[index]
                        rank = int(self.rng.integers(len(self.items) - len(observed[ui])))
                        negative = unseen_at_rank(observed[ui], len(self.items), rank)
                        bpr_step(
                            self.user_factors[ui],
                            self.item_factors[ii],
                            self.item_factors[negative],
                            learning_rate=learning_rate,
                            regularization=regularization,
                        )
                        self.training_updates += 1
                if (
                    not np.isfinite(self.user_factors).all()
                    or not np.isfinite(self.item_factors).all()
                ):
                    raise ValueError("non-finite factors")
        except (FloatingPointError, OverflowError):
            raise ValueError("BPR numerical training failure") from None
