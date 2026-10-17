"""集約Parquetの疎行列によるImplicit ALS / 学習履歴だけを入力する。"""

import time

import numpy as np

from src.infrastructure.recommenders.als import als_solve
from src.infrastructure.recommenders.factors import integer, number
from src.infrastructure.recommenders.full_factors import FullFactorModel


class FullALS(FullFactorModel):
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
        use_gpu=False,
        cg_steps=20,
    ):
        if type(use_gpu) is not bool:
            raise ValueError("use_gpu must be boolean")
        integer(cg_steps, "cg_steps")
        integer(factors, "factors")
        integer(iterations, "iterations")
        number(regularization, "regularization", positive=True)
        number(alpha, "alpha")
        number(memory_budget_mb, "memory_budget_mb", positive=True)
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be non-negative integer")
        if confidence_transform not in {"linear", "log1p"}:
            raise ValueError("confidence_transform must be linear or log1p")
        from src.infrastructure.models.model_artifacts import capture_training

        capture_training(self, locals())
        started = time.perf_counter()
        matrix = self.load_learning(learning_path)
        transpose = matrix.T.tocsr()
        degree = int(max(np.diff(matrix.indptr).max(), np.diff(transpose.indptr).max()))
        self.estimated_buffer_bytes = int(
            8
            * (
                2 * (len(self.users) + len(self.items)) * factors
                + 3 * factors**2
                + 2 * degree * factors
            )
            + sum(a.nbytes for m in (matrix, transpose) for a in (m.data, m.indices, m.indptr))
        )
        if self.estimated_buffer_bytes > memory_budget_mb * 1024**2:
            raise ValueError(
                "factor and sparse arrays exceed memory_budget_mb before factor allocation"
            )
        rng = np.random.default_rng(seed)
        self.user_factors = rng.normal(0, 0.01, (len(self.users), factors))
        self.item_factors = rng.normal(0, 0.01, (len(self.items), factors))
        if use_gpu:
            self._fit_gpu(
                matrix,
                factors=factors,
                iterations=iterations,
                regularization=regularization,
                alpha=alpha,
                confidence_transform=confidence_transform,
                seed=seed,
                cg_steps=cg_steps,
                progress=progress,
            )
        else:
            self._fit_cpu(
                matrix,
                transpose,
                iterations=iterations,
                regularization=regularization,
                alpha=alpha,
                confidence_transform=confidence_transform,
                progress=progress,
            )
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False

    def _fit_gpu(
        self,
        matrix,
        *,
        factors,
        iterations,
        regularization,
        alpha,
        confidence_transform,
        seed,
        cg_steps,
        progress,
    ):
        import implicit
        import implicit.gpu
        from implicit.gpu.als import AlternatingLeastSquares

        from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu

        progress(f"GPU preflight: {require_idle_gpu()}")
        confidence = matrix.astype(np.float32)
        confidence.data = 1 + alpha * (
            np.log1p(confidence.data) if confidence_transform == "log1p" else confidence.data
        )
        model = AlternatingLeastSquares(
            factors=factors,
            iterations=iterations,
            regularization=regularization,
            alpha=1.0,
            random_state=seed,
        )
        model.cg_steps = cg_steps
        model.user_factors = implicit.gpu.Matrix(self.user_factors.astype(np.float32))
        model.item_factors = implicit.gpu.Matrix(self.item_factors.astype(np.float32))
        model.fit(
            confidence,
            show_progress=False,
            callback=lambda iteration, elapsed, loss: progress(
                f"ALS GPU iteration {iteration + 1}/{iterations}; seconds={elapsed:.3f}"
            ),
        )
        self.user_factors = model.user_factors.to_numpy()
        self.item_factors = model.item_factors.to_numpy()
        if not all(np.isfinite(a).all() for a in (self.user_factors, self.item_factors)):
            raise ValueError("ALS numerical training failure")
        from src.infrastructure.recommenders.gpu_runtime import gpu_metadata

        self.execution = dict(
            solver="implicit GPU conjugate gradient float32",
            cg_steps=cg_steps,
            implicit=implicit.__version__,
            training_device="cuda",
            evaluation_device="cpu",
            bitwise_reproducible=False,
            **gpu_metadata(),
        )

    def _fit_cpu(
        self,
        matrix,
        transpose,
        *,
        iterations,
        regularization,
        alpha,
        confidence_transform,
        progress,
    ):
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
