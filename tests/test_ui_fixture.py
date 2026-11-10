"""UI tests: the fixture server's API contract, plus a browser smoke test (skipped without Playwright).

    python -m unittest tests.test_ui_fixture -v
"""
import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_fixture_server as fx  # noqa: E402


def start(fresh=False, speed=20.0):
    world = fx.World(fresh=fresh)
    handler = type("H", (fx.Handler,), {"world": world, "missing": set(), "speed": speed, "quiet": True})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=fx.run_loop, args=(world, speed, 999, True), daemon=True).start()
    return srv, world, "http://127.0.0.1:%d" % srv.server_address[1]


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.headers, r.read()


def post(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class FixtureContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.world, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_static_files(self):
        for path, ctype in (("/", "text/html"), ("/app.css", "text/css"), ("/app.js", "application/javascript"), ("/favicon.svg", "image/svg+xml")):
            st, hdr, body = get(self.base, path)
            self.assertEqual(st, 200, path)
            self.assertIn(ctype, hdr["Content-Type"])
            self.assertTrue(body)

    def test_no_external_requests_in_ui(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
        for name in ("index.html", "app.css", "app.js"):
            with open(os.path.join(root, name), encoding="utf-8") as f:
                text = f.read()
            for bad in ('src="http', "src='http", 'href="http', "@import", "fonts.googleapis", "cdn."):
                if bad == 'href="http' and name == "app.js":
                    continue
                self.assertNotIn(bad, text, "%s references an external resource (%s)" % (name, bad))

    def test_summary_shape(self):
        s = json.loads(get(self.base, "/api/summary")[2])
        for k in ("sent_today", "sent_7d", "sent_total", "replies_total", "reply_rate", "awaiting", "nudges_due", "bounced",
                  "skipped_today", "per_hour_current_run", "last_run", "running", "by_source", "dry_run", "gmail_configured"):
            self.assertIn(k, s)
        self.assertLessEqual(s["reply_rate"], 1)

    def test_lists_are_arrays(self):
        for path in ("/api/applications", "/api/jobs?status=skipped", "/api/runs", "/api/events?limit=5", "/api/portal", "/api/env"):
            data = json.loads(get(self.base, path)[2])
            self.assertIsInstance(data, list, path)
        apps = json.loads(get(self.base, "/api/applications")[2])
        self.assertGreaterEqual(len(apps), 20)
        names = {a["contact_name"] for a in apps}
        self.assertTrue({"Patrick Soule", "Nicola Bryan"} <= names)
        detail = json.loads(get(self.base, "/api/applications/%d" % apps[0]["contact_id"])[2])
        for k in ("job", "contact", "emails", "replies", "timeline"):
            self.assertIn(k, detail)

    def test_events_backlog_is_latest_ascending(self):
        evs = json.loads(get(self.base, "/api/events?run_id=7&limit=10")[2])
        self.assertEqual(len(evs), 10)
        self.assertEqual([e["id"] for e in evs], sorted(e["id"] for e in evs))
        self.assertEqual(evs[-1]["type"], "run_finished")

    def test_charts(self):
        c = json.loads(get(self.base, "/api/charts")[2])
        self.assertEqual(len(c["daily"]), 14)
        self.assertEqual(set(c["funnel"]), {"found", "qualified", "contacted", "replied"})
        self.assertIn("latest_replies", c)

    def test_settings_roundtrip_keeps_password(self):
        s = json.loads(get(self.base, "/api/settings")[2])
        self.assertTrue(s["email"]["app_password"].startswith("••••"))
        s["max_emails_per_run"] = 30
        st, out = post(self.base, "/api/settings", s)
        self.assertEqual(st, 200)
        self.assertEqual(out["max_emails_per_run"], 30)
        self.assertTrue(self.world.settings["email"]["app_password"].endswith("wxyz"))

    def test_portal_mark(self):
        items = json.loads(get(self.base, "/api/portal")[2])
        st, out = post(self.base, "/api/portal/%d" % items[0]["id"], {"status": "submitted"})
        self.assertEqual(st, 200)
        self.assertEqual(post(self.base, "/api/portal/%d" % items[0]["id"], {"status": "nope"})[0], 400)

    def test_run_conflict_and_stop(self):
        st, out = post(self.base, "/api/run")
        self.assertEqual(st, 200)
        self.assertIn("run_id", out)
        for _ in range(50):
            if self.world.running:
                break
            time.sleep(0.05)
        self.assertEqual(post(self.base, "/api/run")[0], 409)
        post(self.base, "/api/stop")
        for _ in range(100):
            if not self.world.running:
                break
            time.sleep(0.05)
        self.assertFalse(self.world.running)
        last = [e for e in self.world.events if e["type"] == "run_finished"][-1]
        self.assertEqual(last["data"]["status"], "stopped")


try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    HAVE_PW = True
except Exception:
    HAVE_PW = False


@unittest.skipUnless(HAVE_PW and os.environ.get("JH_UI_BROWSER", "1") != "0", "Playwright not installed")
class BrowserSmoke(unittest.TestCase):
    def test_dashboard_renders_and_runs(self):
        srv, world, base = start(speed=6.0)
        errors = []
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page(viewport={"width": 1024, "height": 768})
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.goto(base + "/")
                pg.wait_for_selector(".tbl tbody tr")
                self.assertEqual(pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)
                self.assertGreaterEqual(pg.locator("#lanes .lane-card").count(), 6)
                pg.keyboard.press("r")
                pg.wait_for_selector("#run-pill.pill-running", timeout=5000)
                pg.wait_for_selector(".rail-item.is-running", timeout=10000)
                pg.click(".tbl tbody tr")
                pg.wait_for_selector("#drawer.open .mail")
                pg.keyboard.press("Escape")
                pg.click("#tab-portal")
                pg.wait_for_selector(".pq-item")
                pg.keyboard.press("/")
                self.assertEqual(pg.evaluate("document.activeElement.id"), "q")
                b.close()
        finally:
            world.stop_flag.set()
            srv.shutdown()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
