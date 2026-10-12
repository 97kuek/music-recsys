"""合成配布ファイルで取り込みを検証する。実データはテストで使用しない。"""

import bz2
import csv
import hashlib
import io
import json

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
