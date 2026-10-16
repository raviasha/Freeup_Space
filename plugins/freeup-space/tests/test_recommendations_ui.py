"""Real widget and backend over disposable fixture documents, without file moves."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_keep_latest_recommendations_select_hidden_families_restore_and_preview():
    api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, "-u", str(root / "tests/widget_harness.py")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=dict(os.environ, FREEUP_TEST_RECOMMENDATIONS="1", FREEUP_TEST_ARCHIVE_COUNT="0"))
    try:
        url = process.stdout.readline().strip()
        with api.sync_playwright() as p:
            browser = p.chromium.launch(channel=os.environ.get("FREEUP_TEST_BROWSER_CHANNEL"))
            page = browser.new_page()
            page.goto(url)
            widget = page.frame_locator("#widget")
            widget.get_by_role("radio").nth(1).check()
            widget.get_by_role("button", name="Start scan", exact=True).click()
            card = widget.locator(".review-card").filter(has=widget.get_by_role("heading", name="Older versions", exact=True))
            api.expect(card).to_be_visible(timeout=25000)
            api.expect(card).to_contain_text("60 recommended")
            card.get_by_role("button", name="Review files", exact=True).click()
            active = widget.locator(".recommendation-files")
            api.expect(active).to_contain_text("Keep only the latest")
            widget.get_by_role("searchbox").fill("Draft00")
            widget.get_by_role("searchbox").press("Tab")
            active.get_by_role("button", name="Select recommended files (60)", exact=True).click()
            api.expect(widget.locator("#selection")).to_contain_text("60 selected")
            api.expect(widget.get_by_role("button", name="Confirm move to Trash")).to_have_count(0)
            page.locator("#widget").evaluate("frame => frame.src = frame.src")
            api.expect(widget.locator("#selection")).to_contain_text("60 selected", timeout=15000)
            api.expect(widget.get_by_role("searchbox")).to_have_value("Draft00")
            active.get_by_role("button", name="Show versions", exact=True).click()
            api.expect(active.get_by_label("Draft00_v1.docx", exact=True)).to_be_checked()
            api.expect(active.get_by_label("Draft00_v2.docx", exact=True)).not_to_be_checked()
            api.expect(active).to_contain_text("Keep — latest version")
            widget.get_by_role("button", name="Preview selection", exact=True).click()
            api.expect(widget.get_by_role("heading", name="Confirm your cleanup")).to_be_visible(timeout=20000)
            preview_names = widget.locator(".preview-list .item-main > strong")
            assert all(name.endswith("_v1.docx") for name in preview_names.all_text_contents())
            api.expect(widget.locator(".preview-list")).to_contain_text("Keep:")
            widget.get_by_role("button", name="Next preview page", exact=True).click()
            api.expect(widget.locator(".preview-list")).to_contain_text("_v1.docx")
            widget.get_by_role("button", name="Back to selection", exact=True).click()
            age = widget.locator(".review-card").filter(has=widget.get_by_role("heading", name="Not modified recently", exact=True))
            age.get_by_role("button", name="Review files", exact=True).click()
            widget.get_by_role("combobox", name="Inactivity period").select_option("6")
            api.expect(active).to_contain_text("at least 6 months")
            page.locator("#widget").evaluate("frame => frame.src = frame.src")
            api.expect(widget.get_by_role("combobox", name="Inactivity period")).to_have_value("6", timeout=15000)
            api.expect(widget.locator("#selection")).to_contain_text("60 selected")
            widget.get_by_role("combobox", name="Inactivity period").select_option("24")
            api.expect(active).to_contain_text("at least 24 months")
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)
