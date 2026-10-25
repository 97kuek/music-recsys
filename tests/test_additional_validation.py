"""Read-only aggregation verifies evidence and never replaces a published destination."""

import csv
import json
import shutil
from pathlib import Path

import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.interfaces.cli.additional_validation import run


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def bundle(tmp_path):
    evaluation = dict(
        ks=list(range(2, 21)),
        window_days=[30, 90, 180],
        primary_window_days=90,
        day_weights=[0, 0.5, 1, 2],
        primary_day_weight=1,
    )
    identity = dict(
        manifest={"phase": "validation", "dataset_id": "fixture"},
        features_sha256="f" * 64,
        population={"users": 2, "items": 4, "events": 5},
        evaluation=evaluation,
    )
    definitions = dict(gain="fixture gain", usage="unmeasured is null")

    def condition(name, model):
        directory = tmp_path / name
        directory.mkdir(parents=True)
        artifacts = {}
        for artifact in ("scores", "recommendations"):
            path = directory / (artifact + ".parquet")
            path.write_bytes((name + artifact).encode())
            artifacts[artifact] = dict(path=path.name, sha256=file_sha256(path))
        (directory / "recall.png").write_bytes(b"fixture figure")
        config = dict(
            settings=dict(
                evaluation=evaluation,
                seeds=[0],
                model=model,
                parameters={"factors": 256} if model == "als" else {},
            ),
            manifest=identity["manifest"],
            population=identity["population"],
            features_sha256=identity["features_sha256"],
            source_sha256="a" * 64,
            dependency_lock_sha256="b" * 64,
            execution={"evaluation_device": "fixture"},
        )
        accuracy = [
            dict(
                task=task,
                window_days=window,
                day_weight=weight,
                metric=metric,
                k=k,
                mean=None if k == 2 else 0.0,
                n_evaluated=2,
            )
            for task, metric in [
                ("discovery", "binary_ndcg"),
                ("discovery", "recall"),
                ("retention_proxy", "graded_ndcg"),
                ("repeat", "graded_ndcg"),
                ("repeat", "repeat_recall"),
            ]
            for window in evaluation["window_days"]
            for weight in (evaluation["day_weights"] if metric == "graded_ndcg" else [None])
            for k in evaluation["ks"]
        ]
        diversity = [
            dict(
                task=task,
                k=k,
                ild=None,
                catalog_coverage=0.5,
                novelty=0.0,
                ild_evaluated=0,
                ild_excluded=2,
                n_requests=2,
            )
            for task in ("new", "repeat")
            for k in evaluation["ks"]
        ]
        result = dict(
            format="music-recsys-full-result-v1",
            status="succeeded",
            phase="validation",
            model=model,
            model_parameters={"factors": 256} if model == "als" else {},
            population=identity["population"],
            primary=evaluation,
            accuracy=accuracy,
            diversity=diversity,
            efficiency=dict(
                latency_mean=0.0,
                input_tokens=None,
                output_tokens=None,
                api_cost=None,
                llm_calls=None,
                tool_calls=None,
            ),
            artifacts=artifacts,
            definitions=definitions,
            figures=["recall.png"],
        )
        write(directory / "config.json", config)
        write(directory / "result.json", result)
        return directory

    bridge = condition("bridge", "als")
    child = condition("parameters/child", "bm25")

    def reference(directory):
        return dict(
            output=str(directory),
            result=str(directory / "result.json"),
            result_sha256=file_sha256(directory / "result.json"),
            config=str(directory / "config.json"),
            config_sha256=file_sha256(directory / "config.json"),
            source_sha256="a" * 64,
            dependency_lock_sha256="b" * 64,
            historical=False,
        )

    parameter = child.parent
    write(
        parameter / "config.json",
        dict(
            source_sha256="a" * 64,
            dependency_lock_sha256="b" * 64,
            settings={"evaluation": evaluation},
            features_sha256=identity["features_sha256"],
        ),
    )
    write(
        parameter / "result.json",
        dict(
            format="music-recsys-parameter-sensitivity-v1",
            status="succeeded",
            phase="validation",
            planned=1,
            completed=1,
            sources={"child": reference(child)},
            source_sha256="a" * 64,
            dependency_lock_sha256="b" * 64,
            figures=[],
        ),
    )
    comp = tmp_path / "complementarity"
    write(
        comp / "config.json",
        dict(
            inputs={**identity, "definitions": definitions},
            sources={name: reference(path) for name, path in (("als", bridge), ("bm25", child))},
        ),
    )
    (comp / "users.parquet").write_bytes(b"fixture per-user diagnostics")
    write(
        comp / "result.json",
        dict(
            format="music-recsys-complementarity-v1",
            status="succeeded",
            phase="validation",
            population=identity["population"],
            ks=evaluation["ks"],
            window_days=evaluation["window_days"],
            models=["als", "bm25"],
            metrics=[dict(task="new", metric="union_recall", k=2, mean=None)],
            efficiency=dict(evaluation_seconds=0.0),
            artifacts={
                "users": dict(path="users.parquet", sha256=file_sha256(comp / "users.parquet"))
            },
            figures=[],
        ),
    )
    provenance = tmp_path / "provenance.json"
    write(
        provenance,
        dict(
            format="music-recsys-additional-provenance-audit-v1",
            snapshot_source_sha256="a" * 64,
            lock={"snapshot_sha256": "b" * 64},
            controls=[],
            limits=["Fixture only; no measured recommendation quality"],
        ),
    )
    return dict(
        results=[bridge / "result.json", parameter, comp],
        provenance=provenance,
        output=tmp_path / "report",
        expected_users=2,
        expected_new_conditions=2,
        expected_bridges=1,
        expected_parameters=1,
    )


def test_joins_all_k_and_periods_with_sources_and_preserves_nulls(tmp_path):
    options = bundle(tmp_path)
    source_paths = list(tmp_path.rglob("*"))
    before = {str(path): file_sha256(path) for path in source_paths if path.is_file()}
    output = run(**options)
    assert {p.name for p in output.iterdir()} == {
        "config.json",
        "result.json",
        "metrics.csv",
        "run.log",
    }
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "succeeded" and result["new_conditions"] == 2
    assert len(result["conditions"]) == 2
    assert "accuracy" not in result
    assert all(not Path(figure["path"]).is_absolute() for figure in result["figures"])
    rows = list(csv.DictReader((output / "metrics.csv").open()))
    assert {r["window_days"] for r in rows if r["aspect"] == "accuracy"} == {"30", "90", "180"}
    assert {int(r["k"]) for r in rows if r["aspect"] == "diversity"} == set(range(2, 21))
    assert {r["mean"] for r in rows if r["metric"] == "binary_ndcg"} == {"", "0.0"}
    assert all(r["result_sha256"] and r["config_sha256"] for r in rows)
    assert all(file_sha256(path) == digest for path, digest in before.items())
    with pytest.raises(FileExistsError):
        run(**options)


def test_legacy_popularity_preserves_unknown_lock_and_efficiency_without_rewriting(tmp_path):
    options = bundle(tmp_path)
    legacy = tmp_path / "legacy"
    shutil.copytree(tmp_path / "parameters/child", legacy)
    config = json.loads((legacy / "config.json").read_text())
    config["settings"]["model"] = "popularity"
    del config["dependency_lock_sha256"]
    write(legacy / "config.json", config)
    result = json.loads((legacy / "result.json").read_text())
    result["model"] = "popularity"
    del result["model_parameters"]
    write(legacy / "result.json", result)
    comp_path = tmp_path / "complementarity"
    comp = json.loads((comp_path / "config.json").read_text())
    reference = dict(comp["sources"]["bm25"])
    reference.update(
        output=str(legacy),
        result=str(legacy / "result.json"),
        config=str(legacy / "config.json"),
        result_sha256=file_sha256(legacy / "result.json"),
        config_sha256=file_sha256(legacy / "config.json"),
        dependency_lock_sha256=None,
        historical=True,
    )
    comp["sources"]["popularity"] = reference
    write(comp_path / "config.json", comp)
    comp_result = json.loads((comp_path / "result.json").read_text())
    comp_result["models"].append("popularity")
    write(comp_path / "result.json", comp_result)
    before = {p.name: file_sha256(p) for p in legacy.iterdir()}
    output = run(**options)
    report = json.loads((output / "result.json").read_text())
    source = next(c for c in report["conditions"] if c["result"] == str(legacy / "result.json"))
    assert source["dependency_lock_sha256"] is None
    assert source["legacy_missing_fields"] == ["dependency_lock_sha256", "model_parameters"]
    rows = list(csv.DictReader((output / "metrics.csv").open()))
    elapsed = next(
        r for r in rows if r.get("model") == "popularity" and r["metric"] == "evaluation_seconds"
    )
    assert elapsed["mean"] == ""
    assert {p.name: file_sha256(p) for p in legacy.iterdir()} == before


@pytest.mark.parametrize(
    "name", ["existing", "artifact", "config", "failed", "comp-artifact", "audit"]
)
def test_refuses_existing_or_unverifiable_sources_without_publication(tmp_path, name):
    options = bundle(tmp_path)
    if name == "existing":
        options["output"].mkdir()
    elif name == "artifact":
        (tmp_path / "bridge/scores.parquet").write_bytes(b"tampered")
    elif name == "config":
        path = tmp_path / "parameters/child/config.json"
        config = json.loads(path.read_text())
        config["settings"]["seeds"] = [9]
        write(path, config)
    elif name == "failed":
        path = tmp_path / "bridge/result.json"
        result = json.loads(path.read_text())
        result["status"] = "failed"
        write(path, result)
    elif name == "comp-artifact":
        (tmp_path / "complementarity/users.parquet").write_bytes(b"tampered")
    else:
        path = options["provenance"]
        audit = json.loads(path.read_text())
        audit["snapshot_source_sha256"] = "c" * 64
        write(path, audit)
    with pytest.raises((ValueError, FileExistsError)):
        run(**options)
    assert not (options["output"] / "result.json").exists()
    assert not (options["output"] / "metrics.csv").exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("expected_users", 3),
        ("expected_new_conditions", 22),
        ("expected_bridges", 2),
        ("expected_parameters", 4),
    ],
)
def test_refuses_population_or_incomplete_plans(tmp_path, key, value):
    options = bundle(tmp_path)
    options[key] = value
    with pytest.raises(ValueError):
        run(**options)
    assert not options["output"].exists()
