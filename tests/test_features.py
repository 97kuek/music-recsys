import json

import pytest


def test_features_are_snapshot_bound_and_legacy_format_still_works(tmp_path):
    from src.features import load_features

    p = tmp_path / "features.json"
    p.write_text(
        json.dumps(
            {"dataset_id": "toy", "snapshot_id": "s1", "features": {"a": {"rock": 1.0}, "b": {}}}
        )
    )
    assert load_features(p, dataset_id="toy", snapshot_id="s1") == {"a": {"rock": 1.0}, "b": {}}
    with pytest.raises(ValueError, match="出典"):
        load_features(p, dataset_id="toy", snapshot_id="s2")


@pytest.mark.parametrize(
    "vector",
    [
        {"rock": True},
        {"rock": -1},
        {"rock": float("nan")},
        {"rock": float("inf")},
        {"": 1},
        [],
        None,
    ],
)
def test_feature_values_reject_invalid_data(tmp_path, vector):
    from src.features import load_features

    p = tmp_path / "features.json"
    p.write_text(json.dumps({"dataset_id": "toy", "snapshot_id": "s", "features": {"a": vector}}))
    with pytest.raises(ValueError):
        load_features(p, dataset_id="toy", snapshot_id="s")


def test_exported_feature_content_checksum_rejects_tampering(tmp_path):
    from src.features import load_features, write_features

    p = tmp_path / "features.json"
    raw = write_features(
        p,
        dataset_id="toy",
        snapshot_id="s",
        features={"a": {"rock": 2}},
        source={"metadata_sha256": "0" * 64},
    )
    assert raw["source"]["metadata_sha256"] == "0" * 64
    assert load_features(p, dataset_id="toy", snapshot_id="s")["a"]["rock"] == 2
    changed = json.loads(p.read_text())
    changed["features"]["a"]["rock"] = 8
    p.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="checksum"):
        load_features(p, dataset_id="toy", snapshot_id="s")
    with pytest.raises(FileExistsError):
        write_features(p, dataset_id="toy", snapshot_id="s", features={}, source={})


def test_unrepresentable_numeric_features_are_rejected_as_validation_errors(tmp_path):
    from src.features import load_features

    p = tmp_path / "huge.json"
    p.write_text(
        json.dumps({"dataset_id": "toy", "snapshot_id": "s", "features": {"a": {"rock": 10**400}}})
    )
    with pytest.raises(ValueError):
        load_features(p, dataset_id="toy", snapshot_id="s")
