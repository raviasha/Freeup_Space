"""Completion summaries use receipts; the fixture server never moves user files."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('moved_count,skipped_count,free_after,expected_change', [
    (1, 1, 1000000, '0 B'),
    (1, 1, 1004096, '+4.0 KiB'),
    (0, 1, 995904, '−4.0 KiB'),
    (1, 1, None, 'Unavailable'),
    (0, 3, 1000000, '0 B'),
    (3, 0, 1000000, '0 B'),
])
def test_cleanup_shows_space_summary_before_file_details(moved_count, skipped_count, free_after, expected_change):
    api = pytest.importorskip('playwright.sync_api')
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, '-u', str(root / 'tests/widget_harness.py')],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = process.stdout.readline().strip()
        with api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('FREEUP_TEST_BROWSER_CHANNEL'))
            page = browser.new_page()

            def completion_receipt(route):
                response = route.fetch()
                result = response.json()
                view = result.get('_meta', {}).get('widget', {})
                if view.get('status') == 'done':
                    receipt = view['receipt']
                    items = receipt['moved']
                    receipt.update(moved=items[:moved_count],
                                   skipped=[dict(item, reason='snapshot-mismatch') for item in items[moved_count:moved_count+skipped_count]],
                                   failed=[dict(item, reason='access-denied') for item in items[moved_count+skipped_count:]],
                                   logical_moved_bytes=sum(item['bytes_moved'] for item in items[:moved_count]),
                                   free_space_after=free_after)
                route.fulfill(response=response, json=result)

            page.route('**/api', completion_receipt)
            page.goto(url)
            widget = page.frame_locator('#widget')
            widget.get_by_role('button', name='Start scan', exact=True).click()
            api.expect(widget.get_by_role('heading', name='Review by category')).to_be_visible(timeout=20000)
            widget.get_by_role('button', name='Archives').click()
            for index in range(3):
                widget.get_by_role('checkbox', name=f'sample-{index:02d}.zip', exact=True).check()
            widget.get_by_role('button', name='Preview selection').click()
            api.expect(widget.get_by_role('heading', name='Confirm your cleanup')).to_be_visible(timeout=10000)
            # All apply calls in this harness return simulated fixture receipts.
            widget.get_by_role('button', name='Confirm move to Trash').click()
            summary = widget.get_by_role('region', name='Cleanup summary')
            api.expect(summary).to_be_visible(timeout=10000)
            for remount in (False, True):
                if remount:
                    page.locator('#widget').evaluate('(frame) => frame.src = frame.src')
                api.expect(summary).to_contain_text(f'{moved_count} moved · {skipped_count} skipped · {3-moved_count-skipped_count} failed')
                heading = widget.get_by_role('heading', name='Cleanup finished with some items left' if moved_count < 3 else 'Selected items moved to Trash')
                api.expect(heading).to_be_visible()
                if moved_count < 3:
                    api.expect(heading).not_to_have_class('success')
                moved = summary.locator('.stat').filter(has_text='Moved to Trash / Recycle Bin')
                api.expect(moved.locator('strong')).to_have_text(f'{4*moved_count:.1f} KiB' if moved_count else '0 B')
                space = summary.locator('.stat').filter(has_text='Observed free-space change')
                api.expect(space.locator('strong')).to_have_text(expected_change)
                api.expect(summary).to_contain_text('Space may remain occupied until you empty Trash / Recycle Bin.')
                assert summary.bounding_box()['y'] < widget.locator('#app > .path, #app > .notice').first.bounding_box()['y']
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)
