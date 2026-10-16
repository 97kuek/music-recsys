import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from music_recsys.listening_data import file_sha256
from music_recsys.webapp.distribution import ROOT_FILES, archive


def test_archive_includes_worktree_but_excludes_secrets_and_logs(tmp_path):
    root, bundle = tmp_path / "source", tmp_path / "bundle"
    for name in (*ROOT_FILES, "src/music_recsys/new.py", "frontend/src/App.tsx"):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("uncommitted content")
    for name in (
        ".env",
        "frontend/.env",
        "src/music_recsys/private.key",
        "data/learning.parquet",
        "state/log.sqlite",
    ):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("not distributed")
    bundle.mkdir()
    (bundle / "config.json").write_text("{}")
    (bundle / "transfer.json").write_text(
        json.dumps(
            {
                "format": "otolab-transfer-v1",
                "files": {"config.json": file_sha256(bundle / "config.json")},
            }
        )
    )
    output = archive(root, bundle, tmp_path / "app.zip", version="0.2.0")
    with zipfile.ZipFile(output) as package:
        names = package.namelist()
        assert "music-recsys-mac-0.2.0/src/music_recsys/new.py" in names
        assert "music-recsys-mac-0.2.0/bundle/config.json" in names
        assert not any(name.endswith((".env", ".key", ".sqlite", ".parquet")) for name in names)
        assert package.read("music-recsys-mac-0.2.0/VERSION") == b"0.2.0\n"
        assert (
            package.getinfo("music-recsys-mac-0.2.0/MusicRecsys.command").external_attr >> 16
            == 0o100755
        )
    with pytest.raises(FileExistsError):
        archive(root, bundle, output, version="0.2.0")


def test_mac_launcher_uses_bundle_and_persistent_state(tmp_path):
    root = tmp_path / "otolab mac"
    (root / "deploy").mkdir(parents=True)
    script = Path("deploy/mac.sh").read_text()
    (root / "deploy/mac.sh").write_text(script)
    (root / "bundle").mkdir()
    (root / "bundle/transfer.json").write_text("{}")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "uname").write_text("#!/bin/sh\necho Darwin\n")
    # Docker操作は模擬。実Mac・Dockerでの成功と区別する
    (binaries / "docker").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$OTOLAB_BUNDLE" "$OTOLAB_STATE" "$*" >> "$TEST_LOG"\nexit 0\n'
    )
    for path in binaries.iterdir():
        path.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTOLAB_")}
    env.update(
        HOME=str(tmp_path / "home"),
        PATH=f"{binaries}:/usr/bin:/bin",
        TEST_LOG=str(tmp_path / "commands"),
    )
    response = subprocess.run(
        ["bash", str(root / "deploy/mac.sh"), "start"], env=env, capture_output=True, text=True
    )
    assert response.returncode == 0, response.stderr
    assert "http://127.0.0.1:8765" in response.stdout
    assert (tmp_path / "home/OtoLab/state").is_dir()
    calls = (tmp_path / "commands").read_text()
    assert str(root / "bundle") in calls
    assert "up --build -d" in calls
    assert "--project-directory" not in calls
    env["OTOLAB_ACCESS_TEAM"] = "remote"
    response = subprocess.run(
        ["bash", str(root / "deploy/mac.sh"), "start"], env=env, capture_output=True, text=True
    )
    assert response.returncode == 1
