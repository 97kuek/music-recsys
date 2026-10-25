"""BM25 item-neighbor weighting with bounded sparse co-occurrence blocks."""

import time

import numpy as np

from src.infrastructure.recommenders.full_factors import FullFactorModel
from src.infrastructure.recommenders.full_itemcf import FullItemCF


class FullBM25(FullFactorModel):
    def __init__(
        self, learning_path, *, neighbors=20, k1=1.2, b=0.75, use_gpu=False, progress=print
    ):
        from implicit.nearest_neighbours import bm25_weight
        from scipy.sparse import csr_matrix

        from src.infrastructure.models.training import capture_training
        from src.infrastructure.recommenders.factors import integer, number

        integer(neighbors, "neighbors")
        number(k1, "k1", positive=True)
        number(b, "b")
        if b > 1 or type(use_gpu) is not bool:
            raise ValueError("b must be within [0,1] and use_gpu must be boolean")
        capture_training(self, locals())
        started = time.perf_counter()
        matrix = self.load_learning(learning_path)
        self.histories = matrix.copy()
        self.histories.data[:] = 1
        # Same item-by-user orientation and BM25 transformation as implicit 0.7.2.
        weighted = bm25_weight(matrix.T, K1=k1, B=b).tocsr()
        xp = np
        if use_gpu:
            from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu

            progress(f"GPU preflight: {require_idle_gpu()}")
            import cupy as xp
            from cupyx.scipy.sparse import csr_matrix as gpu_csr

            weighted = gpu_csr(weighted)
        width = min(neighbors, max(0, len(self.items) - 1))
        self.targets = np.zeros((len(self.items), width), dtype=np.int32)
        self.weights = np.zeros((len(self.items), width), dtype=np.float64)
        transpose = weighted.T.tocsr()
        for start in range(0, len(self.items), 128):
            stop = min(start + 128, len(self.items))
            block = (weighted[start:stop] @ transpose).toarray()
            block[xp.arange(stop - start), xp.arange(start, stop)] = -xp.inf
            selected = xp.argsort(-block, axis=1, kind="stable")[:, :width]
            values = xp.take_along_axis(block, selected, axis=1)
            if use_gpu:
                selected, values = xp.asnumpy(selected), xp.asnumpy(values)
            self.targets[start:stop], self.weights[start:stop] = selected, values
            if start % 1024 == 0 or stop == len(self.items):
                progress(
                    f"BM25 neighbors {stop:,}/{len(self.items):,}; {time.perf_counter() - started:.1f}s"
                )
        self.neighbors = neighbors
        self.neighbor_matrix = csr_matrix(
            (self.weights.ravel(), self.targets.ravel(), np.arange(len(self.items) + 1) * width),
            shape=(len(self.items), len(self.items)),
        )
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False
        self._gpu = None
        self.execution = dict(
            backend="cupyx sparse BM25" if use_gpu else "scipy sparse BM25",
            dtype="float64",
            solver="BM25 weighted item dot product, top neighbors",
            block_size=128,
            training_values="play_count",
            inference_values="binary history",
        )
        if use_gpu:
            from src.infrastructure.recommenders.gpu_runtime import gpu_metadata

            self.execution.update(
                gpu_metadata(),
                training_device="cpu weighting / cuda neighbor products",
                scoring_device="cuda",
                random_computation=False,
                bitwise_reproducible=False,
            )

    def rank(self, user, history, task, top_k=20):
        return FullItemCF.rank(self, history, task, top_k)

    def gpu_score_batch(self, user_ids):
        import cupy as cp
        from cupyx.scipy.sparse import csr_matrix

        if self._gpu is None:
            self._gpu = (csr_matrix(self.histories), csr_matrix(self.neighbor_matrix))
        histories, neighbors = self._gpu
        return (histories[cp.asarray(user_ids)] @ neighbors).toarray()
