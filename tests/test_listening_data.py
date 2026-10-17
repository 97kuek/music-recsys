import json

import pytest
from listening_support import write_bundle


def config():
    from src.listening_types import ListeningConfig

    return ListeningConfig(window_days=(90,), ks=(1, 10, 20))


def test_loader_preserves_learning_and_keeps_truth_out_of_requests(tmp_path):
    import duckdb

    from src.listening_data import load_listening_dataset, read_user_observations

    data = load_listening_dataset(write_bundle(tmp_path / "bundle"), config())
    requests = {(r.user_id, r.task): r for r in data.requests}
    assert requests["u", "new"].candidate_ids == ("b",)
    assert requests["u", "repeat"].candidate_ids == ("a",)
    assert len(data.train_interactions) == 3
    assert not hasattr(requests["u", "new"], "observations")
    with duckdb.connect() as connection:
        connection.read_parquet(str(data.observations_path)).create_view("observations")
        assert read_user_observations(connection, "u")[0].evaluation_active_days == 2
        assert read_user_observations(connection, "unknown") == ()


@pytest.mark.parametrize("path", ["../outside.parquet", "/tmp/outside.parquet"])
def test_loader_rejects_escaping_references(tmp_path, path):
    from src.listening_data import load_listening_dataset

    bundle = write_bundle(tmp_path / "bundle")
    target = bundle / "manifest.json"
    raw = json.loads(target.read_text())
    raw["files"]["learning"]["path"] = path
    target.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_listening_dataset(bundle, config())


@pytest.mark.parametrize("mutation", ["hash", "duplicate", "count", "cold", "days"])
def test_loader_rejects_corrupt_or_inconsistent_observations(tmp_path, mutation):
    from src.listening_data import load_listening_dataset

    obs = {
        "user_id": "u",
        "item_id": "b",
        "window_days": 90,
        "learning_play_count": 0,
        "learning_active_days": 0,
        "evaluation_play_count": 3,
        "evaluation_active_days": 2,
    }
    if mutation == "count":
        obs["learning_play_count"] = 1
        obs["learning_active_days"] = 1
    if mutation == "cold":
        obs["user_id"] = "cold"
    if mutation == "days":
        obs["evaluation_active_days"] = 4
    bundle = write_bundle(
        tmp_path / "bundle", observations=[obs, obs] if mutation == "duplicate" else [obs]
    )
    if mutation == "hash":
        with (bundle / "observations.parquet").open("ab") as stream:
            stream.write(b"corruption")
    with pytest.raises(ValueError):
        load_listening_dataset(bundle, config())


def test_real_parquet_counts_enforce_user_limit(tmp_path):
    from src.listening_data import load_listening_dataset

    learning = [
        {"user_id": f"u{i}", "item_id": "a", "play_count": 1, "active_days": 1} for i in range(2001)
    ]
    bundle = write_bundle(tmp_path / "bundle", learning=learning, observations=[])
    with pytest.raises(ValueError, match="2000"):
        load_listening_dataset(bundle, config())


def test_learning_event_limit_is_checked_before_reconstructing_interactions(tmp_path):
    from src.listening_data import load_listening_dataset

    bundle = write_bundle(
        tmp_path / "bundle",
        learning=[{"user_id": "u", "item_id": "a", "play_count": 5_000_001, "active_days": 1}],
        observations=[],
    )
    with pytest.raises(ValueError, match="5 million"):
        load_listening_dataset(bundle, config())


def test_positive_short_window_requires_observations_in_every_longer_window(tmp_path):
    from src.listening_data import load_listening_dataset
    from src.listening_types import ListeningConfig

    bundle = write_bundle(tmp_path / "bundle")
    path = bundle / "manifest.json"
    raw = json.loads(path.read_text())
    raw["window_days"] = [90, 180]
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="window"):
        load_listening_dataset(bundle, ListeningConfig(window_days=(90, 180)))


@pytest.mark.parametrize("weight", [-1, True, float("nan"), float("inf")])
def test_nonfinite_or_invalid_gain_configuration_is_rejected(weight):
    from src.listening_types import ListeningConfig

    with pytest.raises(ValueError):
        ListeningConfig(day_weights=(weight,))
