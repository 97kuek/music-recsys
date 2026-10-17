"""データ取得・変換の境界。"""

from pathlib import Path
from typing import Protocol

from src.domain.datasets import AdapterInput, Artifact, Catalog, DatasetRelease


class ArtifactStore(Protocol):
    root: Path

    def put(self, path: Path, *, format: str, **metadata) -> Artifact: ...


class DatasetAdapter(Protocol):
    name: str
    version: str
    release: DatasetRelease

    def prepare(self, store: "ArtifactStore", sources: dict[str, Artifact]) -> AdapterInput: ...

    def load(self, inputs: AdapterInput) -> Catalog: ...
