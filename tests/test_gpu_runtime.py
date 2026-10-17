import subprocess

import pytest

from src.infrastructure.recommenders import gpu_runtime


def test_gpu_preflight_accepts_own_context_but_rejects_other_jobs(monkeypatch):
    monkeypatch.setattr(gpu_runtime.os, "getpid", lambda: 100)
    answers = iter(["100\n", "RTX 3090, 1 MiB, 24576 MiB, 0 %"])
    monkeypatch.setattr(gpu_runtime.subprocess, "check_output", lambda *a, **kw: next(answers))
    assert "RTX 3090" in gpu_runtime.require_idle_gpu()
    monkeypatch.setattr(gpu_runtime.subprocess, "check_output", lambda *a, **kw: "100\n200\n")
    with pytest.raises(RuntimeError, match="200"):
        gpu_runtime.require_idle_gpu()


def test_gpu_metadata_keeps_missing_information_null(monkeypatch):
    def unavailable(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "nvidia-smi")

    monkeypatch.setattr(gpu_runtime.subprocess, "check_output", unavailable)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    assert gpu_runtime.gpu_metadata() == dict(gpu_inventory=None, cuda_visible_devices=None)
