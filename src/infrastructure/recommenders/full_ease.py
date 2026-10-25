"""EASE: binary train-only X, B=-P/diag(P), diag(B)=0 (Steck, WWW 2019)."""

import time

import numpy as np

from src.infrastructure.recommenders.factors import integer, number
from src.infrastructure.recommenders.full_factors import FullFactorModel


class FullEASE(FullFactorModel):
    def __init__(
        self,
        learning_path,
        *,
        regularization=500.0,
        use_gpu=False,
        block_size=256,
        memory_budget_mb=22528,
        progress=print,
    ):
        from src.infrastructure.models.training import capture_training

        number(regularization, "regularization", positive=True)
        number(memory_budget_mb, "memory_budget_mb", positive=True)
        integer(block_size, "block_size")
        if type(use_gpu) is not bool:
            raise ValueError("use_gpu must be boolean")
        capture_training(self, locals())
        started = time.perf_counter()
        matrix = self.load_learning(learning_path)
        n = len(self.items)
        dtype = np.float32 if use_gpu else np.float64
        sparse_bytes = sum(a.nbytes for a in (matrix.data, matrix.indices, matrix.indptr))
        dense_bytes = n * n * np.dtype(dtype).itemsize
        block_bytes = 32 * n * min(block_size, n)
        if use_gpu:
            # Only device allocations consume VRAM. CSR data is cast to float32;
            # the transpose is freed before the second dense matrix is allocated.
            gpu_sparse_bytes = (
                matrix.nnz * (np.dtype(np.float32).itemsize + matrix.indices.dtype.itemsize)
                + matrix.indptr.nbytes
            )
            self.buffer_estimates = dict(
                gram=dense_bytes + 2 * gpu_sparse_bytes + block_bytes + 1024**3,
                inverse=2 * dense_bytes + gpu_sparse_bytes + block_bytes + 1024**3,
            )
            self.estimated_buffer_bytes = int(max(self.buffer_estimates.values()))
        else:
            self.estimated_buffer_bytes = int(4 * dense_bytes + 2 * sparse_bytes + block_bytes)
        if self.estimated_buffer_bytes > memory_budget_mb * 1024**2:
            raise ValueError("EASE arrays exceed memory_budget_mb before dense allocation")
        if use_gpu:
            self._fit_gpu(matrix, regularization, block_size, memory_budget_mb, progress)
        else:
            self.history_weights = matrix
            self.history_weights.data.fill(1.0)
            gram = (matrix.T @ matrix).toarray()
            gram.flat[:: n + 1] += regularization
            inverse = np.linalg.solve(gram, np.eye(n))
            self.weights = -inverse / np.diag(inverse)[None, :]
            np.fill_diagonal(self.weights, 0.0)
            self.execution = dict(
                backend="numpy/scipy",
                dtype="float64",
                training_device="cpu",
                solver="dense regularized linear solve",
                bitwise_reproducible=False,
            )
        self.execution.update(
            input_transform="binary positive learning user-item pairs",
            reference="https://arxiv.org/abs/1905.03375",
            estimated_buffer_bytes=self.estimated_buffer_bytes,
            **({"device_buffer_estimates": self.buffer_estimates} if use_gpu else {}),
            random_computation=False,
        )
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False

    def _fit_gpu(self, matrix, regularization, block_size, memory_budget_mb, progress):
        import cupy as cp
        from cupy_backends.cuda.libs import cublas, cusolver
        from cupyx.scipy.linalg import solve_triangular
        from cupyx.scipy.sparse import csr_matrix

        from src.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

        progress(f"GPU preflight: {require_idle_gpu()}")
        free, _ = cp.cuda.runtime.memGetInfo()
        if self.estimated_buffer_bytes > free:
            raise ValueError("EASE estimated arrays exceed currently free GPU memory")
        n = len(self.items)
        self.history_weights = csr_matrix(matrix.astype(np.float32))
        self.history_weights.data.fill(1.0)
        transpose = self.history_weights.T.tocsr()
        # Fortran layout permits in-place cuSOLVER and triangular solves without full copies.
        factor = cp.empty((n, n), dtype=cp.float32, order="F")
        handle = cp.cuda.device.get_cusolver_handle()
        size = cusolver.spotrf_bufferSize(
            handle, cublas.CUBLAS_FILL_MODE_LOWER, n, factor.data.ptr, n
        )
        peak = self.estimated_buffer_bytes + size * 4
        if peak > min(free, memory_budget_mb * 1024**2):
            raise ValueError("EASE cuSOLVER workspace exceeds GPU memory budget")
        progress(f"EASE GPU array/workspace estimate: {peak / 1024**3:.2f} GiB; {n:,} items")
        for start in range(0, n, block_size):
            stop = min(start + block_size, n)
            factor[start:stop] = (transpose[start:stop] @ self.history_weights).toarray()
            if start % (block_size * 16) == 0 or stop == n:
                cp.cuda.Stream.null.synchronize()
                progress(f"EASE GPU Gram {stop:,}/{n:,}")
        del transpose
        cp.get_default_memory_pool().free_all_blocks()
        factor.diagonal()[:] += regularization
        workspace = cp.empty(size, dtype=cp.float32)
        info = cp.empty(1, dtype=cp.int32)
        cusolver.spotrf(
            handle,
            cublas.CUBLAS_FILL_MODE_LOWER,
            n,
            factor.data.ptr,
            n,
            workspace.data.ptr,
            size,
            info.data.ptr,
        )
        status = int(info.item())
        if status:
            raise ValueError(f"EASE Cholesky failed: cuSOLVER info={status}")
        del workspace, info
        cp.get_default_memory_pool().free_all_blocks()
        progress("EASE GPU Cholesky complete; block inverse solves")
        self.weights = cp.empty((n, n), dtype=cp.float32, order="F")
        inverse_diagonal = cp.empty(n, dtype=cp.float32)
        for start in range(0, n, block_size):
            stop = min(start + block_size, n)
            columns = cp.arange(start, stop)
            rhs = cp.zeros((n, stop - start), dtype=cp.float32, order="F")
            rhs[columns, cp.arange(stop - start)] = 1
            rhs = solve_triangular(factor, rhs, lower=True, overwrite_b=True)
            rhs = solve_triangular(factor, rhs, lower=True, trans="T", overwrite_b=True)
            diagonal = rhs[columns, cp.arange(stop - start)].copy()
            if not bool(cp.all(cp.isfinite(rhs))) or not bool(cp.all(diagonal > 0)):
                raise ValueError("EASE inverse has nonfinite values or nonpositive diagonal")
            inverse_diagonal[start:stop] = diagonal
            rhs /= -diagonal[None, :]
            rhs[columns, cp.arange(stop - start)] = 0
            self.weights[:, start:stop] = rhs
            if start % (block_size * 16) == 0 or stop == n:
                cp.cuda.Stream.null.synchronize()
                progress(f"EASE GPU coefficients {stop:,}/{n:,}")
        del factor, rhs
        cp.get_default_memory_pool().free_all_blocks()
        # Reconstruct four inverse columns and verify A P=I using sparse GPU products.
        sample = cp.asarray(np.unique(np.linspace(0, n - 1, min(4, n), dtype=int)))
        inverse = -self.weights[:, sample] * inverse_diagonal[sample][None, :]
        inverse[sample, cp.arange(len(sample))] = inverse_diagonal[sample]
        residual = self.history_weights.T @ (self.history_weights @ inverse)
        residual += regularization * inverse
        residual[sample, cp.arange(len(sample))] -= 1
        relative = float(cp.max(cp.linalg.norm(residual, axis=0)).item())
        if not np.isfinite(relative) or relative > 0.05:
            raise ValueError(f"EASE inverse residual too large: {relative}")
        cp.cuda.Stream.null.synchronize()
        free_after, total = cp.cuda.runtime.memGetInfo()
        self.execution = dict(
            backend="cupy/cupyx CUDA",
            dtype="float32",
            training_device="cuda",
            solver="in-place cuSOLVER Cholesky; cuBLAS triangular block solves",
            block_size=block_size,
            planned_peak_bytes=peak,
            gpu_allocated_after_fit_bytes=total - free_after,
            inverse_sample_max_relative_residual=relative,
            inverse_sample_columns=cp.asnumpy(sample).tolist(),
            inverse_diagonal_min=float(inverse_diagonal.min().item()),
            inverse_diagonal_max=float(inverse_diagonal.max().item()),
            bitwise_reproducible=False,
            cpu_gpu_difference="float64 dense solve vs float32 Cholesky",
            **gpu_metadata(),
        )
        progress(f"EASE GPU inverse residual={relative:.6g}")

    def gpu_score_batch(self, user_ids):
        import cupy as cp

        return self.history_weights[cp.asarray(user_ids)] @ self.weights

    def rank(self, user, history, task, top_k=20):
        if task not in {"new", "repeat"}:
            raise ValueError("task must be new or repeat")
        gpu = self.execution["training_device"] == "cuda"
        if gpu:
            import cupy as cp

            xp = cp
            scores = self.gpu_score_batch([self.users[user]]).ravel()
        else:
            xp = np
            scores = np.asarray(self.history_weights[self.users[user]] @ self.weights).ravel()
        allowed = xp.zeros(len(self.items), dtype=bool)
        allowed[xp.asarray([self.index[i] for i in history], dtype=xp.int32)] = True
        candidates = xp.flatnonzero(~allowed if task == "new" else allowed)
        plays = xp.asarray(self.plays)
        order = xp.lexsort(xp.stack((candidates, -plays[candidates], -scores[candidates])))[:top_k]
        selected = candidates[order]
        if gpu:
            selected = cp.asnumpy(selected)
        return self.items[selected].tolist()
