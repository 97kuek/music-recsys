import json
import os
import runpy
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.infrastructure.research.configuration import load_settings


@pytest.fixture
def final_configs(tmp_path):
    from src.interfaces.cli.evaluate import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    configs = []
    for name in ("plays", "users"):
        raw = load_settings(Path(f"configs/popularity-{name}.json"))
        raw["experiment"].update(
            validation_dataset=str(bundle / "validation"),
            test_dataset=str(bundle / "test"),
            features=str(bundle / "features.json"),
            output=str(tmp_path / "popularity" / name),
        )
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(raw))
        run(path)
        configs.append(path)
    return configs


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
def test_final_test_matches_test_reference_preserves_validation_and_refuses_repeat(
    final_configs, gpu
):
    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.datasets.listening_data import load_listening_dataset
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark
    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.infrastructure.recommenders import create_recommender
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.popularity_test import run

    configs = [load_study_config(p) for p in final_configs]
    original = [(c.output / "result.json").read_bytes() for c in configs]
    output = run(final_configs, gpu=gpu)
    summary = json.loads((output / "result.json").read_text())
    assert summary["phase"] == "test" and summary["status"] == "succeeded"
    assert summary["paired_differences"]
    from src.interfaces.cli.catalog import collect

    _, records = collect(output.parent)
    assert {e["condition"] for e, _ in records} == {"plays", "users"}
    for config, validation in zip(configs, original, strict=True):
        assert (config.output / "result.json").read_bytes() == validation
        result = json.loads((config.output / "test/result.json").read_text())
        assert result["phase"] == "test"
        assert {r["phase"] for r in result["observations"]} == {"test"}
        data = load_listening_dataset(config.test_dataset, config.evaluation)
        features = load_features(
            config.features,
            dataset_id=data.dataset_id,
            snapshot_id=data.manifest["source"]["snapshot_id"],
        )
        reference = run_listening_benchmark(
            data,
            create_recommender(
                "popularity", data.train_interactions, parameters=config.parameters
            ).recommend,
            config=config.evaluation,
            model_name="popularity",
            features=features,
            tracking=TrackingConfig(enabled=False),
        )

        def key(r):
            return (r["task"], r["window_days"], r["day_weight"], r["metric"], r["k"])

        expected = {key(r): r for r in reference.summary}
        for row in result["accuracy"]:
            assert row["mean"] == pytest.approx(expected[key(row)]["value"])
            assert row["n_evaluated"] == expected[key(row)]["n_evaluated"]
        for actual, expected in zip(
            result["diversity"], reference.beyond_accuracy["metrics"], strict=True
        ):
            for metric in ("ild", "novelty", "catalog_coverage", "ild_evaluated", "ild_excluded"):
                assert actual[metric] == pytest.approx(expected[metric])
        actual = pq.read_table(config.output / "test/recommendations.parquet").to_pylist()
        assert sorted((r["user_id"], r["task"], r["item_ids"]) for r in actual) == sorted(
            (r["request"]["user_id"], r["request"]["task"], list(r["response"]["item_ids"]))
            for r in reference.runs
        )
    with pytest.raises(FileExistsError):
        run(final_configs, gpu=gpu)


def test_final_training_rejects_changed_provenance_and_missing_history(final_configs):
    from copy import deepcopy

    from src.infrastructure.datasets.listening_data import validate_bundle
    from src.infrastructure.research.full_test import validate_training
    from src.infrastructure.research.search import load_study_config

    config = load_study_config(final_configs[0])
    manifest, paths, counts = validate_bundle(config.test_dataset)
    bad = deepcopy(manifest)
    bad["source"]["split_id"] = "another-split"
    with pytest.raises(ValueError, match="provenance"):
        validate_training(config, bad, paths, counts)
    with pytest.raises(ValueError, match="all Train"):
        validate_training(config, manifest, paths, dict(counts, events=1))
    validation, previous, _ = validate_bundle(config.validation_dataset)
    with pytest.raises(ValueError, match="omits"):
        validate_training(config, manifest, dict(paths, learning=previous["learning"]), counts)


def test_failed_aggregate_finishes_without_rescoring_or_changing_test(final_configs, monkeypatch):
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli import evaluate, popularity_test

    configs = [load_study_config(p) for p in final_configs]
    output = configs[0].output.parent / "test"
    original = popularity_test._summarize

    def fail(*args, **kwargs):
        (output / "metrics.csv").write_text("incomplete", encoding="utf-8")
        raise UnicodeEncodeError("ascii", "–", 0, 1, "test failure")

    monkeypatch.setattr(popularity_test, "_summarize", fail)
    with pytest.raises(UnicodeEncodeError):
        popularity_test.run(final_configs, gpu=False)
    saved = [(c.output / "test/result.json").read_bytes() for c in configs]
    (output / "metrics.csv").rename(output / "metrics.csv.partial")
    monkeypatch.setattr(popularity_test, "_summarize", original)

    def forbidden(*args, **kwargs):
        pytest.fail("Final Test must never be rescored to repair an aggregate")

    monkeypatch.setattr(evaluate, "run", forbidden)
    popularity_test.finish(final_configs)
    assert json.loads((output / "result.json").read_text(encoding="utf-8"))["status"] == "succeeded"
    assert saved == [(c.output / "test/result.json").read_bytes() for c in configs]
    with pytest.raises(FileExistsError):
        popularity_test.finish(final_configs)


def test_replot_preserves_scoring_evidence_and_archives_old_figures(
    final_configs, tmp_path, monkeypatch
):
    from matplotlib.figure import Figure

    from src.infrastructure.datasets.listening_data import file_sha256
    from src.interfaces.cli import evaluate, popularity_test
    from src.interfaces.cli.popularity_replot import replot

    output = popularity_test.run(final_configs, gpu=False)
    directories = [output, output.parent / "plays/test", output.parent / "users/test"]
    protected = {
        p: file_sha256(p)
        for directory in directories
        for name in (
            "config.json",
            "result.json",
            "metrics.csv",
            "scores.parquet",
            "recommendations.parquet",
        )
        if (p := directory / name).exists()
    }
    original_pngs = {p: file_sha256(p) for d in directories for p in d.glob("*.png")}

    def forbidden(*args, **kwargs):
        pytest.fail("Replot must never evaluate Test again")

    monkeypatch.setattr(evaluate, "run", forbidden)
    archive_root = tmp_path / "provenance"
    with monkeypatch.context() as patch:
        patch.setattr(
            Figure, "savefig", lambda *a, **k: (_ for _ in ()).throw(OSError("render failed"))
        )
        with pytest.raises(OSError, match="render failed"):
            replot(final_configs, archive_root=archive_root)
    assert original_pngs == {p: file_sha256(p) for p in original_pngs}
    assert protected == {p: file_sha256(p) for p in protected}

    manifest_path = replot(final_configs, archive_root=archive_root)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "succeeded" and manifest["rescored"] is False
    assert protected == {p: file_sha256(p) for p in protected}
    for record in manifest["figures"]:
        assert file_sha256(record["path"]) == record["sha256"]
        if record["archived"]:
            assert (
                file_sha256(manifest_path.parent / record["archived"]) == record["previous_sha256"]
            )
    assert (output / "coverage_new.png").exists()
    assert (output / "delta_recall_new.png").exists()
    assert str(manifest_path) in (output / "run.log").read_text()

    # A changed individual score artifact must prevent further publication.
    scores = output.parent / "plays/test/scores.parquet"
    with scores.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        replot(final_configs, archive_root=archive_root)
