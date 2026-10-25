import json
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
from listening_support import write_partitions


def make_study(root):
    from src.infrastructure.datasets.temporal import TemporalConfig
    from src.interfaces.cli.listening_export import export_listening_phase

    files = write_partitions(root / "source")
    validation = pq.read_table(files["validation"])
    pq.write_table(
        pa.Table.from_pylist(
            [
                *validation.to_pylist(),
                {"user_id": "v", "item_id": "a", "event_time": datetime(2017, 4, 1)},
                {"user_id": "v", "item_id": "b", "event_time": datetime(2017, 4, 1)},
            ],
            schema=validation.schema,
        ),
        files["validation"],
    )
    table = pq.read_table(files["test"])
    pq.write_table(
        pa.Table.from_pylist(
            [
                *table.to_pylist(),
                {"user_id": "v", "item_id": "cold_item", "event_time": datetime(2018, 9, 21)},
            ],
            schema=table.schema,
        ),
        files["test"],
    )
    for phase in ("validation", "test"):
        export_listening_phase(
            files,
            root / phase,
            TemporalConfig(),
            phase=phase,
            provenance={"snapshot_id": "fixture", "split_id": "split", "dataset_key": "fixture"},
        )

    def config(*, metric="graded_ndcg", task="repeat", day_weight=1.0, window=90):
        from src.infrastructure.research.search import load_study_config

        path = root / "study.toml"
        path.write_text(
            '[experiment]\nname="listening-grid"\nprotocol="listening-v2"\nvalidation_dataset="validation"\ntest_dataset="test"\noutput="runs"\n[model]\nname="itemcf"\n[search]\nstage="A"\nseeds=[0,42]\nmax_trials=4\n[search.grid]\nneighbors=[1,2]\n[evaluation]\nwindow_days=[30,90,180]\nday_weights=[0.0,1.0,2.0]\n'
            f"primary_window_days={window}\nprimary_day_weight={day_weight}\n[selection]\nk=10\n"
            + (f'metric="{metric}"\n' if metric else "")
            + (f'task="{task}"\n' if task else "")
        )
        return load_study_config(path)

    return config


def update_manifest_file(bundle, role):
    from src.infrastructure.datasets.listening_data import file_sha256

    target = bundle / "manifest.json"
    raw = json.loads(target.read_text())
    path = bundle / raw["files"][role]["path"]
    raw["files"][role].update(
        sha256=file_sha256(path),
        byte_size=path.stat().st_size,
        row_count=pq.ParquetFile(path).metadata.num_rows,
    )
    target.write_text(json.dumps(raw))
