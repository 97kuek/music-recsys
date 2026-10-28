"""研究CLI・HTTP・配布検証のcomposition root。具象Adapterをここで組み立てる。"""

import json
from pathlib import Path

from backend.application.study.runtime import Runtime
from backend.domain.recommendation.models import ModelSpec
from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.media.playback import AudioAsset, LocalPlayback, parse_expiry
from backend.infrastructure.models.artifacts import load_model
from backend.infrastructure.persistence.sessions import LocalStore
from backend.infrastructure.recommenders.tag_seed import TagSeedRecommender


def create_evaluation():
    from backend.application.experiments import Evaluation
    from backend.infrastructure.research.evaluation import LocalEvaluation

    return Evaluation(LocalEvaluation(create_full_report().write))


def create_full_report():
    from backend.infrastructure.research.reports import FullReport
    from backend.interfaces.notebook.evaluation_figures import ACCURACY_NAMES
    from backend.interfaces.notebook.full_report import draw

    return FullReport(draw, (name for name, _ in ACCURACY_NAMES.values()))


def create_factor_report():
    from backend.infrastructure.research.reports import FactorReport
    from backend.interfaces.notebook.factor_report import draw

    return FactorReport(draw)


def create_itemcf_report():
    from backend.infrastructure.research.reports import ItemCFReport
    from backend.interfaces.notebook.itemcf_report import draw

    return ItemCFReport(draw)


def create_factor_study():
    from backend.application.experiments import FactorStudy
    from backend.infrastructure.research.reports import StudyReport
    from backend.infrastructure.research.studies import LocalStudy
    from backend.interfaces.notebook.study_report import draw

    return FactorStudy(LocalStudy(StudyReport(draw).write))


def create_sensitivity():
    from backend.application.experiments import Sensitivity
    from backend.infrastructure.research.sensitivity import LocalSensitivity
    from backend.infrastructure.research.sensitivity_report import SensitivityReport
    from backend.interfaces.notebook.parameter_figures import draw as publication_draw
    from backend.interfaces.notebook.sensitivity_report import draw

    return Sensitivity(LocalSensitivity(SensitivityReport(draw, publication_draw).write))


def run_sensitivity_condition(path, name):
    from backend.infrastructure.research.parameters import conditions
    from backend.infrastructure.research.search import load_study_config

    trials = dict(conditions(load_study_config(path)))
    if name not in trials:
        raise ValueError("unknown condition")
    return create_evaluation().run(trials[name])


def run_factor_condition(path, name):
    from backend.application.ports.experiments import EvaluationOptions
    from backend.infrastructure.research.grid import factor_conditions
    from backend.infrastructure.research.search import load_study_config

    config = load_study_config(path)
    matches = [trial for label, trial in factor_conditions(config) if label == name]
    if len(matches) != 1:
        raise ValueError("unknown condition")
    trial = matches[0]
    return create_evaluation().run(
        trial,
        EvaluationOptions(
            export_path=trial.output / "model", model_version=f"{config.name}-{name}"
        ),
    )


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
