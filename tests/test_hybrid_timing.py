import os

import numpy as np
import pytest

from backend.infrastructure.research.hybrid_timing import rank_union


def test_union_scoring_respects_popularity_id_ties_and_padding():
    scores = np.array([[3, 3, 3, 9, -2], [0, 0, 0, 0, 0]], dtype=float)
    plays = np.array([2, 4, 4, 1, 100])
    pool = np.array([[2, 4, 0, 1, -1], [3, 2, 1, -1, -1]])
    ranked = rank_union(scores, pool, plays, xp=np)
    assert ranked.tolist() == [[1, 2, 0, 4, -1], [1, 2, 3, -1, -1]]


def test_union_with_scorers_prefix_preserves_topk():
    rng = np.random.default_rng(7)
    scores = rng.integers(0, 10, (8, 300)).astype(float)
    plays = rng.integers(1, 20, 300)
    ids = np.broadcast_to(np.arange(300), scores.shape)
    original = np.lexsort((ids, -np.broadcast_to(plays, scores.shape), -scores), axis=1)
    for i, row in enumerate(original):
        pool = np.unique(np.r_[row[:100], rng.choice(300, 100, replace=False)])[None, :]
        actual = rank_union(scores[i : i + 1], pool, plays, xp=np)
        np.testing.assert_array_equal(actual[0], row[:20])


@pytest.mark.skipif(os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test")
def test_cuda_union_matches_independent_full_score_order_with_ties_and_padding():
    import cupy as cp

    rng = np.random.default_rng(19)
    scores = rng.integers(-4, 5, (8, 350)).astype(np.float32)
    plays = rng.integers(1, 20, 350)
    pools = rng.integers(0, 350, (8, 200), dtype=np.int32)
    pools[:, -7:] = -1
    pools[-1] = -1
    ranked = cp.asnumpy(rank_union(cp.asarray(scores), cp.asarray(pools), cp.asarray(plays), xp=cp))
    for i, pool in enumerate(pools):
        valid = sorted(
            (int(j) for j in pool if j >= 0), key=lambda j: (-scores[i, j], -plays[j], j)
        )
        expected = (valid + [-1] * len(pool))[:20]
        assert ranked[i].tolist() == expected


def test_changed_experiment_is_rejected_before_gpu_or_output_creation(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from backend.infrastructure.research import hybrid_timing

    (tmp_path / "config.json").write_text("{}")
    monkeypatch.setitem(sys.modules, "cupy", None)

    def mismatch(config):
        raise ValueError("保存結果と現在の設定が異なります")

    monkeypatch.setattr(hybrid_timing, "read_result", mismatch)
    with pytest.raises(ValueError, match="設定が異なります"):
        hybrid_timing.run(SimpleNamespace(output=tmp_path))
    assert not (tmp_path / "efficiency.partial").exists()


def test_cli_plan_does_not_start_training(monkeypatch, capsys):
    import sys

    from backend.interfaces.cli.hybrid_timing import main

    monkeypatch.setattr(sys, "argv", ["hybrid_timing", "--config", "configs/hybrid/base.json"])
    monkeypatch.setattr(
        "backend.infrastructure.research.hybrid_timing.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    main()
    assert "--run required" in capsys.readouterr().out


# This executes the actual model, frozen candidate readers and publication boundary.


@pytest.mark.skipif(os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test")
@pytest.mark.parametrize("refit_drift,mmr_drift", [(False, False), (True, False), (False, True)])
def test_gpu_timing_matches_every_frozen_recommendation(
    tmp_path, monkeypatch, refit_drift, mmr_drift
):
    import json
    import runpy
    from pathlib import Path

    import cupy as cp

    from backend.infrastructure.datasets.listening_data import file_sha256
    from backend.infrastructure.research.hybrid_timing import run
    from backend.infrastructure.research.pool_report import run as pool_run
    from backend.infrastructure.research.search import load_study_config
    from backend.infrastructure.research.union_report import run as union_run
    from backend.interfaces.notebook.timing_figures import figures

    setup = runpy.run_path(str(Path(__file__).with_name("test_hybrid.py")))[
        "test_full_hybrid_validation_pairs_seeds_keeps_sources_and_never_reads_test"
    ]
    setup(tmp_path, monkeypatch)
    config = load_study_config(tmp_path / "hybrid.json")
    pool_run(config, xp=cp, execution={"backend": "CUDA test"}, save_figures=lambda *a: None)
    union_run(config, xp=cp, execution={"backend": "CUDA test"}, save_figures=lambda *a: None)
    original = {p: file_sha256(p) for p in config.output.rglob("*") if p.is_file()}
    if refit_drift:
        from backend.infrastructure.recommenders.full_ease import FullEASE

        score = FullEASE.gpu_score_batch

        def changed_score(self, users):
            values = score(self, users)
            return values + cp.arange(values.shape[1], dtype=values.dtype)[None, :]

        monkeypatch.setattr(FullEASE, "gpu_score_batch", changed_score)
    if mmr_drift:
        from backend.infrastructure.recommenders.pipeline import BatchedPipeline

        rerank = BatchedPipeline.rerank

        def changed_ranking(self, *args, **kwargs):
            ranking, diagnostics = rerank(self, *args, **kwargs)
            return ranking[:, ::-1].copy(), diagnostics

        monkeypatch.setattr(BatchedPipeline, "rerank", changed_ranking)
    output = run(config, repetitions=2, save_figures=figures)
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "succeeded" and result["selection"] == "not_selected"
    comparison = result["refit_comparison"]
    assert comparison["requests"] == 4 * 2 * 3 * 2
    assert (comparison["changed_top20_lists"] > 0) == refit_drift
    reranking_changed = sum(
        r["changed_top20_lists"] for r in result["reranking_comparison"].values()
    )
    assert (reranking_changed > 0) == mmr_drift
    total_changed = comparison["changed_top20_lists"] + reranking_changed
    assert result["exact_top20_checks"] + total_changed == 4 * 2 * 4 * 3 * 2
    import pyarrow.parquet as pq

    assert pq.read_table(output / comparison["differences"]).num_rows == total_changed
    assert result["execution"]["repetitions"] == 2
    assert all(file_sha256(p) == digest for p, digest in original.items())
    assert not (tmp_path / "MUST_NOT_READ_TEST").exists()
    with pytest.raises(FileExistsError):
        run(config, repetitions=2)
