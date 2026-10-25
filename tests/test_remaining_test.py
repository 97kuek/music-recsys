import json
import os
import runpy
from dataclasses import asdict
from pathlib import Path

import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.configuration import load_settings
from src.infrastructure.research.search import load_study_config


def small_case(tmp_path, model, *, gpu=False):
    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/content.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "data/experiments" / model / "source"),
    )
    params = {
        "content": dict(use_idf=True, aggregation="binary"),
        "bm25": dict(neighbors=2, k1=1.2, b=0.75),
        "ease": dict(regularization=10.0, block_size=2, memory_budget_mb=4096),
        "bpr": dict(
            factors=2, iterations=2, learning_rate=0.01, regularization=0.01, memory_budget_mb=4096
        ),
    }[model]
    raw["model"] = dict(name=model, parameters={**params, "use_gpu": gpu})
    raw["search"] = dict(stage="A", seeds=[42], max_trials=1)
    path = tmp_path / "case.json"
    path.write_text(json.dumps(raw))
    return load_study_config(path)


def test_recommended_recipe_has_exactly_the_agreed_37_validation_conditions():
    from src.interfaces.cli.final_test import plan

    p = plan()
    assert {m: len(c) for m, c in p.items()} == dict(bpr=21, ease=3, content=4, bm25=9)
    assert len({c.output for cases in p.values() for c in cases}) == 37
    assert all(c.metric is None and c.task is None for cases in p.values() for c in cases)
    assert {c.parameters["regularization"] for c in p["ease"]} == {100.0, 500.0, 2000.0}
    assert {(c.parameters["factors"], c.parameters["iterations"]) for c in p["bpr"]} == {
        (32, 500),
        (64, 500),
        (128, 500),
        (256, 500),
        (128, 30),
        (128, 100),
        (128, 200),
    }
    assert {c.seeds for c in p["bpr"]} == {(42,), (43,), (44,)}


@pytest.mark.parametrize("model", ["content", "bm25", "ease", "bpr"])
@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
            ),
        ),
    ],
)
def test_full_final_test_preserves_validation_and_recomputes_accuracy(tmp_path, model, gpu):
    from src.infrastructure.research.als_report import score_audit
    from src.infrastructure.research.full_test import restore
    from src.interfaces.cli.evaluate import run

    config = small_case(tmp_path, model, gpu=gpu)
    run(config)
    assert asdict(restore(config.output)) == asdict(config)
    original = {
        n: file_sha256(config.output / n)
        for n in ["config.json", "result.json", "scores.parquet", "recommendations.parquet"]
    }
    output = run(
        config, phase="test", ease_memory_budget_mb=4096 if gpu and model == "ease" else None
    )
    result = json.loads((output / "result.json").read_text())
    saved = json.loads((output / "config.json").read_text())
    assert saved["phase"] == result["phase"] == "test"
    assert saved["training_periods"] == ["train", "validation"]
    assert saved["manifest"]["prediction_at"] == "2018-09-21T00:00:00"
    assert (
        result["population"]["events"]
        > json.loads((config.output / "result.json").read_text())["population"]["events"]
    )
    assert original == {n: file_sha256(config.output / n) for n in original}
    assert score_audit(dict(result=result, result_path=str(output / "result.json"))) == 627
    assert result["primary"]["ks"] == list(range(2, 21))
    assert {r["task"] for r in result["diversity"]} == {"new", "repeat"}
    with pytest.raises(FileExistsError):
        run(config, phase="test")


def test_freeze_and_cpu_report_are_immutable_and_publish_all_aspects(tmp_path, monkeypatch):
    from src.interfaces.cli import final_test as cli
    from src.interfaces.cli.evaluate import run
    from src.interfaces.cli.final_report import summarize
    from src.paths import PROJECT_ROOT

    config = small_case(tmp_path, "content")
    run(config)
    (tmp_path / "uv.lock").write_bytes((PROJECT_ROOT / "uv.lock").read_bytes())
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    protocols = cli.freeze({"content": [config]}, ease_memory_budget_mb=None)
    target = tmp_path / "data/experiments/content/test"
    frozen = (target / "config.json").read_bytes()
    entry = protocols["content"]["conditions"][0]
    cli.evaluate_condition("content", entry["condition"])
    summarize(target, protocols["content"])
    result = json.loads((target / "result.json").read_text())
    assert result["phase"] == "test" and result["selection"] == "not_selected"
    assert all(
        (target / n).exists() and (target / n).with_suffix(".pdf").exists()
        for n in result["figures"]
    )
    assert (target / "metrics.csv").is_file() and (target / "paired.csv").is_file()
    assert (target / "config.json").read_bytes() == frozen
    with pytest.raises(FileExistsError):
        summarize(target, protocols["content"])
    cli.freeze({"content": [config]}, ease_memory_budget_mb=None)
    changed = json.loads(frozen)
    changed["policy"] = "retune"
    (target / "config.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Frozen Test protocol changed"):
        cli.freeze({"content": [config]}, ease_memory_budget_mb=None)


def test_seed_summary_does_not_average_intervals_or_hide_ild_population_changes():
    from src.interfaces.cli.final_report import average_rows

    def result(value, n):
        return dict(
            accuracy=[
                dict(
                    task="discovery",
                    window_days=90,
                    day_weight=None,
                    metric="recall",
                    k=20,
                    mean=value,
                    n_evaluated=2,
                    n_excluded=3,
                    ci_low=0,
                    ci_high=1,
                )
            ],
            diversity=[
                dict(
                    task="new",
                    k=20,
                    ild=value,
                    ild_evaluated=n,
                    ild_excluded=5 - n,
                    catalog_coverage=0.5,
                    novelty=2,
                )
            ],
            efficiency={"model": "bpr", "training_seconds": 1},
            observations=[],
        )

    rows = average_rows([result(0.2, 2), result(0.4, 3)], dict(condition="bpr", model="bpr"))
    accuracy = next(r for r in rows if r["aspect"] == "accuracy")
    assert accuracy["mean"] == pytest.approx(0.3) and accuracy["seed_sd"] == pytest.approx(
        2**0.5 / 10
    )
    assert accuracy["ci_low"] is accuracy["ci_high"] is None and accuracy["n_evaluated"] == 2
    ild = next(r for r in rows if r.get("metric") == "ild")
    assert (
        ild["n_evaluated"] is None and ild["n_evaluated_min"] == 2 and ild["n_evaluated_max"] == 3
    )


def test_test_input_provenance_is_checked_before_model_fit(tmp_path, monkeypatch):
    from src.infrastructure.recommenders.full_content import FullContent
    from src.interfaces.cli.evaluate import run

    config = small_case(tmp_path, "content")
    run(config)
    manifest = config.test_dataset / "manifest.json"
    saved = json.loads(manifest.read_text())
    saved["source_files"]["train"]["row_count"] += 1
    manifest.write_text(json.dumps(saved))

    def forbidden(*args, **kwargs):
        raise AssertionError("fit must not begin")

    monkeypatch.setattr(FullContent, "__init__", forbidden)
    with pytest.raises(ValueError):
        run(config, phase="test")
