"""GPU runtime provenance without assuming sandbox visibility reflects the host."""

import os
import subprocess


def gpu_metadata():
    try:
        inventory = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,uuid,driver_version", "--format=csv,noheader"],
            text=True,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        inventory = None
    return dict(
        gpu_inventory=inventory, cuda_visible_devices=os.environ.get("CUDA_VISIBLE_DEVICES")
    )


def require_idle_gpu():
    """Refuse to start training while another compute process owns the GPU."""
    processes = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        text=True,
        timeout=10,
    ).strip()
    others = [p.strip() for p in processes.splitlines() if p.strip() != str(os.getpid())]
    if others:
        raise RuntimeError(f"GPU is in use by other compute processes: {', '.join(others)}")
    return subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader",
        ],
        text=True,
        timeout=10,
    ).strip()
