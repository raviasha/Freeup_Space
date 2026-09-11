"""Exercise iframe remounts against the real widget and fixture-only server."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("stage", ["scanning", "review", "preview", "done"])
def test_remount_restores_scan(stage):
    playwright_api = pytest.importorskip("playwright.sync_api")
    sync_playwright, expect = playwright_api.sync_playwright, playwright_api.expect

    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-u', str(root / 'tests/widget_harness.py')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               env={**os.environ, "FREEUP_TEST_SCAN_DELAY": "2" if stage == "scanning" else "0"})
    try:
        url = process.stdout.readline().strip()
        assert url.startswith('http://127.0.0.1:')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("FREEUP_TEST_BROWSER_CHANNEL"))
            page = browser.new_page()
            page.goto(url)
            widget = page.frame_locator('#widget')
            widget.get_by_role('button', name='Start scan', exact=True).click()
            heading = {"scanning": "Quick Scan", "review": "Review by category",
                       "preview": "Confirm your cleanup", "done": "Selected items moved to Trash"}[stage]
            if stage != "scanning":
                expect(widget.get_by_role('heading', name='Review by category')).to_be_visible(timeout=20000)
            if stage in {"preview", "done"}:
                widget.get_by_role('button', name='Archives').click()
                widget.get_by_role('checkbox').first.check()
                widget.get_by_role('button', name='Preview selection').click()
                expect(widget.get_by_role('heading', name='Confirm your cleanup')).to_be_visible(timeout=10000)
            if stage == "done":
                # The fixture server simulates Trash; it never moves real user files.
                widget.get_by_role('button', name='Confirm move to Trash').click()
            expect(widget.get_by_role('heading', name=heading, exact=True)).to_be_visible(timeout=10000)
            original_run = widget.locator('body').evaluate('() => state.run_id')
            page.locator('#widget').evaluate('(frame) => frame.src = frame.src')
            expect(widget.get_by_role('heading', name=heading, exact=True)).to_be_visible(timeout=5000)
            assert widget.locator('body').evaluate('() => state.run_id') == original_run
            expect(widget.get_by_role('button', name='Start scan', exact=True)).to_have_count(0)
            # Hosts may replay the startup tool result again without remounting.
            page.evaluate("frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:view},'*')")
            expect(widget.get_by_role('heading', name=heading, exact=True)).to_be_visible()
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)


def test_expired_session_cannot_show_stale_start_controls():
    playwright_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-u', str(root / 'tests/widget_harness.py')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = process.stdout.readline().strip()
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("FREEUP_TEST_BROWSER_CHANNEL"))
            page = browser.new_page()
            page.goto(url)
            widget = page.frame_locator('#widget')
            playwright_api.expect(widget.get_by_role('button', name='Start scan', exact=True)).to_be_visible()
            def expired(route):
                if route.request.post_data_json.get('name') == 'scan_status':
                    route.fulfill(json={"isError": True, "content": [{"type": "text", "text": "Widget session expired."}]})
                else:
                    route.continue_()
            page.route('**/api', expired)
            page.locator('#widget').evaluate('(frame) => frame.src = frame.src')
            playwright_api.expect(widget.get_by_role('alert')).to_contain_text('Widget session expired.')
            playwright_api.expect(widget.get_by_role('button', name='Start scan', exact=True)).to_have_count(0)
            playwright_api.expect(widget.get_by_role('button', name='Reconnect', exact=True)).to_be_visible()
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)

def test_visibility_reconnect_blocks_controls_until_live_status():
    playwright_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-u', str(root / 'tests/widget_harness.py')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = process.stdout.readline().strip()
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("FREEUP_TEST_BROWSER_CHANNEL"))
            page = browser.new_page()
            page.goto(url)
            widget = page.frame_locator('#widget')
            playwright_api.expect(widget.get_by_role('button', name='Start scan', exact=True)).to_be_visible()
            held = []
            def delay_status(route):
                if route.request.post_data_json.get('name') == 'scan_status':
                    held.append(route)
                else:
                    route.continue_()
            page.route('**/api', delay_status)
            with page.expect_request(lambda request: request.url.endswith('/api') and request.post_data_json.get('name') == 'scan_status'):
                widget.locator('body').evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
            playwright_api.expect(widget.get_by_role('button', name='Start scan', exact=True)).to_have_count(0)
            assert held
            held.pop().continue_()
            playwright_api.expect(widget.get_by_role('button', name='Start scan', exact=True)).to_be_visible()
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)
