"""Local persistence and isolated process execution for resumable factor studies."""

import json
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.provenance import source_hash
from src.infrastructure.research.grid import factor_conditions
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.paths import PROJECT_ROOT


def verify(trial):
    from src.infrastructure.models.artifacts import load_model
    from src.infrastructure.provenance import source_hash
    from src.infrastructure.research.comparison import verified_result

    result, saved, _, _ = verified_result(trial, trial.evaluation)
    if saved["source_sha256"] != source_hash():
        raise ValueError("completed condition source changed; use a new study output")
    if saved["dependency_lock_sha256"] != file_sha256(PROJECT_ROOT / "uv.lock"):
        raise ValueError("completed condition dependency lock changed")
    serving = result["serving_model"]
    load_model(trial.output / "model", expected_manifest_sha256=serving["manifest_sha256"])
    return result


class LocalStudy:
    def __init__(self, summarize):
        self._summarize = summarize

    @contextmanager
    def open(self, path):
        config = load_study_config(path)
        planned = list(factor_conditions(config))
        identity = dict(
            format="music-recsys-factor-study-config-v1",
            settings=json.loads(json.dumps(asdict(config), default=str)),
            source_sha256=source_hash(),
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            manifest_sha256=file_sha256(config.validation_dataset / "manifest.json"),
            features_sha256=file_sha256(config.features),
            plan=[
                dict(condition=name, parameters=trial.parameters, seed=trial.seeds[0])
                for name, trial in planned
            ],
        )
        with study_lock(config), (config.output / "run.log").open("a") as log:
            saved = config.output / "config.json"
            if saved.exists() and json.loads(saved.read_text()) != identity:
                raise ValueError("study source/input/plan changed; use a new output")
            if not saved.exists():
                atomic_json(saved, identity)
            previous = config.output / "result.json"
            complete = (
                previous.exists() and json.loads(previous.read_text()).get("status") == "succeeded"
            )
            yield _StudySession(config, planned, log, complete, self._summarize)


class _StudySession:
    def __init__(self, config, planned, log, complete, summarize):
        self._config = config
        self._trials = dict(planned)
        self._log = log
        self._complete = complete
        self._summarize = summarize

    @property
    def output(self):
        return self._config.output

    @property
    def conditions(self):
        return tuple(self._trials)

    @property
    def complete(self):
        return self._complete

    def check_space(self, minimum_free_gb):
        if shutil.disk_usage(self.output).free < minimum_free_gb * 1024**3:
            raise RuntimeError(
                f"less than {minimum_free_gb} GiB free disk; resume after freeing space"
            )

    def completed(self, name):
        trial = self._trials[name]
        if (trial.output / "result.json").exists():
            return True
        if (trial.output / "model").exists():
            raise ValueError(f"incomplete condition {name} has a model; inspect before resuming")
        return False

    def execute(self, name):
        # Keep each training process isolated so GPU/CPU memory is released between conditions.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "src.interfaces.cli.factor_study",
                "--config",
                str(self._config.path),
                "--condition",
                name,
            ],
            check=True,
        )

    def verify(self, name):
        return verify(self._trials[name])

    def evidence(self, name, result):
        trial = self._trials[name]
        return dict(
            condition=name,
            status="succeeded",
            result=f"{name}/result.json",
            result_sha256=file_sha256(trial.output / "result.json"),
            training_seconds=result["efficiency"]["training_seconds"],
            evaluation_seconds=result["efficiency"]["evaluation_seconds"],
        )

    def summarize(self, finished):
        return self._summarize(
            self._config, [(name, self._trials[name], result) for name, result in finished]
        )

    def record(self, message):
        text = f"{datetime.now(UTC).isoformat()} {message}"
        print(text, flush=True)
        self._log.write(text + "\n")
        self._log.flush()

    def checkpoint(self, state):
        atomic_json(self.output / "result.json", state)
