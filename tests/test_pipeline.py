import os

import numpy as np
import pytest

from src.domain.recommendation.pipeline import (
    Candidate,
    FusionSpec,
    HybridCondition,
    mmr_reference,
    rrf_pool,
)
from src.infrastructure.recommenders.pipeline import BatchedPipeline


def test_full_union_keeps_shared_middle_candidate_and_nullable_scores():
    a = [Candidate(f"a{i}", i, None) for i in range(1, 101)]
    b = [Candidate(f"b{i}", i, float(101 - i)) for i in range(1, 101)]
    a[59] = Candidate("shared", 60, None)
    b[59] = Candidate("shared", 60, 3.0)
    plays = dict.fromkeys([c.item_id for c in a + b], 1)
    pool = rrf_pool([a, b], FusionSpec(), plays)
    assert len(pool) == 199 and pool[0].item_id == "shared"
    assert pool[0].source_ranks == (60, 60) and pool[0].source_scores == (None, 3.0)


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
def test_batched_union_and_mmr_match_independent_domain_reference(gpu):
    if gpu:
        import cupy as xp
    else:
        xp = np
    rng = np.random.default_rng(61)
    items = [f"i{i:03}" for i in range(30)]
    features = {
        i: {f"t{t}": float(rng.integers(1, 6)) for t in rng.choice(20, 4, replace=False)}
        for i in items
    }
    features[items[0]] = {}
    del features[items[1]]
    plays = rng.integers(1, 20, len(items))
    left = np.stack([rng.choice(len(items), 12, replace=False) for _ in range(8)])
    right = np.stack([rng.choice(len(items), 12, replace=False) for _ in range(8)])
    left[-1, 2:] = -1
    right[-1, 3:] = -1
    left[-2] = -1
    right[-2] = -1
    spec = FusionSpec(top_m=12)
    engine = BatchedPipeline(features, items, plays, spec, xp=xp)
    pools, scores, counts = engine.fuse(xp.asarray(left), xp.asarray(right))
    to_cpu = (lambda x: x) if xp is np else xp.asnumpy
    for lambda_ in (1.0, 0.8, 0.5, 0.0):
        actual, details = engine.rerank(pools, scores, lambda_=lambda_)
        for n in range(len(left)):
            sources = [
                [Candidate(items[i], r, None) for r, i in enumerate(ids[ids >= 0], 1)]
                for ids in (left[n], right[n])
            ]
            pool = rrf_pool(sources, spec, dict(zip(items, plays, strict=True)))
            assert [items[i] for i in to_cpu(pools[n]) if i >= 0] == [p.item_id for p in pool]
            np.testing.assert_allclose(
                to_cpu(scores[n])[: len(pool)], [p.rrf_score for p in pool], atol=1e-15
            )
            expected = mmr_reference(pool, features, lambda_=lambda_)
            assert [items[i] for i in to_cpu(actual[n]) if i >= 0] == [p.item_id for p in expected]
            assert int(counts[n]) == len(pool)
    with pytest.raises(ValueError, match="Duplicate"):
        engine.fuse(xp.asarray([[1, 1]]), xp.asarray([[2, 3]]))


def test_study_prepares_shared_sources_once_and_keeps_failures_visible():
    from src.application.hybrid import run_study

    events = []

    class Port:
        def prepare(self, name):
            events.append(("prepare", name))
            return {"source": name}

        def evaluate(self, condition, candidates):
            events.append(("evaluate", condition.name))
            assert set(candidates) == set(condition.sources)
            return {"condition": condition.name}

        def summarize(self, results):
            assert len(results) == 2
            return "done"

    conditions = (
        HybridCondition("rrf", "rrf", ("a", "b"), 42),
        HybridCondition("mmr", "mmr", ("a", "b"), 42, 0.5),
    )
    assert run_study(("a", "b"), conditions, Port()) == "done"
    assert events[:2] == [("prepare", "a"), ("prepare", "b")]
    assert len(events) == 4


def test_candidate_io_preserves_empty_lists_and_nullable_free_raw_scores(tmp_path):
    from src.infrastructure.datasets.candidates import CandidateWriter, candidate_batches

    directory = tmp_path / "source"
    directory.mkdir()
    writer = CandidateWriter(directory / "candidates.parquet")
    for user in ("u", "v"):
        writer.add(
            dict(
                user_id=user,
                task="new",
                item_indices=[],
                raw_scores=[],
                source_ranks=[],
                candidate_count=0,
            )
        )
        writer.add(
            dict(
                user_id=user,
                task="repeat",
                item_indices=[1],
                raw_scores=[0.0],
                source_ranks=[1],
                candidate_count=1,
            )
        )
    writer.close()
    batch = list(candidate_batches([dict(directory=str(directory))], users=1))
    assert len(batch) == 2 and batch[0][0][0]["item_indices"] == []
    assert batch[0][1][0]["raw_scores"] == [0.0]
