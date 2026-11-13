import json
import os
import runpy
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scipy import sparse

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.recommenders.full_transition import FullTransition
from backend.infrastructure.recommenders.transition_rules import (
    accumulate_rules,
    event_batches,
    normalize_rules,
    prune_rules,
)

GPU = pytest.param(
    True,
    marks=pytest.mark.skipif(
        os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test"
    ),
)


@pytest.mark.parametrize("gpu", [False, GPU])
@pytest.mark.parametrize("batch_size", [1, 2, 7])
def test_rules_match_independent_pairs_across_boundaries_and_users(gpu, batch_size):
    xp, sp = np, sparse
    if gpu:
        import cupy as xp
        from cupyx.scipy import sparse as sp
    users = np.array([0, 0, 0, 0, 1, 1, 1])
    items = np.array([0, 1, 0, 2, 2, 1, 0])
    times = np.array([0, 10, 20, 200000, 0, 10, 20]) * 1000000
    expected = np.zeros((3, 3))
    pairs = 0
    for right in range(len(users)):
        for left in range(max(0, right - 3), right):
            if users[left] == users[right] and 0 < times[right] - times[left] <= 86400e6:
                expected[items[left], items[right]] += 1 / (right - left)
                pairs += 1
    batches = [
        tuple(a[start : start + batch_size] for a in [users, items, times])
        for start in range(0, len(users), batch_size)
    ]
    rules, stats = accumulate_rules(batches, 3, distance=3, max_gap_days=1, xp=xp, sparse=sp)
    actual = rules.toarray()
    if gpu:
        actual = xp.asnumpy(actual)
    np.testing.assert_allclose(actual, expected)
    assert stats == dict(retained_transition_events=7, transition_pairs=pairs)


def inputs(tmp_path):
    events, learning = tmp_path / "events.parquet", tmp_path / "learning.parquet"
    # a->c twice makes c preferred to the more globally popular b for an a-only listener.
    rows = [
        dict(user_id=u, item_id=i, event_time=datetime(2020, 1, 1) + timedelta(hours=h))
        for u, i, h in [
            ("u", "a", 0),
            ("v", "a", 0),
            ("v", "c", 1),
            ("v", "a", 2),
            ("v", "c", 3),
            ("w", "b", 0),
            ("w", "b", 1),
            ("w", "b", 2),
            ("w", "b", 3),
            ("z", "a", 0),
            ("z", "b", 0),  # ambiguous timestamps excluded
        ]
    ]
    import collections

    counts = collections.Counter((r["user_id"], r["item_id"]) for r in rows)
    pq.write_table(pa.Table.from_pylist(rows[::-1]), events)
    pq.write_table(
        pa.Table.from_pylist(
            [dict(user_id=u, item_id=i, play_count=n) for (u, i), n in counts.items()]
        ),
        learning,
    )
    return events, learning, rows


@pytest.mark.parametrize("gpu", [False, GPU])
@pytest.mark.parametrize("symmetric", [False, True])
def test_directional_ranking_pruning_and_ambiguous_events(tmp_path, gpu, symmetric):
    events, learning, rows = inputs(tmp_path)
    kwargs = dict(
        events_path=[str(events)],
        events_sha256=[file_sha256(events)],
        prediction_at="2020-01-02",
        use_gpu=gpu,
        distance=1,
        neighbors=1,
        symmetric=symmetric,
        input_memory_mb=128,
        progress=lambda _: None,
    )
    m = FullTransition(learning, **kwargs)
    assert m.rank("u", {"a"}, "new") == ["c", "b"]
    assert m.rank("u", {"a"}, "repeat") == ["a"]
    assert m.execution["retained_transition_events"] == len(rows) - 2
    assert m.execution["transition_pairs"] == 6
    raw = np.array([[0, 0, 2], [0, 3, 0], [1, 0, 0]], dtype=float)
    if symmetric:
        raw = (raw + raw.T) / 2
    actual = m.rules.toarray()
    if gpu:
        import cupy as cp

        actual = cp.asnumpy(actual)
        np.testing.assert_allclose(
            cp.asnumpy(m.gpu_score_batch([m.users["u"]])),
            m.histories.get()[m.users["u"]].toarray() @ raw,
        )
    np.testing.assert_allclose(actual, raw)
    stream = list(event_batches([events], m.users, m.index, "128MB", batch_size=1))
    assert sum(len(x[0]) for x in stream) == len(rows) - 2
    assert all(m.users["z"] not in x[0] for x in stream)


def test_events_must_match_learning_and_be_before_prediction(tmp_path):
    events, learning, rows = inputs(tmp_path)
    kwargs = dict(
        events_path=[str(events)],
        events_sha256=[file_sha256(events)],
        prediction_at="2020-01-02",
        input_memory_mb=128,
        progress=lambda _: None,
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        FullTransition(learning, **dict(kwargs, events_sha256=["bad"]))
    with pytest.raises(ValueError, match="future"):
        FullTransition(learning, **dict(kwargs, prediction_at="2020-01-01"))
    pq.write_table(pa.Table.from_pylist(rows[:-1]), events)
    with pytest.raises(ValueError, match="reproduce learning"):
        FullTransition(learning, **dict(kwargs, events_sha256=[file_sha256(events)]))


@pytest.mark.parametrize("gpu", [False, GPU])
def test_first_listen_changes_training_only_and_never_promotes_ambiguous_repeats(tmp_path, gpu):
    events, learning, rows = inputs(tmp_path)
    # z's a first appeared simultaneously with b; its later repeat stays ineligible.
    rows.append(dict(user_id="z", item_id="a", event_time=datetime(2020, 1, 1, 5)))
    pq.write_table(pa.Table.from_pylist(rows), events)
    records = pq.read_table(learning).to_pylist()
    for record in records:
        if record["user_id"] == "z" and record["item_id"] == "a":
            record["play_count"] += 1
    pq.write_table(pa.Table.from_pylist(records), learning)
    kwargs = dict(
        events_path=[str(events)],
        events_sha256=[file_sha256(events)],
        prediction_at="2020-01-02",
        use_gpu=gpu,
        distance=1,
        neighbors=3,
        input_memory_mb=128,
        progress=lambda _: None,
    )
    baseline = FullTransition(learning, **kwargs)
    first = FullTransition(learning, **kwargs, transition_events="first_listen")
    assert first.users == baseline.users
    actual = first.rules.toarray()
    difference = (first.histories - baseline.histories).toarray()
    if gpu:
        import cupy as cp

        actual = cp.asnumpy(actual)
        difference = cp.asnumpy(difference)
    np.testing.assert_array_equal(difference, 0)
    np.testing.assert_array_equal(actual, [[0, 0, 1], [0, 0, 0], [0, 0, 0]])
    assert first.execution["retained_transition_events"] == 4
    assert first.execution["transition_pairs"] == 1
    assert first.rank("u", {"a"}, "new") == ["c", "b"]
    assert first.rank("z", {"a", "b"}, "repeat") == ["a", "b"]
    with pytest.raises(ValueError, match="transition_events"):
        FullTransition(learning, **kwargs, transition_events="future")


@pytest.mark.parametrize("config_name", ["directed", "first-listen", "degree-half"])
def test_full_evaluation_never_opens_future_events_and_preserves_test(tmp_path, config_name):
    from backend.infrastructure.research.configuration import load_settings
    from backend.interfaces.cli.evaluate import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings(f"configs/transition_rules/{config_name}.json")
    raw["model"]["parameters"].update(use_gpu=False, input_memory_mb=128)
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "result"),
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    run(path)
    (bundle / "source" / "test.parquet").unlink()
    output = run(path, phase="test")
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["phase"] == "test" and result["status"] == "succeeded"
    assert "transition rules" in result["definitions"]["ranking"]
    assert (
        len(pq.read_table(output / "recommendations.parquet")) == 2 * result["population"]["users"]
    )
    with pytest.raises(FileExistsError):
        run(path, phase="test")


@pytest.mark.parametrize("gpu", [False, GPU])
@pytest.mark.parametrize("power", [0, 0.5, 1])
def test_degree_normalization_uses_unpruned_mass_and_preserves_zero_nodes(gpu, power):
    xp, sp = np, sparse
    if gpu:
        import cupy as xp
        from cupyx.scipy import sparse as sp
    # Popular target 1 has more raw evidence, but target 2 is more specific to source 0.
    raw = np.array([[0, 4, 3, 0], [0, 96, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0.0]])
    expected = np.zeros_like(raw)
    for i, j in zip(*raw.nonzero(), strict=True):
        expected[i, j] = raw[i, j] / (sum(raw[i]) * sum(raw[:, j])) ** power
    original = sp.csr_matrix(xp.asarray(raw))
    normalized = normalize_rules(original, power, xp=xp, sparse=sp)
    actual = normalized.toarray()
    pruned = prune_rules(normalized, 1, xp=xp, sparse=sp).toarray()
    unchanged = original.toarray()
    if gpu:
        actual, pruned, unchanged = map(xp.asnumpy, (actual, pruned, unchanged))
    np.testing.assert_allclose(actual, expected)
    np.testing.assert_array_equal(unchanged, raw)
    assert np.isfinite(actual).all()
    assert np.flatnonzero(pruned[0]).tolist() == ([1] if power == 0 else [2])


@pytest.mark.parametrize("power", [-1, 1.1, float("nan"), float("inf"), True, "0.5"])
def test_degree_normalization_rejects_invalid_power(power):
    with pytest.raises(ValueError, match="degree_power"):
        normalize_rules(sparse.eye(3).tocsr(), power, xp=np, sparse=sparse)
