"""Independent full-score oracle and band-specific ranked recall checks."""

import os

import numpy as np
import pytest

from backend.domain.recommendation.pipeline import FusionSpec
from backend.infrastructure.recommenders.pipeline import BatchedPipeline
from backend.infrastructure.research.union_strata import ranked_recall, summarize
from backend.infrastructure.research.union_verify import verify_source_order


def test_prefix_identity_matches_full_union_rescoring_including_ties_and_short_lists():
    rng = np.random.default_rng(2)
    for size in (0, 7, 101, 350):
        scores = rng.integers(0, 8, size=size)
        plays = rng.integers(0, 4, size=size)
        full = sorted(range(size), key=lambda i: (-scores[i], -plays[i], i))
        other = rng.permutation(size)[:100].tolist()
        pool = set(full[:100]) | set(other)
        rescored = sorted(pool, key=lambda i: (-scores[i], -plays[i], i))
        for k in range(2, 21):
            assert rescored[:k] == full[:k]


def check_ranked_recall(xp):
    rankings = [
        np.array([[0, 1, 2, -1], [3, 4, -1, -1], [-1] * 4]),
        np.array([[1, 5, 0, -1], [4, 6, 3, -1], [-1] * 4]),
    ]
    truth = [[0, 2, 5, 7], [3, 6], []]
    popularity = np.arange(8) % 4
    added = np.array([[False, True, False, False], [False, True, False, False], [False] * 4])
    ks = [2, 3, 4]
    host = np.asarray if xp is np else xp.asnumpy
    for band, valid, values in ranked_recall(
        [xp.asarray(r) for r in rankings], truth, popularity, xp.asarray(added), ks, xp=xp
    ):
        for i in range(3):
            targets = {t for t in truth[i] if band is None or popularity[t] == band}
            assert bool(host(valid)[i]) == bool(targets)
            for j, k in enumerate(ks):
                lists = [set(r[i, :k]) for r in rankings]
                lists.append(set(rankings[1][i, :k][added[i, :k]]))
                expected = [len(s & targets) / len(targets) if targets else 0 for s in lists]
                np.testing.assert_allclose(host(values)[i, :, j], expected)


def test_ranked_recall_keeps_band_denominator_padding_and_original_added_positions():
    check_ranked_recall(np)


@pytest.mark.skipif(os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test")
def test_cuda_ranked_recall():
    import cupy as cp

    check_ranked_recall(cp)


def test_seed_pairing_bootstrap_and_nulls():
    key, empty = ("new", 90, "history", "Q1"), ("new", 90, "history", "Q2")
    first = {
        key: (["a", "b"], np.array([[[0], [1], [1]], [[0], [1], [1]]], dtype=float)),
        empty: ([], np.empty((0, 3, 1))),
    }
    second = {
        key: (["a", "b"], np.array([[[1], [1], [0]], [[1], [1], [0]]], dtype=float)),
        empty: ([], np.empty((0, 3, 1))),
    }
    rows = summarize([first, second], np.array([0, 0, 0]), [20], xp=np, bootstrap=32)
    row = next(r for r in rows if r["stratum"] == "Q1" and r["condition"] == "rrf-minus-ease")
    assert row["mean"] == row["ci_low"] == row["ci_high"] == 0.5
    assert row["n_evaluated"] == 2 and row["n_excluded"] == 1
    assert all(r["mean"] is None and r["ci_low"] is None for r in rows if r["stratum"] == "Q2")
    second[key] = (["b", "a"], second[key][1])
    with pytest.raises(ValueError, match="eligible users"):
        summarize([first, second], np.array([0, 0, 0]), [20], xp=np)


def test_saved_score_order_and_tie_break_are_required():
    row = dict(item_indices=[1, 0, 2], candidate_count=3, raw_scores=[2, 2, 1])
    verify_source_order(row, [1, 3, 2], 3)
    with pytest.raises(ValueError, match="order"):
        verify_source_order(row, [3, 1, 2], 3)
    with pytest.raises(ValueError, match="budget"):
        verify_source_order(dict(row, candidate_count=4), [1, 3, 2], 3)


def test_collect_verifies_saved_pool_rankings_and_full_population(tmp_path):
    import hashlib
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from backend.infrastructure.datasets.candidates import CandidateWriter
    from backend.infrastructure.research.union_verify import collect

    items, plays = [str(i) for i in range(6)], np.arange(6, 0, -1)
    users = [f"u{i:02d}" for i in range(65)]
    engine = BatchedPipeline({}, items, plays, FusionSpec(), xp=np)
    arrays = []
    for source, ids in (("ease", list(range(6))), ("bpr-s42", list(reversed(range(6))))):
        directory = tmp_path / "candidates" / source
        directory.mkdir(parents=True)
        w = CandidateWriter(directory / "candidates.parquet")
        for user, task in ((u, t) for u in users for t in ("new", "repeat")):
            w.add(
                dict(
                    user_id=user,
                    task=task,
                    item_indices=ids,
                    raw_scores=list(range(6, 0, -1)),
                    source_ranks=list(range(1, 7)),
                    candidate_count=6,
                )
            )
        w.close()
        a = np.full((len(users), 200), -1, dtype=np.int32)
        a[:, :6] = ids
        arrays.append(a)
    pool, _, _ = engine.fuse(*arrays)
    for name, ids in (("ease", arrays[0][0, :6]), ("ease-bpr-rrf-s42", pool[0, :6])):
        d = tmp_path / name
        d.mkdir()
        pq.write_table(
            pa.Table.from_pylist(
                [
                    dict(
                        user_id=user,
                        task=t,
                        item_ids=[items[i] for i in ids],
                        candidate_count=6,
                        pool_count=6,
                    )
                    for start in range(0, len(users), 64)
                    for t in ("new", "repeat")
                    for user in users[start : start + 64]
                ]
            ),
            d / "recommendations.parquet",
        )
    obs = tmp_path / "observations.parquet"
    pq.write_table(
        pa.table(
            dict(
                user_id=users,
                item_id=["0"] * 65,
                window_days=[90] * 65,
                learning_play_count=[0] * 65,
            )
        ),
        obs,
    )
    writer = SimpleNamespace(add=lambda row: None)
    args = (
        tmp_path,
        42,
        [(u, 6) for u in users],
        items,
        plays,
        np.zeros(65, dtype=int),
        np.arange(6) % 4,
        obs,
        SimpleNamespace(window_days=[90], ks=[2, 20]),
        engine,
        writer,
        writer,
        lambda _: None,
    )
    rows, digest = collect(*args)
    expected = hashlib.sha256()
    for start in range(0, len(users), 64):
        for task in ("new", "repeat"):
            for user in users[start : start + 64]:
                expected.update((user + "\0" + task + "\0").encode())
            expected.update(pool[start : start + 64].tobytes())
    assert digest == expected.hexdigest()
    assert rows["new", 90, "all", "all"][0] == users
    path = tmp_path / "ease/recommendations.parquet"
    table = pq.read_table(path).to_pylist()
    table[0]["item_ids"].reverse()
    pq.write_table(pa.Table.from_pylist(table), path)
    with pytest.raises(ValueError, match="top-K"):
        collect(*args)


def test_cli_plan_needs_no_gpu_or_data(monkeypatch, capsys):
    import json
    import sys

    from backend.interfaces.cli.hybrid_union import main

    monkeypatch.setattr(sys, "argv", ["hybrid_union", "--config", "configs/hybrid/base.json"])
    main()
    assert json.loads(capsys.readouterr().out)["population"] == "all learning users"
