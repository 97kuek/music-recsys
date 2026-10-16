"""Train-catalog TF-IDF content profiles, keeping item tags and histories sparse."""

import time
from collections import Counter

import numpy as np

from music_recsys.recommenders.full_factors import FullFactorModel


def normalize_rows(matrix):
    from scipy.sparse import diags

    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    inverse = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    return (diags(inverse) @ matrix).tocsr()


class FullContent(FullFactorModel):
    def __init__(
        self,
        learning_path,
        *,
        features,
        use_idf=True,
        aggregation="binary",
        use_gpu=False,
        progress=print,
    ):
        from scipy.sparse import csr_matrix

        from music_recsys.features import validate_features
        from music_recsys.model_artifacts import capture_training

        if type(use_idf) is not bool or type(use_gpu) is not bool:
            raise ValueError("use_idf and use_gpu must be boolean")
        if aggregation not in {"binary", "log_count"}:
            raise ValueError("aggregation must be binary or log_count")
        validate_features(features)
        capture_training(self, locals())
        # Features themselves are identified by the experiment's verified features hash.
        self.training_provenance["parameters"].pop("features", None)
        started = time.perf_counter()
        self.history_weights = self.load_learning(learning_path)
        self.history_weights.data = (
            np.ones_like(self.history_weights.data)
            if aggregation == "binary"
            else np.log1p(self.history_weights.data)
        )
        df = Counter(t for item in self.items for t, v in features.get(item, {}).items() if v > 0)
        terms = {t: n for n, t in enumerate(sorted(df))}
        rows, columns, data = [], [], []
        for row, item in enumerate(self.items):
            for term, value in features.get(item, {}).items():
                if value > 0:
                    rows.append(row)
                    columns.append(terms[term])
                    data.append(float(value))
        vectors = csr_matrix((data, (rows, columns)), shape=(len(self.items), len(terms)))
        idf = np.array([np.log((1 + len(self.items)) / (1 + df[t])) + 1 for t in terms])
        self.vectors = normalize_rows(vectors.multiply(idf) if use_idf else vectors)
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False
        self.execution = dict(
            backend="scipy sparse TF-IDF",
            dtype="float64",
            solver="normalized tag cosine",
            profile_aggregation=aggregation,
            tag_dimensions=len(terms),
            dense_user_tag_matrix=False,
        )
        self._gpu = None
        if use_gpu:
            from music_recsys.recommenders.gpu_runtime import require_idle_gpu

            progress(f"GPU preflight: {require_idle_gpu()}")
        progress(
            f"Content: {len(self.items):,} items, {len(terms):,} tags, sparse profiles per batch"
        )

    def rank(self, user, history, task, top_k=20):
        if task not in {"new", "repeat"}:
            raise ValueError("task must be new or repeat")
        profile = normalize_rows(self.history_weights[self.users[user]] @ self.vectors)
        scores = (profile @ self.vectors.T).toarray().ravel()
        allowed = np.zeros(len(self.items), dtype=bool)
        allowed[[self.index[i] for i in history]] = True
        if task == "new":
            allowed = ~allowed
        candidates = np.flatnonzero(allowed)
        order = np.lexsort((candidates, -self.plays[candidates], -scores[candidates]))[:top_k]
        return self.items[candidates[order]].tolist()

    def gpu_score_batch(self, user_ids):
        import cupy as cp
        from cupyx.scipy.sparse import csr_matrix

        if self._gpu is None:
            self._gpu = (csr_matrix(self.history_weights), csr_matrix(self.vectors))
        histories, vectors = self._gpu
        profiles = histories[cp.asarray(user_ids)] @ vectors
        norms = cp.sqrt(cp.asarray(profiles.multiply(profiles).sum(axis=1)).ravel())
        inverse = 1.0 / cp.where(norms > 0, norms, 1.0)
        profiles = profiles.multiply(inverse[:, None]).tocsr()
        return (profiles @ vectors.T).toarray()
