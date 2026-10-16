"""集約Parquetの疎行列によるImplicit ALS / 学習履歴だけを入力する。"""

import time

import numpy as np

from music_recsys.recommenders.als import als_solve
from music_recsys.recommenders.factors import integer, number


class FullALS:
    def __init__(
        self,
        learning_path,
        *,
        factors=32,
        iterations=20,
        regularization=0.1,
        alpha=10.0,
        confidence_transform="log1p",
        seed=0,
        memory_budget_mb=512,
        progress=print,
    ):
        import duckdb
        from scipy.sparse import coo_matrix

        integer(factors, "factors")
        integer(iterations, "iterations")
        number(regularization, "regularization", positive=True)
        number(alpha, "alpha")
        number(memory_budget_mb, "memory_budget_mb", positive=True)
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be non-negative integer")
        if confidence_transform not in {"linear", "log1p"}:
            raise ValueError("confidence_transform must be linear or log1p")
        started = time.perf_counter()
        with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as c:
            data = c.execute(
                "SELECT user_id,item_id,play_count FROM read_parquet(?) ORDER BY user_id,item_id",
                [str(learning_path)],
            ).fetchnumpy()
        users, ui = np.unique(data["user_id"], return_inverse=True)
        self.items, ii = np.unique(data["item_id"], return_inverse=True)
        counts = np.asarray(data["play_count"], dtype=float)
        if not len(counts) or not np.isfinite(counts).all() or (counts <= 0).any():
            raise ValueError("learning counts must be finite and positive")
        self.users = {u: i for i, u in enumerate(users)}
        self.index = {item: i for i, item in enumerate(self.items)}
        matrix = coo_matrix((counts, (ui, ii)), shape=(len(users), len(self.items))).tocsr()
        matrix.sum_duplicates()
        matrix.sort_indices()
        transpose = matrix.T.tocsr()
        self.plays = np.asarray(matrix.sum(axis=0)).ravel()
        degree = int(max(np.diff(matrix.indptr).max(), np.diff(transpose.indptr).max()))
        self.estimated_buffer_bytes = int(
            8
            * (2 * (len(users) + len(self.items)) * factors + 3 * factors**2 + 2 * degree * factors)
            + sum(a.nbytes for m in (matrix, transpose) for a in (m.data, m.indices, m.indptr))
        )
        if self.estimated_buffer_bytes > memory_budget_mb * 1024**2:
            raise ValueError(
                "factor and sparse arrays exceed memory_budget_mb before factor allocation"
            )
        del data, ui, ii, counts
        rng = np.random.default_rng(seed)
        self.user_factors = rng.normal(0, 0.01, (len(users), factors))
        self.item_factors = rng.normal(0, 0.01, (len(self.items), factors))
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                for iteration in range(iterations):
                    for target, other, rows in (
                        (self.user_factors, self.item_factors, matrix),
                        (self.item_factors, self.user_factors, transpose),
                    ):
                        gram = other.T @ other
                        for index in range(rows.shape[0]):
                            a, b = rows.indptr[index : index + 2]
                            observed = list(zip(rows.indices[a:b], rows.data[a:b], strict=True))
                            target[index] = als_solve(
                                other,
                                observed,
                                alpha=alpha,
                                regularization=regularization,
                                gram=gram,
                                confidence_transform=confidence_transform,
                            )
                    progress(f"ALS iteration {iteration + 1}/{iterations}")
                if not all(np.isfinite(a).all() for a in (self.user_factors, self.item_factors)):
                    raise ValueError("non-finite factors")
        except (FloatingPointError, np.linalg.LinAlgError):
            raise ValueError("ALS numerical training failure") from None
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False

    def rank(self, user, history, task, top_k=20):
        if task not in {"new", "repeat"}:
            raise ValueError("task must be new or repeat")
        allowed = np.zeros(len(self.items), dtype=bool)
        allowed[[self.index[i] for i in history]] = True
        if task == "new":
            allowed = ~allowed
        candidates = np.flatnonzero(allowed)
        scores = self.item_factors[candidates] @ self.user_factors[self.users[user]]
        order = np.lexsort((candidates, -self.plays[candidates], -scores))[:top_k]
        return self.items[candidates[order]].tolist()
