import json
import os

import pytest

from src.model_artifacts import load_model
from src.profiles import InitialProfile
from src.webapp.prepare import demo
from src.webapp.public_demo import export


@pytest.fixture(scope="module")
def public_bundle(tmp_path_factory):
    config = demo(tmp_path_factory.mktemp("public-demo") / "source")
    return config


def test_public_export_rejects_real_data_and_corruption(public_bundle, tmp_path):
    config = json.loads(public_bundle.read_text())
    bad = tmp_path / "real.json"
    bad.write_text(json.dumps({**config, "mode": "music4all"}))
    with pytest.raises(ValueError, match="synthetic"):
        export(bad, tmp_path / "public")
    out = tmp_path / "demo"
    export(public_bundle, out)
    data = json.loads((out / "demo.json").read_text())
    assert len(data["tracks"]) == 12
    assert {p.suffix for p in (out / "audio").iterdir()} == {".wav"}
    assert not list(out.rglob("*.npy")) and not list(out.rglob("*.sqlite"))
    assert all(t["sample"] and t["item_id"].startswith("sample-") for t in data["tracks"])
    assert "users" not in data
    with pytest.raises(FileExistsError):
        export(public_bundle, out)


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_PUBLIC_UI") != "1", reason="explicit public demo browser test"
)
def test_public_demo_without_backend(public_bundle):
    from playwright.sync_api import sync_playwright

    config = json.loads(public_bundle.read_text())
    entry = next(m for m in config["models"] if "itemcf" in m["path"])
    _, model = load_model(
        public_bundle.parent / entry["path"], expected_manifest_sha256=entry["sha256"]
    )
    request = InitialProfile("check", ("sample-00",)).to_request(
        "check", tuple(model.index), top_k=5
    )
    expected = model.recommend(request).item_ids
    url = os.environ.get("MUSIC_RECSYS_PUBLIC_URL", "http://127.0.0.1:8787")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
        api_requests = []
        errors = []
        page.on("request", lambda r: api_requests.append(r.url) if "/api/" in r.url else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        assert page.title() == "music-recsys"
        page.get_by_role("heading", name="利用について").wait_for()
        assert page.locator("body").evaluate("el=>getComputedStyle(el).colorScheme") == "light"
        assert page.get_by_role("checkbox").count() == 0
        page.locator(".consent-copy").evaluate(
            "el=>{el.scrollTop=el.scrollHeight;el.dispatchEvent(new Event('scroll'))}"
        )
        page.get_by_role("button", name="同意して進む").click()
        page.locator(".track-open").first.click()
        page.get_by_role("dialog").wait_for()
        page.wait_for_timeout(600)
        assert page.locator("audio").evaluate("el=>el.currentTime>0")
        page.get_by_role("button", name="この曲を選択").click()
        page.get_by_role("button", name="一覧に戻る").click()
        page.get_by_role("button", name="選択して次へ").click()
        page.locator(".recommendations [data-item]").first.wait_for()
        actual = page.locator(".recommendations [data-item]").evaluate_all(
            "els=>els.map(el=>el.dataset.item)"
        )
        assert tuple(actual) == expected
        page.locator(".recommendations .track-open").first.click()
        page.wait_for_timeout(600)
        assert page.locator("audio").evaluate("el=>el.currentTime>0")
        page.get_by_role("button", name="一覧に戻る").click()
        page.get_by_role("button", name="次へ", exact=True).click()
        page.get_by_role("button", name="終了する").click()
        page.get_by_role("heading", name="終了しました").wait_for()
        page.get_by_role("button", name="データを削除して戻る").click()
        page.get_by_role("heading", name="利用について").wait_for()
        assert not api_requests, api_requests
        assert not errors, errors
        assert page.evaluate(
            "document.documentElement.scrollHeight<=innerHeight && document.documentElement.scrollWidth<=innerWidth"
        )
        browser.close()
