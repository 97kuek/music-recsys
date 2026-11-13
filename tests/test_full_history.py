import json
import os
import runpy
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.infrastructure.recommenders.full_history import FullHistory


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test"
            ),
        ),
    ],
)
def test_frequency_uses_own_counts_and_explicit_popularity_for_unseen(tmp_path, gpu):
    p = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, play_count=n)
                for u, i, n in [
                    ("u", "a", 8),
                    ("u", "b", 1),
                    ("v", "a", 1),
                    ("v", "c", 9),
                    ("v", "d", 2),
                ]
            ]
        ),
        p,
    )
    model = FullHistory(p, use_gpu=gpu, progress=lambda _: None)
    assert model.rank("u", {"a", "b"}, "repeat") == ["a", "b"]
    assert model.rank("u", {"a", "b"}, "new") == ["c", "d"]
    if gpu:
        import cupy as cp

        np.testing.assert_array_equal(
            cp.asnumpy(model.gpu_score_batch([0, 1])), [[8, 1, 0, 0], [1, 0, 9, 2]]
        )


@pytest.mark.parametrize("history_basis", ["play_count", "active_days"])
def test_frequency_full_validation_without_test(tmp_path, history_basis):
    from backend.infrastructure.research.configuration import load_settings
    from backend.interfaces.cli.evaluate import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings(Path("configs/history_frequency/base.json"))
    raw["model"]["parameters"].update(
        use_gpu=False, input_memory_mb=128, history_basis=history_basis
    )
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "do-not-open"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "result"),
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    output = run(path)
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "succeeded" and result["model"] == "history_frequency"
    assert f"per-user learning {history_basis}" in result["definitions"]["ranking"]
    assert (
        len(pq.read_table(output / "recommendations.parquet")) == 2 * result["population"]["users"]
    )


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test"
            ),
        ),
    ],
)
def test_days_separate_burst_from_repeated_days_without_changing_popularity(tmp_path, gpu):
    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, play_count=c, active_days=d)
                for u, i, c, d in [
                    ("u", "a", 100, 1),
                    ("u", "b", 10, 10),
                    ("v", "c", 8, 2),
                    ("v", "d", 9, 1),
                ]
            ]
        ),
        path,
    )
    model = FullHistory(path, history_basis="active_days", use_gpu=gpu, progress=lambda _: None)
    np.testing.assert_array_equal(model.plays, [100, 10, 8, 9])
    assert model.rank("u", {"a", "b"}, "repeat") == ["b", "a"]
    assert model.rank("u", {"a", "b"}, "new") == ["d", "c"]
    if gpu:
        import cupy as cp

        np.testing.assert_array_equal(
            cp.asnumpy(model.gpu_score_batch([0, 1])), [[1, 10, 0, 0], [0, 0, 2, 1]]
        )
    with pytest.raises(ValueError, match="history_basis"):
        FullHistory(path, history_basis="future_days")


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test"
            ),
        ),
    ],
)
def test_last_play_order_and_train_only_evidence(tmp_path, gpu):
    from datetime import datetime

    from backend.infrastructure.datasets.listening_data import file_sha256

    learning = tmp_path / "learning.parquet"
    events = tmp_path / "train.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id="u", item_id="old", play_count=2),
                dict(user_id="u", item_id="recent", play_count=1),
                dict(user_id="v", item_id="unseen", play_count=1),
            ]
        ),
        learning,
    )
    rows = [
        dict(user_id=u, item_id=i, event_time=datetime(2020, 1, d))
        for u, i, d in [("u", "old", 1), ("u", "old", 2), ("u", "recent", 4), ("v", "unseen", 3)]
    ]
    pq.write_table(pa.Table.from_pylist(rows), events)
    kwargs = dict(
        history_basis="last_play",
        events_path=str(events),
        events_sha256=file_sha256(events),
        prediction_at="2020-01-05T00:00:00",
        use_gpu=gpu,
        progress=lambda _: None,
    )
    m = FullHistory(learning, **kwargs)
    assert m.rank("u", {"old", "recent"}, "repeat") == ["recent", "old"]
    assert m.rank("u", {"old", "recent"}, "new") == ["unseen"]
    np.testing.assert_array_equal(m.plays, [2, 1, 1])
    if gpu:
        import cupy as cp

        np.testing.assert_allclose(
            cp.asnumpy(m.gpu_score_batch([0, 1])), [[0.25, 0.5, 0], [0, 0, 1 / 3]]
        )
    second = tmp_path / "validation.parquet"
    first = tmp_path / "early.parquet"
    pq.write_table(pa.Table.from_pylist(rows[:2]), first)
    pq.write_table(pa.Table.from_pylist(rows[2:]), second)
    merged = FullHistory(
        learning,
        **dict(
            kwargs,
            events_path=[str(first), str(second)],
            events_sha256=[file_sha256(first), file_sha256(second)],
        ),
    )
    assert merged.rank("u", {"old", "recent"}, "repeat") == ["recent", "old"]
    with pytest.raises(ValueError, match="reproduce learning counts"):
        FullHistory(
            learning, **dict(kwargs, events_path=[str(first)], events_sha256=[file_sha256(first)])
        )
    with pytest.raises(ValueError, match="hash mismatch"):
        FullHistory(learning, **dict(kwargs, events_sha256="wrong"))
    rows[0]["event_time"] = datetime(2020, 1, 5)
    pq.write_table(pa.Table.from_pylist(rows), events)
    with pytest.raises(ValueError, match="future"):
        FullHistory(learning, **dict(kwargs, events_sha256=file_sha256(events)))
    pq.write_table(pa.Table.from_pylist(rows[1:]), events)
    with pytest.raises(ValueError, match="reproduce learning counts"):
        FullHistory(learning, **dict(kwargs, events_sha256=file_sha256(events)))


@pytest.mark.parametrize("basis", ["play_count", "active_days", "last_play", "decayed_count_30d"])
def test_history_later_period_rebuilds_training_without_test_events(tmp_path, basis):
    from backend.infrastructure.research.configuration import load_settings
    from backend.interfaces.cli.evaluate import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/history_frequency/base.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "result"),
    )
    raw["model"]["parameters"].update(use_gpu=False, input_memory_mb=128, history_basis=basis)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    run(path)
    # Test events must never be opened by the predictor; precomputed observations suffice.
    (bundle / "source" / "test.parquet").unlink()
    output = run(path, phase="test")
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["phase"] == "test" and result["status"] == "succeeded"
    assert result["model_parameters"]["history_basis"] == basis
    with pytest.raises(FileExistsError):
        run(path, phase="test")


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test"
            ),
        ),
    ],
)
def test_recency_log_count_combines_counts_and_age_with_same_unseen_ties(tmp_path, gpu):
    from datetime import datetime

    from backend.infrastructure.datasets.listening_data import file_sha256

    learning, events = tmp_path / "learning.parquet", tmp_path / "events.parquet"
    entries = [("u", "old", 7, 2), ("u", "recent", 1, 4), ("v", "unseen", 1, 3)]
    pq.write_table(
        pa.Table.from_pylist([dict(user_id=u, item_id=i, play_count=c) for u, i, c, _ in entries]),
        learning,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, event_time=datetime(2020, 1, d))
                for u, i, c, d in entries
                for _ in range(c)
            ]
        ),
        events,
    )
    kwargs = dict(
        events_path=str(events),
        events_sha256=file_sha256(events),
        prediction_at="2020-01-05T00:00:00",
        use_gpu=gpu,
        progress=lambda _: None,
    )
    plain = FullHistory(learning, history_basis="last_play", **kwargs)
    mixed = FullHistory(learning, history_basis="recency_log_count", **kwargs)
    assert plain.rank("u", {"old", "recent"}, "repeat") == ["recent", "old"]
    assert mixed.rank("u", {"old", "recent"}, "repeat") == ["old", "recent"]
    assert (
        mixed.rank("u", {"old", "recent"}, "new")
        == plain.rank("u", {"old", "recent"}, "new")
        == ["unseen"]
    )
    np.testing.assert_array_equal(mixed.plays, [7, 1, 1])
    if gpu:
        import cupy as cp

        actual = cp.asnumpy(mixed.gpu_score_batch([0, 1]))
    else:
        actual = mixed.history_weights.toarray()
    np.testing.assert_allclose(actual, [[0.75, 0.5, 0], [0, 0, 1 / 3]])


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test"
            ),
        ),
    ],
)
def test_recent_count_window_boundary_zero_and_future_rejection(tmp_path, gpu):
    from datetime import datetime

    from backend.infrastructure.datasets.listening_data import file_sha256

    learning, events = tmp_path / "learning.parquet", tmp_path / "events.parquet"
    entries = [
        ("u", "old", 7, 1),
        ("u", "boundary", 1, 2),
        ("u", "recent", 1, 31),
        ("v", "unseen", 2, 1),
    ]
    pq.write_table(
        pa.Table.from_pylist([dict(user_id=u, item_id=i, play_count=c) for u, i, c, d in entries]),
        learning,
    )
    rows = [
        dict(user_id=u, item_id=i, event_time=datetime(2020, 1, d))
        for u, i, c, d in entries
        for _ in range(c)
    ]
    pq.write_table(pa.Table.from_pylist(rows), events)
    kwargs = dict(
        history_basis="recency_recent_30d",
        events_path=str(events),
        events_sha256=file_sha256(events),
        prediction_at="2020-02-01T00:00:00",
        use_gpu=gpu,
        progress=lambda _: None,
    )
    model = FullHistory(learning, **kwargs)
    assert model.rank("u", {"old", "boundary", "recent"}, "repeat") == ["recent", "boundary", "old"]
    assert model.rank("u", {"old", "boundary", "recent"}, "new") == ["unseen"]
    np.testing.assert_array_equal(model.plays, [1, 7, 1, 2])
    if gpu:
        import cupy as cp

        actual = cp.asnumpy(model.gpu_score_batch([0, 1]))
    else:
        actual = model.history_weights.toarray()
    np.testing.assert_allclose(actual, [[1 / 31, 0, 0.5, 0], [0, 0, 0, 0]])
    rows[0]["event_time"] = datetime(2020, 2, 1)
    pq.write_table(pa.Table.from_pylist(rows), events)
    with pytest.raises(ValueError, match="future"):
        FullHistory(learning, **dict(kwargs, events_sha256=file_sha256(events)))


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test"
            ),
        ),
    ],
)
def test_decay_sums_each_event_and_keeps_old_positive_scores(tmp_path, gpu):
    from datetime import datetime, timedelta

    from backend.infrastructure.datasets.listening_data import file_sha256

    learning, events = tmp_path / "learning.parquet", tmp_path / "events.parquet"
    cutoff = datetime(2020, 5, 1)
    rows = [
        dict(user_id=u, item_id=i, event_time=cutoff - timedelta(days=d))
        for u, i, d in [("u", "a", 30), ("u", "a", 60), ("u", "b", 30), ("v", "c", 2400)]
    ]
    pq.write_table(pa.Table.from_pylist(rows), events)
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, play_count=c)
                for u, i, c in [("u", "a", 2), ("u", "b", 1), ("v", "c", 1)]
            ]
        ),
        learning,
    )
    model = FullHistory(
        learning,
        history_basis="decayed_count_30d",
        events_path=str(events),
        events_sha256=file_sha256(events),
        prediction_at=cutoff.isoformat(),
        use_gpu=gpu,
        progress=lambda _: None,
    )
    if gpu:
        import cupy as cp

        actual = cp.asnumpy(model.gpu_score_batch([0, 1]))
    else:
        actual = model.history_weights.toarray()
    expected = np.log1p([[0.5 + 0.25, 0.5, 0], [0, 0, 2.0**-80]]) / np.log(2.0)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=0)
    assert actual[1, 2] > 0
    assert model.rank("u", {"a", "b"}, "repeat") == ["a", "b"]
    assert model.rank("u", {"a", "b"}, "new") == ["c"]
