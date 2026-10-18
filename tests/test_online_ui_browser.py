"""The actual participant UI, using isolated synthetic audio and databases."""

import os

import pytest
from playwright.sync_api import expect, sync_playwright
from test_event_delivery_browser import browser_code
from test_event_delivery_browser import live_app as lifecycle_app

live_app = lifecycle_app

pytestmark = pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_ONLINE_UI") != "1", reason="explicit online UI test"
)


def test_open_page_shows_backend_outage_and_can_reconnect(live_app):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(base_url=live_app)
        page.clock.install()
        page.goto("/")
        expect(page.get_by_label("参加コード", exact=True)).to_be_visible()
        page.route(
            "**/api/status",
            lambda route: route.fulfill(status=503, json={"detail": "Mac offline"}),
        )
        page.clock.run_for(30_001)
        expect(page.get_by_text("現在接続できません", exact=True)).to_be_visible()
        page.unroute("**/api/status")
        page.get_by_role("button", name="接続を再確認", exact=True).click()
        expect(page.get_by_label("参加コード", exact=True)).to_be_visible()
        browser.close()


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 844), (320, 568)])
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
        code_input = page.get_by_label("参加コード", exact=True)
        expect(code_input).to_be_visible()
        assert code_input.evaluate(
            "el => { const s=getComputedStyle(el); return s.borderTopStyle==='solid' && s.borderTopWidth==='1px' && s.boxShadow==='none' && s.appearance==='none'; }"
        )
        assert context.request.get("/api/session/state").status == 401
        page.get_by_role("button", name="このアプリについて", exact=True).click()
        help_dialog = page.get_by_role("dialog", name="このアプリについて")
        expect(help_dialog.get_by_text("1. このアプリでできること", exact=True)).to_be_visible()
        expect(help_dialog.get_by_text("2. データの保存と取り扱い", exact=True)).to_be_visible()
        expect(help_dialog.locator('input[type="checkbox"]')).to_have_count(0)
        assert help_dialog.evaluate(
            "el => { const s=getComputedStyle(el.querySelector('.consent-copy p')), b=getComputedStyle(document.body); return s.fontFamily===b.fontFamily && s.fontSize===b.fontSize && s.letterSpacing===b.letterSpacing; }"
        )
        page.get_by_role("button", name="説明を閉じる", exact=True).click()
        expect(page.get_by_role("button", name="初めて利用する", exact=True)).to_have_count(0)
        participant_code = browser_code(live_app)
        assert len(participant_code) == 4 and participant_code.isdigit()
        code_input.fill(participant_code)
        page.get_by_role("button", name="同意して始める・再開する", exact=True).click()
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
        page.get_by_role("button", name="おすすめを評価する", exact=True).click()
        expect(page.get_by_role("group", name="リスト全体の満足度")).to_be_visible()
        page.get_by_role("button", name="満足度 4", exact=True).click()
        for name in ("試聴に戻る", "終了する"):
            box = page.get_by_role("button", name=name, exact=True).bounding_box()
            assert box and box["y"] >= 0 and box["y"] + box["height"] <= height
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if os.environ.get("MUSIC_RECSYS_SCREENSHOTS") == "1":
            page.screenshot(path=f"/tmp/music-feedback-{width}.png")
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


def test_participant_code_resumes_from_another_browser(live_app):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        first = browser.new_context(base_url=live_app)
        page = first.new_page()
        page.goto("/")
        page.route(
            "**/api/sessions",
            lambda route: route.fulfill(status=503, json={"detail": "一時的に開始できません"}),
            times=1,
        )
        code = browser_code(live_app)
        page.get_by_label("参加コード", exact=True).fill(code)
        page.get_by_role("button", name="同意して始める・再開する", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("一時的に開始できません")
        page.get_by_role("button", name="同意して始める・再開する", exact=True).click()
        expect(page.get_by_role("heading", name="曲を選択", exact=True)).to_be_visible()
        original = first.request.get("/api/session/state").json()
        returning = browser.new_context(base_url=live_app)
        revisit = returning.new_page()
        revisit.goto("/")
        revisit.get_by_label("参加コード", exact=True).fill("invalid")
        revisit.get_by_role("button", name="同意して始める・再開する", exact=True).click()
        expect(revisit.get_by_role("alert")).to_contain_text("参加コードを確認してください")
        revisit.get_by_label("参加コード", exact=True).fill(code)
        revisit.get_by_role("button", name="同意して始める・再開する", exact=True).click()
        expect(revisit.get_by_role("heading", name="曲を選択", exact=True)).to_be_visible()
        assert (
            returning.request.get("/api/session/state").json()["session_id"]
            == original["session_id"]
        )
        # Code login rotates the session credential; the previous browser cannot keep writing.
        assert first.request.get("/api/session/state").status == 401
        assert returning.request.delete("/api/session", headers={"X-Music-App": "1"}).ok
        returning.close()
        first.close()
        browser.close()
