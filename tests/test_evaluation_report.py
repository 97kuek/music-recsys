import json

import pandas as pd
import pytest


def completed(factory):
    from src.infrastructure.research.search import load_study_config, run_search, smoke

    config = factory(metric=None)
    config.path.write_text(config.path.read_text().replace("neighbors=[1,2]", "neighbors=[1]"))
    config = load_study_config(config.path)
    smoke(config)
    run_search(config)
    return config


def test_flat_report_preserves_trials_and_separates_figures(listening_study, tmp_path):
    from src.infrastructure.research.search import read_json
    from src.interfaces.notebook.evaluation_report import build_report

    config = completed(listening_study)
    before = read_json(config.output / "study.json")
    originals = {
        key: read_json(config.output / trial["report"]) for key, trial in before["trials"].items()
    }
    out = build_report(config.path, tmp_path / "export")
    result = json.loads((out / "result.json").read_text())
    for key, original in originals.items():
        assert result["trials"][key]["report"] == original
    assert set(result["analysis"]["aspects"]) == {
        "discovery",
        "preference",
        "diversity",
        "efficiency",
        "observations",
    }
    assert set(pd.read_csv(out / "metrics.csv").dropna(subset=["k"]).k) == set(range(2, 21))
    assert len(list(out.glob("*.png"))) == 10
    assert not (out / "ild.png").exists()  # fixture has no measured tag diversity
    assert not any(path.is_dir() for path in out.iterdir())
    assert not (out / "index.html").exists()
    assert not (out / "config.original.toml").exists()
    assert result["analysis"]["aspects"]["efficiency"][0]["input_tokens"] is None
    with pytest.raises(FileExistsError):
        build_report(config.path, out)


def test_compact_result_can_be_read_and_reused(listening_study):
    from src.infrastructure.research.search import (
        load_study,
        run_search,
        study_lock,
        verify_trial,
    )
    from src.infrastructure.research.store import compact
    from src.interfaces.notebook.search_analysis import trial_curves

    config = completed(listening_study)
    with study_lock(config):
        compact(config)
    assert not (config.output / "study.json").exists()
    assert not (config.output / "trials").exists()
    study = load_study(config)
    assert all(verify_trial(config, t) for t in study["trials"].values())
    assert not trial_curves(config, study["planned"][0], resamples=100).empty
    reused = run_search(config)
    assert reused["executed_trials"] == 0 and reused["reused_trials"] == 2
    with study_lock(config):
        compact(config)
    assert load_study(config)["status"] == "succeeded"


def test_single_axes_for_each_metric(listening_study):
    import matplotlib.pyplot as plt

    from src.infrastructure.research.search import load_study
    from src.interfaces.notebook.evaluation_figures import (
        accuracy_figures,
        diversity_figures,
        efficiency_figures,
    )
    from src.interfaces.notebook.evaluation_views import diversity_table, efficiency_table
    from src.interfaces.notebook.search_analysis import trial_curves

    config = completed(listening_study)
    curves = trial_curves(config, load_study(config)["planned"][0], resamples=100)
    from src.interfaces.notebook.listening_analysis import primary_rows

    figures = accuracy_figures(primary_rows(curves))
    figures.update(diversity_figures(diversity_table(curves)))
    figures.update(efficiency_figures(efficiency_table(curves)))
    assert all(len(fig.axes) == 1 for fig in figures.values())
    plt.close("all")


def test_cli_smoke_search_and_status_use_flat_output(listening_study, monkeypatch):
    import sys

    from src.infrastructure.research.search import load_study, load_study_config
    from src.interfaces.cli.experiment_search import main

    config = listening_study(metric=None)
    config.path.write_text(config.path.read_text().replace("neighbors=[1,2]", "neighbors=[1]"))
    config = load_study_config(config.path)
    for action in ("status", "smoke", "search", "status"):
        monkeypatch.setattr(sys, "argv", ["search", "--config", str(config.path), action])
        assert main() == 0
    assert not (config.output / "trials").exists()
    assert not (config.output / "report").exists()
    assert not (config.output / "logs").exists()
    assert (config.output / "result.json").is_file()
    assert (config.output / "metrics.csv").is_file()
    assert load_study(config)["status"] == "succeeded"


def test_archive_tampering_and_historical_mutations_are_rejected(listening_study, monkeypatch):
    import src.infrastructure.research.search.state as state
    from src.infrastructure.research.search import (
        load_study,
        run_search,
        study_lock,
        verify_trial,
    )
    from src.infrastructure.research.store import atomic_json, compact, read_document

    config = completed(listening_study)
    with study_lock(config):
        compact(config)
    monkeypatch.setattr(state, "source_hash", lambda: "different-code")
    study = load_study(config)
    assert study["status"] == "succeeded"
    with pytest.raises(ValueError, match="コード"):
        run_search(config)
    document = read_document(config.output / "result.json")
    key = study["planned"][0]
    document["trials"][key]["curves"]["metrics"][0]["ndcg"] = 0.123456
    atomic_json(config.output / "result.json", document)
    with pytest.raises(ValueError, match="改変"):
        verify_trial(config, study["trials"][key])


def test_unmeasured_figures_omitted_but_measured_zero_kept(tmp_path):
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import (
        efficiency_figures,
        prune_generated_figures,
    )

    frame = pd.DataFrame(
        [
            dict(
                model="popularity",
                latency_mean=0.0,
                input_tokens=None,
                output_tokens=None,
                api_cost=None,
                llm_calls=None,
                tool_calls=None,
            )
        ]
    )
    figures = efficiency_figures(frame)
    assert set(figures) == {"latency"}
    for fig in figures.values():
        plt.close(fig)
    for filename in ("latency.png", "input_tokens.png", "custom.png"):
        (tmp_path / filename).touch()
    prune_generated_figures(tmp_path, ["latency.png"])
    assert {p.name for p in tmp_path.iterdir()} == {"latency.png", "custom.png"}
