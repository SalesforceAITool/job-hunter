"""Job board: Python fetch of remote Salesforce jobs (24/48 h), eligibility, dedupe, auto-fill queue, apply hand-off."""
import json
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from test_backend_helpers import TempData
from jh import actions, boards, db, jobboard, ligate

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def ago(h):
    return (NOW - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh(jobs):
    return {"jobs": jobs}


def gh_job(i, title="Salesforce Developer", loc="Remote - India", hours=3, content="Remote role.", first=True):
    j = {"id": i, "title": title, "location": {"name": loc}, "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/%d" % i,
         "content": content, "updated_at": ago(1), "company_name": "Acme"}
    if first:
        j["first_published"] = ago(hours)
    return j


def li_card(i, title="Senior Salesforce Developer", company="Acme", loc="India", when="2 hours ago"):
    return ('<li><div class="base-card" data-entity-urn="urn:li:jobPosting:%d">'
            '<a class="base-card__full-link" href="https://in.linkedin.com/jobs/view/x-%d?position=1">'
            '<span class="sr-only">%s</span></a><h3 class="base-search-card__title">%s</h3>'
            '<h4 class="base-search-card__subtitle"><a href="#">%s</a></h4>'
            '<span class="job-search-card__location">%s</span><time class="job-search-card__listdate--new" '
            'datetime="2026-10-21">%s</time></div></li>' % (i, i, title, title, company, loc, when))


def li_jd(text, apply_url=None, applicants=12):
    a = ('<code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/externalApply/1?url=%s"--></code>'
         % apply_url) if apply_url else ""
    return ('<span class="topcard__flavor topcard__flavor--bullet">India</span>%s'
            '<span class="num-applicants__caption">%d applicants</span>'
            '<div class="show-more-less-html__markup">%s</div><button class="show-more-less-html__button">x</button>'
            % (a, applicants, text))


class Fake:
    """URL -> (status, payload) for the JSON fetcher; LinkedIn HTML via html()."""

    def __init__(self, json_map=None, html_map=None):
        self.json_map, self.html_map, self.calls = json_map or {}, html_map or {}, []

    def __call__(self, url):
        self.calls.append(url)
        for k, v in self.json_map.items():
            if k in url:
                st, payload = v
                raw = payload if isinstance(payload, str) else json.dumps(payload)
                return st, (None if isinstance(payload, str) else payload), raw
        return 404, None, ""

    def html(self, url, timeout=20):
        self.calls.append(url)
        for k, v in self.html_map.items():
            if k in url:
                return v
        return 404, ""


class JobBoardBase(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        jobboard._state.update(last_fast=0.0, last_li=0.0, last=None, loaded=True, running=False)
        self.conn.execute("DELETE FROM career_boards")
        self.conn.execute("INSERT INTO career_boards (platform, token, status, last_ok) VALUES ('greenhouse','acme','active',?)",
                          (db.now_iso(),))
        boards.SEED_BOARDS_SAVED = boards.SEED_BOARDS
        boards.SEED_BOARDS = []

    def tearDown(self):
        boards.SEED_BOARDS = boards.SEED_BOARDS_SAVED
        super().tearDown()

    def refresh(self, fake, li=False):
        return jobboard.refresh(self.conn, li=li, fetcher=fake, html_get=fake.html, now=NOW, pause=0)

    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM listings ORDER BY id")]


class FetchAndClassifyTests(JobBoardBase):
    def test_window_title_and_eligibility(self):
        fake = Fake({"boards-api.greenhouse.io/v1/boards/acme": (200, gh([
            gh_job(1, hours=3),                                                     # open (India)
            gh_job(2, title="Account Executive, Salesforce", hours=2),              # not technical -> never stored
            gh_job(3, title="Salesforce Architect", hours=60),                      # older than 48 h
            gh_job(4, title="Salesforce Admin", loc="Remote - US", hours=5,
                   content="Must be authorized to work in the United States."),     # blocked
            gh_job(5, title="Salesforce Engineer", loc="Remote - Germany", hours=5, content="Remote within Germany."),
        ]))})
        c = self.refresh(fake)
        got = {r["title"]: (r["elig"], r["apply_kind"], r["platform"]) for r in self.rows()}
        self.assertEqual(set(got), {"Salesforce Developer", "Salesforce Admin", "Salesforce Engineer"})
        self.assertEqual(got["Salesforce Developer"], ("open", "portal", "greenhouse"))
        self.assertEqual(got["Salesforce Admin"][0], "blocked")
        self.assertEqual(got["Salesforce Engineer"][0], "locked")
        self.assertEqual(c["new"], 3)
        self.assertEqual(c["old"], 1)

    def test_greenhouse_without_first_published_is_not_fresh_on_first_crawl(self):
        self.conn.execute("UPDATE career_boards SET last_ok=NULL")
        fake = Fake({"boards/acme": (200, gh([gh_job(1, first=False)]))})
        self.refresh(fake)
        self.assertEqual(self.rows(), [])            # updated_at is an edit date, not a posting date
        fake2 = Fake({"boards/acme": (200, gh([gh_job(1, first=False), gh_job(2, title="Salesforce QA Lead", first=False)]))})
        self.conn.execute("UPDATE career_boards SET last_ok=?", (db.now_iso(),))
        self.refresh(fake2)
        self.assertEqual(len(self.rows()), 2)        # board seen before -> new ids are new since our last look
        self.assertTrue(all(r["posted_at"] == ago(0) for r in self.rows()))

    def test_linkedin_regions_jd_and_easy_apply_vs_external(self):
        fake = Fake(html_map={
            "geoId=102713980": (200, li_card(4000011, loc="Bengaluru, India") + li_card(4000012, title="Salesforce CPQ Developer",
                                                                                company="Beta", loc="India")),
            "jobPosting/4000011": (200, li_jd("Remote role for India. Apex, LWC.")),
            "jobPosting/4000012": (200, li_jd("Remote, India.", apply_url="https%3A%2F%2Fjobs.lever.co%2Fbeta%2Fabc")),
        })
        self.cfg["jobboard"]["li_regions"] = ["in"]
        jobboard.refresh(self.conn, cfg=self.cfg, li=True, fetcher=Fake(), html_get=fake.html, now=NOW, pause=0)
        got = {r["company"]: r for r in self.rows()}
        self.assertEqual(got["Acme"]["apply_kind"], "easy_apply")
        self.assertEqual(got["Acme"]["elig"], "open")
        self.assertEqual(got["Acme"]["applicants"], 12)
        self.assertEqual(got["Beta"]["apply_kind"], "portal")
        self.assertEqual(got["Beta"]["platform"], "lever")
        self.assertEqual(got["Acme"]["posted_at"], ago(2))

    def test_linkedin_429_pauses_and_is_not_an_error_storm(self):
        fake = Fake(html_map={"geoId=": (429, "")})
        self.cfg["jobboard"]["li_regions"] = ["world", "in", "us"]
        c = jobboard.refresh(self.conn, cfg=self.cfg, li=True, fetcher=Fake(), html_get=fake.html, now=NOW, pause=0)
        self.assertEqual(len([u for u in fake.calls if "geoId" in u]), 1)    # stops at the first 429
        self.assertTrue(any("slow down" in e for e in c["errors"]))

    def test_dedupe_linkedin_and_greenhouse_same_job(self):
        url = "https://job-boards.greenhouse.io/acme/jobs/1"
        fake = Fake({"boards/acme": (200, gh([gh_job(1)]))},
                    html_map={"geoId=102713980": (200, li_card(4000021, title="Salesforce Developer", company="Acme Inc")),
                              "jobPosting/4000021": (200, li_jd("Remote India", apply_url=url.replace(":", "%3A").replace("/", "%2F")))})
        self.cfg["jobboard"]["li_regions"] = ["in"]
        jobboard.refresh(self.conn, cfg=self.cfg, li=True, fetcher=fake, html_get=fake.html, now=NOW, pause=0)
        self.assertEqual(len(self.rows()), 1)
        self.assertIn("linkedin", self.rows()[0]["sources"])
        self.assertEqual(self.rows()[0]["apply_kind"], "portal")

    def test_aggregator_company_is_not_merged_by_title(self):
        a = {"source": "careers", "ext_id": "lv:jobgether:1", "title": "Salesforce Developer", "company": "Jobgether",
             "url": "https://jobs.lever.co/jobgether/1", "apply_url": "https://jobs.lever.co/jobgether/1", "location": "Remote",
             "remote": True, "open": True, "text": "", "pub": NOW}
        b = dict(a, ext_id="lv:jobgether:2", url="https://jobs.lever.co/jobgether/2", apply_url="https://jobs.lever.co/jobgether/2")
        jobboard.upsert(self.conn, a, NOW)
        jobboard.upsert(self.conn, b, NOW)
        self.assertEqual(len(self.rows()), 2)

    def test_refresh_never_touches_jobs(self):
        actions.save_job(self.conn, None, "posts", "posts", "Acme", "Salesforce Developer", url="https://x.example/1")
        before = [tuple(r) for r in self.conn.execute("SELECT * FROM jobs")]
        self.refresh(Fake({"boards/acme": (200, gh([gh_job(1)]))}))
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM jobs")], before)
        self.assertIsNotNone(self.rows()[0]["job_id"])     # but the listing points at the known job

    def test_over_200_cards_all_stored(self):
        jobs = [gh_job(i, title="Salesforce Developer %d" % i) for i in range(1, 260)]
        self.refresh(Fake({"boards/acme": (200, gh(jobs))}))
        self.assertEqual(len(self.rows()), 259)

    def test_board_404_counts_toward_dead_429_does_not(self):
        self.refresh(Fake({"boards/acme": (429, "")}))
        self.assertEqual(self.conn.execute("SELECT misses FROM career_boards").fetchone()[0], 0)
        self.refresh(Fake({}))
        self.assertEqual(self.conn.execute("SELECT misses FROM career_boards").fetchone()[0], 1)

    def test_refresh_single_flight(self):
        gate = threading.Event()
        orig = jobboard.refresh
        jobboard.refresh = lambda conn, **k: gate.wait(2) or {}
        try:
            self.assertTrue(jobboard.refresh_in_thread())
            self.assertFalse(jobboard.refresh_in_thread())
            gate.set()
            for _ in range(50):
                if not jobboard.is_refreshing():
                    break
                time.sleep(0.05)
            self.assertFalse(jobboard.is_refreshing())
        finally:
            jobboard.refresh = orig


class AdapterTests(unittest.TestCase):
    def test_platform_of(self):
        P = jobboard.platform_of
        self.assertEqual(P("https://acme.wd5.myworkdayjobs.com/en-US/x/job/1"), ("workday", "login_portal"))
        self.assertEqual(P("https://apply.workable.com/acme/j/ABC/"), ("workable", "portal"))
        self.assertEqual(P("https://himalayas.app/companies/a/jobs/b"), ("himalayas", "board"))
        self.assertEqual(P("https://careers.acme.com/jobs/1")[1], "company_site")
        self.assertEqual(P("https://evilgreenhouse.io/x"), ("evilgreenhouse.io", "company_site"))

    def test_workable_and_recruitee(self):
        wk = boards.wk_cards({"name": "Devsinc", "jobs": [{"title": "Salesforce Developer", "shortcode": "AB12",
                                                            "telecommuting": True, "published_on": "2026-10-21",
                                                            "locations": [{"country": "India", "countryCode": "IN"}],
                                                            "application_url": "https://apply.workable.com/j/AB12/apply"}]}, "devsinc")
        self.assertEqual((wk[0]["company"], wk[0]["remote"], wk[0]["open"]), ("Devsinc", True, True))
        self.assertEqual(wk[0]["pub"].date().isoformat(), "2026-10-21")
        rc = boards.rc_cards({"offers": [{"id": 9, "title": "Salesforce Consultant", "remote": True, "status": "published",
                                          "country_code": "IN", "published_at": "2026-10-21 08:00:00 UTC",
                                          "careers_url": "https://acme.recruitee.com/o/sf", "company_name": "Acme"}]}, "acme")
        self.assertEqual(rc[0]["pub"], datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc))
        self.assertTrue(rc[0]["open"])
        self.assertEqual(boards.board_of("https://apply.workable.com/devsinc-17/j/6566FF4D2E/"), ("workable", "devsinc-17"))
        self.assertEqual(boards.board_of("https://acme.recruitee.com/o/sf"), ("recruitee", "acme"))

    def test_wwr_rss_and_arbeitnow(self):
        xml = ("<rss><channel><item><title><![CDATA[Acme: Senior Salesforce Developer]]></title>"
               "<region>Anywhere in the World</region><link>https://weworkremotely.com/remote-jobs/acme-sf</link>"
               "<pubDate>Tue, 21 Oct 2026 09:00:00 +0000</pubDate><description>&lt;p&gt;Apex&lt;/p&gt;</description></item>"
               "</channel></rss>")
        c = jobboard.wwr_cards(xml)[0]
        self.assertEqual((c["company"], c["title"], c["open"]), ("Acme", "Senior Salesforce Developer", True))
        a = jobboard.arbeitnow_cards({"data": [{"slug": "x", "company_name": "B", "title": "Salesforce Dev", "remote": True,
                                                "location": "Berlin", "created_at": 1792573200, "url": "https://www.arbeitnow.com/jobs/x"}]})
        self.assertTrue(a[0]["remote"])
        self.assertFalse(a[0]["open"])


class QueueAndApplyTests(JobBoardBase):
    def seed(self, n=3, kind="portal", elig="open", hours=2, applicants=None):
        ids = []
        for i in range(n):
            c = {"source": "careers", "ext_id": "gh:acme:%s%d" % (kind, i), "title": "Salesforce Developer %s %d" % (kind, i),
                 "company": "Acme", "url": "https://job-boards.greenhouse.io/acme/jobs/%d" % i,
                 "apply_url": "https://job-boards.greenhouse.io/acme/jobs/%d" % i if kind == "portal" else
                 "https://acme.wd1.myworkdayjobs.com/x/%d" % i,
                 "location": "Remote - India" if elig == "open" else "Remote", "remote": True,
                 "open": True if elig == "open" else None, "text": "Remote. Apex.", "pub": NOW - timedelta(hours=hours)}
            lid, _ = jobboard.upsert(self.conn, c, NOW)
            if applicants is not None:
                self.conn.execute("UPDATE listings SET applicants=? WHERE id=?", (applicants, lid))
            ids.append(lid)
        return ids

    def test_auto_pick_cap_kinds_and_budget(self):
        self.seed(4)
        self.seed(2, kind="login")                        # Workday: never auto (needs his account)
        self.cfg["jobboard"].update(per_run=3, daily_cap=4)
        got = jobboard.auto_pick(self.conn, self.cfg, now=NOW)
        self.assertEqual(len(got), 3)
        self.assertEqual(jobboard.auto_pick(self.conn, self.cfg, now=NOW), [])     # queue not empty yet
        self.conn.execute("UPDATE listings SET status='done' WHERE status='queued'")
        self.assertEqual(len(jobboard.auto_pick(self.conn, self.cfg, now=NOW)), 1)  # daily cap 4
        kinds = {r["apply_kind"] for r in self.rows() if r["queued_by"] == "auto"}
        self.assertEqual(kinds, {"portal"})

    def test_budget_stops_auto(self):
        self.seed(2)
        from jh import events
        events.emit(self.conn, 1, "board_apply", "lane_finished", "done", "success", {"cost_usd": 7.0})
        self.cfg["jobboard"]["daily_usd"] = 6
        self.assertEqual(jobboard.auto_pick(self.conn, self.cfg, now=NOW), [])

    def test_claim_creates_job_and_finish_lifecycle(self):
        ids = self.seed(2)
        jobboard.queue(self.conn, ids)
        ea, forms, _ = jobboard.claim(self.conn, 7, limit=5, html_get=Fake().html)
        self.assertEqual((len(ea), len(forms)), (0, 2))
        self.assertTrue(all(isinstance(t["job_id"], int) for t in forms))
        self.assertEqual({r["status"] for r in self.rows()}, {"working"})
        # the AI filled the first one; the second was not reached
        self.conn.execute("INSERT INTO portal_apps (job_id, ats, status) VALUES (?, 'greenhouse', 'prefilled')",
                          (forms[0]["job_id"],))
        jobboard.finish(self.conn, 7, ids)
        st = {r["id"]: r["status"] for r in self.rows()}
        self.assertEqual((st[ids[0]], st[ids[1]]), ("done", "queued"))
        jobboard.claim(self.conn, 8, limit=5, html_get=Fake().html)
        jobboard.finish(self.conn, 8, ids)
        self.assertEqual({r["id"]: r["status"] for r in self.rows()}[ids[1]], "failed")   # 2 tries -> needs you

    def test_states_for_the_page(self):
        ids = self.seed(3)
        jobboard.queue(self.conn, [ids[0]])
        jobboard.set_status(self.conn, ids[1], "hidden")
        d = jobboard.list_listings(self.conn, hours=24, now=NOW)
        st = {x["id"]: x["state"] for x in d["listings"]}
        self.assertEqual((st[ids[0]], st[ids[1]], st[ids[2]]), ("queued", "hidden", "new"))
        self.assertIn("status", d)


class LinkedInGateTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        ligate._st.update(last=0.0, until=None, strikes=0)

    def test_strike_persists_and_blocks(self):
        calls = []
        st, _ = ligate.get("https://www.linkedin.com/x", fetch=lambda u, t: (calls.append(u) or (429, "")))
        self.assertEqual(st, 429)
        self.assertTrue(ligate.cooling())
        ligate._st.update(until=None)                       # "restart": state comes back from app_meta
        self.assertTrue(ligate.cooling())
        st, _ = ligate.get("https://www.linkedin.com/y", fetch=lambda u, t: (calls.append(u) or (200, "")))
        self.assertEqual((st, len(calls)), (429, 1))         # no request while paused

    def tearDown(self):
        ligate._st.update(last=0.0, until=0.0, strikes=0)
        super().tearDown()


class ReviewRegressionTests(QueueAndApplyTests):
    """Pre-landing review fixes: refresh storms, board_apply restart loops, reprocessing, bad cards, false merges."""

    def test_due_is_not_forever_true_when_linkedin_cannot_run(self):
        t = time.time()
        jobboard._state.update(last_fast=t, last_li=0.0)
        cfg = {"jobboard": dict(self.cfg["jobboard"], on=True, li_regions=[])}
        import os
        os.environ.pop("JOBHUNTER_OFFLINE", None)
        try:
            self.assertFalse(jobboard.due(self.conn, cfg, now_ts=t + 60))        # no LinkedIn regions picked
            cfg["jobboard"]["li_regions"] = ["in"]
            ligate._st.update(until=t + 900, strikes=1)
            self.assertFalse(jobboard.due(self.conn, cfg, now_ts=t + 60))        # LinkedIn paused
            ligate._st.update(until=0.0, strikes=0)
            self.assertTrue(jobboard.due(self.conn, cfg, now_ts=t + 60))         # LinkedIn due for real
            jobboard._state.update(last_li=t)
            self.assertTrue(jobboard.due(self.conn, cfg, now_ts=t + 31 * 60))    # fast refresh due
        finally:
            os.environ["JOBHUNTER_OFFLINE"] = "1"
            ligate._st.update(last=0.0, until=0.0, strikes=0)

    def test_failed_refresh_counts_as_an_attempt(self):
        orig = jobboard.refresh

        def boom(conn, **k):
            raise RuntimeError("db locked")
        jobboard.refresh = boom
        try:
            jobboard._state.update(last_fast=0.0)
            self.assertTrue(jobboard.refresh_in_thread())
            for _ in range(100):
                if not jobboard.is_refreshing():
                    break
                time.sleep(0.02)
            self.assertGreater(jobboard._state["last_fast"], 0)
        finally:
            jobboard.refresh = orig

    def test_bad_cards_are_skipped_not_fatal(self):
        good = {"source": "careers", "ext_id": "gh:acme:ok", "title": "Salesforce Developer", "company": "Acme",
                "url": "https://job-boards.greenhouse.io/acme/jobs/9", "apply_url": "https://job-boards.greenhouse.io/acme/jobs/9",
                "location": "Remote - India", "remote": True, "open": True, "text": "Remote", "pub": NOW - timedelta(hours=1)}
        naive = dict(good, ext_id="gh:acme:naive", title="Salesforce Admin", pub=datetime(2026, 10, 21, 10, 0),
                     url="https://job-boards.greenhouse.io/acme/jobs/8", apply_url="https://job-boards.greenhouse.io/acme/jobs/8")
        counts = {"read": 0, "salesforce": 0, "old": 0, "new": 0, "updated": 0, "by_source": {}}
        jobboard.ingest(self.conn, [{"title": "Salesforce QA", "company": "X"},              # no ext_id
                                    dict(good, ext_id="gh:acme:str", pub="yesterday"),       # odd date type
                                    "not a card", naive, good], NOW, counts)
        self.assertEqual(sorted(r["title"] for r in self.rows()), ["Salesforce Admin", "Salesforce Developer"])

    def test_generic_company_careers_url_does_not_merge_roles(self):
        base = {"source": "himalayas", "company": "Acme", "url": "https://himalayas.app/x", "apply_url": "https://acme.com/careers",
                "location": "Remote", "remote": True, "open": True, "text": "Remote", "pub": NOW}
        jobboard.upsert(self.conn, dict(base, ext_id="him:1", title="Salesforce Developer"), NOW)
        jobboard.upsert(self.conn, dict(base, ext_id="him:2", title="Salesforce Architect"), NOW)
        self.assertEqual(len(self.rows()), 2)

    def test_ai_verdict_after_claim_is_handled_not_requeued(self):
        ids = self.seed(2)
        jobboard.queue(self.conn, ids)
        _, forms, _ = jobboard.claim(self.conn, 7, limit=5, html_get=Fake().html)
        since = db.now_iso()
        later = "9" + since[1:]                    # the AI set "qualified + Easy Apply on LinkedIn" after the claim
        self.conn.execute("UPDATE jobs SET updated_at=? WHERE id=?", (later, forms[0]["job_id"]))
        jobboard.finish(self.conn, 7, ids, since=since)
        st = {r["id"]: r["status"] for r in self.rows()}
        self.assertEqual((st[ids[0]], st[ids[1]]), ("done", "queued"))

    def test_stop_puts_rows_back_on_the_list_and_auto_never_repicks(self):
        ids = self.seed(3)
        jobboard.queue(self.conn, ids[:2], by="auto")
        jobboard.claim(self.conn, 7, limit=1, html_get=Fake().html)
        jobboard.finish(self.conn, 7, ids[:2], stopped=True)
        jobboard.release_queue(self.conn, "new", "Stopped before it was filled")
        self.assertEqual(jobboard.pending(self.conn), 0)                   # nothing for the watcher to restart
        self.assertEqual({r["status"] for r in self.rows()}, {"new"})
        got = jobboard.auto_pick(self.conn, self.cfg, now=NOW)
        self.assertEqual(got, [ids[2]])                                    # the stopped ones are not re-picked

    def test_set_status_never_overrides_a_claim(self):
        ids = self.seed(1)
        jobboard.queue(self.conn, ids)
        jobboard.claim(self.conn, 7, limit=5, html_get=Fake().html)
        self.assertIn("error", jobboard.set_status(self.conn, ids[0], "hidden"))
        self.assertEqual(self.rows()[0]["status"], "working")

    def test_try_again_allowed_for_forms_that_need_him(self):
        ids = self.seed(1)
        jobboard.queue(self.conn, ids)
        _, forms, _ = jobboard.claim(self.conn, 7, limit=5, html_get=Fake().html)
        self.conn.execute("INSERT INTO portal_apps (job_id, ats, status, ready) VALUES (?, 'greenhouse', 'needs_input', 0)",
                          (forms[0]["job_id"],))
        jobboard.finish(self.conn, 7, ids)
        self.assertEqual(jobboard.queue(self.conn, ids, by="auto")["queued"], [])
        self.assertEqual(jobboard.queue(self.conn, ids, by="you")["queued"], ids)
        self.conn.execute("UPDATE portal_apps SET ready=1, status='prefilled'")
        self.conn.execute("UPDATE listings SET status='done'")
        self.assertEqual(jobboard.queue(self.conn, ids, by="you")["queued"], [])

    def test_auto_claim_never_reopens_a_skipped_job(self):
        ids = self.seed(1)
        jobboard.queue(self.conn, ids, by="auto")
        r = self.conn.execute("SELECT * FROM listings WHERE id=?", (ids[0],)).fetchone()
        actions.save_job(self.conn, None, "himalayas", "careers", r["company"], r["title"], decision="skipped",
                         skip_reason="US only")
        ea, forms, _ = jobboard.claim(self.conn, 7, limit=5, html_get=Fake().html)
        self.assertEqual(ea + forms, [])
        self.assertEqual(self.rows()[0]["status"], "new")


class BoardRunLoopTests(TempData, unittest.TestCase):
    """A board_apply run that cannot work its queue must not leave it queued (the watcher would restart forever)."""

    def setUp(self):
        super().setUp()
        from jh import runner
        self.runner = runner
        self._orig = runner._load_engine
        c = {"source": "careers", "ext_id": "gh:acme:1", "title": "Salesforce Developer", "company": "Acme",
             "url": "https://job-boards.greenhouse.io/acme/jobs/1", "apply_url": "https://job-boards.greenhouse.io/acme/jobs/1",
             "location": "Remote - India", "remote": True, "open": True, "text": "Remote", "pub": jobboard._now()}
        self.lid, _ = jobboard.upsert(self.conn, c, jobboard._now())
        jobboard.queue(self.conn, [self.lid])

    def tearDown(self):
        self.runner.stop_run()
        self.runner.wait(10)
        self.runner._load_engine = self._orig
        super().tearDown()

    def test_engine_missing_releases_the_queue(self):
        def missing():
            raise ImportError("no engine")
        self.runner._load_engine = missing
        self.runner.start_run("jobboard", cfg=self.cfg, lanes=["board_apply"])
        self.assertTrue(self.runner.wait(30))
        self.assertEqual(jobboard.pending(self.conn), 0)
        r = self.conn.execute("SELECT status, note FROM listings WHERE id=?", (self.lid,)).fetchone()
        self.assertEqual(r["status"], "failed")
        self.assertIn("AI engine not available", r["note"])

    def test_stop_during_lane_does_not_requeue(self):
        import types
        started = threading.Event()

        def run_lane(run_id, lane, cfg, emit, stop):
            started.set()
            stop.wait(10)
            return {"status": "stopped"}
        self.runner._load_engine = lambda: types.SimpleNamespace(run_lane=run_lane)
        self.runner.start_run("jobboard", cfg=self.cfg, lanes=["board_apply"])
        self.assertTrue(started.wait(10))
        self.runner.stop_run()
        self.assertTrue(self.runner.wait(30))
        self.assertEqual(jobboard.pending(self.conn), 0)
        self.assertEqual(self.conn.execute("SELECT status FROM listings WHERE id=?", (self.lid,)).fetchone()[0], "new")

    def test_watcher_does_not_restart_on_an_unchanged_queue(self):
        from jh import config, watch
        self.cfg["autopilot"]["on"] = False          # only the Job board dispatch is under test here
        config.save(self.cfg)
        calls = []
        w = watch.Watcher(None, fetcher=lambda u, t=20: (200, ""), start_run=lambda trig, **k: calls.append(trig) or 1,
                          sleep=lambda s: None, jobboard=True)
        w.tick()
        w.tick()
        self.assertEqual(calls, ["jobboard"])          # the stub run never drained the queue: no restart loop


if __name__ == "__main__":
    unittest.main()


class LinkedInPacingTests(JobBoardBase):
    def test_linkedin_deferred_while_a_run_uses_it_and_apply_links_feed_boards(self):
        self.cfg["linkedin_safe"] = False; __import__("jh.config").config.save(self.cfg)   # LinkedIn behaviour under test
        from jh import runner
        orig = runner.is_running
        runner.is_running = lambda: True
        try:
            c = jobboard.refresh(self.conn, cfg=self.cfg, fetcher=Fake(), now=NOW, pause=0)
        finally:
            runner.is_running = orig
        self.assertTrue(c.get("li_deferred"))
        self.assertFalse(c["li"])
        # an ATS apply link on a LinkedIn job adds that company's board (flywheel)
        fake = Fake(html_map={"geoId=102713980": (200, li_card(4000031, company="Zeta")),
                              "jobPosting/4000031": (200, li_jd("Remote India", apply_url="https%3A%2F%2Fjobs.lever.co%2Fzetaco%2Fx1"))})
        self.cfg["jobboard"]["li_regions"] = ["in"]
        jobboard.refresh(self.conn, cfg=self.cfg, li=True, fetcher=Fake(), html_get=fake.html, now=NOW, pause=0)
        self.assertTrue(self.conn.execute("SELECT 1 FROM career_boards WHERE platform='lever' AND token='zetaco'").fetchone())


class GuestRemoteRuleTests(unittest.TestCase):
    def test_city_level_guest_job_needs_remote_in_jd(self):
        from jh import screen
        self.assertEqual(screen.guest_remote_check("Lexington, KY", "Join our team in our Lexington office. Apex.")[0], False)
        self.assertTrue(screen.guest_remote_check("Bengaluru, Karnataka, India", "This is a fully remote role.")[0])
        self.assertTrue(screen.guest_remote_check("United States", "Apex developer.")[0])        # country level: AI checks
        self.assertTrue(screen.guest_remote_check("Pune, Maharashtra, India", "")[0])            # JD not read: AI checks
        self.assertTrue(screen.guest_remote_check("India (Remote)", "Apex.")[0])
