import json
import os
import runpy
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.infrastructure.recommenders.full_ease import FullEASE


def learning(tmp_path):
    path = tmp_path / "learning.parquet"
    # Repeated rows/counts must collapse to binary positives, not confidence weights.
    records = [
        dict(user_id=u, item_id=i, play_count=count)
        for u, i, count in [
            ("u", "a", 30),
            ("u", "b", 1),
            ("u", "a", 2),
            ("v", "b", 2),
            ("v", "c", 1),
            ("w", "a", 1),
            ("w", "c", 5),
            ("w", "d", 1),
        ]
    ]
    pq.write_table(pa.Table.from_pylist(records[::-1]), path)
    return path


def oracle():
    x = np.array([[1, 1, 0, 0], [0, 1, 1, 0], [1, 0, 1, 1]], dtype=float)
    weights = np.zeros((4, 4))
    # Independent constrained ridge-regression oracle, no inverse-formula reuse.
    for column in range(4):
        other = np.arange(4) != column
        design = x[:, other]
        weights[other, column] = np.linalg.solve(
            design.T @ design + 2 * np.eye(3), design.T @ x[:, column]
        )
    return x, weights


def test_ease_matches_constrained_ridge_and_candidate_rules(tmp_path):
    model = FullEASE(learning(tmp_path), regularization=2, progress=lambda _: None)
    x, expected = oracle()
    np.testing.assert_allclose(model.weights, expected, atol=1e-15)
    np.testing.assert_array_equal(np.diag(model.weights), 0)
    np.testing.assert_array_equal(model.history_weights.toarray(), x)
    scores = x @ expected
    for row, user in enumerate(model.users):
        history = set(model.items[x[row] > 0])
        for task in ("new", "repeat"):
            allowed = np.flatnonzero((x[row] > 0) == (task == "repeat"))
            order = np.lexsort((allowed, -model.plays[allowed], -scores[row, allowed]))
            assert model.rank(user, history, task) == model.items[allowed[order]].tolist()
    with pytest.raises(ValueError, match="task"):
        model.rank("u", {"a", "b"}, "invalid")
    with pytest.raises(ValueError, match="memory_budget"):
        FullEASE(learning(tmp_path), memory_budget_mb=0.00001)
    for kwargs in ({"regularization": 0}, {"block_size": 0}, {"use_gpu": 1}):
        with pytest.raises(ValueError):
            FullEASE(learning(tmp_path), **kwargs)


def test_ease_cpu_full_validation_is_complete_and_never_opens_test(tmp_path):
    from src.infrastructure.research.configuration import load_settings
    from src.interfaces.cli.evaluate import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings(Path("configs/ease.json"))
    raw["model"]["parameters"].update(use_gpu=False, memory_budget_mb=128)
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "test-must-not-be-opened"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "result"),
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    output = run(path)
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "succeeded"
    assert result["phase"] == "validation"
    assert {r["k"] for r in result["accuracy"]} == set(range(2, 21))
    assert result["observations"] and result["diversity"]
    assert result["efficiency"]["api_cost"] is None
    records = pq.read_table(output / "recommendations.parquet").to_pylist()
    assert len(records) == 2 * result["population"]["users"]
    assert len(list(output.glob("*.png"))) == 16
    assert not list(output.glob("*.partial"))
    with pytest.raises(FileExistsError):
        run(path)


@pytest.mark.skipif(os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test")
def test_ease_gpu_coefficients_scores_and_rankings_match_oracle(tmp_path, monkeypatch):
    import cupy as cp

    from src.infrastructure.evaluation.gpu_evaluation import GPURanker

    path = learning(tmp_path)
    gpu = FullEASE(path, regularization=2, use_gpu=True, block_size=2, progress=lambda _: None)
    x, expected = oracle()
    np.testing.assert_allclose(cp.asnumpy(gpu.weights), expected, atol=1e-7)
    np.testing.assert_array_equal(cp.asnumpy(gpu.weights.diagonal()), 0)
    np.testing.assert_allclose(cp.asnumpy(gpu.gpu_score_batch([0, 1, 2])), x @ expected, atol=1e-7)
    users = list(gpu.users)
    histories = [set(gpu.items[row > 0]) for row in x]
    actual, _, _ = GPURanker(gpu).rank(users, histories)
    cpu = FullEASE(path, regularization=2, progress=lambda _: None)
    for task in ("new", "repeat"):
        for row, user in enumerate(users):
            indices = cp.asnumpy(actual[task][row])
            expected_ranking = cpu.rank(user, histories[row], task)
            assert gpu.items[indices[indices >= 0]].tolist() == expected_ranking
            assert gpu.rank(user, histories[row], task) == expected_ranking
    # Equal scores exercise popularity and item-ID ties; only final top-K indices cross to CPU.
    gpu.weights.fill(0)
    cpu.weights.fill(0)
    gpu.plays[:] = cpu.plays[:] = [9, 9, 3, 9]
    transfers = []
    original = cp.asnumpy

    def transfer(array, *args, **kwargs):
        transfers.append((array.shape, array.dtype.kind))
        return original(array, *args, **kwargs)

    monkeypatch.setattr(cp, "asnumpy", transfer)
    for task in ("new", "repeat"):
        for row, user in enumerate(users):
            assert gpu.rank(user, histories[row], task, top_k=1) == cpu.rank(
                user, histories[row], task, top_k=1
            )
    assert len(transfers) == 2 * len(users)
    assert all(shape == (1,) and kind in "iu" for shape, kind in transfers)
    assert gpu.execution["training_device"] == "cuda"
    assert gpu.execution["inverse_sample_max_relative_residual"] < 1e-5
