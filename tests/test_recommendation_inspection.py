"""ユーザー切替・モデルの根拠・ローカル音源の対応を検証する。"""

import wave
from dataclasses import replace
from pathlib import Path

import pytest

from src.domain.recommendation.contracts import GroundTruth
from src.infrastructure.datasets.json_dataset import load_dataset
from src.interfaces.notebook import recommendation_inspection as inspection

DATA = Path(__file__).resolve().parents[1] / "tests/fixtures/examples/toy/dataset.json"


def test_selected_user_history_and_popularity_explanation():
    result = inspection.inspect_recommendation(
        load_dataset(DATA, top_k=2), model="popularity", request_id="r1"
    )
    assert result.request.user_id == "u1"
    assert [row["item_id"] for row in result.history] == ["track_a", "track_b"]
    assert result.response.item_ids == ("track_c", "track_d")
    assert [row["score"] for row in result.recommendations] == [2, 1]
    assert all(row["reason"] for row in result.recommendations)
    assert [row["item_id"] for row in result.candidates] == [
        "track_c",
        "track_d",
        "track_e",
        "track_f",
    ]
    assert not any(row["in_history"] for row in result.candidates)
    assert result.model == "popularity"


def test_cold_user_and_ground_truth_do_not_change_inference():
    dataset = load_dataset(DATA, top_k=10)
    result = inspection.inspect_recommendation(dataset, model="popularity", request_id="r4")
    changed = replace(
        dataset, ground_truth=tuple(GroundTruth(r.request_id, ()) for r in dataset.requests)
    )
    other = inspection.inspect_recommendation(changed, model="popularity", request_id="r4")
    assert result.response == other.response
    assert result.history == []
    assert result.response.item_ids == (
        "track_a",
        "track_b",
        "track_c",
        "track_d",
        "track_e",
        "track_f",
    )
    assert result.recommendations[-1]["score"] == 0


@pytest.mark.parametrize("model", ["llm", "agent", "typo"])
def test_unavailable_model_does_not_silently_use_popularity(model):
    with pytest.raises(ValueError, match=model):
        inspection.inspect_recommendation(load_dataset(DATA, top_k=2), model=model, request_id="r1")


def test_unknown_request_rejected():
    with pytest.raises(ValueError, match="request"):
        inspection.inspect_recommendation(
            load_dataset(DATA, top_k=2), model="popularity", request_id="unknown"
        )


def test_audio_uses_only_the_explicit_track_mapping(tmp_path):
    pytest.importorskip("IPython")
    path = tmp_path / "test-tone.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 80)
    player = inspection.audio_player("track_c", {"track_c": path})
    assert player.data == path.read_bytes()
    assert player.autoplay is False
    assert inspection.audio_player("track_d", {"track_c": path}) is None
    with pytest.raises(FileNotFoundError):
        inspection.audio_player("track_c", {"track_c": tmp_path / "missing.wav"})
