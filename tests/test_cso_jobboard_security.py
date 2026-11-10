"""CSO audit regressions for the Job board: hostile board data, the daily AI budget, the Easy Apply switch,
linear-time parsing of untrusted feeds, bounded gunzip."""
import gzip
import time
import unittest
from datetime import timedelta

from test_backend_helpers import TempData
from test_backend_jobboard import NOW, Fake, JobBoardBase, gh, gh_job
from test_team_security import _Srv
from jh import actions, boards, config, db, engine, events, jobboard, watch


def card(i, **kw):
    c = {"source": "careers", "ext_id": "gh:acme:sec%d" % i, "title": "Salesforce Developer %d" % i, "company": "Acme",
         "url": "https://job-boards.greenhouse.io/acme/jobs/%d" % i,
         "apply_url": "https://job-boards.greenhouse.io/acme/jobs/%d" % i, "location": "Remote - India",
         "remote": True, "open": True, "text": "Remote. Apex.", "pub": NOW - timedelta(hours=2)}
    c.update(kw)
    return c


class HostileUrlTests(JobBoardBase):
    def test_board_links_are_http_on_public_hosts_only(self):
        bad = ["javascript:alert(1)", "file:///C:/Users/a/AppData/Roaming/JobHunter/config.json", "data:text/html,x",
               "http://127.0.0.1:8765/#board", "http://localhost:8765/", "http://192.168.1.1/admin", "http://[::1]/",
               "http://router.local/", "https://intranet/apply"]
        for i, u in enumerate(bad):
            lid, _ = jobboard.upsert(self.conn, card(100 + i, url=u, apply_url=u), NOW)
            r = self.conn.execute("SELECT url, apply_url, apply_kind FROM listings WHERE id=?", (lid,)).fetchone()
            self.assertEqual((r["url"], r["apply_url"]), ("", None), u)
            self.assertNotIn(r["apply_kind"], ("company_site", "portal", "login_portal"), u)
            self.assertEqual(jobboard.platform_of(u), (None, None), u)

    def test_own_site_prefill_never_on_a_local_host(self):
        lid, _ = jobboard.upsert(self.conn, card(1, apply_url="https://careers.acme.com/jobs/1"), NOW)
        job = actions.save_job(self.conn, None, "board_apply", "board", "Acme", "Salesforce Developer 1",
                               url="https://careers.acme.com/jobs/1")["job_id"]
        self.conn.execute("UPDATE listings SET job_id=?, url='http://127.0.0.1:8765/' WHERE id=?", (job, lid))
        self.assertEqual(actions.board_form_ok(self.conn, job, "https://127.0.0.1:8765/", "board_apply"), (None, False))
        self.assertEqual(actions.board_form_ok(self.conn, job, "https://careers.acme.com/apply", "board_apply"),
                         ("company_site", True))

    def test_oracle_object_storage_is_not_a_job_portal(self):
        self.assertEqual(jobboard.platform_of("https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/x")[1],
                         "login_portal")
        u = "https://objectstorage.us-ashburn-1.oraclecloud.com/n/x/b/y/o/apply.html"
        self.assertEqual(jobboard.platform_of(u)[1], "company_site")
        self.assertFalse(actions.submit_host_ok(u))

    def test_ai_target_block_drops_non_web_links(self):
        t = {"job_id": 7, "company": "Acme", "role": "Salesforce Developer", "platform": "acme", "kind": "company_site",
             "url": "file:///C:/Users/a/config.json", "apply_url": "http://127.0.0.1:8765/", "elig": "open"}
        prompt = engine.compose_system_prompt("board_apply", engine.lane_prompt_path("board_apply"), targets=[t])
        self.assertIn("7 — Acme", prompt)
        self.assertNotIn("file:///", prompt)
        self.assertNotIn("127.0.0.1:8765/ —", prompt)
        self.assertIn("apply: - — job page: -", prompt)

    def test_malformed_card_does_not_abort_the_refresh(self):
        jobs = [gh_job(1), dict(gh_job(2), title=12345), dict(gh_job(3), absolute_url={"x": 1}), gh_job(4, title="Salesforce QA")]
        c = self.refresh(Fake({"boards/acme": (200, gh(jobs))}))
        titles = {r["title"] for r in self.rows()}
        self.assertIn("Salesforce Developer", titles)
        self.assertIn("Salesforce QA", titles)
        self.assertGreaterEqual(c["new"], 2)

    def test_board_token_cannot_change_the_api_host(self):
        self.assertEqual(boards.board_url("recruitee", "acme"), "https://acme.recruitee.com/api/offers/")
        for plat, tok in (("recruitee", "evil.com/x?"), ("recruitee", "a.b"), ("workable", "../x"), ("lever", "a/../b"),
                          ("ashby", "x@evil.com"), ("greenhouse", "")):
            with self.assertRaises(ValueError, msg=(plat, tok)):
                boards.board_url(plat, tok)
        self.conn.execute("INSERT INTO career_boards (platform, token, status, last_ok) VALUES ('recruitee','evil.com/x?','active',?)",
                          (db.now_iso(),))
        fake = Fake({"boards/acme": (200, gh([gh_job(1)]))})
        self.refresh(fake)
        self.assertFalse(any("evil.com" in u for u in fake.calls))


class BudgetAndSwitchTests(JobBoardBase):
    def seed(self, n, kind="portal"):
        ids = []
        for i in range(n):
            lid, _ = jobboard.upsert(self.conn, card(i + (50 if kind == "easy_apply" else 0)), NOW)
            if kind == "easy_apply":
                self.conn.execute("UPDATE listings SET source='linkedin', apply_kind='easy_apply', jd_checked=1,"
                                  " url='https://www.linkedin.com/jobs/view/40000%02d/' WHERE id=?" % i, (lid,))
            ids.append(lid)
        return ids

    def jobboard_run_cost(self, lane, usd):
        rid = self.conn.execute("INSERT INTO runs (started_at, status, trigger, stats_json) VALUES (?, 'done', 'jobboard', '{}')",
                                (db.now_iso(),)).lastrowid
        events.emit(self.conn, rid, lane, "lane_finished", "done", "success", {"cost_usd": usd})

    def test_easy_apply_cost_of_job_board_runs_counts_toward_the_budget(self):
        self.seed(2)
        self.jobboard_run_cost("easy_apply", 4.0)
        self.jobboard_run_cost("board_apply", 2.5)
        events.emit(self.conn, None, "easy_apply", "lane_finished", "done", "success", {"cost_usd": 50})   # not a board run
        self.assertEqual(jobboard.spent_today(self.conn), 6.5)
        self.cfg["jobboard"]["daily_usd"] = 6
        self.assertEqual(jobboard.auto_pick(self.conn, self.cfg, now=NOW), [])

    def test_over_budget_auto_picks_go_back_but_his_own_picks_run(self):
        auto = self.seed(2)
        jobboard.queue(self.conn, auto, by="auto")
        mine, _ = jobboard.upsert(self.conn, card(9), NOW)
        jobboard.queue(self.conn, [mine], by="you")
        self.jobboard_run_cost("board_apply", 9.0)
        self.cfg["jobboard"]["daily_usd"] = 6
        ea, forms, notes = jobboard.claim(self.conn, 1, limit=5, html_get=Fake().html, cfg=self.cfg)
        self.assertEqual([t["listing_id"] for t in ea + forms], [mine])
        st = {r["id"]: r["status"] for r in self.rows()}
        self.assertEqual({st[i] for i in auto}, {"new"})
        self.assertEqual(jobboard.pending(self.conn), 0)       # no empty-run loop in the watcher
        self.assertTrue(any("budget" in n for n in notes))

    def test_easy_apply_off_in_settings_stops_auto_fill_on_linkedin(self):
        self.seed(2, kind="easy_apply")
        self.cfg["easy_apply"] = False
        self.assertEqual(jobboard.auto_pick(self.conn, self.cfg, now=NOW), [])
        self.cfg["easy_apply"] = True
        picked = jobboard.auto_pick(self.conn, self.cfg, now=NOW)
        self.assertEqual(len(picked), 2)
        self.cfg["easy_apply"] = False                         # switched off after the pick
        ea, forms, _ = jobboard.claim(self.conn, 1, limit=5, html_get=Fake().html, cfg=self.cfg)
        self.assertEqual((ea, forms), ([], []))
        jobboard.queue(self.conn, picked[:1], by="you")        # his own click still fills it
        ea, forms, _ = jobboard.claim(self.conn, 2, limit=5, html_get=Fake().html, cfg=self.cfg)
        self.assertEqual(len(ea), 1)


class LinearParsingTests(unittest.TestCase):
    def assertFast(self, fn, *a, limit=3.0):
        t0 = time.time()
        fn(*a)
        self.assertLess(time.time() - t0, limit)

    def test_hostile_rss_and_html_are_linear(self):
        self.assertFast(jobboard.wwr_cards, "<item>" * 30000 + "x" * 2_000_000)
        self.assertFast(jobboard.wwr_cards, "<item>" + "<title>" * 200000 + "</item>")
        self.assertFast(boards._strip, "<script>" * 30000 + "x" * 2_000_000)
        self.assertFast(boards._strip, "<" * 300000)

    def test_strip_and_rss_still_work(self):
        self.assertEqual(boards._strip("<p>Apex<script>evil()</script> &amp; LWC</p><style>x{}</style>"), "Apex & LWC")
        self.assertEqual(boards._strip("a <script>never closed"), "a never closed")
        xml = ("<rss><items/><item><title>Acme: Salesforce Dev</title><link>https://weworkremotely.com/remote-jobs/a</link>"
               "<region>Anywhere</region></item><item><title><![CDATA[B: Salesforce Admin]]></title></item></rss>")
        self.assertEqual([(c["company"], c["title"]) for c in jobboard.wwr_cards(xml)],
                         [("Acme", "Salesforce Dev"), ("B", "Salesforce Admin")])


class JobBoardRoutes(_Srv, unittest.TestCase):
    def test_new_post_routes_refuse_cross_site_and_null_origin(self):
        for path, body in (("/api/jobboard/apply", {"ids": [1]}), ("/api/jobboard/refresh", {}),
                           ("/api/jobboard/settings", {"daily_usd": 100}), ("/api/jobboard/1", {"status": "hidden"})):
            for hdr in ({"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "same-site"}):
                st, _ = self.req("POST", path, body, hdr)
                self.assertEqual(st, 403, (path, hdr))
        self.assertEqual(config.load()["jobboard"]["daily_usd"], 6)

    def test_jobboard_settings_cannot_touch_engine_and_api_sends_nosniff(self):
        before = config.load()
        st, d = self.req("POST", "/api/jobboard/settings",
                         {"daily_usd": 9, "engine": {"claude_path": "C:/evil.exe"}, "data_dir": "C:/x"}, self.same_origin())
        self.assertEqual(st, 200)
        cfg = config.load()
        self.assertEqual((cfg["jobboard"]["daily_usd"], cfg["engine"]["claude_path"], cfg.get("data_dir")),
                         (9, before["engine"]["claude_path"], before.get("data_dir")))
        c = __import__("http.client").client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/jobboard")
        r = c.getresponse(); r.read(); c.close()
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")


class GunzipTests(unittest.TestCase):
    def test_decompression_bomb_is_cut(self):
        bomb = gzip.compress(b"\0" * 50_000_000)
        self.assertLess(len(bomb), 200_000)
        self.assertEqual(len(watch.gunzip_limited(bomb, 1_000_000)), 1_000_000)
        self.assertEqual(watch.gunzip_limited(gzip.compress(b'{"ok": 1}'), 100), b'{"ok": 1}')


if __name__ == "__main__":
    unittest.main()
