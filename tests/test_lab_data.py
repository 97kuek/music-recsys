import bz2
import csv
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from src.interfaces.cli.lab_data import prepare_files, verify_prepared


def test_file_preparation_train_only_selection_reuse_and_tamper(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pytest.importorskip("duckdb")
    cache, tags = tmp_path / "events.parquet", tmp_path / "tags.bz2"
    # a_future sorts first but has no Train history. Repeated plays must survive.
    rows = [
        ("a_future", "x", "2017-04-01"),
        ("b", "x", "2017-03-01"),
        ("b", "y", "2017-03-02"),
        ("b", "x", "2017-04-01"),
        ("b", "x", "2017-04-01"),
        ("b", "y", "2018-09-22"),
        ("c", "z", "2017-03-01"),
    ]
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"user_id": u, "item_id": i, "event_time": datetime.fromisoformat(t)}
                for u, i, t in rows
            ]
        ),
        cache,
    )
    with bz2.open(tags, "wt") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["id", "(tag, weight)"])
        writer.writerows([("x", "{'rock': 10}"), ("x", "{'rock': 10}"), ("y", "{}")])
    output = tmp_path / "prepared"
    ready = prepare_files(cache, tags, output, users=1)
    history = pq.read_table(output / "events.parquet")
    assert set(history["user_id"].to_pylist()) == {"b"}
    assert history.num_rows == 5
    assert ready["tags_missing"] == 0
    assert ready["tags_empty"] == 1
    assert ready["evaluation_executed"] is False
    from matplotlib import pyplot as plt

    from src.interfaces.notebook.split_figures import load_split_frame, split_figure

    split_frame = load_split_frame(output / "validation")
    # Count repeated listening events, rather than unique user-track pairs or days.
    assert split_frame.events.tolist() == [2, 2, 1]
    assert split_frame.share_pct.tolist() == [40, 40, 20]
    assert split_frame.tracks.tolist() == [2, 1, 1]
    filtered = load_split_frame(output / "validation", exclude_before="2017-03-02")
    assert filtered.events.tolist() == [1, 2, 1]
    assert filtered.tracks.tolist() == [1, 1, 1]
    assert filtered.share_pct.tolist() == [25, 50, 25]
    assert filtered.iloc[0].start == "2017-03-02T00:00:00"
    assert filtered.attrs["excluded_events"] == 1
    figure = split_figure(split_frame)
    try:
        assert [bar.get_width() for bar in figure.axes[0].patches] == [40, 40, 20]
    finally:
        plt.close(figure)
    summary_path = output / "source/summary.json"
    original_summary = summary_path.read_bytes()
    summary_path.write_bytes(original_summary + b"\n")
    with pytest.raises(ValueError, match="checksum"):
        load_split_frame(output / "validation")
    summary_path.write_bytes(original_summary)
    # 共通Runnerで読めることと、TestのTrain+Validation出典の一致を確認する。
    from src.infrastructure.research.search import load_study_config
    from src.infrastructure.research.search_protocols import load_data, validate_test_training

    config = replace(
        load_study_config(Path(__file__).parents[1] / "configs/popularity-plays.json"),
        validation_dataset=output / "validation",
        test_dataset=output / "test",
        features=output / "features.json",
    )
    validation = load_data(config, config.validation_dataset)
    test = load_data(config, config.test_dataset)
    validate_test_training(config, test, validation)
    assert prepare_files(cache, tags, output, users=1) == ready
    with pytest.raises(ValueError, match="入力・処理条件"):
        prepare_files(cache, tags, output, users=2)
    with (output / "features.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="checksum"):
        verify_prepared(output)


def test_preparation_failure_does_not_publish(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pytest.importorskip("duckdb")
    cache, tags = tmp_path / "events.parquet", tmp_path / "tags.bz2"
    pq.write_table(
        pa.table({"user_id": ["u"], "item_id": ["x"], "event_time": [datetime(2017, 4, 1)]}), cache
    )
    tags.write_bytes(b"unused")
    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="Train"):
        prepare_files(cache, tags, output, users=1)
    assert not output.exists()
