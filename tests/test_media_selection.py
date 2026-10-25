import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from src.bootstrap import create_runtime
from src.interfaces.cli.online_bundle import package, verify
from src.interfaces.cli.online_media import prepare
from src.interfaces.cli.online_prepare import demo


@pytest.fixture
def media(tmp_path):
    path = demo(tmp_path / "source")
    config = json.loads(path.read_text())
    config.update(mode="music4all", audio={})
    path.write_text(json.dumps(config))
    metadata = tmp_path / "metadata.csv"
    metadata.write_text(
        "item_id,title,artists\nsample-00,First,Artist A\nsample-01,Second,Artist B\n"
    )
    permission = tmp_path / "permission.json"
    permission.write_text(
        json.dumps(
            {
                "web_preview": True,
                "access_scope": "restricted-study",
                "evidence_ref": "fixture-only",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "item_ids": [f"sample-{i:02}" for i in range(12)],
            }
        )
    )
    audio = tmp_path / "audio-source"
    audio.mkdir()
    for i in range(2):
        shutil.copyfile(path.parent / f"sample-{i:02}.wav", audio / f"sample-{i:02}.wav")
    return path, metadata, audio, permission


def test_selected_audio_and_catalog_define_same_candidates_for_every_model(media, tmp_path):
    source, metadata, audio, permission = media
    before = source.read_bytes()
    selected = ["sample-00", "sample-01"]
    config = prepare(*media, tmp_path / "study", item_ids=selected)
    runtime = create_runtime(config, database=tmp_path / "state.sqlite")
    assert set(runtime.catalog.tracks) == set(selected)
    assert all(set(items) == set(selected) for items in runtime.recommendations.candidates.values())
    prepared = json.loads(config.read_text())
    assert set(prepared["audio"]) == set(selected)
    assert len(list((config.parent / "audio").iterdir())) == 2
    assert source.read_bytes() == before
    assert (config.parent / "permission.json").read_bytes() == permission.read_bytes()


@pytest.mark.parametrize("items", [[], ["sample-00", "sample-00"], ["unpermitted"], [1]])
def test_invalid_selection_never_publishes_partial_media(media, tmp_path, items):
    output = tmp_path / "study"
    with pytest.raises(ValueError, match="selected item IDs"):
        prepare(*media, output, item_ids=items)
    assert not output.exists()


def test_no_expiry_survives_media_packaging_and_runtime_relocation(media, tmp_path):
    permission = media[3]
    record = json.loads(permission.read_text())
    record["expires_at"] = None
    permission.write_text(json.dumps(record))
    selected = ["sample-00", "sample-01"]
    prepared = prepare(*media, tmp_path / "study", item_ids=selected)
    config = package(prepared, tmp_path / "relocated")
    verify(config.parent)
    runtime = create_runtime(config, database=tmp_path / "state.sqlite")
    saved = json.loads(config.read_text())
    assert json.loads((config.parent / "permission.json").read_text())["expires_at"] is None
    assert all(entry["expires_at"] is None for entry in saved["audio"].values())
    for item in selected:
        assert runtime.catalog.track(item)["playback_status"] == "available"


@pytest.mark.parametrize(
    "expiry", ["missing", "", "2030-01-01T00:00:00", "2000-01-01T00:00:00+00:00"]
)
def test_missing_invalid_or_expired_permission_never_publishes_media(media, tmp_path, expiry):
    permission = media[3]
    record = json.loads(permission.read_text())
    if expiry == "missing":
        del record["expires_at"]
    else:
        record["expires_at"] = expiry
    permission.write_text(json.dumps(record))
    output = tmp_path / "study"
    with pytest.raises((KeyError, ValueError)):
        prepare(*media, output, item_ids=["sample-00", "sample-01"])
    assert not output.exists()


@pytest.mark.parametrize(
    "changed", [{"web_preview": False}, {"access_scope": "public"}, {"evidence_ref": ""}]
)
def test_no_expiry_still_requires_recorded_restricted_study_permission(media, tmp_path, changed):
    permission = media[3]
    record = json.loads(permission.read_text())
    record.update(expires_at=None, **changed)
    permission.write_text(json.dumps(record))
    output = tmp_path / "study"
    with pytest.raises(ValueError, match="permission"):
        prepare(*media, output, item_ids=["sample-00", "sample-01"])
    assert not output.exists()
