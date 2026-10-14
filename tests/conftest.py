"""合成配布ファイルで取り込みを検証する。実データはテストで使用しない。"""

import bz2
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest


@pytest.fixture
def music4all_factory(tmp_path):
    pytest.importorskip("pyarrow", reason="install the data dependency group")
    pytest.importorskip("duckdb", reason="install the data dependency group")
    from music_recsys.music4all import convert_events

    def create(name="source", tag_rows=None, events_data=None):
        root = tmp_path / name
        root.mkdir()
        tags = root / "id_tags_dict.tsv.bz2"
        events = root / "userid_trackid_timestamp.tsv.bz2"
        events.write_bytes(
            bz2.compress(
                events_data
                if events_data is not None
                else b"user_id\ttrack_id\ttimestamp\n"
                b"2\t03\t2020-01-01 00:00:00\n"
                b"0002\t02\t2020-01-02 00:00:00\n"
                b"0010\t01\t2020-01-03 00:00:00\n"
                b"2\t99\t2020-01-04 00:00:00\n"
            )
        )
        rows = (
            tag_rows
            if tag_rows is not None
            else [
                ("01", "{}"),
                ("02", "{'rock': 80}"),
                ("02", "{'rock': 80}"),
                ("99", "{'pop': 50}"),
            ]
        )
        text = io.StringIO()
        writer = csv.writer(text, delimiter="\t", lineterminator="\n")
        writer.writerow(["id", "(tag, weight)"])
        writer.writerows(rows)
        tags.write_bytes(bz2.compress(text.getvalue().encode()))
        record = root / "record.json"
        record.write_text(
            json.dumps(
                {
                    "id": 15394646,
                    "files": [
                        {
                            "key": p.name,
                            "size": p.stat().st_size,
                            "checksum": "md5:" + hashlib.md5(p.read_bytes()).hexdigest(),
                            "links": {"self": "https://example.test/" + p.name},
                        }
                        for p in (tags, events)
                    ],
                }
            )
        )
        cache = root / "events.parquet"
        convert_events(events, cache)
        return {"record": record, "tags": tags, "events": events, "cache": cache}

    return create


@pytest.fixture
def study(tmp_path):
    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/toy/dataset.json").read_text()
    )
    raw["provenance"] = {
        "phase": "validation",
        "snapshot_id": "fixture",
        "split_id": "fixture-split",
        "training_periods": ["train"],
    }
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps(raw))
    test = json.loads(json.dumps(raw))
    test["provenance"].update(phase="test", training_periods=["train", "validation"])
    by_id = {r["request_id"]: r for r in raw["requests"]}
    for truth in raw["ground_truth"]:
        user = by_id[truth["request_id"]]["user_id"]
        test["train_interactions"].extend(
            {"user_id": user, "item_id": item} for item in truth["relevant_item_ids"]
        )
    for r in test["requests"]:
        r["history"] = sorted(
            {x["item_id"] for x in test["train_interactions"] if x["user_id"] == r["user_id"]}
        )
    for truth in test["ground_truth"]:
        seen = set(by_id[truth["request_id"]]["history"])
        truth["relevant_item_ids"] = [x for x in raw["catalog_ids"] if x not in seen][:1]
    (tmp_path / "test.json").write_text(json.dumps(test))
    config = tmp_path / "study.toml"

    def write(
        grid="reverse = [false, true]", objective=True, budget=20, seeds="[0, 42]", stage="A"
    ):
        config.write_text(
            '[experiment]\nname="fixture"\nvalidation_dataset="validation.json"\n'
            'test_dataset="test.json"\noutput="runs"\n'
            f'[model]\nname="fixture"\n[search]\nstage="{stage}"\nseeds={seeds}\n'
            f"max_trials={budget}\n[search.grid]\n{grid}\n"
            + ('[selection]\nmetric="ndcg"\nk=10\ndirection="maximize"\n' if objective else "")
        )
        from music_recsys.experiment_search import load_study_config

        return load_study_config(config)

    return write
