"""Browser checks use only temporary fixtures and simulated Trash."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("via_item", [False, True])
def test_select_group_includes_hidden_matches_and_survives_remount(via_item):
    api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, "-u", str(root / "tests/widget_harness.py")],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = process.stdout.readline().strip()
        with api.sync_playwright() as p:
            browser = p.chromium.launch(channel=os.environ.get("FREEUP_TEST_BROWSER_CHANNEL"))
            page = browser.new_page()
            page.goto(url)
            widget = page.frame_locator("#widget")
            widget.get_by_role("button", name="Start scan", exact=True).click()
            api.expect(widget.get_by_role("heading", name="Similar-name groups")).to_be_visible(timeout=20000)
            if via_item:
                widget.get_by_role("button", name="Archives", exact=False).click()
                widget.get_by_role("searchbox").fill("sample-00")
                widget.get_by_role("searchbox").press("Tab")
                api.expect(widget.get_by_role("checkbox")).to_have_count(1)
                widget.get_by_role("button", name="Select similar (60)", exact=True).click()
            else:
                widget.get_by_role("button", name="Select all 60 files", exact=True).click()
                widget.get_by_role("button", name="Show group files", exact=True).click()
                api.expect(widget.locator(".group-files")).to_be_visible()
            api.expect(widget.locator("#selection")).to_contain_text("60 selected")
            api.expect(widget.get_by_role("button", name="Confirm move to Trash")).to_have_count(0)
            page.locator("#widget").evaluate("(frame) => frame.src = frame.src")
            api.expect(widget.locator("#selection")).to_contain_text("60 selected", timeout=10000)
            if not via_item:
                widget.get_by_role("button", name="Next", exact=True).click()
                api.expect(widget.get_by_label("sample-59.zip", exact=True)).to_be_checked()
            widget.get_by_role("button", name="Preview selection", exact=True).click()
            api.expect(widget.get_by_role("heading", name="Confirm your cleanup")).to_be_visible(timeout=15000)
            widget.get_by_role("button", name="Next preview page", exact=True).click()
            api.expect(widget.locator(".preview-list")).to_contain_text("sample-59.zip")
            widget.get_by_role("button", name="Back to selection", exact=True).click()
            api.expect(widget.locator("#selection")).to_contain_text("60 selected")
            widget.get_by_role("button", name="Clear group", exact=True).first.click()
            api.expect(widget.locator("#selection")).to_contain_text("0 selected")
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)


def test_large_group_uses_bounded_edits_and_saved_selection_preview():
    api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, "-u", str(root / "tests/widget_harness.py")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=dict(os.environ, FREEUP_TEST_ARCHIVE_COUNT="1205"))
    try:
        url = process.stdout.readline().strip()
        with api.sync_playwright() as p:
            browser = p.chromium.launch(channel=os.environ.get("FREEUP_TEST_BROWSER_CHANNEL"))
            page = browser.new_page()
            requests = []
            page.on("request", lambda r: requests.append(r.post_data_json) if r.url.endswith("/api") else None)
            page.goto(url)
            widget = page.frame_locator("#widget")
            widget.get_by_role("button", name="Start scan", exact=True).click()
            widget.get_by_role("button", name="Select all 1205 files", exact=True).click(timeout=20000)
            api.expect(widget.locator("#selection")).to_contain_text("1205 selected")
            widget.get_by_role("button", name="Preview selection", exact=True).click()
            api.expect(widget.get_by_role("heading", name="Confirm your cleanup")).to_be_visible(timeout=20000)
            preview = next(r["args"] for r in requests if r["name"] == "preview_selection")
            assert "ids" not in preview, "Preview should reference the saved selection instead of resending all IDs"
            assert len(preview["selection_digest"]) == 64
            widget.get_by_role("button", name="Back to selection", exact=True).click()
            api.expect(widget.locator("#selection")).to_contain_text("1205 selected")
            with page.expect_response(lambda r: r.url.endswith("/api") and
                    r.request.post_data_json.get("name") == "save_review_state" and r.request.post_data_json.get("args", {}).get("ids") == []):
                widget.get_by_role("button", name="Clear selection", exact=True).click()
            api.expect(widget.locator("#selection")).to_contain_text("0 selected")
            saves = [r["args"] for r in requests if r["name"] == "save_review_state"]
            assert all(len(r.get("ids", [])) <= 500 and len(r.get("add_ids", [])) <= 500 and len(r.get("remove_ids", [])) <= 500 for r in saves)
            assert any(r.get("ids") == [] for r in saves)
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)
