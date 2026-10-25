import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.application.recommendation import RecommendationService
from src.domain.catalog import (
    CatalogTrack,
    PlaybackAvailability,
    PlaybackContext,
    TrackCatalog,
    TrackLink,
    TrackRef,
    TrackSource,
)
from src.domain.recommendation.contracts import RecommendationRequest
from src.domain.recommendation.models import FixedStrategy
from src.domain.recommendation.profiles import InitialProfile
from src.domain.study.events import SessionLog, StudySession
from src.infrastructure.models.artifacts import export_model, load_model
from src.infrastructure.recommenders.als import als_solve
from src.infrastructure.recommenders.full_als import FullALS
from src.infrastructure.recommenders.full_bpr import FullBPR
from src.infrastructure.recommenders.full_itemcf import FullItemCF
from src.infrastructure.recommenders.full_popularity import FullPopularity


@pytest.fixture
def learning(tmp_path):
    path = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id=u, item_id=i, play_count=n)
                for u, i, n in [
                    ("u", "a", 10),
                    ("u", "b", 1),
                    ("v", "b", 2),
                    ("v", "c", 5),
                    ("w", "c", 2),
                    ("w", "d", 1),
                ]
            ]
        ),
        path,
    )
    return path


def trained(name, learning):
    if name in ("plays", "users"):
        return FullPopularity(learning, basis="play_count" if name == "plays" else "user_count")
    if name == "itemcf":
        return FullItemCF(learning, neighbors=2, progress=lambda _: None)
    return {"als": FullALS, "bpr": FullBPR}[name](
        learning, factors=3, iterations=2, seed=42, progress=lambda _: None
    )


@pytest.mark.parametrize("name", ["plays", "users", "itemcf", "als", "bpr"])
def test_full_model_roundtrip_matches_benchmark_rank_without_training(
    name, learning, tmp_path, monkeypatch
):
    model = trained(name, learning)
    destination = tmp_path / "model"
    checksum = export_model(model, destination, version_id="v1")
    requests = [
        RecommendationRequest(task, "u", ("a", "b"), ("a", "b", "c", "d"), 3, task)
        for task in ("new", "repeat")
    ]
    expected = []
    for request in requests:
        if name == "itemcf":
            ranking = model.rank(set(request.history), request.task, request.top_k)
        elif name in ("als", "bpr"):
            ranking = model.rank(request.user_id, set(request.history), request.task, request.top_k)
        else:
            allowed = [
                i
                for i, item in enumerate(model.items)
                if (item in request.history) == (request.task == "repeat")
            ]
            ranking = [
                str(model.items[i])
                for i in sorted(allowed, key=lambda i: (-model.scores[i], str(model.items[i])))[
                    : request.top_k
                ]
            ]
        expected.append(tuple(ranking))

    def fail(*args, **kwargs):
        raise AssertionError("load/inference must not train")

    for cls in (FullPopularity, FullItemCF, FullALS, FullBPR):
        monkeypatch.setattr(cls, "__init__", fail)
    spec, restored = load_model(destination, expected_manifest_sha256=checksum)
    service = RecommendationService(((spec, restored),))
    for request, ranking in zip(requests, expected, strict=True):
        execution = service.recommend(request, FixedStrategy("v1"))
        assert execution.response.item_ids == ranking
        subset = replace(request, candidate_ids=("b", "d", "out-of-training"))
        assert set(restored.recommend(subset).item_ids) <= {"b", "d"}
    for array in restored.arrays.values():
        assert not array.flags.writeable
    assert spec.parameters == model.training_provenance["parameters"]
    assert len(spec.training_ref) == 64
    with pytest.raises(FileExistsError):
        export_model(model, destination, version_id="v1")


@pytest.mark.parametrize(
    "tamper", ["array", "manifest", "missing", "dimensions", "catalog", "path"]
)
def test_load_rejects_corruption(tamper, learning, tmp_path):
    from src.infrastructure.datasets.listening_data import file_sha256

    directory = tmp_path / "model"
    checksum = export_model(trained("als", learning), directory, version_id="v1")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if tamper == "array":
        with (directory / "item_factors.npy").open("ab") as stream:
            stream.write(b"corrupt")
    elif tamper == "missing":
        (directory / "users.npy").unlink()
    elif tamper == "manifest":
        manifest["spec"]["seed"] = 99
    elif tamper == "catalog":
        manifest["spec"]["catalog_ref"] = "wrong"
    elif tamper == "path":
        manifest["files"]["../escape"] = manifest["files"].pop("items")
    elif tamper == "dimensions":
        path = directory / "item_factors.npy"
        np.save(path, np.zeros((1, 3)), allow_pickle=False)
        manifest["files"]["item_factors"].update(
            sha256=file_sha256(path), shape=[1, 3], dtype="float64"
        )
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, FileNotFoundError)):
        load_model(directory)
    with pytest.raises(ValueError, match="manifest hash"):
        load_model(directory, expected_manifest_sha256=checksum)


def test_cold_start_routes_and_fold_in(learning, tmp_path):
    profile = InitialProfile("new-participant", ("a",), ("artist:1",))
    request = profile.to_request(
        "r", ("a", "b", "c", "d", "new-track"), artist_tracks={"artist:1": ("b",)}
    )
    assert request.history == ("a", "b") and request.task == "new"
    for name in ("itemcf", "als", "bpr"):
        model = trained(name, learning)
        directory = tmp_path / name
        export_model(model, directory, version_id=name)
        spec, restored = load_model(directory)
        assert restored.route(request) == (
            "itemcf_history" if name == "itemcf" else "popularity_fallback"
        )
        if name == "itemcf":
            assert restored.recommend(request).item_ids == tuple(
                model.rank(set(request.history), "new")
            )
        else:
            assert restored.recommend(request).item_ids == ("c", "d")
        assert restored.route(replace(request, history=("new-track",))) == "popularity_fallback"
        if name == "als":
            spec, folded = load_model(directory, als_fold_in=True)
            before = folded.arrays["item_factors"].copy()
            vector = als_solve(
                model.item_factors,
                [(0, 1), (1, 1)],
                alpha=10,
                regularization=0.1,
                confidence_transform="log1p",
            )
            expected = np.array([2, 3])[
                np.lexsort(
                    (np.array([2, 3]), -model.plays[[2, 3]], -(model.item_factors[[2, 3]] @ vector))
                )
            ]
            assert folded.recommend(request).item_ids == tuple(model.items[expected])
            np.testing.assert_array_equal(before, folded.arrays["item_factors"])
            execution = RecommendationService(((spec, folded),)).recommend(
                request, FixedStrategy(name)
            )
            assert execution.inference_route == "als_fold_in_binary_seeds"
            now = datetime(2026, 1, 1, tzinfo=UTC)
            log = SessionLog(
                StudySession(
                    "s",
                    request.user_id,
                    "study",
                    "v1",
                    "cold-als",
                    "AB",
                    1,
                    "v1",
                    now,
                    now + timedelta(days=1),
                )
            )
            log.record(execution, now=now)
            assert (
                log.export(now=now)["requests"][0]["inference_route"] == execution.inference_route
            )


def test_bpr_inactive_user_and_bias_survive(learning, tmp_path):
    path = tmp_path / "dense.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [dict(user_id="u", item_id=i, play_count=n) for i, n in [("a", 1), ("b", 5)]]
        ),
        path,
    )
    model = trained("bpr", path)
    export_model(model, tmp_path / "model", version_id="v1")
    _, restored = load_model(tmp_path / "model")
    assert restored.arrays["item_factors"].shape[1] == 4
    request = RecommendationRequest("r", "u", ("a", "b"), ("a", "b"), 20, "repeat")
    assert restored.recommend(request).item_ids == tuple(model.rank("u", {"a", "b"}, "repeat"))


def test_catalog_link_requires_recording_confirmation_and_scoped_availability():
    original = CatalogTrack("m4a", (TrackSource(TrackRef("music4all", "m4a"), "v2"),))
    catalog = TrackCatalog((original,))
    source = TrackSource(TrackRef("provider", "external"), "revision", title="Song")
    link = TrackLink("m4a", source, "same_recording", "review:1")
    updated = catalog.with_link(link)
    assert updated.resolve(source.ref) == "m4a" and catalog.resolve(source.ref) is None
    for relation in ("unconfirmed", "different_version"):
        with pytest.raises(ValueError):
            catalog.with_link(replace(link, relation=relation))
    context = PlaybackContext(source.ref, "JP", "u", "preview")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    availability = PlaybackAvailability(context, "unavailable", now, now + timedelta(minutes=1))
    presentation = updated.presentation("m4a", context, availability, at=now)
    assert presentation.displayable and presentation.playback_status == "unavailable"
    assert (
        updated.presentation(
            "m4a", context, availability, at=now + timedelta(minutes=1)
        ).playback_status
        == "unknown"
    )
    with pytest.raises(ValueError, match="context"):
        updated.presentation("m4a", replace(context, mode="full"), availability, at=now)


def test_initial_profile_does_not_reuse_matching_training_user(learning, tmp_path):
    model = trained("als", learning)
    export_model(model, tmp_path / "model", version_id="v1")
    _, restored = load_model(tmp_path / "model", als_fold_in=True)
    request = InitialProfile("u", ("a",)).to_request("r", ("a", "b", "c"))
    assert restored.route(request) == "als_fold_in_binary_seeds"
    with pytest.raises(ValueError, match="repeat"):
        replace(request, task="repeat")


@pytest.mark.parametrize("kind", ["als", "bpr"])
def test_gpu_trained_artifact_roundtrip(kind, learning, tmp_path):
    import os

    if os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1":
        pytest.skip("GPU test requires explicit MUSIC_RECSYS_TEST_GPU=1")
    cls = {"als": FullALS, "bpr": FullBPR}[kind]
    model = cls(learning, factors=32, iterations=2, use_gpu=True, progress=lambda _: None)
    checksum = export_model(model, tmp_path / kind, version_id=f"{kind}-gpu")
    _, restored = load_model(tmp_path / kind, expected_manifest_sha256=checksum)
    for task in ("new", "repeat"):
        request = RecommendationRequest(task, "u", ("a", "b"), ("a", "b", "c", "d"), 20, task)
        assert restored.recommend(request).item_ids == tuple(model.rank("u", {"a", "b"}, task))
    assert restored.arrays["item_factors"].dtype == model.item_factors.dtype


def test_itemcf_cache_retains_training_origin_and_supports_legacy_cache(learning, tmp_path):
    cache = tmp_path / "cache"
    original = FullItemCF(learning, neighbors=2, cache_dir=cache, progress=lambda _: None)
    cached = FullItemCF(learning, neighbors=2, cache_dir=cache, progress=lambda _: None)
    assert cached.cache_hit
    assert (
        cached.training_provenance["cache_origin"]["training_provenance"]
        == original.training_provenance
    )
    cache_file = cache / "cosine-100.npz"
    with np.load(cache_file, allow_pickle=False) as saved:
        legacy = {name: saved[name] for name in saved.files if name != "training_provenance"}
    np.savez_compressed(cache_file, **legacy)
    restored = FullItemCF(learning, neighbors=2, cache_dir=cache, progress=lambda _: None)
    assert restored.training_provenance["cache_origin"]["training_provenance"] is None
    export_model(restored, tmp_path / "model", version_id="legacy-cache")
    _, deployed = load_model(tmp_path / "model")
    request = InitialProfile("new", ("a",)).to_request("r", ("a", "b", "c", "d"))
    assert deployed.recommend(request).item_ids == tuple(original.rank({"a"}, "new"))
