"""モデル実装に依存しないMLflow実験記録。接続情報と生データは成果物に含めない。"""

import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class TrackingError(RuntimeError):
    """記録の失敗。元の例外本文や接続情報は利用者へ出さない。"""


@dataclass(frozen=True)
class TrackingConfig:
    enabled: bool = False
    tracking_uri: str = "http://127.0.0.1:5000"
    experiment_name: str = "music-recsys"
    run_name: str | None = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("tracking.enabled must be a boolean")
        if not isinstance(self.tracking_uri, str) or not self.tracking_uri.strip():
            raise ValueError("tracking URI must not be empty")
        parsed = urlsplit(self.tracking_uri)
        if parsed.scheme not in {"http", "https", "sqlite"}:
            raise ValueError("tracking URI must use HTTP(S) or SQLite")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("tracking URI must not contain credentials, query or fragment")
        if not isinstance(self.experiment_name, str) or not self.experiment_name.strip():
            raise ValueError("tracking experiment name must not be empty")
        if self.run_name is not None and (
            not isinstance(self.run_name, str) or not self.run_name.strip()
        ):
            raise ValueError("tracking run name must not be empty")

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None = None) -> "TrackingConfig":
        raw = dict(settings or {})
        if set(raw) - {"enabled", "tracking_uri", "experiment_name", "run_name"}:
            raise ValueError("unknown tracking configuration setting")
        toggle = os.environ.get("MUSIC_RECSYS_TRACKING")
        if toggle is not None:
            if toggle.lower() not in {"true", "false"}:
                raise ValueError("MUSIC_RECSYS_TRACKING must be true or false")
            raw["enabled"] = toggle.lower() == "true"
        for variable, name in (
            ("MLFLOW_TRACKING_URI", "tracking_uri"),
            ("MLFLOW_EXPERIMENT_NAME", "experiment_name"),
            ("MLFLOW_RUN_NAME", "run_name"),
        ):
            if variable in os.environ:
                raw[name] = os.environ[variable]
        return cls(**raw)


@dataclass(frozen=True)
class TrackingResult:
    run_id: str
    experiment_id: str
    status: str


class ExperimentRun:
    """Notebook、従来推薦、LLM / Agentで共有する明示的なrun context。

    paramsやmetricsには実験条件・集計値を渡す。認証情報・生データは渡さない。
    MLflowのグローバルactive runを変更せず、Notebookの別runと干渉しない。
    """

    def __init__(self, config: TrackingConfig, *, params: Mapping[str, Any] | None = None):
        if not config.enabled:
            raise ValueError("ExperimentRun requires enabled tracking")
        self.config = config
        self.params = dict(params or {})
        self.run_id: str | None = None
        self.experiment_id: str | None = None
        self.status = "RUNNING"
        self._client: Any = None

    def __enter__(self) -> "ExperimentRun":
        try:
            from mlflow import MlflowClient

            self._client = MlflowClient(tracking_uri=self.config.tracking_uri)
            experiment = self._client.get_experiment_by_name(self.config.experiment_name)
            if experiment is None:
                try:
                    experiment_id = self._client.create_experiment(self.config.experiment_name)
                except Exception:
                    # 同時に同名experimentが作成された場合だけ既存のものを使う。
                    experiment = self._client.get_experiment_by_name(self.config.experiment_name)
                    if experiment is None:
                        raise
                    experiment_id = experiment.experiment_id
            else:
                experiment_id = experiment.experiment_id
            self.experiment_id = experiment_id
            tags = {"music_recsys.tracking": "common"}
            if self.config.run_name:
                tags["mlflow.runName"] = self.config.run_name
            self.run_id = self._client.create_run(experiment_id, tags=tags).info.run_id
            self.log_params(self.params)
            return self
        except ImportError:
            raise TrackingError(
                "MLflow is unavailable; install the tracking dependency group"
            ) from None
        except Exception as error:
            self._fail_safely()
            if isinstance(error, TrackingError):
                raise
            raise TrackingError(f"MLflow run creation failed ({type(error).__name__})") from None

    def log_params(self, params: Mapping[str, Any]) -> None:
        try:
            from mlflow.entities import Param

            self._client.log_batch(
                self.run_id,
                params=[
                    Param(key, str(value)) for key, value in params.items() if value is not None
                ],
            )
        except Exception as error:
            raise TrackingError(
                f"MLflow parameter logging failed ({type(error).__name__})"
            ) from None

    def log_metrics(self, metrics: Mapping[str, float | int | None], *, step: int = 0) -> None:
        try:
            values = {key: float(value) for key, value in metrics.items() if value is not None}
        except (TypeError, ValueError, OverflowError):
            raise ValueError("tracking metrics must be numeric") from None
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("tracking metrics must be finite")
        try:
            from mlflow.entities import Metric

            timestamp = int(time.time() * 1000)
            self._client.log_batch(
                self.run_id,
                metrics=[Metric(key, value, timestamp, step) for key, value in values.items()],
            )
        except Exception as error:
            raise TrackingError(f"MLflow metric logging failed ({type(error).__name__})") from None

    def log_artifact(self, path: Path, *, artifact_path: str = "reports") -> None:
        """明示された集計成果物を送る。データ本体・生履歴は呼び出し側で除外する。"""
        try:
            self._client.log_artifact(self.run_id, str(path), artifact_path)
        except Exception as error:
            raise TrackingError(
                f"MLflow artifact logging failed ({type(error).__name__})"
            ) from None

    def mark_failed(self) -> None:
        self.status = "FAILED"

    def _fail_safely(self) -> None:
        self.status = "FAILED"
        if self.run_id is not None:
            try:
                self._client.set_terminated(self.run_id, "FAILED")
            except Exception:
                pass  # 元の記録エラーを隠さない。サーバー切断時のstatus更新は保証できない。

    def __exit__(self, error_type, error, traceback) -> bool:
        if error_type is not None:
            try:
                self._client.set_tag(self.run_id, "error_type", error_type.__name__)
            except Exception:
                pass
            self._fail_safely()
            return False
        self.status = "FAILED" if self.status == "FAILED" else "FINISHED"
        try:
            self._client.set_terminated(self.run_id, self.status)
        except Exception as failure:
            self._fail_safely()
            raise TrackingError(
                f"MLflow run completion failed ({type(failure).__name__})"
            ) from None
        return False

    @property
    def result(self) -> TrackingResult:
        if self.run_id is None or self.experiment_id is None or self.status == "RUNNING":
            raise ValueError("tracking result is available only after the run ends")
        return TrackingResult(self.run_id, self.experiment_id, self.status)
