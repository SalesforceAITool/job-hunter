"""Design-review regressions (Needs-you strip, quiet history rows, feed de-duplication, chart history series).

    python -m unittest tests.test_ui_design -v
Skipped when Playwright is not installed or JH_UI_BROWSER=0.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_ui_fixture import HAVE_PW, start  # noqa: E402

if HAVE_PW:
    from playwright.sync_api import sync_playwright


@unittest.skipUnless(HAVE_PW and os.environ.get("JH_UI_BROWSER", "1") != "0", "Playwright not installed")
class DesignRegressions(unittest.TestCase):
    def setUp(self):
        self._browsers = []

    def _close(self):
        for b in self._browsers:
            b.close()
        self._browsers = []

    def _page(self, p, fresh=False, width=1024, height=768, theme="dark"):
        srv, world, base = start(fresh=fresh, speed=6.0)
        self.addCleanup(lambda: (world.stop_flag.set(), srv.shutdown()))
        b = p.chromium.launch()
        self._browsers.append(b)
        ctx = b.new_context(viewport={"width": width, "height": height})
        ctx.add_init_script("try{localStorage.setItem('jh-theme','%s')}catch(e){}" % theme)
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base + "/")
        pg.wait_for_selector(".tbl tbody tr")
        return pg, errors

    def test_needs_you_strip_and_history_rows(self):
        with sync_playwright() as p:
            pg, errors = self._page(p)
            pg.wait_for_selector("#needs:not([hidden])")
            # auto-submit (default on): ready forms show as "being submitted", never as a job for Akshay
            pg.wait_for_selector("#needs .need-ok")
            self.assertNotIn("ready to submit", pg.inner_text("#needs"))
            pg.click("#needs .need-ok .btn")
            pg.wait_for_selector("#tab-portal[aria-selected='true']")
            pg.click("#tab-all")
            self.assertGreater(pg.locator(".tbl tbody tr.row-hist").count(), 0)
            # No horizontal overflow at the minimum supported width.
            self.assertEqual(pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)
            self._close()
            self.assertEqual(errors, [])

    def test_feed_collapses_identical_consecutive_lines(self):
        with sync_playwright() as p:
            pg, errors = self._page(p, theme="light")
            before = pg.locator("#feed .fl").count()
            for i in (1, 2, 3):
                pg.evaluate("""i => window.JH.handleEvent({id: 900000 + i, run_id: window.JH.state.runId, lane: 'jobright',
                    level: 'info', type: 'step', message: 'Same line', ts: new Date().toISOString(), data: {}})""", i)
            self.assertEqual(pg.locator("#feed .fl").count(), before + 1)
            self.assertEqual(pg.locator("#feed .fl").last.locator(".fl-rep").inner_text(), "×3")
            self._close()
            self.assertEqual(errors, [])

    def test_fresh_install_asks_for_gmail(self):
        with sync_playwright() as p:
            srv, world, base = start(fresh=True, speed=6.0)
            self.addCleanup(lambda: (world.stop_flag.set(), srv.shutdown()))
            b = p.chromium.launch()
            try:
                pg = b.new_page(viewport={"width": 1280, "height": 800})
                pg.goto(base + "/")
                pg.wait_for_selector("#needs .need", timeout=8000)
                text = pg.locator("#needs").inner_text()
            finally:
                b.close()
            self.assertIn("Set up Gmail to start sending", text)


if __name__ == "__main__":
    unittest.main()
