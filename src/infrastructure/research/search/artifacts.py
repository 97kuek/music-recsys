"""Hash and integrity checks for saved search artifacts."""

import hashlib
import json
from pathlib import Path

from src.infrastructure.research.store import read_value as read_json


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def seal(value):
    return {**value, "content_sha256": digest(value)}


def read_sealed(path):
    if not Path(path).exists():
        raise ValueError("設定の選択・固定がまだです。02_analysisで状態を確認してください")
    raw = read_json(path)
    checksum = raw.pop("content_sha256", None)
    if checksum != digest(raw):
        raise ValueError("比較条件不一致: 選択artifactが改変されています")
    return {**raw, "content_sha256": checksum}
