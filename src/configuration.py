"""Shared experiment settings with explicit inheritance and source-relative paths."""

import json
import tomllib
from pathlib import Path


def load_settings(path, *, sources=None, _parents=()):
    path = Path(path).resolve()
    if path in _parents:
        raise ValueError(f"Circular configuration inheritance: {path}")
    content = path.read_bytes()
    if sources is not None:
        sources[path] = content
    text = content.decode("utf-8")
    raw = json.loads(text) if path.suffix == ".json" else tomllib.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be an object")
    parent = raw.pop("extends", None)
    base = {}
    if parent is not None:
        if not isinstance(parent, str) or not parent:
            raise ValueError("extends must be a nonempty path")
        base = load_settings(path.parent / parent, sources=sources, _parents=(*_parents, path))
    for section, keys in (
        ("experiment", ("validation_dataset", "test_dataset", "features", "output")),
        ("search", ("parent",)),
    ):
        for key in keys:
            value = raw.get(section, {}).get(key)
            if value:
                raw[section][key] = str((path.parent / value).resolve())
    return _merge(base, raw)


def _merge(base, override):
    result = dict(base)
    for key, value in override.items():
        result[key] = (
            _merge(result[key], value)
            if isinstance(result.get(key), dict) and isinstance(value, dict)
            else value
        )
    return result
