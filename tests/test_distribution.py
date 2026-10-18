import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.interfaces.cli.online_distribution import ROOT_FILES, archive


@pytest.mark.parametrize("mode", ["demo", "music4all"])
def test_archive_includes_worktree_but_excludes_secrets_and_logs(tmp_path, mode):
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
    (bundle / "config.json").write_text(json.dumps({"mode": mode}))
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
        assert ("music-recsys-mac-0.2.0/DEMO" in names) == (mode == "demo")
        assert (
            package.getinfo("music-recsys-mac-0.2.0/MusicRecsys.command").external_attr >> 16
            == 0o100755
        )
    with pytest.raises(FileExistsError):
        archive(root, bundle, output, version="0.2.0")
    if mode != "demo":
        with pytest.raises(ValueError, match="synthetic demo"):
            archive(root, bundle, tmp_path / "invalid.zip", version="0.4.3-demo")


@pytest.mark.parametrize("action", ["start", "researcher", "start-remote", "start-share"])
@pytest.mark.parametrize("demo_zip,configured_days", [(False, None), (True, None), (True, "7")])
def test_mac_launcher_uses_bundle_and_persistent_state(tmp_path, action, demo_zip, configured_days):
    remote = action in {"start-remote", "start-share"}
    root = tmp_path / "music-recsys mac"
    (root / "deploy").mkdir(parents=True)
    script = Path("deploy/mac.sh").read_text()
    (root / "deploy/mac.sh").write_text(script)
    (root / "bundle").mkdir()
    (root / "bundle/transfer.json").write_text("{}")
    if demo_zip:
        (root / "DEMO").write_text("Synthetic audio preview only\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "uname").write_text("#!/bin/sh\necho Darwin\n")
    # Docker操作は模擬。実Mac・Dockerでの成功と区別する
    (binaries / "docker").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$MUSIC_RECSYS_BUNDLE" "$MUSIC_RECSYS_STATE" "retention=$MUSIC_RECSYS_RETENTION_DAYS" "$*" >> "$TEST_LOG"\nexit 0\n'
    )
    (binaries / "ngrok").write_text('#!/bin/sh\nprintf "%s\n" "$*" >> "$TEST_LOG"\nexit 0\n')
    for path in binaries.iterdir():
        path.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTOLAB_", "MUSIC_RECSYS_"))}
    env.update(
        HOME=str(tmp_path / "home"),
        PATH=f"{binaries}:/usr/bin:/bin",
        TEST_LOG=str(tmp_path / "commands"),
    )
    if configured_days:
        env["MUSIC_RECSYS_RETENTION_DAYS"] = configured_days
    if remote:
        env.update(
            MUSIC_RECSYS_ACCESS_TEAM="study-test",
            MUSIC_RECSYS_ACCESS_AUD="audience-test",
            MUSIC_RECSYS_ALLOWED_ORIGINS="https://music.example",
            MUSIC_RECSYS_ALLOWED_HOSTS="music.example",
            MUSIC_RECSYS_RETENTION_DAYS="7",
        )
    if action == "start-share":
        env.update(
            MUSIC_RECSYS_SHARE_URL="https://study.synthetic.workers.dev",
            MUSIC_RECSYS_TUNNEL_URL="https://synthetic.ngrok-free.app",
        )
    response = subprocess.run(
        ["bash", str(root / "deploy/mac.sh"), action], env=env, capture_output=True, text=True
    )
    assert response.returncode == 0, response.stderr
    expected_url = (
        "http://localhost:8766/#online-analysis"
        if action == "researcher"
        else "http://127.0.0.1:8765"
    )
    assert ("未認証401" if remote else expected_url) in response.stdout
    state_name = "demo-online-state" if demo_zip and not remote else "state"
    assert (tmp_path / f"home/music-recsys/{state_name}").is_dir()
    calls = (tmp_path / "commands").read_text()
    assert str(root / "bundle") in calls
    assert "up --build -d" in calls
    service = "researcher" if action == "researcher" else "app"
    assert f"up --build -d {service}" in calls
    assert f"exec -T {service} python" in calls
    assert "--profile researcher" in calls
    assert "--project-directory" not in calls
    if action == "start-share":
        assert "https://study.synthetic.workers.dev/" in response.stdout
        assert "--url https://synthetic.ngrok-free.app --inspect=false" in calls
    expected_days = "7" if remote else (configured_days or ("1" if demo_zip else ""))
    assert f"retention={expected_days}\n" in calls
    if not remote and demo_zip:
        assert f"新規参加の保存日数: {expected_days} 日" in response.stdout
    if remote:
        assert "assert e.code == 401" in calls
        del env["MUSIC_RECSYS_RETENTION_DAYS"]
        missing_days = subprocess.run(
            ["bash", str(root / "deploy/mac.sh"), action], env=env, capture_output=True, text=True
        )
        assert missing_days.returncode == 1
        assert "MUSIC_RECSYS_RETENTION_DAYS" in missing_days.stderr
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
    response = subprocess.run(
        ["bash", str(root / "deploy/mac.sh"), "researcher"], env=env, capture_output=True, text=True
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


def test_fixed_link_can_be_displayed_while_docker_is_stopped(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "uname").write_text("#!/bin/sh\necho Darwin\n")
    (binaries / "docker").write_text("#!/bin/sh\nexit 1\n")
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{binaries}:/usr/bin:/bin",
        "MUSIC_RECSYS_SHARE_URL": "https://study.synthetic.workers.dev",
    }
    result = subprocess.run(
        ["bash", "deploy/mac.sh", "share-link"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "https://study.synthetic.workers.dev/"


def test_pages_launcher_registers_ephemeral_tunnel_without_exposing_token(tmp_path):
    root = tmp_path / "package"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy/pages.sh").write_text(Path("deploy/pages.sh").read_text())
    (root / "deploy/mac.sh").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" "$MUSIC_RECSYS_ALLOWED_HOSTS" '
        '"$MUSIC_RECSYS_ALLOWED_ORIGINS" > "$TEST_START"\n'
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    scripts = {
        "uname": "echo Darwin\n",
        "cloudflared": "echo 'https://changing-example.trycloudflare.com'\nexec sleep 30\n",
        "curl": 'printf "%s\\n" "$*" > "$TEST_ARGS"\ncp "$2" "$TEST_CONFIG"\n',
    }
    for name, script in scripts.items():
        binary = binaries / name
        binary.write_text("#!/bin/sh\n" + script)
        binary.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{binaries}:/usr/bin:/bin",
        "MUSIC_RECSYS_PAGES_URL": "https://study-example.pages.dev",
        "MUSIC_RECSYS_PUBLISH_TOKEN": "a" * 64,
        "TEST_START": str(tmp_path / "start"),
        "TEST_ARGS": str(tmp_path / "args"),
        "TEST_CONFIG": str(tmp_path / "config"),
    }
    process = subprocess.Popen(
        ["bash", str(root / "deploy/pages.sh")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(60):
            if (tmp_path / "config").exists():
                break
            time.sleep(0.05)
        assert (tmp_path / "config").exists()
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    assert "配布する固定リンク: https://study-example.pages.dev/" in stdout
    assert "a" * 64 not in stdout + stderr + (tmp_path / "args").read_text()
    assert (tmp_path / "start").read_text().splitlines() == [
        "start-remote",
        "study-example.pages.dev",
        "https://study-example.pages.dev",
    ]
    config = (tmp_path / "config").read_text()
    assert "https://study-example.pages.dev/api/_tunnel" in config
    assert "https://changing-example.trycloudflare.com" in config
    config_path = (tmp_path / "args").read_text().split()[1]
    assert not Path(config_path).exists()
