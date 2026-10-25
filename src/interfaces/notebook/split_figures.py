"""保存済み時系列分割のイベント比率をNotebookで表示する。"""

import json
from datetime import datetime
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256


def load_split_frame(validation_dataset, *, exclude_before=None):
    """選択されたValidationと同じ出典の分割集計のみを読み込む。"""
    import pandas as pd

    validation_dataset = Path(validation_dataset)
    root = validation_dataset.parent
    ready = json.loads((root / "ready.json").read_text())
    for relative in ("source/summary.json", f"{validation_dataset.name}/manifest.json"):
        expected = ready["files"].get(relative)
        if expected is None or file_sha256(root / relative) != expected:
            raise ValueError(f"Split source checksum mismatch: {relative}")
    manifest = json.loads((validation_dataset / "manifest.json").read_text())
    summary = json.loads((root / "source/summary.json").read_text())
    if (
        manifest["protocol"] != "listening-v2"
        or manifest["phase"] != "validation"
        or manifest["source"]["split_id"] != ready["split_id"]
        or manifest["source"]["snapshot_id"] != ready["snapshot_id"]
        or manifest["temporal"] != summary["temporal"]
    ):
        raise ValueError("Split source does not match the selected Validation input")
    excluded_events = 0
    if exclude_before is not None:
        summary, excluded_events = _filtered_summary(root, ready, summary, exclude_before)
    counts = [summary["partitions"][phase]["events"] for phase in ("train", "validation", "test")]
    tracks = [summary["partitions"][phase]["tracks"] for phase in ("train", "validation", "test")]
    total = summary["source"]["events"]
    if (
        any(type(count) is not int or count < 0 for count in counts)
        or any(type(count) is not int or count < 0 for count in tracks)
        or type(total) is not int
        or total <= 0
        or sum(counts) != total
    ):
        raise ValueError("Split event counts must be measured, nonnegative and exhaustive")
    boundaries = summary["temporal"]
    frame = pd.DataFrame(
        {
            "partition": ["Train", "Validation", "Test"],
            "events": counts,
            "share_pct": [100 * count / total for count in counts],
            "tracks": tracks,
            "start": [
                summary["source"]["min_time"],
                boundaries["train_end"],
                boundaries["validation_end"],
            ],
            "end": [
                boundaries["train_end"],
                boundaries["validation_end"],
                summary["source"]["max_time"],
            ],
        }
    )
    frame.attrs["dataset_id"] = manifest["dataset_id"]
    frame.attrs["exclude_before"] = exclude_before
    frame.attrs["excluded_events"] = excluded_events
    return frame


def _filtered_summary(root, ready, summary, exclude_before):
    """原本を変更せず、日時除外後の表示用集計を元イベントから再計算する。"""
    import duckdb

    cutoff = datetime.fromisoformat(exclude_before)
    train_end = datetime.fromisoformat(summary["temporal"]["train_end"])
    validation_end = datetime.fromisoformat(summary["temporal"]["validation_end"])
    if cutoff.tzinfo is not None or cutoff >= train_end:
        raise ValueError("Display filter must be timezone-naive and precede the Train cutoff")
    events = root / "events.parquet"
    if file_sha256(events) != ready["files"].get("events.parquet"):
        raise ValueError("Split source checksum mismatch: events.parquet")
    with duckdb.connect(config={"memory_limit": "1GB", "threads": 2}) as connection:
        rows = connection.execute(
            """SELECT CASE WHEN event_time < ? THEN 'train'
                WHEN event_time < ? THEN 'validation' ELSE 'test' END AS partition,
                count(*) AS events, count(DISTINCT item_id) AS tracks,
                min(event_time), max(event_time)
                FROM read_parquet(?) WHERE event_time >= ? GROUP BY partition""",
            [train_end, validation_end, str(events), cutoff],
        ).fetchall()
    if {row[0] for row in rows} != {"train", "validation", "test"}:
        raise ValueError("Display filter must retain events in all three partitions")
    partitions = {phase: {"events": count, "tracks": tracks} for phase, count, tracks, _, _ in rows}
    total = sum(row[1] for row in rows)
    return {
        "temporal": summary["temporal"],
        "partitions": partitions,
        "source": {
            "events": total,
            "min_time": min(row[3] for row in rows).isoformat(),
            "max_time": max(row[4] for row in rows).isoformat(),
        },
    }, summary["source"]["events"] - total


def split_figure(frame):
    """幅がイベント数に比例する積み上げ棒グラフを1枚返す。"""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    fig, ax = plt.subplots(figsize=(14, 3.4))
    left = 0
    for row, color in zip(frame.itertuples(), ("#3566A8", "#00877D", "#C86A22"), strict=True):
        ax.barh(0, row.share_pct, left=left, height=0.58, color=color)
        ax.text(
            left + row.share_pct / 2,
            0.08,
            f"{row.partition}\n{row.share_pct:.1f}%",
            ha="center",
            va="center",
            color="white",
            fontsize=12,
            fontweight="bold",
            linespacing=1.6,
        )
        ax.text(
            left + row.share_pct / 2,
            -0.19,
            f"{row.tracks:,} tracks",
            ha="center",
            va="center",
            color="white",
            fontsize=10.5,
        )
        ax.plot([left, left], [-0.30, -0.43], color="#596675", linewidth=1)
        ax.text(
            left,
            -0.52,
            row.start[:10],
            ha="left" if row.partition == "Train" else "center",
            va="top",
            fontsize=10,
        )
        left += row.share_pct
    ax.plot([100, 100], [-0.30, -0.43], color="#596675", linewidth=1)
    ax.text(100, -0.52, frame.iloc[-1].end[:10], ha="right", va="top", fontsize=10)
    ax.set(xlim=(0, 100), ylim=(-0.8, 0.65), yticks=[], xticks=range(0, 101, 20))
    ax.set_xlabel("Share of listening events", fontsize=13, labelpad=12)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.tick_params(axis="x", labelsize=11, length=0, pad=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.text(0, 0.44, "Earlier", color="#596675", fontsize=11)
    ax.text(100, 0.44, "Later", ha="right", color="#596675", fontsize=11)
    fig.subplots_adjust(left=0.04, right=0.97, top=0.94, bottom=0.27)
    return fig
