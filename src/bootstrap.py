"""HTTP・配布検証から共有するcomposition root。具象Adapterをここで組み立てる。"""

import json
from pathlib import Path

from src.application.study.runtime import Runtime
from src.domain.recommendation.models import ModelSpec
from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.media.playback import AudioAsset, LocalPlayback, parse_expiry
from src.infrastructure.models.artifacts import load_model
from src.infrastructure.persistence.sessions import LocalStore
from src.infrastructure.recommenders.tag_seed import TagSeedRecommender


def create_runtime(config_path, *, database, study=None, scope=None):
    path = Path(config_path).resolve()
    config = json.loads(path.read_text())
    models = {}
    for entry in config["models"]:
        if entry.get("format") == "content-tags":
            feature_path = path.parent / entry["path"]
            spec_path = path.parent / entry["spec"]
            if (
                file_sha256(feature_path) != entry["sha256"]
                or file_sha256(spec_path) != entry["spec_sha256"]
            ):
                raise ValueError("content model checksum mismatch")
            spec = ModelSpec(**json.loads(spec_path.read_text()))
            if (
                spec.training_ref != entry["sha256"]
                or spec.catalog_ref != config["catalog"]["sha256"]
            ):
                raise ValueError("content model provenance mismatch")
            model = TagSeedRecommender(json.loads(feature_path.read_text()))
        else:
            spec, model = load_model(
                path.parent / entry["path"],
                expected_manifest_sha256=entry["sha256"],
                als_fold_in=entry.get("als_fold_in", False),
            )
        if spec.version_id in models:
            raise ValueError("duplicate model version")
        models[spec.version_id] = (spec, model)
    catalog_path = path.parent / config["catalog"]["path"]
    if file_sha256(catalog_path) != config["catalog"]["sha256"]:
        raise ValueError("catalog checksum mismatch")
    records = json.loads(catalog_path.read_text())
    tracks = {row["item_id"]: row for row in records}
    assets = {}
    for item, meta in config.get("audio", {}).items():
        audio_path = (path.parent / meta["path"]).resolve()
        if not audio_path.is_relative_to(path.parent) or file_sha256(audio_path) != meta["sha256"]:
            raise ValueError("invalid audio file")
        if item not in tracks or not meta["license_ref"]:
            raise ValueError("unlicensed or unmatched audio")
        if config["mode"] == "music4all" and meta["sample"]:
            raise ValueError("sample audio cannot masquerade as a Music4All recording")
        assets[item] = AudioAsset(
            audio_path,
            meta["license_ref"],
            parse_expiry(meta["expires_at"]),
            meta["sample"],
        )
    playback = LocalPlayback(assets)
    return Runtime(
        config=config,
        config_sha256=file_sha256(path),
        models=models,
        records=records,
        playback=playback,
        store=LocalStore(database),
        study=study,
        scope=scope,
    )
