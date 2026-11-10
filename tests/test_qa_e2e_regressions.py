"""Regressions found by the end-to-end backend QA pass (2026-10-22).

Each test reproduces a defect seen when driving the real server / runner / mock engine end to end.
Found by /qa (backend E2E) on 2026-10-22.
"""
import json
import os
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone

from test_backend_helpers import RESUME, TempData
from jh import actions, config, db, events, followups, jobboard, mailer, qa, resume, runner, watch


def _thread_row(conn, company, sent_day, role="Salesforce Developer", source="posts", email="a@acme.example"):
    jid = conn.execute("INSERT INTO jobs (source, company, role, status, norm_key) VALUES (?,?,?,'contacted',?)",
                       (source, company, role, company.lower() + "|x")).lastrowid
    cid = conn.execute("INSERT INTO contacts (job_id, name, email) VALUES (?, 'Ann Lee', ?)", (jid, email)).lastrowid
    conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, message_id, status, sent_at) VALUES (?,?,'initial','s',?,"
                 "'sent',?)", (jid, cid, "<o%d@x>" % cid, sent_day.isoformat() + "T10:00:00+05:30"))
    return jid, cid


class SettingsValidationRegression(unittest.TestCase):
    # Regression: ISSUE-001 — POST /api/settings with 1e999 / wrong-typed sections returned 500
    def test_infinite_numbers_and_wrong_types_never_raise(self):
        base = config._deep_merge(config.DEFAULTS, {})
        out = config.apply_update(base, json.loads('{"port": 1e999, "max_emails_per_run": 1e999,'
                                                   ' "agencies": {"per_run": 1e999}, "engine": {"lane_timeout_min": 1e999}}'),
                                  from_api=True)
        self.assertEqual(out["port"], config.DEFAULTS["port"])
        self.assertEqual(out["agencies"]["per_run"], config.DEFAULTS["agencies"]["per_run"])
        self.assertEqual(out["engine"]["lane_timeout_min"], config.DEFAULTS["engine"]["lane_timeout_min"])
        out = config.apply_update(base, {"email": "x", "engine": 5, "watch": [1], "jobboard": "y"}, from_api=True)
        self.assertIsInstance(out["email"], dict)
        self.assertIsInstance(out["engine"], dict)
        self.assertEqual(out["watch"]["interval_min"], config.DEFAULTS["watch"]["interval_min"])

    # Regression: ISSUE-012 — jobboard.li_regions sent as a string turned on all nine LinkedIn regions
    def test_li_regions_string(self):
        base = config._deep_merge(config.DEFAULTS, {})
        self.assertEqual(config.apply_update(base, {"jobboard": {"li_regions": "us"}})["jobboard"]["li_regions"], ["us"])
        self.assertEqual(config.apply_update(base, {"jobboard": {"li_regions": 5}})["jobboard"]["li_regions"],
                         config.DEFAULTS["jobboard"]["li_regions"])


class FollowupRegressions(TempData, unittest.TestCase):
    # Regression: ISSUE-002 — agency pitches were nudged after 1 business day; the Agencies tab promises day 3 and 7
    def test_agency_nudges_day_3_and_7(self):
        from jh import agencies
        d = followups.nudge_due
        mon = datetime(2026, 10, 19).date()
        self.assertEqual(d(mon, agency=True), ("nudge1", mon + timedelta(days=3)))
        self.assertEqual(d(mon, mon + timedelta(days=3), agency=True), ("nudge2", mon + timedelta(days=7)))
        fri = datetime(2026, 10, 23).date()          # day 3 = Monday (weekday), day 7 = Friday
        self.assertEqual(d(fri, agency=True)[1].weekday(), 0)
        wed = datetime(2026, 10, 21).date()          # day 3 = Saturday -> moved to Monday
        self.assertEqual(d(wed, agency=True)[1], datetime(2026, 10, 26).date())
        self.assertEqual(d(mon), ("nudge1", mon + timedelta(days=1)))     # job outreach unchanged
        # through thread_state: an agency pseudo job 1 business day old is not due yet
        jid, cid = _thread_row(self.conn, "Heta Staffing", db.today_ist() - timedelta(days=1),
                               role=agencies.AGENCY_ROLE + " - Oct 2026", source="agency")
        t = db.load_threads(self.conn, [cid])[cid]
        self.assertGreater(t["next_followup"], db.today_ist())

    # Regression: ISSUE-003 — a nudge whose SMTP send ended ambiguously (maybe_sent) was sent again next run
    def test_maybe_sent_nudge_not_resent(self):
        _thread_row(self.conn, "Acme", db.today_ist() - timedelta(days=7))
        calls = []

        def flaky(*a, **k):
            calls.append(a[1])
            raise mailer.MailError("connection dropped after DATA", maybe_sent=True)
        orig = mailer.send
        mailer.send = flaky
        try:
            first = followups.run_followups(self.conn, None, config.load())
        finally:
            mailer.send = orig
        second = followups.run_followups(self.conn, None, config.load())
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["errors"], 1)
        self.assertEqual(second["nudges_sent"], 0)
        row = self.conn.execute("SELECT status, error FROM emails WHERE kind='nudge1'").fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertIn("may have been delivered", row["error"])


class PortalVoidRegression(TempData, unittest.TestCase):
    # Regression: ISSUE-004 — portal-lane prefills of jobs skipped only for "no email found" (its documented candidates)
    # were abandoned by void_skipped_prefills on the next /api/portal or /api/summary call (4 such rows in the real DB)
    def test_no_email_skip_keeps_prefill_but_real_skip_voids(self):
        def job(company, skip):
            return actions.save_job(self.conn, None, "jobright", "jobright", company, "Salesforce Developer", decision="skipped",
                                    skip_reason=skip, apply_url="https://job-boards.greenhouse.io/x/jobs/1")["job_id"]
        keep = job("NoEmail Co", "no email found")
        void = job("Hybrid Co", "onsite/hybrid")
        pk = actions.save_portal_prefill(self.conn, None, "portal", keep, "greenhouse", "https://job-boards.greenhouse.io/x/jobs/1",
                                         RESUME, [], ready=True)["portal_id"]
        pv = actions.save_portal_prefill(self.conn, None, "portal", void, "greenhouse", "https://job-boards.greenhouse.io/x/jobs/1",
                                         RESUME, [], ready=True)["portal_id"]
        actions.expire_approvals(self.conn)
        st = dict(self.conn.execute("SELECT id, status FROM portal_apps").fetchall())
        self.assertEqual(st[pk], "prefilled")
        self.assertEqual(st[pv], "abandoned")
        self.assertEqual(actions.approve_submit(self.conn, [pk])["approved"], [pk])
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (keep,)).fetchone()[0], "skipped")  # retry kept


class CsvRegression(TempData, unittest.TestCase):
    # Regression: ISSUE-005 — every empty CSV cell was exported as a lone apostrophe ("" in "=+-@" is True)
    def test_empty_cells_stay_empty(self):
        jid = actions.save_job(self.conn, None, "portal", "manual", "QCo", "Salesforce Developer")["job_id"]
        qa.record(self.conn, None, "portal", jid, [{"question": "Why us?", "answer": "=1+1", "source": "ai"}])
        text = qa.export_log_csv(self.conn)
        self.assertNotIn(",'\r\n", text)
        self.assertNotIn(",'\n", text)
        self.assertIn("'=1+1", text)


class JobBoardBudgetRegressions(TempData, unittest.TestCase):
    def _listing(self, i, kind="portal"):
        url = "https://job-boards.greenhouse.io/acme/jobs/%d" % i
        self.conn.execute("INSERT INTO listings (norm_key, source, sources, company, title, url, apply_url, apply_kind, platform,"
                          " posted_at, status, elig, jd_text, jd_checked) VALUES (?,?,?,?,?,?,?,?,?,?,'new','open','x',1)",
                          ("acme|sf %d" % i, "careers", "careers", "Acme", "Salesforce Developer %d" % i, url, url, kind, "greenhouse",
                           jobboard.iso(jobboard._now() - timedelta(hours=1))))

    # Regression: ISSUE-006 — "AI budget per day (USD)" = 0 meant unlimited auto-fill spending
    def test_zero_budget_means_no_auto_fill(self):
        self._listing(1)
        cfg = config.load()
        cfg["jobboard"].update(daily_usd=0, auto_fill=True, on=True)
        self.assertEqual(jobboard.auto_pick(self.conn, cfg), [])
        cfg["jobboard"]["daily_usd"] = 5
        self.assertEqual(len(jobboard.auto_pick(self.conn, cfg)), 1)

    # Regression: ISSUE-007 — spent_today ignored the Easy Apply lane of a Job board run (auto-fill picks Easy Apply too)
    def test_spent_today_counts_board_runs_easy_apply_lane(self):
        rid = self.conn.execute("INSERT INTO runs (started_at, status, trigger) VALUES (?, 'done', 'jobboard')",
                                (db.now_iso(),)).lastrowid
        other = self.conn.execute("INSERT INTO runs (started_at, status, trigger) VALUES (?, 'done', 'button')",
                                  (db.now_iso(),)).lastrowid
        events.emit(self.conn, rid, "board_apply", "lane_finished", "x", "info", {"cost_usd": 1.0})
        events.emit(self.conn, rid, "easy_apply", "lane_finished", "x", "info", {"cost_usd": 2.0})
        events.emit(self.conn, other, "easy_apply", "lane_finished", "x", "info", {"cost_usd": 4.0})   # not the board's
        self.assertEqual(jobboard.spent_today(self.conn), 3.0)


class JobBoardLifecycleRegressions(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        url = "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/R1"
        self.conn.execute("INSERT INTO listings (norm_key, source, sources, company, title, url, apply_url, apply_kind, platform,"
                          " posted_at, status, elig, jd_text) VALUES ('acme|salesforce developer','careers','careers','Acme',"
                          "'Salesforce Developer',?,?,'login_portal','workday',?,'new','open','x')",
                          (url, url, jobboard.iso(jobboard._now())))
        self.lid = self.conn.execute("SELECT id FROM listings").fetchone()[0]
        self.url = url

    # Regression: ISSUE-008 — "Try filling again" after a sign-in wall: the new prefill was voided at once because the
    # job was still 'skipped' ("needs you: sign in ...")
    def test_retry_after_sign_in_wall_keeps_new_prefill(self):
        jobboard.queue(self.conn, [self.lid])
        _, forms, _ = jobboard.claim(self.conn, None)
        jid = forms[0]["job_id"]
        actions.set_job_status(self.conn, None, "board_apply", jid, "skipped", "needs you: sign in to Workday and apply")
        jobboard.finish(self.conn, None, [self.lid])
        jobboard.queue(self.conn, [self.lid])                     # he signed in and clicked "Try filling again"
        _, forms, _ = jobboard.claim(self.conn, None)
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "qualified")
        actions.save_portal_prefill(self.conn, None, "board_apply", jid, "workday", self.url, RESUME, [], ready=True)
        actions.expire_approvals(self.conn)
        self.assertEqual(self.conn.execute("SELECT status FROM portal_apps WHERE job_id=?", (jid,)).fetchone()[0], "prefilled")

    # Regression: ISSUE-009 — after Stop the dispatcher restarted the Job board run within one tick, and a second Stop
    # marked the job "Could not be filled automatically"
    def test_stop_releases_queue_without_using_a_try(self):
        jobboard.queue(self.conn, [self.lid])
        jobboard.claim(self.conn, None)
        jobboard.finish(self.conn, None, [self.lid], stopped=True)
        r = self.conn.execute("SELECT status, tries FROM listings WHERE id=?", (self.lid,)).fetchone()
        self.assertEqual((r["status"], r["tries"]), ("new", 0))
        self.assertEqual(jobboard.pending(self.conn), 0)
        starts = []
        from jh import config
        self.cfg["autopilot"]["on"] = False           # only the Job board dispatch is under test here
        config.save(self.cfg)
        w = watch.Watcher(None, fetcher=lambda u, t=20: (200, ""), start_run=lambda *a, **k: starts.append(k) or 1, jobboard=True)
        jobboard._state.update(last_fast=time.time(), last_li=time.time(), loaded=True)
        self.assertIsNone(w.tick())
        self.assertEqual(starts, [])

    def test_runner_passes_stop_to_finish(self):
        class Engine:
            @staticmethod
            def run_lane(run_id, lane, cfg, emit, stop):
                runner.stop_run()
                return {"status": "stopped", "counts": {}}
        orig = runner._load_engine
        runner._load_engine = lambda: Engine
        try:
            jobboard.queue(self.conn, [self.lid])
            runner.start_run("jobboard", cfg=self.cfg, lanes=["board_apply"])
            self.assertTrue(runner.wait(30))
        finally:
            runner._load_engine = orig
        r = self.conn.execute("SELECT status, tries FROM listings WHERE id=?", (self.lid,)).fetchone()
        self.assertEqual((r["status"], r["tries"]), ("new", 0))


class JobBoardRobustnessRegressions(TempData, unittest.TestCase):
    # Regression: ISSUE-010 — one non-object record in Jobicy's JSON aborted the whole refresh; one malformed HN hit
    # dropped the whole Hacker News source
    def test_odd_records_do_not_abort_refresh(self):
        from jh import boards
        from test_backend_jobboard import Fake
        saved, boards.SEED_BOARDS = boards.SEED_BOARDS, []
        try:
            self.conn.execute("DELETE FROM career_boards")
            now = datetime.now(timezone.utc)
            fake = Fake({"jobicy.com/api": (200, {"jobs": ["junk", {"id": 1, "jobTitle": "Salesforce Developer", "companyName": "A",
                                                                    "url": "https://jobicy.com/jobs/1", "jobGeo": "Anywhere",
                                                                    "pubDate": now.strftime("%Y-%m-%d %H:%M:%S")}]}),
                         "hacker-news.firebaseio.com/v0/user/whoishiring": (200, {"submitted": [111]}),
                         "hacker-news.firebaseio.com/v0/item/111": (200, {"id": 111, "title": "Ask HN: Who is hiring?"}),
                         "hn.algolia.com": (200, {"hits": [{"objectID": "0", "parent_id": 111, "comment_text": 5},
                                                           {"objectID": "1", "parent_id": 111, "created_at_i": int(now.timestamp()),
                                                            "comment_text": "Theta | Salesforce Developer | Remote (Worldwide)"}]})})
            c = jobboard.refresh(self.conn, li=False, fetcher=fake, html_get=Fake().html, pause=0)
        finally:
            boards.SEED_BOARDS = saved
        self.assertEqual(c["by_source"].get("jobicy"), 1)
        self.assertEqual(c["by_source"].get("hn"), 1)

    # Regression: ISSUE-011 — JOBHUNTER_OFFLINE=1 did not stop the LinkedIn guest fetcher (watch.fetch)
    def test_offline_blocks_linkedin_fetch(self):
        hits = []
        orig = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: hits.append(a) or (_ for _ in ()).throw(OSError("network"))
        try:
            st, _ = watch.fetch("https://www.linkedin.com/jobs-guest/x")
        finally:
            urllib.request.urlopen = orig
        self.assertEqual((st, hits), (0, []))

    # Regression: ISSUE-013 — a non-list skills / project_order from the AI crashed the resume build with TypeError
    def test_resume_clean_config_ignores_non_lists(self):
        cfg = resume.clean_config({"headline": "Salesforce Developer", "skills": 5, "project_order": 7})
        self.assertNotIn("skills", cfg)
        self.assertNotIn("project_order", cfg)


class ApiOverflowRegression(TempData, unittest.TestCase):
    # Regression: ISSUE-014 — ids beyond SQLite's 64-bit range returned 500 on every id route
    def test_huge_ids_are_400_not_500(self):
        import http.client
        from test_backend_server import free_port
        from jh import server
        port = free_port()
        srv = server.create_server(port, web_dir=self.data / "noweb", start_watch=False)
        server.serve_in_thread(srv)
        try:
            h = "99999999999999999999"
            for method, path, body in (("GET", "/api/applications/" + h, None), ("GET", "/api/runs/" + h, None),
                                       ("POST", "/api/jobboard/" + h, {"status": "hidden"}), ("GET", "/api/events?after_id=" + h, None),
                                       ("POST", "/api/jobboard/apply", {"ids": [int(h)]})):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request(method, path, body=json.dumps(body) if body else None, headers={"Content-Type": "application/json"})
                r = c.getresponse()
                r.read()
                c.close()
                self.assertIn(r.status, (200, 400, 404), path)
        finally:
            server.shutdown_server(srv)


class MockBoardApplyLane(TempData, unittest.TestCase):
    """The mock engine's board_apply lane prefills its targets (mirrors the portal lane), so the Job board flow runs
    end to end in tests: Fill application -> board_apply run -> Ready to submit."""

    def test_board_apply_run_with_mock_engine_prefills(self):
        self.cfg["linkedin_safe"] = False; __import__("jh.config").config.save(self.cfg)   # LinkedIn behaviour under test
        os.environ["JOBHUNTER_MOCK_ENGINE"] = "1"
        os.environ["JOBHUNTER_MOCK_SPEED"] = "0.02"
        try:
            url = "https://jobs.lever.co/acme/1"
            self.conn.execute("INSERT INTO listings (norm_key, source, sources, company, title, url, apply_url, apply_kind, platform,"
                              " posted_at, status, elig, jd_text) VALUES ('acme|salesforce developer','careers','careers','Acme',"
                              "'Salesforce Developer',?,?,'portal','lever',?,'new','open','x')", (url, url, jobboard.iso(jobboard._now())))
            lid = self.conn.execute("SELECT id FROM listings").fetchone()[0]
            jobboard.queue(self.conn, [lid])
            runner.start_run("jobboard", lanes=["board_apply"])
            self.assertTrue(runner.wait(60))
            st = {x["id"]: x["state"] for x in jobboard.list_listings(self.conn)["listings"]}
            self.assertEqual(st[lid], "ready")
            self.assertEqual(self.conn.execute("SELECT status FROM listings WHERE id=?", (lid,)).fetchone()[0], "done")
        finally:
            os.environ.pop("JOBHUNTER_MOCK_SPEED", None)


if __name__ == "__main__":
    unittest.main()
