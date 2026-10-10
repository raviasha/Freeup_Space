"""Exercise saved selections in the real UI, with temporary fixture files only."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("restart", [False, True])
def test_checked_items_and_filters_survive_reopening(restart):
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
            api.expect(widget.get_by_role("heading", name="Review by category")).to_be_visible(timeout=20000)
            widget.get_by_role("button", name="Archives", exact=False).click()
            with page.expect_response(lambda r: r.url.endswith('/api') and r.request.post_data_json.get('name') == 'save_review_state', timeout=3000):
                widget.get_by_label("sample-00.zip", exact=True).check()
            with page.expect_response(lambda r: r.url.endswith('/api') and r.request.post_data_json.get('name') == 'save_review_state' and r.request.post_data_json.get('args', {}).get('query') == 'sample-00', timeout=3000):
                widget.get_by_role("searchbox").fill("sample-00")
                widget.get_by_role("searchbox").press("Tab")
            if restart:
                page.evaluate("api('restart_fixture_server', {})")
            page.locator('#widget').evaluate('(frame) => frame.src = frame.src')
            api.expect(widget.get_by_role("heading", name="Review by category")).to_be_visible(timeout=10000)
            api.expect(widget.locator('#selection')).to_contain_text("1 selected")
            api.expect(widget.get_by_role("searchbox")).to_have_value("sample-00")
            api.expect(widget.get_by_label("sample-00.zip", exact=True)).to_be_checked()
            api.expect(widget.get_by_role("checkbox")).to_have_count(1)
            api.expect(widget.get_by_role("button", name="Start scan", exact=True)).to_have_count(0)
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)


def test_close_and_reopen_keeps_saved_scan():
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
            api.expect(widget.get_by_role("heading", name="Review by category")).to_be_visible(timeout=20000)
            widget.get_by_role("button", name="Close widget", exact=True).click(timeout=3000)
            api.expect(widget.get_by_role("heading", name="Widget closed")).to_be_visible()
            page.locator('#widget').evaluate('(frame) => frame.src = frame.src')
            api.expect(widget.get_by_role("heading", name="Widget closed")).to_be_visible()
            widget.get_by_role("button", name="Reopen saved results").click()
            api.expect(widget.get_by_role("heading", name="Review by category")).to_be_visible()
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)


def test_restart_restores_filtered_page_contents():
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
            api.expect(widget.get_by_role("heading", name="Review by category")).to_be_visible(timeout=20000)
            widget.get_by_role("button", name="Archives", exact=False).click()
            widget.get_by_role("searchbox").fill("sample-")
            widget.get_by_role("searchbox").press("Tab")
            api.expect(widget.get_by_text("60 matching items", exact=True)).to_be_visible()
            widget.get_by_role("button", name="Next", exact=True).click()
            api.expect(widget.get_by_text("51–60 of 60", exact=True)).to_be_visible()
            page.evaluate("api('restart_fixture_server', {})")
            page.locator('#widget').evaluate('(frame) => frame.src = frame.src')
            api.expect(widget.get_by_role("heading", name="Review by category")).to_be_visible(timeout=10000)
            api.expect(widget.get_by_role("searchbox")).to_have_value("sample-")
            api.expect(widget.get_by_role("checkbox")).to_have_count(10)
            api.expect(widget.get_by_text("51–60 of 60", exact=True)).to_be_visible()
            api.expect(widget.get_by_label("sample-50.zip", exact=True)).to_be_visible()
            api.expect(widget.get_by_label("example.dmg", exact=True)).to_have_count(0)
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)
