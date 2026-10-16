"""Shared train-only sparse input and exact candidate ranking for full factor models."""

import numpy as np


class FullFactorModel:
    def load_learning(self, learning_path):
        """Load positive counts, preserving sorted user/item indices and popularity ties."""
        import duckdb
        from scipy.sparse import coo_matrix

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
        self.plays = np.asarray(matrix.sum(axis=0)).ravel()
        return matrix

    def rank(self, user, history, task, top_k=20):
        if task not in {"new", "repeat"}:
            raise ValueError("task must be new or repeat")
        allowed = np.zeros(len(self.items), dtype=bool)
        allowed[[self.index[i] for i in history]] = True
        if task == "new":
            allowed = ~allowed
        candidates = np.flatnonzero(allowed)
        scores = self.item_factors[candidates] @ self.user_factors[self.users[user]]
        if 0 < top_k < len(scores):
            cutoff = np.partition(scores, len(scores) - top_k)[len(scores) - top_k]
            selected = np.flatnonzero(scores >= cutoff)
            candidates, scores = candidates[selected], scores[selected]
        order = np.lexsort((candidates, -self.plays[candidates], -scores))[:top_k]
        return self.items[candidates[order]].tolist()
