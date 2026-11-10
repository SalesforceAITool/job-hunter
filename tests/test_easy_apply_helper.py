"""The Easy Apply form helper (engine/prompts/easy_apply_form.js) on a LinkedIn-like modal inside a shadow DOM."""
import unittest
from pathlib import Path

from jh import engine

try:
    from playwright.sync_api import sync_playwright
except Exception:  # not installed on the laptop: skip
    sync_playwright = None

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "easy_apply_modal.html"


@unittest.skipUnless(sync_playwright, "playwright not installed")
class FormHelperTests(unittest.TestCase):
    def test_read_fill_next_never_submit(self):
        js = engine.form_helper_js()
        self.assertNotIn("{{", engine.compose_system_prompt("easy_apply", engine.PROMPTS_DIR / "easy_apply.md", 1))
        with sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as e:
                self.skipTest("no chromium: %s" % e)
            pg = b.new_page()
            pg.goto(FIXTURE.as_uri())
            self.assertEqual(pg.evaluate(js), "ready")
            lines = pg.evaluate("window.__jh.read()").splitlines()
            self.assertIn("fields:7", lines[0])
            self.assertTrue(lines[1].startswith("f1|text|req|How many years of work experience do you have with Apex?"))
            self.assertIn("f3|radio|req|Are you legally authorized to work in the United States?|Yes/No|", lines)
            res = pg.evaluate("window.__jh.fill({f1:'7',f2:'Immediately',f4:'Sales Cloud; Service Cloud',f7:'false',f9:'x'})")
            self.assertIn("f1:ok", res); self.assertIn("f9:miss", res)
            self.assertEqual(pg.evaluate("window.changes"), ["yrs=7", "sel=a", "c1=true", "c2=true", "follow=false"])
            self.assertIn("miss(no option", pg.evaluate("window.__jh.fill({f2:'3 months'})"))
            self.assertEqual(pg.evaluate("window.__jh.next()"), "clicked: Continue to next step")
            self.assertTrue(pg.evaluate("window.__jh.next()").startswith("NO_NEXT"))   # final page: Submit is never clicked
            b.close()


if __name__ == "__main__":
    unittest.main()
