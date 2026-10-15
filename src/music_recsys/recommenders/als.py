"""暗黙の選好と再生回数の信頼度による正則化ALS。"""

from music_recsys.recommenders.factors import FactorModel, integer, number


def als_solve(other_factors, observed, *, alpha: float, regularization: float, gram=None):
    import numpy as np

    n = other_factors.shape[1]
    indices = [i for i, _ in observed]
    rows = other_factors[indices]
    confidence = 1 + alpha * np.array([count for _, count in observed], dtype=float)
    a = (other_factors.T @ other_factors if gram is None else gram) + regularization * np.eye(n)
    a = a + (rows.T * (confidence - 1)) @ rows
    return np.linalg.solve(a, rows.T @ confidence)


class ALSRecommender(FactorModel):
    def __init__(
        self,
        interactions,
        *,
        factors: int = 32,
        iterations: int = 10,
        alpha: float = 10.0,
        regularization: float = 0.1,
        seed: int = 0,
        memory_budget_mb: float = 512,
    ) -> None:
        integer(iterations, "iterations")
        number(alpha, "alpha")
        number(regularization, "regularization", positive=True)
        super().__init__(
            interactions, factors=factors, seed=seed, memory_budget_mb=memory_budget_mb
        )
        import numpy as np

        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                for _ in range(iterations):
                    for target, other, rows in (
                        (self.user_factors, self.item_factors, self.user_rows),
                        (self.item_factors, self.user_factors, self.item_rows),
                    ):
                        gram = other.T @ other
                        for index, observed in enumerate(rows):
                            target[index] = als_solve(
                                other,
                                observed,
                                alpha=alpha,
                                regularization=regularization,
                                gram=gram,
                            )
                if (
                    not np.isfinite(self.user_factors).all()
                    or not np.isfinite(self.item_factors).all()
                ):
                    raise ValueError("non-finite factors")
        except (FloatingPointError, np.linalg.LinAlgError):
            raise ValueError("ALS numerical training failure") from None
