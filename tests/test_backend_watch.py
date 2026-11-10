import json
import threading
import types
import unittest

from test_backend_helpers import RESUME, TempData
from jh import actions, db, engine, qa, runner, watch

CARD = '''<li>
  <div class="base-card relative w-full hover:no-underline base-card--link base-search-card base-search-card--link job-search-card"
       data-entity-urn="urn:li:jobPosting:{id}" data-impression-id="x" data-reference-id="y" data-tracking-id="z">
    <a class="base-card__full-link absolute top-0 right-0 bottom-0 left-0 p-0 z-[2]"
       href="https://in.linkedin.com/jobs/view/{slug}-{id}?position=1&amp;pageNum=0&amp;refId=abc&amp;trackingId=def"
       data-tracking-control-name="public_jobs_jserp-result_search-card">
      <span class="sr-only">{title}</span>
    </a>
    <div class="search-entity-media"><img class="artdeco-entity-image" alt="{company}"></div>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">
            {title}
      </h3>
      <h4 class="base-search-card__subtitle">
          <a class="hidden-nested-link" href="https://www.linkedin.com/company/x">{company}</a>
      </h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">{loc}</span>
        <time class="job-search-card__listdate--new" datetime="2026-10-19">{ago}</time>
      </div>
    </div>
  </div>
</li>'''


def page(*cards):
    return "\n".join(CARD.format(**c) for c in cards)


J1 = dict(id="4480001111", slug="salesforce-developer-at-acme", title="Senior Salesforce Developer (LWC &amp; Apex)",
          company="Acme &amp; Co", loc="United States", ago="12 minutes ago")
J2 = dict(id="4480002222", slug="salesforce-account-executive", title="Salesforce Account Executive",
          company="SellCo", loc="United States", ago="20 minutes ago")
J3 = dict(id="4480003333", slug="python-dev", title="Python Developer", company="PyCo", loc="Remote", ago="5 minutes ago")
J4 = dict(id="4480004444", slug="cpq-consultant", title="Salesforce CPQ Consultant", company="Beta", loc="India",
          ago="1 hour ago")


class ParseTests(unittest.TestCase):
    def test_parse_cards(self):
        cards = watch.parse_guest_html(page(J1, J2, J3))
        self.assertEqual([c["ext_id"] for c in cards], ["4480001111", "4480002222", "4480003333"])
        c = cards[0]
        self.assertEqual(c["title"], "Senior Salesforce Developer (LWC & Apex)")
        self.assertEqual(c["company"], "Acme & Co")
        self.assertEqual(c["location"], "United States")
        self.assertEqual(c["url"], "https://www.linkedin.com/jobs/view/4480001111/")
        self.assertEqual(c["ago"], "12 minutes ago")

    def test_parse_fallback_to_view_link_and_junk(self):
        html = '<li><a href="https://www.linkedin.com/jobs/view/salesforce-admin-at-x-4011112222?trk=1">' \
               '<span class="sr-only">Salesforce Admin</span></a></li>'
        self.assertEqual(watch.parse_guest_html(html)[0]["ext_id"], "4011112222")
        self.assertEqual(watch.parse_guest_html(""), [])
        self.assertEqual(watch.parse_guest_html("<html><li>nothing</li></html>"), [])

    def test_title_filter(self):
        for t in ("Salesforce Developer", "Sr. SFDC Engineer", "OmniStudio Consultant", "Apex/LWC Developer",
                  "Salesforce CPQ Architect", "Senior Software Engineer, Salesforce"):
            self.assertTrue(watch.title_ok(t), t)
        for t in ("Salesforce Account Executive", "Python Developer", "SDR - Salesforce", "Sales Manager (Salesforce CRM)",
                  "Salesforce Intern", "Customer Success Manager"):
            self.assertFalse(watch.title_ok(t), t)

    def test_search_url(self):
        u = watch.search_url("india", 3600)
        self.assertIn("geoId=102713980", u)
        self.assertIn("f_AL=true", u)
        self.assertIn("f_WT=2", u)
        self.assertIn("f_TPR=r3600", u)
        self.assertIn("sortBy=DD", u)


class FakeRuns:
    def __init__(self):
        self.calls = []

    def __call__(self, trigger, **kw):
        self.calls.append((trigger, kw))
        return 100 + len(self.calls)


class WatcherTests(TempData, unittest.TestCase):
    def mk(self, pages=None, status=200):
        self.fetched = []

        def fetcher(url):
            self.fetched.append(url)
            if callable(pages):
                return pages(url)
            return status, pages or ""
        self.runs = FakeRuns()
        w = watch.Watcher(None, fetcher=fetcher, start_run=self.runs, sleep=lambda s: None)
        return w

    def turn_on(self, w, **kw):
        cfg = {"on": True, "regions": ["us"], "interval_min": 10, "posts": False}
        cfg.update(kw)
        w.set(cfg)

    def test_off_does_nothing(self):
        w = self.mk(page(J1))
        self.assertIsNone(w.tick())
        self.assertEqual(self.fetched, [])

    def test_new_jobs_start_targeted_easy_apply_run_once(self):
        w = self.mk(page(J1, J2, J3, J4))
        self.turn_on(w)
        res = w.tick(now=1000)
        self.assertEqual(res["kind"], "easy_apply")
        trig, kw = self.runs.calls[0]
        self.assertEqual(trig, "watch")
        self.assertEqual(kw["lanes"], ["easy_apply"])
        self.assertEqual([t["url"] for t in kw["targets"]],
                         ["https://www.linkedin.com/jobs/view/4480001111/", "https://www.linkedin.com/jobs/view/4480004444/"])
        self.assertEqual(kw["regions"], ["us"])
        # next tick: not due yet, nothing queued -> no new run, no fetch
        n = len(self.fetched)
        self.assertIsNone(w.tick(now=1060))
        self.assertEqual(len(self.fetched), n)
        # due again, same cards -> nothing new
        self.assertIsNone(w.tick(now=1000 + 601))
        self.assertEqual(len(self.runs.calls), 1)
        st = w.status()
        self.assertEqual(st["found_today"], 2)
        self.assertEqual({r["status"] for r in st["recent"]}, {"sent"})
        evs = [r["type"] for r in self.conn.execute("SELECT type FROM events WHERE lane='watch'")]
        self.assertIn("watch_found", evs)

    def test_known_jobs_skipped(self):
        actions.save_job(self.conn, None, "easy_apply", "linkedin_us", "Acme", "Senior Salesforce Developer",
                         url="https://www.linkedin.com/jobs/view/4480001111/")
        w = self.mk(page(J1))
        self.turn_on(w)
        self.assertIsNone(w.tick(now=1000))
        row = self.conn.execute("SELECT status FROM watch_seen WHERE ext_id='4480001111'").fetchone()
        self.assertEqual(row["status"], "known")

    def test_backoff_on_rate_limit_and_unparsed_page(self):
        w = self.mk(status=429)
        self.turn_on(w, interval_min=10)
        w.tick(now=1000)
        st = w.status()
        self.assertIn("429", st["last_error"])
        self.assertEqual(st["backoff_min"], 20)
        w.tick(now=1000 + 20 * 60 + 1)
        self.assertEqual(w.status()["backoff_min"], 40)
        w2 = self.mk("<html><body><ul><li class='x'>Sign in to see jobs</li></ul></body></html>")
        self.turn_on(w2)
        w2.tick(now=5000)
        self.assertIn("format", w2.status()["last_error"])
        self.assertTrue((self.data / "logs" / "watch_last_page.html").exists())

    def test_busy_runner_keeps_queue(self):
        w = self.mk(page(J1))
        self.turn_on(w)
        orig = runner.is_running
        runner.is_running = lambda: True
        try:
            self.assertIsNone(w.tick(now=1000))
        finally:
            runner.is_running = orig
        self.assertEqual(self.runs.calls, [])
        self.assertEqual(w.status()["queued"], 1)
        self.assertEqual(w.tick(now=1010)["kind"], "easy_apply")   # idle now -> starts with the queued job

    def test_posts_pass_when_nothing_queued(self):
        w = self.mk(page())
        self.turn_on(w, posts=True, posts_interval_min=30)
        self.assertIsNone(w.tick(now=0))       # posts first due 60 s after switching on
        import time as _t
        res = w.tick(now=_t.time() + 120)
        self.assertEqual(res["kind"], "posts")
        self.assertTrue(self.runs.calls[-1][1]["quick"])

    def test_approvals_go_first_even_when_watch_off(self):
        pid = self._ready_portal()
        actions.approve_submit(self.conn, [pid])
        w = self.mk(page(J1))
        res = w.tick(now=1000)
        self.assertEqual(res["kind"], "submit")
        self.assertEqual(self.runs.calls[0][0], "approved")
        self.assertEqual(self.runs.calls[0][1]["lanes"], ["submit"])

    def _ready_portal(self, ready=True, company="Acme"):
        jid = actions.save_job(self.conn, None, "easy_apply", "linkedin_us", company, "Salesforce Developer",
                               url="https://www.linkedin.com/jobs/view/42/")["job_id"]
        r = actions.save_portal_prefill(self.conn, None, "easy_apply", jid, "linkedin_easy_apply",
                                        "https://www.linkedin.com/jobs/view/42/", RESUME, [], ready=ready)
        return r["portal_id"]


class SubmitTests(TempData, unittest.TestCase):
    def portal(self, ready, company):
        jid = actions.save_job(self.conn, None, "easy_apply", "linkedin_us", company, "Salesforce Developer")["job_id"]
        return actions.save_portal_prefill(self.conn, None, "easy_apply", jid, "linkedin_easy_apply",
                                           "https://www.linkedin.com/jobs/view/%d/" % (1000 + jid), RESUME,
                                           [] if ready else ["Sponsorship — left for you"], ready=ready)["portal_id"]

    def test_approve_only_ready_and_open(self):
        a, b = self.portal(True, "A"), self.portal(False, "B")
        res = actions.approve_submit(self.conn, [a, b, 999, True, "x"])
        self.assertEqual(res["approved"], [a])
        self.assertEqual({r["id"] for r in res["rejected"]}, {b, 999})
        q = actions.submit_queue(self.conn)
        self.assertEqual([x["id"] for x in q], [a])
        self.assertEqual(actions.pending_approvals(self.conn), 1)

    def test_mark_submitted_enforces_approval(self):
        a, b = self.portal(True, "A"), self.portal(True, "B")
        actions.approve_submit(self.conn, [a])
        self.assertIn("error", actions.mark_submitted(self.conn, None, "submit", b, "sent"))
        self.assertTrue(actions.mark_submitted(self.conn, None, "submit", a, "Your application was sent")["ok"])
        row = self.conn.execute("SELECT status, submitted_at, approved_at FROM portal_apps WHERE id=?", (a,)).fetchone()
        self.assertEqual(row["status"], "submitted")
        self.assertTrue(row["submitted_at"])
        self.assertIsNone(row["approved_at"])
        self.assertIn("error", actions.mark_submitted(self.conn, None, "submit", a, "again"))   # no double
        evs = [r["type"] for r in self.conn.execute("SELECT type FROM events")]
        self.assertIn("portal_submitted", evs)

    def test_failed_goes_back_and_approval_expires(self):
        a = self.portal(True, "A")
        actions.approve_submit(self.conn, [a])
        actions.mark_submit_failed(self.conn, None, "submit", a, "new required question: visa")
        row = self.conn.execute("SELECT status, ready, approved_at, submit_note FROM portal_apps WHERE id=?", (a,)).fetchone()
        self.assertEqual((row["status"], row["ready"], row["approved_at"]), ("needs_input", 0, None))
        self.assertIn("visa", row["submit_note"])
        b = self.portal(True, "B")
        actions.approve_submit(self.conn, [b])
        self.conn.execute("UPDATE portal_apps SET approved_at='2020-01-01T00:00:00+05:30' WHERE id=?", (b,))
        self.assertEqual(actions.submit_queue(self.conn), [])

    def test_runner_submit_lane_only_when_picked_and_releases_leftovers(self):
        a = self.portal(True, "A")
        actions.approve_submit(self.conn, [a])
        calls = []
        orig = runner._load_engine
        runner._load_engine = lambda: types.SimpleNamespace(
            run_lane=lambda run_id, lane, cfg, emit, stop: calls.append(lane) or {"status": "done"})
        try:
            runner.start_run("button", cfg=self.cfg)          # default run: submit must NOT run
            self.assertTrue(runner.wait(30))
            self.assertNotIn("submit", calls)
            runner.start_run("approved", cfg=self.cfg, lanes=["submit"])
            self.assertTrue(runner.wait(30))
            self.assertEqual(calls[-1], "submit")
        finally:
            runner._load_engine = orig
        row = self.conn.execute("SELECT approved_at, ready, submit_note FROM portal_apps WHERE id=?", (a,)).fetchone()
        self.assertIsNone(row["approved_at"])          # released: dispatcher won't loop on it
        self.assertEqual(row["ready"], 1)               # still approvable with one click
        self.assertIn("ended", row["submit_note"])

    def test_manual_mark_submitted_sets_time(self):
        a = self.portal(True, "A")
        actions.set_portal_status(self.conn, a, "submitted")
        self.assertTrue(self.conn.execute("SELECT submitted_at FROM portal_apps WHERE id=?", (a,)).fetchone()[0])


class BrowserPickTests(TempData, unittest.TestCase):
    def test_prompt_and_tool(self):
        dev = "3b7d6d57-059f-4afc-aa29-2371bfb387e2"
        t = engine.compose_system_prompt("posts", str(engine.PROMPTS_DIR / "posts.md"), 1, browser="Browser 2", device_id=dev)
        self.assertIn("Chrome browser: deviceId %s — LOCKED" % dev, t)
        self.assertIn("starts with **\"Akshay Kumar\"**", t)
        self.assertIn("list_connected_browsers", t)
        # a name alone is not a lock (names are renumbered): the lane searches this computer only
        t2 = engine.compose_system_prompt("posts", str(engine.PROMPTS_DIR / "posts.md"), 1, browser="Browser 2")
        self.assertIn("Chrome browser: not set yet", t2)
        self.assertIn("onThisComputer", t2)
        import importlib.util, os
        spec = importlib.util.spec_from_file_location("jhmcp", str(engine.APP_ROOT / "engine" / "jobhunt_mcp.py")
                                                      if hasattr(engine, "APP_ROOT") else os.path.join(os.path.dirname(engine.__file__), "..", "engine", "jobhunt_mcp.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        ctx = m.Ctx()
        from jh import config
        self.assertTrue(m.call_tool(ctx, "set_preferred_browser", {"name": "Browser 4"})[1])        # device id required
        res, err = m.call_tool(ctx, "set_preferred_browser", {"name": "Browser 4", "device_id": dev})
        self.assertFalse(err)
        self.assertEqual(config.load()["engine"]["chrome_device_id"], dev)
        # locked: the engine can never move LinkedIn work to another browser (another laptop, someone else's Chrome)
        res, err = m.call_tool(ctx, "set_preferred_browser", {"name": "Browser 1", "device_id": "84f268b1-acab-4492-b586-bbdde90ebdaa"})
        self.assertTrue(err)
        self.assertIn("locked", res["error"])
        self.assertEqual(config.load()["engine"]["chrome_device_id"], dev)
        self.assertFalse(m.call_tool(ctx, "set_preferred_browser", {"name": "Browser 2", "device_id": dev})[1])


class PromptTests(unittest.TestCase):
    def test_targets_and_quick_in_prompt(self):
        t = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), 1, "24h", ["us"],
                                         [{"title": "SF Dev", "company": "Acme", "location": "US",
                                           "url": "https://www.linkedin.com/jobs/view/123/"},
                                          {"title": "evil", "url": "https://evil.example/x"}])
        self.assertIn("WATCH MODE", t)
        self.assertIn("https://www.linkedin.com/jobs/view/123/", t)
        self.assertNotIn("evil.example", t)
        p = engine.compose_system_prompt("posts", str(engine.PROMPTS_DIR / "posts.md"), 1, "24h", None, None, True)
        self.assertIn("quick pass", p)
        s = engine.compose_system_prompt("submit", str(engine.PROMPTS_DIR / "submit.md"), 1)
        self.assertIn("get_submit_queue", s)
        self.assertIn("Immediate joiner", s)    # answers bank rules apply to the submit lane too


if __name__ == "__main__":
    unittest.main()
