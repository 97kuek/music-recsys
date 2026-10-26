"""Local resources and isolated processes for sensitivity workflows."""

import json
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from src.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from src.infrastructure.provenance import source_hash
from src.infrastructure.research.comparison import staged_report
from src.infrastructure.research.configuration import load_settings
from src.infrastructure.research.parameters import conditions, controls, verify
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.paths import PROJECT_ROOT


class LocalSensitivity:
    def __init__(self, summarize):
        self._summarize = summarize

    @contextmanager
    def open(self, path):
        config = load_study_config(path)
        planned = list(conditions(config))
        references = list(controls(config))
        manifest, _, population = validate_bundle(config.validation_dataset)
        if manifest["phase"] != "validation":
            raise ValueError("Validation only; Test is not opened")
        identity = dict(
            format="music-recsys-parameter-sensitivity-config-v1",
            settings=json.loads(json.dumps(asdict(config), default=str)),
            search=load_settings(config.path)["search"],
            source_sha256=source_hash(),
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            manifest_sha256=file_sha256(config.validation_dataset / "manifest.json"),
            features_sha256=file_sha256(config.features),
        )

        with study_lock(config), (config.output / "run.log").open("a") as log:
            saved = config.output / "config.json"
            if saved.exists() and json.loads(saved.read_text(encoding="utf-8")) != identity:
                raise ValueError("study source/input/plan changed; use a new output")
            if not saved.exists():
                atomic_json(saved, identity)
            marker = config.output / "result.json"
            previous = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else None
            yield _SensitivitySession(
                config,
                planned,
                references,
                (manifest, population),
                identity,
                previous,
                log,
                self._summarize,
            )


class _SensitivitySession:
    def __init__(self, config, planned, references, validated, identity, previous, log, summarize):
        self._config = config
        self._planned = dict(planned)
        self._trials = dict([*references, *planned])
        self._historical = {name for name, _ in references}
        self._validated = validated
        self._identity = identity
        self._previous = previous
        self._log = log
        self._summarize = summarize

    @property
    def output(self):
        return self._config.output

    @property
    def complete(self):
        return self._previous is not None

    @property
    def conditions(self):
        return tuple(self._trials)

    @property
    def batches(self):
        if not self._identity["search"].get("shared_neighbors"):
            return ()
        if self._config.model != "bm25" or not self._config.parameters.get("use_gpu"):
            raise ValueError("shared_neighbors requires GPU BM25")
        return tuple(
            dict.fromkeys((t.parameters["k1"], t.parameters["b"]) for t in self._planned.values())
        )

    def batch_completed(self, batch):
        k1, b = batch
        group = [
            t for t in self._planned.values() if t.parameters["k1"] == k1 and t.parameters["b"] == b
        ]
        if all((t.output / "result.json").exists() for t in group):
            return True
        if any(t.output.exists() for t in group):
            raise ValueError("partial shared-neighbor group; inspect preserved evidence")
        return False

    def check_space(self, minimum_free_gb):
        if shutil.disk_usage(self.output).free < minimum_free_gb * 1024**3:
            raise RuntimeError("insufficient free disk")

    def _record(self, message):
        text = f"{datetime.now(UTC).isoformat()} {message}"
        print(text, flush=True)
        self._log.write(text + "\n")
        self._log.flush()

    def execute_batch(self, batch):
        k1, b = batch
        self._record(f"START shared BM25 k1={k1:g} b={b:g}")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "src.interfaces.cli.bm25_grid",
                "--config",
                str(self._config.path),
                "--k1",
                str(k1),
                "--b",
                str(b),
            ],
            check=True,
        )

    def completed(self, name):
        trial = self._trials[name]
        if name in self._historical or (trial.output / "result.json").exists():
            return True
        if trial.output.exists() and any(p.name != ".lock" for p in trial.output.iterdir()):
            raise ValueError(f"incomplete condition {name}; inspect before retrying")
        return False

    def execute(self, name):
        self._record(f"START {name}")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "src.interfaces.cli.sensitivity",
                "--config",
                str(self._config.path),
                "--condition",
                name,
            ],
            check=True,
        )

    def verify(self, name):
        result, source = verify(
            self._trials[name], historical=name in self._historical, validated=self._validated
        )
        if self.complete and self._previous["sources"][name] != source:
            raise ValueError("published comparison source changed")
        return result, source

    def publish(self, finished):
        config, identity, planned = self._config, self._identity, self._planned
        finished = [(name, self._trials[name], result, source) for name, result, source in finished]
        with staged_report(config.output) as temporary:
            figures = self._summarize(config, finished, temporary)
            atomic_json(
                temporary / "result.json",
                dict(
                    format="music-recsys-parameter-sensitivity-v1",
                    status="succeeded",
                    phase="validation",
                    planned=len(planned),
                    completed=len(planned),
                    figures=figures,
                    sources={name: source for name, _, _, source in finished},
                    note="No model selected; Test not opened; historical source differences retained",
                    source_sha256=identity["source_sha256"],
                    dependency_lock_sha256=identity["dependency_lock_sha256"],
                    inference=(
                        "Exploratory paired differences: each condition minus the first explicit baseline; user bootstrap 2000/seed42, CI at 90d/weight1/K10,20, means all windows/K; no multiplicity correction or training-seed replication; all metrics retained; no model selected"
                        if identity["search"].get("publication")
                        else None
                    ),
                    artifacts={
                        filename: dict(path=filename, sha256=file_sha256(temporary / filename))
                        for filename in ("metrics.csv", "paired.csv")
                        if (temporary / filename).exists()
                    },
                ),
            )
