import numpy as np

from backend.infrastructure.research.history_diagnostics import paired_summary


def test_paired_diagnostic_counts_preserve_harmed_and_unchanged_users():
    left = np.array([[0.2, 0], [0.5, 0.2], [0, 0.4], [0.1, 0.7]])
    right = np.array([[0.3, 0], [0.2, 0.2], [0, 0.6], [0.1 + 1e-14, 0.8]])
    a, b, d, wins, losses = paired_summary(left, right, xp=np)
    np.testing.assert_allclose(a, left.mean(axis=0))
    np.testing.assert_allclose(b, right.mean(axis=0))
    np.testing.assert_allclose(d, [-0.05, 0.075])
    np.testing.assert_array_equal(wins, [1, 2])
    np.testing.assert_array_equal(losses, [1, 0])


def test_single_column_interval_matches_cpu_percentiles():
    from backend.infrastructure.research.personal_metrics import interval

    values = np.arange(17, dtype=float).reshape(-1, 1) / 17
    rng = np.random.default_rng(42)
    samples = values[rng.integers(0, len(values), (2000, len(values)))].mean(axis=1)
    np.testing.assert_allclose(
        interval(values, xp=np), np.quantile(samples, [0.025, 0.975], axis=0)
    )


def test_gpu_single_column_interval_matches_cpu():
    import os

    import pytest

    if os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1":
        pytest.skip("explicit GPU test")
    import cupy as cp

    from backend.infrastructure.research.personal_metrics import interval

    values = np.arange(17, dtype=float).reshape(-1, 1) / 17
    np.testing.assert_allclose(
        interval(cp.asarray(values), xp=cp), interval(values, xp=np), rtol=0, atol=1e-12
    )
