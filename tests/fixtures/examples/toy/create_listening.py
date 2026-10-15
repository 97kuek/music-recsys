"""操作確認用の合成イベントからv2 bundleを生成する。研究の性能結果には使わない。"""

import argparse
from datetime import datetime
from pathlib import Path


def create_example(output):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from music_recsys.listening_export import export_listening_phase
    from music_recsys.temporal import TemporalConfig

    output = Path(output)
    output.mkdir(parents=True)  # 既存のイベント・bundleへの上書きを拒否する。
    source = output / "source"
    source.mkdir()
    events = {
        "train": [
            ("u1", "a", "2017-03-27"),
            ("u1", "b", "2017-03-28"),
            ("u2", "b", "2017-03-27"),
            ("u2", "c", "2017-03-28"),
            ("u3", "a", "2017-03-27"),
            ("u3", "c", "2017-03-28"),
            ("u4", "d", "2017-03-28"),
        ],
        "validation": [
            ("u1", "c", "2017-04-01"),
            ("u1", "c", "2017-04-02"),
            ("u1", "a", "2017-04-01"),
            ("u1", "a", "2017-04-02"),
            ("u1", "a", "2017-04-03"),
            ("u2", "d", "2017-04-01"),
            ("u2", "b", "2017-04-01"),
            ("u3", "b", "2017-04-01"),
        ],
        "test": [
            ("u1", "d", "2018-09-22"),
            ("u2", "a", "2018-09-22"),
            ("u1", "a", "2018-09-22"),
            ("u2", "b", "2018-09-22"),
            ("u3", "b", "2018-09-23"),
        ],
    }
    schema = pa.schema(
        [("user_id", pa.string()), ("item_id", pa.string()), ("event_time", pa.timestamp("us"))]
    )
    files = {}
    for phase, rows in events.items():
        files[phase] = source / f"{phase}.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"user_id": user, "item_id": item, "event_time": datetime.fromisoformat(stamp)}
                    for user, item, stamp in rows
                ],
                schema=schema,
            ),
            files[phase],
        )
    for phase in ("validation", "test"):
        export_listening_phase(
            files,
            output / phase,
            TemporalConfig(),
            phase=phase,
            provenance={
                "dataset_key": "synthetic-listening-toy",
                "snapshot_id": "synthetic-listening-toy-v1",
                "split_id": "synthetic-temporal-v1",
            },
        )
    from music_recsys.features import write_features

    write_features(
        output / "features.json",
        dataset_id="synthetic-listening-toy",
        snapshot_id="synthetic-listening-toy-v1",
        source={"kind": "synthetic-tags"},
        features={
            "a": {"rock": 1.0},
            "b": {"pop": 1.0},
            "c": {"jazz": 1.0},
            "d": {"rock": 0.6, "jazz": 0.4},
        },
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/benchmarks/toy-listening"))
    args = parser.parse_args()
    print(create_example(args.output))


if __name__ == "__main__":
    main()
