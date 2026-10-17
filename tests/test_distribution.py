import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.interfaces.cli.online_distribution import ROOT_FILES, archive


def test_archive_includes_worktree_but_excludes_secrets_and_logs(tmp_path):
    root, bundle = tmp_path / "source", tmp_path / "bundle"
    for name in (
        *ROOT_FILES,
        "src/new.py",
        "frontend/src/app/App.tsx",
        "docs/diagrams/app.svg",
        "docs/architecture.html",
        "frontend/tests/study.test.mjs",
    ):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("uncommitted content")
    for name in (
        ".env",
        "frontend/.env",
        "src/private.key",
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
        assert "music-recsys-mac-0.2.0/src/new.py" in names
        assert "music-recsys-mac-0.2.0/docs/diagrams/app.svg" in names
        assert "music-recsys-mac-0.2.0/docs/architecture.html" in names
        assert "music-recsys-mac-0.2.0/frontend/tests/study.test.mjs" in names
        assert "music-recsys-mac-0.2.0/bundle/config.json" in names
        assert not any(name.endswith((".env", ".key", ".sqlite", ".parquet")) for name in names)
        assert package.read("music-recsys-mac-0.2.0/VERSION") == b"0.2.0\n"
        assert (
            package.getinfo("music-recsys-mac-0.2.0/MusicRecsys.command").external_attr >> 16
            == 0o100755
        )
    with pytest.raises(FileExistsError):
        archive(root, bundle, output, version="0.2.0")


@pytest.mark.parametrize("remote", [False, True])
def test_mac_launcher_uses_bundle_and_persistent_state(tmp_path, remote):
    root = tmp_path / "music-recsys mac"
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
        '#!/bin/sh\nprintf "%s\\n" "$MUSIC_RECSYS_BUNDLE" "$MUSIC_RECSYS_STATE" "$*" >> "$TEST_LOG"\nexit 0\n'
    )
    for path in binaries.iterdir():
        path.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTOLAB_", "MUSIC_RECSYS_"))}
    env.update(
        HOME=str(tmp_path / "home"),
        PATH=f"{binaries}:/usr/bin:/bin",
        TEST_LOG=str(tmp_path / "commands"),
    )
    if remote:
        env.update(
            MUSIC_RECSYS_ACCESS_TEAM="study-test",
            MUSIC_RECSYS_ACCESS_AUD="audience-test",
            MUSIC_RECSYS_ALLOWED_ORIGINS="https://music.example",
            MUSIC_RECSYS_ALLOWED_HOSTS="music.example",
            MUSIC_RECSYS_RETENTION_DAYS="7",
        )
    action = "start-remote" if remote else "start"
    response = subprocess.run(
        ["bash", str(root / "deploy/mac.sh"), action], env=env, capture_output=True, text=True
    )
    assert response.returncode == 0, response.stderr
    assert ("未認証401" if remote else "http://127.0.0.1:8765") in response.stdout
    assert (tmp_path / "home/music-recsys/state").is_dir()
    calls = (tmp_path / "commands").read_text()
    assert str(root / "bundle") in calls
    assert "up --build -d" in calls
    assert "--project-directory" not in calls
    if remote:
        assert "assert e.code == 401" in calls
        del env["MUSIC_RECSYS_ACCESS_AUD"]
        response = subprocess.run(
            ["bash", str(root / "deploy/mac.sh"), action],
            env=env,
            capture_output=True,
            text=True,
        )
        assert response.returncode == 1
        assert "MUSIC_RECSYS_ACCESS_AUD" in response.stderr
    env["MUSIC_RECSYS_ACCESS_TEAM"] = "remote"
    response = subprocess.run(
        ["bash", str(root / "deploy/mac.sh"), "start"], env=env, capture_output=True, text=True
    )
    assert response.returncode == 1


@pytest.mark.parametrize("existing_destination", [False, True])
def test_mac_state_migration_stops_both_projects_and_never_overwrites(
    tmp_path, existing_destination
):
    root = tmp_path / "package"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy/mac.sh").write_text(Path("deploy/mac.sh").read_text())
    home = tmp_path / "home"
    old = home / "OtoLab/state"
    old.mkdir(parents=True)
    (old / "sessions.sqlite").write_bytes(b"original-log")
    new = home / "music-recsys/state"
    if existing_destination:
        new.mkdir(parents=True)
        (new / "sessions.sqlite").write_bytes(b"different-log")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "uname").write_text("#!/bin/sh\necho Darwin\n")
    (binaries / "docker").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_LOG"\nexit 0\n')
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTOLAB_", "MUSIC_RECSYS_"))}
    env.update(HOME=str(home), PATH=f"{binaries}:/usr/bin:/bin", TEST_LOG=str(tmp_path / "calls"))
    result = subprocess.run(
        ["bash", str(root / "deploy/mac.sh"), "migrate-state"],
        env=env,
        capture_output=True,
        text=True,
    )
    if existing_destination:
        assert result.returncode == 1
        assert (old / "sessions.sqlite").read_bytes() == b"original-log"
        assert (new / "sessions.sqlite").read_bytes() == b"different-log"
    else:
        assert result.returncode == 0, result.stderr
        assert not old.exists()
        assert (new / "sessions.sqlite").read_bytes() == b"original-log"
        calls = (tmp_path / "calls").read_text()
        assert calls.count(" down") == 2
        assert "-p otolab" in calls
