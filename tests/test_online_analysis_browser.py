"""Researcher screen rendered from the actual HTTP app, isolated synthetic logs only."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from test_online_analysis import session_payload

from src.infrastructure.persistence.sessions import LocalStore
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_ONLINE_UI") != "1", reason="explicit online UI test"
)


@pytest.mark.parametrize("width", [1440, 390])
def test_actual_researcher_analysis_and_download(tmp_path, width):
    if not Path("src/interfaces/http/static/index.html").is_file():
        pytest.fail("Build frontend first")
    config = demo(tmp_path / "bundle")
    store = LocalStore(tmp_path / "sessions.sqlite")
    for i, ratings in enumerate((dict(A=[2], B=[4], C=[None]), dict(A=[5], B=[3], C=[4]))):
        payload = session_payload(f"synthetic-{i}", f"s{i}", ratings)
        payload["session"]["retain_until"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        store.put(f"test-{i}", payload)
    app = create_app(config, store.path, audience="researcher")
    with TestClient(app) as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": width, "height": 1000}, accept_downloads=True
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            response = client.request(route.request.method, route.request.url)
            headers = {
                k: v
                for k, v in response.headers.items()
                if k not in {"content-encoding", "content-length"}
            }
            route.fulfill(status=response.status_code, headers=headers, body=response.content)

        page.route("**/*", serve)
        page.goto("http://testserver/")
        page.locator("#online-analysis > summary").click()
        panel = page.locator("#online-analysis")
        panel.get_by_role("button", name="保存済みログを再集計").click()
        expect(panel.get_by_text("参加者 2人 / 2セッション", exact=False)).to_be_visible()
        expect(panel.get_by_role("img", name="初回満足度の参加者平均", exact=True)).to_be_visible()
        expect(panel.get_by_role("img", name="参加者ごとの初回満足度", exact=True)).to_be_visible()
        expect(
            panel.get_by_text("LLM/Agent：参加者向け実行は未接続。", exact=False)
        ).to_be_visible()
        expect(panel.get_by_role("img", name="条件別の総API料金（USD）", exact=True)).to_have_count(
            0
        )
        with page.expect_download() as download:
            panel.get_by_role("link", name="lists CSV", exact=True).click()
        text = Path(download.value.path()).read_text()
        assert "source_sha256" in text and "synthetic-0" in text
        assert "selected_item_ids" not in text
        assert not errors
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        context.close()
        browser.close()
