"""実ブラウザの再読込み・タブ閉じと定期チェックポイントを確認する。"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

from src.interfaces.cli.online_prepare import demo

pytestmark = pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_LIFECYCLE_UI") != "1",
    reason="explicit lifecycle browser test",
)


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    if not Path("src/interfaces/http/static/index.html").is_file():
        pytest.fail("Build frontend before enabling lifecycle browser tests")
    directory = tmp_path_factory.mktemp("lifecycle-browser")
    config = demo(directory / "bundle")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MUSIC_RECSYS_", "OTOLAB_"))}
    env["MUSIC_RECSYS_RETENTION_DAYS"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "src.interfaces.http.server",
            "--config",
            str(config),
            "--database",
            str(directory / "sessions.sqlite"),
            "--model-version",
            "demo-itemcf",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode())
            try:
                if httpx.get(url + "/api/status").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.1)
        else:
            pytest.fail("Temporary lifecycle test server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stderr.close()


def begin_playback(page):
    page.goto("/")
    page.get_by_role("heading", name="はじめに", exact=True).wait_for()
    page.get_by_role("button", name="初めて利用する", exact=True).click()
    page.get_by_role("button", name="参加コードを控えて進む", exact=True).click()
    page.locator(".select-song").first.click()
    page.get_by_role("button", name="選択して次へ", exact=True).click()
    page.locator(".recommendations .track-open").first.click()
    page.wait_for_function("() => document.querySelector('audio')?.currentTime > .5")


def wait_for_checkpoint(context, minimum=0):
    for _ in range(80):
        response = context.request.get("/api/session/export")
        assert response.ok, response.text()
        events = response.json()["events"]
        if any(
            e["kind"] == "playback_progress" and e["listened_seconds"] > minimum for e in events
        ):
            return events
        time.sleep(0.1)
    pytest.fail("No playback time checkpoint was saved")


@pytest.mark.parametrize("action", ["reload", "close"])
def test_final_time_survives_browser_navigation(live_app, action):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(base_url=live_app, viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        begin_playback(page)
        original = context.request.get("/api/session/state").json()
        page.wait_for_timeout(900)  # Less than the periodic five-second checkpoint.
        if action == "reload":
            page.reload()
        else:
            page.close()
        events = wait_for_checkpoint(context, minimum=0.5)
        ids = [event["event_id"] for event in events]
        assert len(ids) == len(set(ids))
        assert all(
            event["request_id"] == original["recommendation"]["request_id"] for event in events
        )
        if action == "close":
            page = context.new_page()
            page.goto("/")
        page.get_by_role("heading", name="おすすめ", exact=True).wait_for()
        resumed = context.request.get("/api/session/state").json()
        assert resumed["session_id"] == original["session_id"]
        assert resumed["recommendation"]["request_id"] == original["recommendation"]["request_id"]
        assert not errors
        context.close()
        browser.close()


def test_periodic_time_is_saved_without_navigation_or_pause(live_app):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(base_url=live_app)
        page = context.new_page()
        begin_playback(page)
        events = wait_for_checkpoint(context, minimum=1)
        assert not page.evaluate("document.querySelector('audio').paused")
        assert not any(event["kind"] == "pause" for event in events)
        assert sum(event["kind"] == "play" for event in events) == 1
        context.close()
        browser.close()
