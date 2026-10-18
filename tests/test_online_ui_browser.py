"""The actual participant UI, using isolated synthetic audio and databases."""

import os

import pytest
from playwright.sync_api import expect, sync_playwright
from test_event_delivery_browser import live_app as lifecycle_app

live_app = lifecycle_app

pytestmark = pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_ONLINE_UI") != "1", reason="explicit online UI test"
)


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844)])
def test_list_feedback_playback_and_saved_preferences(live_app, width, height):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            base_url=live_app, viewport={"width": width, "height": height}
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("/")
        page.get_by_label("長期嗜好の保存・後日の再利用にも同意する", exact=False).check()
        page.locator(".consent-copy").evaluate(
            "el => {el.scrollTop=el.scrollHeight;el.dispatchEvent(new Event('scroll'))}"
        )
        page.get_by_role("button", name="同意して進む", exact=True).click()
        page.locator(".select-song").first.click()
        page.get_by_role("button", name="選択して次へ", exact=True).click()
        expect(page.locator(".recommendations .song-row")).to_have_count(5)
        expect(page.get_by_role("button", name="おすすめを調整する")).to_have_count(0)
        page.locator(".recommendations .song-actions button").first.click()
        page.locator(".recommendations .track-open").first.click()
        page.wait_for_function("() => document.querySelector('audio').currentTime > .3")
        page.evaluate("() => document.querySelector('audio').currentTime=5")
        page.wait_for_function("() => document.querySelector('audio').currentTime > 5.1")
        page.locator(".mini-player").get_by_role("button", name="次の曲", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('audio').currentTime > .15 && document.querySelector('audio').currentTime < 3"
        )
        page.locator(".mini-player").get_by_role("button", name="前の曲", exact=True).click()
        page.wait_for_function("() => document.querySelector('audio').currentTime > .15")
        page.locator(".recommendations .track-open").nth(2).click()
        page.wait_for_function("() => document.querySelector('audio').currentTime > .15")
        page.get_by_role("button", name="次へ", exact=True).click()
        expect(page.get_by_role("group", name="リスト全体の満足度")).to_be_visible()
        page.get_by_role("button", name="満足度 4", exact=True).click()
        question = page.locator(".list-evaluation fieldset")
        expect(question.locator('input[type="checkbox"]')).to_have_count(2)
        question.locator('input[type="checkbox"]').first.check()
        page.get_by_role("button", name="終了する", exact=True).click()
        expect(page.get_by_role("heading", name="終了しました", exact=True)).to_be_visible()
        payload = context.request.get("/api/session/export").json()
        moves = [e for e in payload["events"] if e["kind"] == "track_switch"]
        assert {e["movement"] for e in moves} == {"next", "previous", "direct"}
        assert all(e["playback_id"] and e["listened_seconds"] < 5 for e in moves)
        evaluations = [e for e in payload["events"] if e["kind"] == "list_evaluation"]
        assert len(evaluations) == 1 and evaluations[0]["rating"] == 4
        assert evaluations[0]["discovery_status"] == "selected"
        first_session = context.request.get("/api/session/state").json()["session_id"]
        page.get_by_role("button", name="保存した好み", exact=True).click()
        expect(page.get_by_role("dialog", name="保存した好み")).to_be_visible()
        page.get_by_role("button", name="閉じる", exact=True).click()
        page.get_by_role("button", name="もう一度", exact=True).click()
        page.locator(".select-song").first.wait_for()
        state = context.request.get("/api/session/state").json()
        assert state["session_id"] != first_session
        assert len(state["profile"]["items"]) >= 2
        page.get_by_role("button", name="保存した好み", exact=True).click()
        choices = page.locator(".saved-preferences fieldset").first.locator(
            'input[type="checkbox"]'
        )
        choices.nth(1).uncheck()
        page.get_by_role("button", name="変更を保存", exact=True).click()
        expect(
            page.get_by_role("status").filter(has_text="次のセッション用に保存しました")
        ).to_be_visible()
        assert (
            len(context.request.get("/api/profile/long-term").json()["user_settings"]["items"]) == 1
        )
        assert len(context.request.get("/api/session/state").json()["profile"]["items"]) >= 2
        page.get_by_role("button", name="プロフィールと全参加データを削除", exact=True).click()
        expect(page.get_by_role("heading", name="はじめに", exact=True)).to_be_visible()
        assert context.request.get("/api/session/state").status == 401
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        context.close()
        browser.close()
