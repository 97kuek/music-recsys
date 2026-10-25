"""原版の引用符・ID・出典を維持し、失敗時に対応表を公開しない。"""

import csv
import json

import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.datasets.music4all_metadata import prepare_metadata

pq = pytest.importorskip("pyarrow.parquet", reason="install the data dependency group")


def inputs(tmp_path, rows=None, items=None):
    source = tmp_path / "source"
    source.mkdir()
    information = source / "id_information.csv"
    rows = (
        rows
        if rows is not None
        else [
            ["b", 'Artist "B", ensemble', 'Title "B"', "?"],
            ["a", "Artist A", "曲名, A", "-"],
        ]
    )
    with information.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["id", "artist", "song", "album_name"])
        writer.writerows(rows)
    (source / "source-manifest.json").write_text(
        json.dumps(
            {
                "format": "music4all-original-source-verification-v1",
                "status": "verified",
                "files": {
                    information.name: {
                        "sha256": file_sha256(information),
                        "bytes": information.stat().st_size,
                        "rows": len(rows),
                    }
                },
            }
        )
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([{"item_id": item} for item in (items or ["a", "b"])]))
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "mode": "music4all",
                "catalog": {"path": catalog.name, "sha256": file_sha256(catalog)},
            }
        )
    )
    return source, config


def test_metadata_preserves_original_strings_and_catalog_order(tmp_path):
    source, config = inputs(tmp_path)
    output = tmp_path / "metadata"
    manifest_path = prepare_metadata(source, config, output)
    expected = [
        dict(item_id="a", title="曲名, A", artists="Artist A", album_name="-"),
        dict(item_id="b", title='Title "B"', artists='Artist "B", ensemble', album_name="?"),
    ]
    with (output / "tracks.csv").open(encoding="utf-8", newline="") as stream:
        assert list(csv.DictReader(stream)) == expected
    assert pq.read_table(output / "tracks.parquet").to_pylist() == expected
    manifest = json.loads(manifest_path.read_text())
    assert manifest["matched_items"] == 2
    assert manifest["sources"]["information"]["sha256"] == file_sha256(
        source / "id_information.csv"
    )
    for name, ref in manifest["files"].items():
        assert file_sha256(output / name) == ref["sha256"]
    with pytest.raises(FileExistsError):
        prepare_metadata(source, config, output)


@pytest.mark.parametrize("changed", ["source", "catalog"])
def test_corrupted_input_is_not_published(tmp_path, changed):
    source, config = inputs(tmp_path)
    path = source / "id_information.csv" if changed == "source" else tmp_path / "catalog.json"
    with path.open("a") as stream:
        stream.write(" ")
    output = tmp_path / "metadata"
    with pytest.raises(ValueError, match="checksum"):
        prepare_metadata(source, config, output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("rows", "items", "message"),
    [
        ([["a", "Artist", "Title", "Album"]], ["A"], "missing"),
        ([["a", "Artist", "Title", "Album"]] * 2, ["a"], "duplicate"),
        ([["a", "Artist | Name", "Title", "Album"]], ["a"], "separator"),
    ],
)
def test_ambiguous_or_missing_metadata_is_not_published(tmp_path, rows, items, message):
    source, config = inputs(tmp_path, rows, items)
    output = tmp_path / "metadata"
    with pytest.raises(ValueError, match=message):
        prepare_metadata(source, config, output)
    assert not output.exists()


def test_write_failure_does_not_publish_partial_metadata(tmp_path, monkeypatch):
    source, config = inputs(tmp_path)
    output = tmp_path / "metadata"

    def fail(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(pq, "write_table", fail)
    with pytest.raises(OSError, match="write failure"):
        prepare_metadata(source, config, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".metadata-*"))
