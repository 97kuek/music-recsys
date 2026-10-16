import json
import os
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_RESEARCH_UI") != "1", reason="explicit researcher UI test"
)
def test_local_demo_browser():
    out = Path("data/app/screenshots")
    out.mkdir(exist_ok=True, parents=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            base_url=os.environ.get("MUSIC_RECSYS_RESEARCH_URL", "http://127.0.0.1:8768"),
            viewport={"width": 1440, "height": 1100},
        )
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("/")
        page.get_by_role("button", name="薄明のループ", exact=False).click()
        page.get_by_role("button", name="この好みで始める", exact=False).click()
        page.get_by_text("Validationの比較条件を見る", exact=True).click()
        page.get_by_text("このデモには実データの評価値を表示しません。", exact=True).wait_for()
        page.get_by_label("比較する推薦方法").select_option("demo-itemcf")
        page.get_by_role("button", name="おすすめを聴く", exact=False).click()
        page.locator(".result").first.wait_for()
        page.locator(".result button").filter(has_text="試聴").first.click()

        for _ in range(50):
            if page.locator("audio").evaluate("(el) => el.currentTime > 0.3"):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("audio did not play")
        modal = page.get_by_role("dialog", name="楽曲プレイヤー")
        first_title = modal.locator(".player-title h2").inner_text()
        page.get_by_role("button", name="次の曲", exact=True).click()

        for _ in range(50):
            if page.locator("audio").evaluate("(el) => el.currentTime > 0.3"):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("audio did not play")
        expect(modal.locator(".player-title h2")).not_to_have_text(first_title)
        page.get_by_role("button", name="前の曲", exact=True).click()
        expect(modal.locator(".player-title h2")).to_have_text(first_title)
        page.get_by_role("button", name="一覧に戻る", exact=True).click()
        page.locator(".result").first.get_by_role("button", name="いいね", exact=False).click()
        page.locator(".result").first.get_by_role("button", name="保存", exact=False).click()
        page.locator("audio").evaluate("el => el.pause()")
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / "researcher-desktop.png"), full_page=True)
        page.get_by_role("button", name="回答せずに終了", exact=True).click()
        page.get_by_role("button", name="自分のログを確認", exact=True).click()
        exported = page.request.get("/api/session/export").json()
        kinds = [e["kind"] for e in exported["events"]]
        assert {"play", "pause", "like", "save", "skip"}.issubset(kinds), kinds
        assert "rating" not in kinds
        page.get_by_role("button", name="セッションのデータを削除", exact=True).click()
        page.get_by_role("status").filter(has_text="削除しました").wait_for()
        assert page.request.get("/api/session/export").status == 401
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("/")
        page.locator(".track-choice").first.wait_for()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
            "mobile overflow"
        )
        page.screenshot(path=str(out / "researcher-mobile.png"), full_page=True)
        assert not errors, errors
        print(
            json.dumps(
                {"events": kinds, "browser_errors": errors, "screenshots": str(out)},
                ensure_ascii=False,
            )
        )
        browser.close()


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_UI") != "1", reason="explicit participant UI test"
)
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 1000},
        {"width": 390, "height": 844},
        {"width": 320, "height": 568},
        {"width": 844, "height": 390},
    ],
)
def test_participant_viewport_and_playback(viewport):
    out = Path("data/app/screenshots")
    out.mkdir(exist_ok=True, parents=True)
    label = f"{viewport['width']}x{viewport['height']}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            base_url="http://127.0.0.1:8765", viewport=viewport, color_scheme="dark"
        )
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def fits():
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight"
            ), label
            assert page.locator(".app-shell").evaluate(
                "el => el.scrollHeight <= el.clientHeight"
            ), label

        def progressing():
            for _ in range(50):
                if page.locator("audio").evaluate("el => el.currentTime > .15"):
                    return
                time.sleep(0.1)
            raise AssertionError("audio did not progress")

        page.goto("/")
        page.get_by_role("heading", name="利用について", exact=True).wait_for()
        assert page.locator("body").evaluate("el => getComputedStyle(el).colorScheme") == "light"
        fits()
        consent = page.locator(".consent-copy")
        if consent.evaluate("el => el.scrollHeight > el.clientHeight + 8"):
            assert page.get_by_role("button", name="同意して進む", exact=True).is_disabled()
        consent.evaluate(
            "el => {el.scrollTop=el.scrollHeight; el.dispatchEvent(new Event('scroll'))}"
        )
        page.screenshot(path=str(out / f"consent-{label}.png"))
        page.get_by_role("button", name="同意して進む", exact=True).click()
        page.locator(".song-row").first.wait_for()
        exit_button = page.get_by_role("button", name="終了・削除", exact=True)
        assert exit_button.locator("svg").count() == 1
        assert not exit_button.inner_text()
        fits()
        assert page.get_by_role("textbox").count() == 0
        assert page.locator("details").count() == 0
        page.locator(".track-open").first.click()
        modal = page.get_by_role("dialog", name="楽曲プレイヤー")
        modal.wait_for()
        progressing()
        square = modal.locator(".album-frame").bounding_box()
        assert abs(square["width"] - square["height"]) < 1, square
        if viewport["width"] > 900:
            assert modal.get_by_role("complementary", name="再生キュー").is_visible()
        assert modal.evaluate(
            "el => el.scrollHeight <= el.clientHeight && el.scrollWidth <= el.clientWidth"
        ), label
        assert page.get_by_role("button", name="一時停止", exact=True).last.is_visible()
        controls = modal.locator(".transport button").evaluate_all(
            "els => els.map(el => { const r=el.getBoundingClientRect(); return r.x+r.width/2 })"
        )
        assert len(controls) == 5
        gaps = [right - left for left, right in zip(controls, controls[1:], strict=False)]
        assert max(gaps) - min(gaps) < 1, gaps
        assert max(gaps) <= 64, gaps
        page.get_by_role("button", name="リピート", exact=True).click()
        assert page.locator("audio").evaluate("el => el.loop")
        assert not page.request.get("/api/session/export").json()["events"]
        page.get_by_role("button", name="この曲を選択", exact=True).click()
        assert page.get_by_role("button", name="選択済み", exact=True).is_visible()
        page.screenshot(path=str(out / f"player-{label}.png"))
        page.get_by_role("button", name="一覧に戻る", exact=True).click()
        fits()
        page.screenshot(path=str(out / f"profile-{label}.png"))
        page.get_by_role("button", name="選択して次へ", exact=True).click()
        page.locator(".recommendations .track-open").first.wait_for()
        fits()
        assert page.get_by_text("Validationの比較条件を見る").count() == 0
        page.locator(".recommendations .track-open").first.click()
        progressing()
        page.get_by_role("dialog").get_by_role("button", name="いいね", exact=True).click()
        first_title = modal.locator(".player-title h2").inner_text()
        modal.evaluate(
            "el => {window.playerCloses=0; new MutationObserver(records => {for (const r of records) if(r.attributeName==='open' && r.oldValue!==null) window.playerCloses++}).observe(el,{attributes:true,attributeFilter:['open'],attributeOldValue:true})}"
        )
        page.get_by_role("button", name="次の曲", exact=True).click()
        expect(modal.locator(".player-title h2")).not_to_have_text(first_title)
        progressing()
        page.get_by_role("button", name="前の曲", exact=True).click()
        expect(modal.locator(".player-title h2")).to_have_text(first_title)
        progressing()
        page.get_by_role("button", name="シャッフル", exact=True).click()
        expect(page.get_by_role("button", name="シャッフル", exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        page.get_by_role("button", name="次の曲", exact=True).click()
        expect(modal.locator(".player-title h2")).not_to_have_text(first_title)
        progressing()
        assert page.evaluate("window.playerCloses") == 0
        page.get_by_role("dialog").get_by_role("button", name="一時停止", exact=True).click()
        exported = page.request.get("/api/session/export").json()
        assert {"play", "pause", "like", "skip"} <= {event["kind"] for event in exported["events"]}
        assert any(
            event.get("listened_seconds", 0) > 0
            for event in exported["events"]
            if event.get("listened_seconds") is not None
        )
        page.get_by_role("button", name="一覧に戻る", exact=True).click()
        page.screenshot(path=str(out / f"recommendations-{label}.png"))
        page.get_by_role("button", name="次へ", exact=True).click()
        fits()
        page.get_by_role("button", name="終了する", exact=True).click()
        page.get_by_role("heading", name="終了しました", exact=True).wait_for()
        fits()
        page.get_by_role("button", name="データを削除して戻る", exact=True).click()
        assert page.request.get("/api/session/state").status == 401
        assert not errors, errors
        browser.close()


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_STUDY_UI") != "1", reason="explicit study UI test"
)
def test_fixed_study_two_conditions():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            base_url="http://127.0.0.1:8766", viewport={"width": 390, "height": 844}
        )
        page.goto("/")
        page.locator(".consent-copy").evaluate(
            "el => {el.scrollTop=el.scrollHeight; el.dispatchEvent(new Event('scroll'))}"
        )
        page.get_by_role("button", name="同意して進む", exact=True).click()
        page.locator(".select-song").first.click()
        page.get_by_role("button", name="選択して次へ", exact=True).click()
        for period in (1, 2):
            page.locator(".recommendations .track-open").first.click()
            page.get_by_role("dialog").wait_for()
            page.wait_for_timeout(500)
            assert page.locator("audio").evaluate("el => el.currentTime > 0")
            assert "demo-itemcf" not in page.locator("body").inner_text()
            assert "demo-als" not in page.locator("body").inner_text()
            page.get_by_role("button", name="一覧に戻る", exact=True).click()
            page.get_by_role("button", name="次へ", exact=True).click()
            page.get_by_role(
                "button", name="次のおすすめへ" if period == 1 else "終了する", exact=True
            ).click()
        page.get_by_role("heading", name="終了しました", exact=True).wait_for()
        page.get_by_role("button", name="データを削除して戻る", exact=True).click()
        browser.close()
