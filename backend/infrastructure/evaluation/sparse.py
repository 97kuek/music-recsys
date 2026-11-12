"""Row-local FP64 reductions, independent of cuSPARSE matvec dispatch and batching."""

from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def _row_sum_kernel():
    import cupy as cp

    return cp.ElementwiseKernel(
        "raw T data, raw I indptr",
        "float64 total",
        """
        double value = 0.0;
        double correction = 0.0;
        for (I j = indptr[i]; j < indptr[i + 1]; ++j) {
            double term = (double)data[j] - correction;
            double next = value + term;
            correction = (next - value) - term;
            value = next;
        }
        total = value;
        """,
        "music_sparse_row_sum_fp64",
    )


def row_sum(matrix, xp=np):
    """Return one sum per CSR row, including zero for empty rows."""
    matrix = matrix.tocsr()
    if xp is np:
        return np.asarray(matrix.sum(axis=1)).reshape(-1)
    result = xp.empty(matrix.shape[0], dtype=xp.float64)
    if matrix.shape[0]:
        _row_sum_kernel()(matrix.data, matrix.indptr, result)
    return result
