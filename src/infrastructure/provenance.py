"""Shared source identity for evaluation and training provenance."""

import hashlib
from pathlib import Path

from src.paths import SOURCE_ROOT


def source_version(root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256((root / name).read_bytes()).digest())
    return "1:" + digest.hexdigest()


def source_hash() -> str:
    root = SOURCE_ROOT
    checksum = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        checksum.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        checksum.update(path.read_bytes() + b"\0")
    return checksum.hexdigest()
