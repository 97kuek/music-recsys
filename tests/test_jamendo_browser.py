"""認証未設定のローカルJamendoモード。実音源の成功テストではない。"""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("OTOLAB_TEST_JAMENDO_UI") != "1", reason="explicit local UI test"
)
def test_unconfigured_jamendo_ui():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto("http://127.0.0.1:8767")
            page.locator(".consent-copy").evaluate(
                "el => {el.scrollTop=el.scrollHeight; el.dispatchEvent(new Event('scroll'))}"
            )
            page.get_by_role("button", name="同意して進む", exact=True).click()
            page.locator(".select-song").first.click()
            page.get_by_role("button", name="選択して次へ").click()
            page.locator("article").first.wait_for()
            assert page.locator(".song-info strong").first.inner_text()
            page.locator(".recommendations .track-open").first.click()
            page.get_by_text("Jamendo: not_configured", exact=False).wait_for()
        finally:
            browser.close()
