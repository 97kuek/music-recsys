"""Publish complete private text files exclusively, without replacing existing exports."""

import os
import tempfile
from pathlib import Path


def write_private_exclusive(target, encoded):
    target = Path(target)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
