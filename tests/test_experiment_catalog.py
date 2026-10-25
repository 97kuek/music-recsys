import csv
import json
import runpy
from pathlib import Path

import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.configuration import load_settings
from src.interfaces.cli.catalog import collect, run
from src.interfaces.cli.evaluate import run as evaluate


def experiments(tmp_path):
    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    method = tmp_path / "popularity"
    for name, basis in [("plays", "play_count"), ("users", "user_count")]:
        raw = load_settings("configs/popularity-plays.json")
        raw["experiment"].update(
            validation_dataset=str(bundle / "validation"),
            features=str(bundle / "features.json"),
            test_dataset=str(tmp_path / "never-open-test"),
            output=str(method / name),
        )
        raw["model"]["parameters"] = dict(basis=basis)
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(raw))
        evaluate(path)
    return method


def test_catalog_groups_configs_results_all_views_without_changing_originals(tmp_path):
    method = experiments(tmp_path)
    originals = {str(p): file_sha256(p) for p in method.rglob("*") if p.is_file()}
    output = run(method)
    result = json.loads((output / "result.json").read_text())
    assert {r["condition"] for r in result["conditions"]} == {"plays", "users"}
    assert all(r["model_weights"] == "not_exported" for r in result["conditions"])
    rows = list(csv.DictReader((output / "metrics.csv").open()))
    assert {r["aspect"] for r in rows} == {"accuracy", "diversity", "efficiency", "observations"}
    assert {r["k"] for r in rows if r["metric"] == "binary_ndcg"} == {str(k) for k in range(2, 21)}
    assert all(r["mean"] == "" for r in rows if r["metric"] == "api_cost")
    assert {p.name for p in output.iterdir()} == {
        "config.json",
        "result.json",
        "metrics.csv",
        "run.log",
    }
    for p, checksum in originals.items():
        assert file_sha256(p) == checksum
    with pytest.raises(FileExistsError):
        run(method)


def test_catalog_rejects_changed_recommendations_before_publication(tmp_path):
    method = experiments(tmp_path)
    path = method / "plays/recommendations.parquet"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        collect(method)
    assert not (method / "summary").exists()


def test_catalog_verifies_a_model_exported_outside_the_experiment_directory(tmp_path):
    from src.infrastructure.models.artifacts import export_model
    from src.infrastructure.recommenders.full_popularity import FullPopularity

    method = experiments(tmp_path)
    config = json.loads((method / "plays/config.json").read_text())
    learning = (
        Path(config["settings"]["validation_dataset"])
        / config["manifest"]["files"]["learning"]["path"]
    )
    directory = tmp_path / "exported-model"
    digest = export_model(FullPopularity(learning), directory, version_id="external-model")
    path = method / "plays/result.json"
    result = json.loads(path.read_text())
    result["serving_model"] = dict(path=str(directory), manifest_sha256=digest)
    path.write_text(json.dumps(result))
    _, records = collect(method)
    evidence = next(e for e, _ in records if e["condition"] == "plays")
    assert evidence["model_weights"] == "available"
    assert evidence["model_path"] == str(directory)
