import math
import os

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from backend.infrastructure.evaluation.sparse import row_sum


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
            ),
        ),
    ],
)
def test_sparse_row_sum_matches_independent_sums_across_batches(gpu):
    rng = np.random.default_rng(391)
    sizes = rng.integers(0, 128, 12800)
    offsets = np.r_[0, np.cumsum(sizes)]
    data = np.exp(rng.uniform(-30, 0, offsets[-1]))
    columns = np.concatenate([np.sort(rng.choice(256, n, replace=False)) for n in sizes])
    matrix = csr_matrix((data, columns, offsets), shape=(len(sizes), 256))
    expected = np.array([math.fsum(data[a:b]) for a, b in zip(offsets[:-1], offsets[1:])])
    if gpu:
        import cupy as xp
        from cupyx.scipy.sparse import csr_matrix as gpu_csr

        matrix = gpu_csr(matrix)
        host = xp.asnumpy
    else:
        xp, host = np, np.asarray
    actual = host(row_sum(matrix, xp))
    np.testing.assert_allclose(actual, expected, atol=1e-14, rtol=1e-14)
    for start, end in ((0, 1), (123, 187), (512, 512), (11111, 12800)):
        sliced = host(row_sum(matrix[start:end], xp))
        np.testing.assert_array_equal(sliced, actual[start:end])
    assert np.isfinite(actual).all()
    assert (actual[sizes == 0] == 0).all()
