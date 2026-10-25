from pathlib import Path

import pytest

from src.interfaces.notebook.notebook_config import ExperimentConfigPicker


def test_picker_requires_explicit_click_and_invalidates_changes(listening_study, monkeypatch):
    config = listening_study()
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))
    picker = ExperimentConfigPicker(config.path.parent)
    assert picker.dropdown.value is None
    with pytest.raises(ValueError, match="未指定"):
        picker.require_loaded()
    picker.dropdown.value = str(config.path)
    with pytest.raises(ValueError, match="未読込み"):
        picker.require_loaded()
    picker.load_button.click()
    loaded = picker.require_loaded()
    assert loaded.path == config.path
    assert not config.output.exists()
    picker.require_current(loaded)
    picker.load_button.click()
    with pytest.raises(ValueError, match="切り替わって"):
        picker.require_current(loaded)
    config.path.write_text(config.path.read_text() + "\n# edited\n")
    with pytest.raises(ValueError, match="変更"):
        picker.require_loaded()
    picker.load_button.click()
    assert picker.require_loaded().path == config.path
    picker.dropdown.value = None
    with pytest.raises(ValueError, match="未指定"):
        picker.require_loaded()


def test_picker_bad_config_never_reuses_previous(listening_study):
    config = listening_study()
    bad = config.path.parent / "bad.toml"
    bad.write_text("invalid = [")
    picker = ExperimentConfigPicker(config.path.parent)
    picker.dropdown.value = str(config.path)
    picker.load_button.click()
    picker.dropdown.value = str(bad)
    picker.load_button.click()
    assert "読込み失敗" in picker.status.value
    with pytest.raises(ValueError, match="未指定"):
        picker.require_loaded()
    picker.refresh_button.click()
    assert picker.dropdown.value is None


def test_picker_empty_directory(tmp_path):
    picker = ExperimentConfigPicker(tmp_path)
    assert "設定ファイルなし" in picker.status.value
    picker.load_button.click()
    with pytest.raises(ValueError, match="未指定"):
        picker.require_loaded()


def test_picker_hides_nested_generated_conditions_until_requested(tmp_path):
    import json

    from src.infrastructure.research.configuration import load_settings

    raw = load_settings("configs/popularity-plays.json")
    generated = [
        "itemcf/cosine/20.json",
        "itemcf/jaccard/100.json",
        "als/32.json",
    ]
    visible = [
        "bpr-500epoch.json",
        "als/256.json",
        "als/512.json",
        "bm25/50.json",
    ]
    for name in [*generated, *visible]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))
    picker = ExperimentConfigPicker(tmp_path)
    values = {value for _, value in picker.dropdown.options if value}
    assert values == {str(tmp_path / name) for name in visible}
    picker.show_trials.value = True
    values = {value for _, value in picker.dropdown.options if value}
    assert values == {str(tmp_path / name) for name in [*generated, *visible]}
    assert picker.dropdown.value is None


@pytest.mark.parametrize("name", ["00_lab_start", "01_data", "02_analysis", "03_recommendation"])
def test_notebooks_do_not_use_environment_as_selection(name, monkeypatch):
    from test_notebook import _run_notebook_cells

    monkeypatch.setenv(
        "MUSIC_RECSYS_EXPERIMENT_CONFIG",
        str(Path("configs/popularity-plays.json").resolve()),
    )
    with pytest.raises(ValueError, match="未指定"):
        _run_notebook_cells(name + ".ipynb", 3)
