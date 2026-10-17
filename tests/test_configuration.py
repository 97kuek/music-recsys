import json

import pytest

from src.configuration import load_settings


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
    from src.notebook_config import ExperimentConfigPicker

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
    picker = ExperimentConfigPicker(tmp_path)
    assert len(picker.dropdown.options) == 2
    picker.dropdown.value = str(path)
    picker.load_button.click()
    assert picker.require_loaded().name == "trial"
    base.write_text(base.read_text() + "\n")
    with pytest.raises(ValueError, match="変更"):
        picker.require_loaded()
