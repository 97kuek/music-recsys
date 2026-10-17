"""処理版を実際のソースから識別する。"""

import hashlib
from pathlib import Path


def source_version(root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256((root / name).read_bytes()).digest())
    return "1:" + digest.hexdigest()
