"""Publish complete sequence weights exclusively, preserving existing checkpoints."""

import os
import tempfile
from pathlib import Path


def save_checkpoint(path, network, items, seed):
    import torch

    path = Path(path)
    if path.exists():
        raise FileExistsError("Preserve existing sequence checkpoint")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            torch.save(
                {
                    "state_dict": {k: v.detach().cpu() for k, v in network.state_dict().items()},
                    "items": items,
                    "seed": seed,
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory hard link publishes atomically and rejects a concurrent writer.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
