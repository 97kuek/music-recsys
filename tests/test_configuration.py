import json

import pytest

from backend.infrastructure.research.configuration import load_settings


def test_inheritance_paths_overrides_and_cycles(tmp_path):
    parent = tmp_path / "shared" / "base.json"
    parent.parent.mkdir()
    parent.write_text(
        json.dumps(
            {
                "experiment": {"validation_dataset": "input", "output": "unused"},
                "evaluation": {"ks": [2, 3], "window_days": [90]},
            }
        )
    )
    child = tmp_path / "trial.json"
    child.write_text(
        json.dumps(
            {
                "extends": "shared/base.json",
                "experiment": {"output": "results"},
                "evaluation": {"ks": [20]},
            }
        )
    )
    sources = {}
    raw = load_settings(child, sources=sources)
    assert raw["experiment"] == {
        "validation_dataset": str(parent.parent / "input"),
        "output": str(tmp_path / "results"),
    }
    assert raw["evaluation"] == {"ks": [20], "window_days": [90]}
    assert set(sources) == {parent, child}
    parent.write_text('{"extends": "../trial.json"}')
    with pytest.raises(ValueError, match="Circular"):
        load_settings(child)


def test_picker_invalidates_shared_settings_and_hides_template(tmp_path):
    from backend.interfaces.notebook.config import ConfigPicker

    base = tmp_path / "common.json"
    base.write_text(
        json.dumps(
            {
                "experiment": {
                    "protocol": "listening-v2",
                    "validation_dataset": "validation",
                    "output": "results",
                }
            }
        )
    )
    path = tmp_path / "trial.json"
    path.write_text(
        json.dumps(
            {
                "extends": "common.json",
                "experiment": {"name": "trial"},
                "model": {"name": "popularity"},
            }
        )
    )
    picker = ConfigPicker(tmp_path)
    assert len(picker.dropdown.options) == 2
    picker.dropdown.value = str(path)
    picker.load_button.click()
    assert picker.require_loaded().name == "trial"
    base.write_text(base.read_text() + "\n")
    with pytest.raises(ValueError, match="変更"):
        picker.require_loaded()


def test_relocated_result_preserves_saved_provenance_and_checks_all_conditions(tmp_path):
    import runpy
    from dataclasses import asdict, replace
    from pathlib import Path

    from backend.infrastructure.datasets.listening_data import file_sha256, read_bundle
    from backend.infrastructure.research.results import read_result
    from backend.infrastructure.research.search import load_study_config

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/popularity/plays.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "result"),
    )
    original = tmp_path / "plays.json"
    original.write_text(json.dumps(raw))
    config = load_study_config(original)
    config.output.mkdir()
    saved = dict(
        settings=json.loads(json.dumps(asdict(config), default=str)),
        manifest=read_bundle(config.validation_dataset)[0],
        features_sha256=file_sha256(config.features),
        source_sha256="original-processing-version",
    )
    saved_path = config.output / "config.json"
    saved_path.write_text(json.dumps(saved))
    (config.output / "result.json").write_text('{"status":"succeeded"}')
    digest = file_sha256(saved_path)
    relocated = tmp_path / "popularity" / "plays.json"
    relocated.parent.mkdir()
    original.rename(relocated)
    moved = load_study_config(relocated)
    assert read_result(moved) == {"status": "succeeded"}
    assert file_sha256(saved_path) == digest
    for changed in (replace(moved, seeds=(99,)), replace(moved, parameters={"basis": "users"})):
        with pytest.raises(ValueError, match="設定が異なります"):
            read_result(changed)
    (bundle / "features.json").write_text("{}")
    with pytest.raises(ValueError, match="入力が異なります"):
        read_result(moved)
