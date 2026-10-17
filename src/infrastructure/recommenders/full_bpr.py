"""Train-only Parquet BPR-MF using implicit CPU or GPU SGD."""

import time

import numpy as np

from src.infrastructure.recommenders.factors import integer, number
from src.infrastructure.recommenders.full_factors import FullFactorModel


class FullBPR(FullFactorModel):
    def __init__(
        self,
        learning_path,
        *,
        factors=32,
        iterations=30,
        learning_rate=0.01,
        regularization=0.01,
        seed=0,
        memory_budget_mb=4096,
        progress=print,
        use_gpu=False,
    ):
        import implicit
        from implicit.bpr import BayesianPersonalizedRanking

        if type(use_gpu) is not bool:
            raise ValueError("use_gpu must be boolean")
        integer(factors, "factors")
        integer(iterations, "iterations")
        number(learning_rate, "learning_rate", positive=True)
        number(regularization, "regularization")
        number(memory_budget_mb, "memory_budget_mb", positive=True)
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be non-negative integer")
        from src.infrastructure.models.model_artifacts import capture_training

        capture_training(self, locals())
        started = time.perf_counter()
        matrix = self.load_learning(learning_path)
        self.active_users = np.diff(matrix.indptr) < len(self.items)
        self.estimated_buffer_bytes = int(
            8 * (len(self.users) + len(self.items)) * (factors + 1)
            + 4 * sum(a.nbytes for a in (matrix.data, matrix.indices, matrix.indptr))
        )
        if self.estimated_buffer_bytes > memory_budget_mb * 1024**2:
            raise ValueError("factor and sparse arrays exceed memory_budget_mb")
        matrix.data[:] = 1
        if use_gpu:
            from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu

            progress(f"GPU preflight: {require_idle_gpu()}")
        model = BayesianPersonalizedRanking(
            use_gpu=use_gpu,
            factors=factors,
            iterations=iterations,
            learning_rate=learning_rate,
            regularization=regularization,
            random_state=seed,
            num_threads=1,
            verify_negative_samples=True,
            dtype=np.float32,
        )
        model.fit(
            matrix,
            show_progress=False,
            callback=lambda epoch, elapsed, correct, skipped: progress(
                f"BPR epoch {epoch + 1}/{iterations}; seconds={elapsed:.3f}; skipped={skipped}"
            ),
        )
        if use_gpu:
            model = model.to_cpu()
        self.user_factors = model.user_factors
        self.item_factors = model.item_factors
        if not all(np.isfinite(a).all() for a in (self.user_factors, self.item_factors)):
            raise ValueError("BPR numerical training failure")
        self.execution = dict(
            solver=f"implicit {'GPU' if use_gpu else 'CPU'} BPR SGD float32 with item bias",
            training_device="cuda" if use_gpu else "cpu",
            evaluation_device="cpu",
            bitwise_reproducible=not use_gpu,
            implicit=implicit.__version__,
            num_threads=None if use_gpu else 1,
            verify_negative_samples=True,
            sampling="uniform positive CSR entries with replacement; negative from CSR item entries; observed negatives skipped",
            epochs=iterations,
        )
        if use_gpu:
            from src.infrastructure.recommenders.gpu_runtime import gpu_metadata

            self.execution.update(gpu_metadata())
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False

    def rank(self, user, history, task, top_k=20):
        if not self.active_users[self.users[user]]:
            if task not in {"new", "repeat"}:
                raise ValueError("task must be new or repeat")
            return (
                []
                if task == "new"
                else self.items[
                    np.lexsort((np.arange(len(self.items)), -self.plays))[:top_k]
                ].tolist()
            )
        return super().rank(user, history, task, top_k)
