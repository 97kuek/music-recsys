"""Publish a verified, read-only index of additional Validation diagnostics."""

import argparse
import csv
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.figure_retention import figure_references
from src.infrastructure.research.store import atomic_json

FULL = "music-recsys-full-result-v1"
PARAMETERS = "music-recsys-parameter-sensitivity-v1"
COMPLEMENTARITY = "music-recsys-complementarity-v1"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _success(result):
    if result.get("status") != "succeeded" or result.get("phase") != "validation":
        raise ValueError("Only succeeded Validation sources may be published")


def _inside(directory, name):
    path = (directory / name).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise ValueError("Source artifact must be an existing file inside its result directory")
    return path


def _complete_metrics(result, evaluation):
    expected = {
        (task, metric, window, weight, k)
        for task, metric in (
            ("discovery", "binary_ndcg"),
            ("discovery", "recall"),
            ("retention_proxy", "graded_ndcg"),
            ("repeat", "graded_ndcg"),
            ("repeat", "repeat_recall"),
        )
        for window in evaluation["window_days"]
        for weight in (evaluation["day_weights"] if metric == "graded_ndcg" else [None])
        for k in evaluation["ks"]
    }
    actual = [
        (row["task"], row["metric"], row["window_days"], row["day_weight"], row["k"])
        for row in result["accuracy"]
    ]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError("Source accuracy conditions do not cover every required metric/window/K")
    diversity = [(row["task"], row["k"]) for row in result["diversity"]]
    expected_diversity = {(task, k) for task in ("new", "repeat") for k in evaluation["ks"]}
    if set(diversity) != expected_diversity or len(diversity) != len(expected_diversity):
        raise ValueError("Source diversity conditions do not cover both tasks and every K")
    required = {
        "latency_mean",
        "input_tokens",
        "output_tokens",
        "api_cost",
        "llm_calls",
        "tool_calls",
    }
    if not required <= result["efficiency"].keys():
        raise ValueError("Source efficiency must retain every required measured or null field")


class _Sources:
    def __init__(self, output, users, audit):
        self.output, self.users, self.audit = output, users, audit
        self.identity, self.definitions = None, None
        self.leaves, self.references = {}, {}
        self.figures = {}

    def reference(self, directory):
        directory = Path(directory).resolve()
        if directory == self.output or directory.is_relative_to(self.output):
            raise ValueError("Report cannot contain itself as a source")
        ref = dict(
            result=str(directory / "result.json"),
            result_sha256=file_sha256(directory / "result.json"),
            config=str(directory / "config.json"),
            config_sha256=file_sha256(directory / "config.json"),
        )
        previous = self.references.setdefault(str(directory), ref)
        if previous != ref:
            raise ValueError("Source changed during report verification")
        return ref

    def identity_matches(self, identity):
        if "definitions" in identity:
            self.definitions_match(identity["definitions"])
        identity = {
            key: identity[key]
            for key in ("manifest", "features_sha256", "population", "evaluation")
        }
        if identity["population"]["users"] != self.users:
            raise ValueError("Source must contain the complete expected learning population")
        if identity["manifest"].get("phase") != "validation":
            raise ValueError("Source manifest is not Validation")
        evaluation = identity["evaluation"]
        if evaluation["ks"] != list(range(2, 21)):
            raise ValueError("Source must retain every K from 2 through 20")
        if self.identity is not None and identity != self.identity:
            raise ValueError("Source input, population, features or evaluation conditions differ")
        self.identity = identity

    def definitions_match(self, definitions):
        if self.definitions is not None and definitions != self.definitions:
            raise ValueError("Source evaluation definitions differ")
        self.definitions = definitions

    def artifacts(self, directory, result):
        required = (
            {"users"} if result["format"] == COMPLEMENTARITY else {"scores", "recommendations"}
        )
        if not required <= result["artifacts"].keys():
            raise ValueError("Source is missing required per-user artifacts")
        for artifact in result["artifacts"].values():
            path = _inside(directory, artifact["path"])
            if file_sha256(path) != artifact["sha256"]:
                raise ValueError("Source artifact checksum changed")

    def figure_refs(self, directory, result):
        for reference in figure_references(directory, result.get("figures", [])):
            self.figures[reference["path"]] = dict(
                reference, path=os.path.relpath(reference["path"], self.output)
            )

    def leaf(self, directory, *, declared=None, new=False):
        directory = Path(directory).resolve()
        reference = self.reference(directory)
        if declared is not None:
            for key in ("result_sha256", "config_sha256"):
                if declared[key] != reference[key]:
                    raise ValueError("Declared source result/config checksum changed")
        if str(directory) not in self.leaves:
            result, config = _read(reference["result"]), _read(reference["config"])
            _success(result)
            if result.get("format") != FULL:
                raise ValueError("Condition source must be a full Validation result")
            identity = dict(
                manifest=config["manifest"],
                features_sha256=config["features_sha256"],
                population=result["population"],
                evaluation=config["settings"]["evaluation"],
            )
            if (
                config["population"] != result["population"]
                or result["primary"] != identity["evaluation"]
            ):
                raise ValueError("Condition result/config identity differs")
            if (
                config["settings"]["model"] != result["model"]
                or (
                    "model_parameters" in result
                    and config["settings"]["parameters"] != result["model_parameters"]
                )
                or ("model_parameters" not in result and result["model"] != "popularity")
                or len(config["settings"]["seeds"]) != 1
            ):
                raise ValueError("Condition result/config model parameters differ")
            self.identity_matches(identity)
            _complete_metrics(result, identity["evaluation"])
            definitions = {
                k: v for k, v in result["definitions"].items() if k not in {"ranking", "latency"}
            }
            self.definitions_match(definitions)
            self.artifacts(directory, result)
            self.figure_refs(directory, result)
            self.leaves[str(directory)] = dict(
                result=result,
                config=config,
                reference={
                    **reference,
                    "source_sha256": config.get("source_sha256"),
                    "dependency_lock_sha256": config.get("dependency_lock_sha256"),
                    "execution": config.get("execution"),
                    "legacy_missing_fields": [
                        key
                        for key in ("dependency_lock_sha256", "model_parameters")
                        if key not in (config if key == "dependency_lock_sha256" else result)
                    ],
                },
                new=False,
            )
        leaf = self.leaves[str(directory)]
        if declared is not None:
            for key in ("source_sha256", "dependency_lock_sha256"):
                if declared[key] != leaf["reference"][key]:
                    raise ValueError("Declared source processing provenance changed")
            for name in ("recommendations", "scores"):
                key = name + "_sha256"
                if key in declared and declared[key] != leaf["result"]["artifacts"][name]["sha256"]:
                    raise ValueError("Declared source artifact checksum differs")
        if new:
            if (
                leaf["reference"]["source_sha256"] != self.audit["snapshot_source_sha256"]
                or leaf["reference"]["dependency_lock_sha256"]
                != self.audit["lock"]["snapshot_sha256"]
            ):
                raise ValueError("New condition differs from the audited source snapshot")
            leaf["new"] = True
        return leaf


def _rows(sources, complementarity):
    for directory, leaf in sources.leaves.items():
        result, config = leaf["result"], leaf["config"]
        metadata = dict(
            source_kind="condition",
            condition=Path(directory).name,
            model=result["model"],
            parameters=json.dumps(
                result.get("model_parameters", config["settings"]["parameters"]), sort_keys=True
            ),
            parameters_recorded_in_result="model_parameters" in result,
            seed=config["settings"]["seeds"][0],
            new_condition=leaf["new"],
            **leaf["reference"],
        )
        metadata.pop("execution")
        for row in result["accuracy"]:
            yield {**metadata, "aspect": "accuracy", **row}
        for row in result["diversity"]:
            for metric in ("ild", "catalog_coverage", "novelty"):
                yield dict(
                    **metadata,
                    aspect="diversity",
                    task=row["task"],
                    k=row["k"],
                    metric=metric,
                    mean=row[metric],
                    n_evaluated=row.get(metric + "_evaluated", row.get("n_requests")),
                    n_excluded=row.get(metric + "_excluded"),
                )
        efficiency_fields = {
            "training_seconds",
            "evaluation_seconds",
            "ranking_seconds",
            "peak_memory_bytes",
        } | result["efficiency"].keys()
        for metric in sorted(efficiency_fields):
            if metric != "model":
                yield dict(
                    **metadata,
                    aspect="efficiency",
                    task="all",
                    metric=metric,
                    mean=result["efficiency"].get(metric),
                )
    for reference, result in complementarity:
        for row in result["metrics"]:
            yield {**reference, "source_kind": "complementarity", "aspect": "diagnostic", **row}
        for metric, value in result["efficiency"].items():
            yield dict(
                **reference,
                source_kind="complementarity",
                aspect="efficiency",
                task="all",
                metric=metric,
                mean=value,
            )


def run(
    results,
    provenance,
    output,
    *,
    expected_users=119133,
    expected_new_conditions=22,
    expected_bridges=2,
    expected_parameters=4,
):
    """Verify completed files only; never evaluate, select models, or open Test data."""
    output, provenance = Path(output).resolve(), Path(provenance).resolve()
    if output.exists():
        raise FileExistsError("Additional Validation output must be new")
    audit = _read(provenance)
    if audit.get("format") != "music-recsys-additional-provenance-audit-v1":
        raise ValueError("An additional Validation provenance audit is required")
    audit_hash = file_sha256(provenance)
    sources = _Sources(output, expected_users, audit)
    stages, complementarity, new_conditions = [], [], set()
    bridge_count = parameter_count = 0
    paths = [Path(p).resolve() for p in results]
    paths = [p / "result.json" if p.is_dir() else p for p in paths]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate stage result")
    for path in paths:
        directory = path.parent
        if path.name != "result.json":
            raise ValueError("Each source must be a completed result.json")
        result, config = _read(path), _read(directory / "config.json")
        _success(result)
        reference = sources.reference(directory)
        kind = result.get("format")
        if kind == FULL:
            bridge_count += 1
            sources.leaf(directory, new=True)
            new_conditions.add(str(directory))
        elif kind == PARAMETERS:
            parameter_count += 1
            if (
                result["source_sha256"] != audit["snapshot_source_sha256"]
                or config["source_sha256"] != result["source_sha256"]
                or result["dependency_lock_sha256"] != audit["lock"]["snapshot_sha256"]
                or config["dependency_lock_sha256"] != result["dependency_lock_sha256"]
            ):
                raise ValueError("Parameter stage differs from the audited source snapshot")
            if result["planned"] != result["completed"] or result["planned"] <= 0:
                raise ValueError("Parameter stage has incomplete planned conditions")
            count = 0
            for declared in result["sources"].values():
                new = not declared["historical"]
                child = Path(declared["output"]).resolve()
                sources.leaf(child, declared=declared, new=new)
                if new:
                    if not child.is_relative_to(directory):
                        raise ValueError("New parameter condition is outside its stage")
                    count += 1
                    if str(child) in new_conditions:
                        raise ValueError("New condition occurs in multiple stages")
                    new_conditions.add(str(child))
            if count != result["planned"]:
                raise ValueError("Parameter stage sources differ from its planned count")
            if (
                config["settings"]["evaluation"] != sources.identity["evaluation"]
                or config["features_sha256"] != sources.identity["features_sha256"]
            ):
                raise ValueError("Parameter stage evaluation/features differ from its conditions")
        elif kind == COMPLEMENTARITY:
            sources.identity_matches(config["inputs"])
            if result["population"] != config["inputs"]["population"]:
                raise ValueError("Complementarity population differs from its config")
            if result["ks"] != config["inputs"]["evaluation"]["ks"]:
                raise ValueError("Complementarity K values differ from its config")
            if result["window_days"] != config["inputs"]["evaluation"]["window_days"]:
                raise ValueError("Complementarity observation windows differ from its config")
            if set(result["models"]) != set(config["sources"]):
                raise ValueError("Complementarity source models differ")
            for declared in config["sources"].values():
                sources.leaf(Path(declared["result"]).parent, declared=declared)
            sources.artifacts(directory, result)
            complementarity.append((reference, result))
        else:
            raise ValueError("Unsupported additional Validation source format")
        sources.figure_refs(directory, result)
        stages.append(dict(**reference, format=kind))
    if (
        bridge_count != expected_bridges
        or parameter_count != expected_parameters
        or len(complementarity) != 1
        or len(new_conditions) != expected_new_conditions
    ):
        raise ValueError("Additional Validation stage/new-condition counts are incomplete")
    for control in audit.get("controls", []):
        reference = sources.reference(control["output"])
        if any(reference[k] != control[k] for k in ("result_sha256", "config_sha256")):
            raise ValueError("Historical control differs from its provenance audit")
    rows = list(_rows(sources, complementarity))
    columns = [
        "source_kind",
        "condition",
        "model",
        "parameters",
        "seed",
        "new_condition",
        "aspect",
        "task",
        "window_days",
        "day_weight",
        "model_left",
        "model_right",
        "metric",
        "k",
        "mean",
    ]
    columns += sorted({key for row in rows for key in row} - set(columns))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="additional-validation-", dir=output.parent
    ) as temporary:
        staged = Path(temporary) / "report"
        staged.mkdir()
        atomic_json(
            staged / "config.json",
            dict(
                format="music-recsys-additional-validation-config-v1",
                implementation_sha256=file_sha256(Path(__file__)),
                sources=stages,
                provenance=dict(path=str(provenance), sha256=audit_hash),
                expected=dict(
                    users=expected_users,
                    new_conditions=expected_new_conditions,
                    bridges=expected_bridges,
                    parameters=expected_parameters,
                ),
            ),
        )
        with (staged / "metrics.csv").open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        atomic_json(
            staged / "result.json",
            dict(
                format="music-recsys-additional-validation-v1",
                status="succeeded",
                phase="validation",
                population=sources.identity["population"],
                evaluation=sources.identity["evaluation"],
                new_conditions=len(new_conditions),
                stages=stages,
                conditions=[
                    dict(**leaf["reference"], new_condition=leaf["new"])
                    for leaf in sources.leaves.values()
                ],
                provenance=dict(path=str(provenance), sha256=audit_hash, audit=audit),
                figures=list(sources.figures.values()),
                artifacts=dict(
                    metrics=dict(path="metrics.csv", sha256=file_sha256(staged / "metrics.csv"))
                ),
                note="Read-only Validation diagnostics; no model selected; Test not opened; unknown values remain null",
            ),
        )
        (staged / "run.log").write_text(
            f"{datetime.now(UTC).isoformat()} SUCCEEDED: {len(new_conditions)} new conditions; "
            f"{expected_users} learning users; verified sources and artifacts\n",
            encoding="utf-8",
        )
        for reference in sources.references.values():
            for key in ("result", "config"):
                if file_sha256(reference[key]) != reference[key + "_sha256"]:
                    raise ValueError("Source changed before report publication")
        if file_sha256(provenance) != audit_hash:
            raise ValueError("Provenance audit changed before publication")
        output.mkdir()  # Claim the new destination before the atomic directory publication.
        try:
            os.replace(staged, output)
        except BaseException:
            output.rmdir()
            raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", action="append", required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run([path for group in args.results for path in group], args.provenance, args.output))


if __name__ == "__main__":
    main()
