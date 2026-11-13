"""Immutable Validation candidate sources and evaluation for the fixed Hybrid study."""

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, replace
from itertools import islice
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from backend.infrastructure.datasets.candidates import CandidateWriter
from backend.infrastructure.datasets.full_io import user_rows
from backend.infrastructure.datasets.listening_data import file_sha256, read_bundle, validate_bundle
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.hybrid_config import plan, read, reference, verify_references
from backend.infrastructure.research.results import verify_artifacts
from backend.infrastructure.research.store import atomic_json
from backend.paths import PROJECT_ROOT


class HybridStudy:
    def __init__(self, config, *, report=None, phase="validation", ease_memory_budget_mb=None):
        if phase not in {"validation", "test"}:
            raise ValueError("Hybrid phase must be validation or test")
        self.phase, self.report = phase, report
        if ease_memory_budget_mb is not None:
            from backend.infrastructure.recommenders.factors import number

            number(ease_memory_budget_mb, "ease_memory_budget_mb", positive=True)
        self.ease_memory_budget_mb = ease_memory_budget_mb
        self.spec, self.retrieval, self.sources, self.conditions = plan(config)
        self.validation_reference = None
        if phase == "test":
            from backend.infrastructure.research.results import read_result

            previous = read_result(config)
            if previous["status"] != "succeeded" or previous["phase"] != "validation":
                raise ValueError("Test requires the unchanged successful Validation plan")
            self.validation_reference = reference(config.output)
            config = replace(config, output=config.output.parent / "test")
        self.config, self.output = config, config.output
        dataset = config.test_dataset if phase == "test" else config.validation_dataset
        self.manifest, self.paths, self.counts = validate_bundle(dataset)
        if self.manifest["phase"] != phase:
            raise ValueError("Hybrid input phase differs")
        self.input_hashes = {str(p): file_sha256(p) for p in self.paths.values()}
        self.features_sha = file_sha256(config.features)
        self.signature = dict(
            settings=json.loads(json.dumps(asdict(config), default=str)),
            manifest=self.manifest,
            features_sha256=self.features_sha,
            source_sha256=source_hash(),
        )
        if self.validation_reference is not None:
            self.signature["validation_reference"] = self.validation_reference
            self.signature["input_sha256"] = self.input_hashes
        if ease_memory_budget_mb is not None:
            self.signature["execution_options"] = dict(ease_memory_budget_mb=ease_memory_budget_mb)
        validation_manifest, _ = read_bundle(config.validation_dataset)
        for source in self.sources.values():
            saved = read(Path(source["directory"]) / "config.json")
            if (
                saved["manifest"] != validation_manifest
                or saved["features_sha256"] != self.features_sha
                or saved["settings"]["evaluation"] != self.signature["settings"]["evaluation"]
            ):
                raise ValueError("Validation model input, features or evaluation differs")
        self._features = None

    def freeze(self):
        import duckdb

        if (self.output / "result.json").exists():
            raise FileExistsError("Completed Hybrid evaluation is immutable")
        target = self.output / "config.json"
        document = dict(
            format="music-recsys-full-config-v1",
            **self.signature,
            pipeline=asdict(self.spec),
            retrieval_m=self.retrieval,
            sources=self.sources,
            conditions=[asdict(c) for c in self.conditions],
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            selection="not_selected",
            phase=self.phase,
            policy="No Test selection; all learning users; same source weights across single/RRF/MMR; four aspects and K2-20; Test refits every source on Test learning only; existing single-model Test has already been inspected",
        )
        document = json.loads(json.dumps(document))
        if target.exists():
            if read(target) != document:
                raise ValueError("Frozen Hybrid settings or source changed")
        else:
            if self.output.exists():
                raise FileExistsError("Partial study without a frozen config")
            self.output.mkdir(parents=True)
            atomic_json(target, document)
        catalog = self.output / "catalog.parquet"
        if not catalog.exists():
            with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as con:
                table = con.execute(
                    "SELECT item_id,sum(play_count)::DOUBLE plays,count(*)::BIGINT users FROM read_parquet(?) GROUP BY item_id ORDER BY item_id",
                    [str(self.paths["learning"])],
                ).to_arrow_table()
            import pyarrow.parquet as pq

            pq.write_table(table, catalog.with_suffix(".partial"), compression="zstd")
            os.replace(catalog.with_suffix(".partial"), catalog)
        return document

    def catalog(self):
        import pyarrow.parquet as pq

        table = pq.read_table(self.output / "catalog.parquet")
        return (
            np.asarray(table["item_id"].to_pylist()),
            np.asarray(table["plays"]),
            dict(zip(table["item_id"].to_pylist(), table["users"].to_pylist(), strict=True)),
        )

    def features(self):
        if self._features is None:
            from backend.infrastructure.datasets.features import load_features

            self._features = load_features(
                self.config.features,
                dataset_id=self.manifest["dataset_id"],
                snapshot_id=self.manifest["source"]["snapshot_id"],
            )
        return self._features

    def check(self):
        if read(self.output / "config.json")["source_sha256"] != source_hash():
            raise ValueError("Hybrid processing source changed")
        verify_references(s["evidence"] for s in self.sources.values())
        if file_sha256(self.config.features) != self.features_sha:
            raise ValueError("Feature artifact changed")
        if self.validation_reference is not None:
            verify_references([self.validation_reference])
            if any(file_sha256(p) != digest for p, digest in self.input_hashes.items()):
                raise ValueError("Frozen Test input changed")

    def prepare(self, source_id):
        self.check()
        directory = self.output / "candidates" / source_id
        if not (directory / "result.json").exists():
            if directory.exists():
                raise FileExistsError("Partial candidate output is preserved")
            if shutil.disk_usage(self.output).free < 512 * 1024**2:
                raise RuntimeError("Insufficient disk for candidates; existing data preserved")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "backend.interfaces.cli.hybrid",
                    "--config",
                    str(self.config.path),
                    "--phase",
                    self.phase,
                    "--source",
                    source_id,
                    *(
                        ["--ease-memory-budget-mb", str(self.ease_memory_budget_mb)]
                        if self.ease_memory_budget_mb is not None
                        else []
                    ),
                ],
                check=True,
            )
        result = read(directory / "result.json")
        if result["signature"] != self.signature or result["source"] != self.sources[source_id]:
            raise ValueError("Candidate cache identity differs")
        verify_artifacts(directory, result["artifacts"])
        return dict(directory=str(directory), **result)

    def build_source(self, source_id):
        import cupy as cp

        from backend.infrastructure.evaluation.gpu_evaluation import GPURanker
        from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

        self.check()
        print("Candidate GPU preflight: " + require_idle_gpu(), flush=True)
        source = self.sources[source_id]
        directory = self.output / "candidates" / source_id
        directory.mkdir(parents=True, exist_ok=False)
        with (directory / "run.log").open("x") as log:

            def progress(text):
                print(text, flush=True)
                log.write(text + "\n")
                log.flush()

            atomic_json(
                directory / "config.json",
                dict(signature=self.signature, source=source, retrieval_m=self.retrieval),
            )
            items, plays, _ = self.catalog()
            started = time.perf_counter()
            if source["model"] == "bpr" and self.phase == "validation":
                from backend.infrastructure.models.artifacts import load_model

                path = Path(source["directory"]) / "model"
                manifest = read(path / "manifest.json")
                spec, deployed = load_model(
                    path, expected_manifest_sha256=file_sha256(path / "manifest.json")
                )
                if (
                    spec.algorithm != "bpr"
                    or spec.seed != source["seed"]
                    or spec.training_ref != file_sha256(self.paths["learning"])
                    or spec.parameters != source["parameters"]
                ):
                    raise ValueError("Saved BPR weights differ from the Validation version")
                model = SimpleNamespace(
                    **{k: v for k, v in deployed.arrays.items() if k != "users"},
                    index=deployed.index,
                    users=deployed.users,
                )
                model.items = np.asarray(deployed.arrays["items"])
                model.plays = np.asarray(deployed.arrays["plays"])
                training_seconds = read(Path(source["directory"]) / "result.json")["efficiency"][
                    "training_seconds"
                ]
                execution = dict(
                    training_reused=True,
                    model_manifest_sha256=file_sha256(path / "manifest.json"),
                    training_execution=manifest.get("training_execution"),
                )
            else:
                from backend.infrastructure.recommenders.full_bm25 import FullBM25
                from backend.infrastructure.recommenders.full_bpr import FullBPR
                from backend.infrastructure.recommenders.full_content import FullContent
                from backend.infrastructure.recommenders.full_ease import FullEASE
                from backend.infrastructure.recommenders.full_transition import FullTransition

                cls = {
                    "ease": FullEASE,
                    "bm25": FullBM25,
                    "content": FullContent,
                    "bpr": FullBPR,
                    "transition_rules": FullTransition,
                }[source["model"]]
                extra = dict(features=self.features()) if source["model"] == "content" else {}
                if source["model"] == "bpr":
                    extra["seed"] = source["seed"]
                if source["model"] == "transition_rules":
                    dataset = (
                        self.config.validation_dataset
                        if self.phase == "validation"
                        else self.config.test_dataset
                    )
                    roles = ("train",) if self.phase == "validation" else ("train", "validation")
                    extra.update(
                        events_path=[
                            str(dataset.parent / "source" / f"{role}.parquet") for role in roles
                        ],
                        events_sha256=[
                            self.manifest["source_files"][role]["sha256"] for role in roles
                        ],
                        prediction_at=self.manifest["prediction_at"],
                    )
                parameters = dict(source["parameters"])
                if source["model"] == "ease" and self.ease_memory_budget_mb is not None:
                    if not parameters.get("use_gpu"):
                        raise ValueError("EASE memory override requires GPU EASE")
                    parameters["memory_budget_mb"] = self.ease_memory_budget_mb
                model = cls(self.paths["learning"], **parameters, **extra, progress=progress)
                training_seconds = model.training_seconds
                execution = dict(
                    training_reused=False,
                    training_execution=model.execution,
                    training_parameters={
                        **parameters,
                        **({"seed": source["seed"]} if source["model"] == "bpr" else {}),
                    },
                )
            if not np.array_equal(model.items, items) or not np.array_equal(model.plays, plays):
                raise ValueError("Source catalog/popularity differs")
            model_ready = time.perf_counter() - started
            ranker = GPURanker(model)
            writer = CandidateWriter(directory / "candidates.parquet.partial")
            processed = 0
            ranking_seconds = 0.0
            try:
                learning = iter(user_rows(self.paths["learning"]))
                while batch := list(islice(learning, 128)):
                    users = [u for u, _ in batch]
                    histories = [{r[1] for r in rows} for _, rows in batch]
                    ranked, counts, scores, elapsed = ranker.candidates(
                        users, histories, top_m=self.retrieval
                    )
                    ranking_seconds += elapsed
                    host = {
                        task: (cp.asnumpy(ranked[task]), cp.asnumpy(scores[task]))
                        for task in ("new", "repeat")
                    }
                    for n, user in enumerate(users):
                        for task in ("new", "repeat"):
                            ids, values = host[task]
                            valid = ids[n] >= 0
                            n_items = int(valid.sum())
                            writer.add(
                                dict(
                                    user_id=user,
                                    task=task,
                                    item_indices=ids[n, valid].tolist(),
                                    raw_scores=values[n, valid].tolist(),
                                    source_ranks=list(range(1, n_items + 1)),
                                    candidate_count=int(counts[task][n]),
                                )
                            )
                    processed += len(users)
                    if processed % 8192 < 128:
                        progress(f"Candidates {source_id}: {processed:,}/{self.counts['users']:,}")
            finally:
                writer.close()
            if processed != self.counts["users"]:
                raise ValueError("Incomplete candidate population")
            self.check()
            os.replace(directory / "candidates.parquet.partial", directory / "candidates.parquet")
            result = dict(
                status="succeeded",
                phase=self.phase,
                signature=self.signature,
                source=source,
                population=self.counts,
                retrieval_m=self.retrieval,
                training_seconds=training_seconds,
                model_ready_seconds=model_ready,
                ranking_seconds=ranking_seconds,
                elapsed_seconds=time.perf_counter() - started,
                execution=dict(**gpu_metadata(), **execution),
                artifacts=dict(
                    candidates=dict(
                        path="candidates.parquet",
                        sha256=file_sha256(directory / "candidates.parquet"),
                    )
                ),
            )
            atomic_json(directory / "result.json", result)
            progress("SUCCEEDED")
        return result

    def evaluate(self, condition, candidates):
        from backend.infrastructure.evaluation.hybrid import evaluate

        self.check()
        directory = self.output / condition.name
        if (directory / "result.json").exists():
            result = read(directory / "result.json")
            if read(directory / "config.json")["signature"] != self.signature or result[
                "condition"
            ] != json.loads(json.dumps(asdict(condition))):
                raise ValueError("Saved Hybrid condition changed")
            verify_artifacts(directory, result["artifacts"])
            return dict(directory=str(directory), **result)
        if directory.exists():
            raise FileExistsError("Partial Hybrid evaluation is preserved")
        return evaluate(self, condition, list(candidates.values()))

    def summarize(self, results):
        self.check()
        return self.report(self, results)
