"""Token savers: Python skip rules, LinkedIn pre-screen, remote boards (Jobicy / HN / career pages), screen_jobs."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_backend_helpers import TempData
from jh import actions, boards, engine, liprefetch, runner, screen, seen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def guest_card(i, title="Senior Salesforce Developer", company="Acme", loc="United States"):
    return ('<li><div class="base-card" data-entity-urn="urn:li:jobPosting:%d">'
            '<h3 class="base-search-card__title">%s</h3><h4 class="base-search-card__subtitle">%s</h4>'
            '<span class="job-search-card__location">%s</span><time datetime="2026-10-20">1 hour ago</time></div></li>'
            % (i, title, company, loc))


def jd_page(text, apply_url=None, applicants=None, closed=False, loc="United States"):
    h = '<section class="top-card-layout"><span class="topcard__flavor topcard__flavor--bullet">%s</span>' % loc
    if apply_url:
        h += '<code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/externalApply/1?url=%s&amp;urlHash=x"--></code>' % apply_url
    if applicants is not None:
        h += '<figcaption class="num-applicants__caption">%d applicants</figcaption>' % applicants
    if closed:
        h += '<figure>No longer accepting applications</figure>'
    h += ('</section><div class="description__text description__text--rich"><section class="show-more-less-html">'
          '<div class="show-more-less-html__markup relative">%s</div>'
          '<button class="show-more-less-html__button">Show more</button></section></div>'
          '<li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Employment type</h3>'
          '<span class="description__job-criteria-text">Contract</span></li>' % text)
    return h


class ScreenTests(unittest.TestCase):
    def test_rules(self):
        cases = [
            (("Salesforce Developer", "Must be authorized to work in the United States.", "United States"), False, "requires US work authorization"),
            (("Salesforce Developer", "W2 only. No C2C.", "Texas, United States"), False, "requires US work authorization"),
            (("Salesforce Developer", "We cannot sponsor visas.", "Austin, TX"), False, "requires US work authorization"),
            (("Salesforce Developer", "We cannot sponsor visas. Work from anywhere.", "Worldwide"), True, ""),
            (("Salesforce Developer", "Must be authorized to work in the US", "Bengaluru, India"), True, ""),
            (("Salesforce Developer", "This is a hybrid role: 3 days a week in the office.", "Pune, India"), False, "onsite/hybrid"),
            (("Salesforce Developer", "Apex. Hybrid cloud integrations. Fully remote.", "United States"), True, ""),
            (("Salesforce Developer", "Fluent German is required.", ""), False, "requires German"),
            (("Salesforce Developer", "Nice team.", "Poland"), False, "remote only for Poland"),
            (("Salesforce Developer", "Remote from anywhere; contractors welcome.", "Poland"), True, ""),
            (("Salesforce Developer", "Remote within Canada only.", ""), False, "remote only for Canada"),
            (("Salesforce Account Executive", "", ""), False, "not a fit: sales role"),
            (("Python Developer", "", ""), False, "not a fit: not a salesforce role"),
            (("Salesforce Developer", "Active Secret clearance required.", "United States"), False, "requires US work authorization"),
            (("Salesforce Developer", "", "Remote (Hybrid)"), False, "onsite/hybrid"),
            (("Salesforce CPQ Developer", "Remote, US hours. Apex LWC CPQ.", "United States"), True, ""),
        ]
        for args, ok, why in cases:
            got_ok, got_why = screen.screen(*args)
            self.assertEqual(got_ok, ok, (args, got_why))
            self.assertTrue(got_why.startswith(why), (args, got_why))


class LinkedInPrescreenTests(TempData, unittest.TestCase):
    def fetcher(self, search_pages, jds):
        calls = []

        def f(url):
            calls.append(url)
            if "seeMoreJobPostings" in url:
                for key, body in search_pages.items():
                    if "geoId=%s" % liprefetch.GEO[key] in url and "start=0" in url:
                        return 200, body
                return 200, ""
            jid = url.rsplit("/", 1)[-1]
            return (200, jds[jid]) if jid in jds else (404, "")
        f.calls = calls
        return f

    def test_collect_screens_and_saves(self):
        cards = "".join([guest_card(4100000101, company="GoodCo"), guest_card(4100000102, company="OnsiteCo"),
                         guest_card(4100000103, company="UsOnly"), guest_card(4100000104, company="ExtCo"),
                         guest_card(4100000105, title="Account Executive", company="SalesCo"),
                         guest_card(4100000106, company="SeenCo"), guest_card(4100000107, company="ClosedCo")])
        jds = {"4100000101": jd_page("Remote. Apex, LWC, Agentforce.", applicants=12),
               "4100000102": jd_page("This is a hybrid role, 3 days a week in the office."),
               "4100000103": jd_page("You must be authorized to work in the United States."),
               "4100000104": jd_page("Remote role.", apply_url="https%3A%2F%2Fjobs.lever.co%2Fextco%2Fabc"),
               "4100000107": jd_page("Remote.", closed=True)}
        actions.save_job(self.conn, None, "posts", "posts", "SeenCo", "Senior Salesforce Developer",
                         url="https://www.linkedin.com/jobs/view/4100000106/", decision="skipped", skip_reason="onsite/hybrid")
        f = self.fetcher({"us": cards}, jds)
        res = liprefetch.collect(self.conn, 1, "easy_apply", regions=["us"], fetcher=f)
        c = res["counts"]
        self.assertEqual([t["company"] for t in res["targets"]], ["GoodCo"])
        t = res["targets"][0]
        self.assertTrue(t["jd"] and isinstance(t["job_id"], int))
        self.assertEqual((c["not_salesforce"], c["seen"], c["closed"]), (1, 1, 1))
        self.assertEqual(c["screened_out"], 3)              # onsite, US-only, external apply
        rows = {r["company"]: r for r in self.conn.execute("SELECT * FROM jobs")}
        self.assertEqual(rows["GoodCo"]["status"], "qualified")
        self.assertIn("Agentforce", rows["GoodCo"]["jd_text"])
        self.assertEqual(rows["GoodCo"]["source"], "linkedin_us")
        self.assertEqual(rows["OnsiteCo"]["skip_reason"], "onsite/hybrid")
        self.assertTrue(rows["UsOnly"]["skip_reason"].startswith("requires US work authorization"))
        self.assertEqual((rows["ExtCo"]["status"], rows["ExtCo"]["notes"]), ("qualified", "external apply (no Easy Apply)"))
        self.assertEqual(rows["ExtCo"]["apply_url"], "https://jobs.lever.co/extco/abc")
        self.assertEqual(rows["ClosedCo"]["skip_reason"], "posting closed")
        self.assertNotIn("SalesCo", rows)                   # not saved: no AI and no DB noise for non-SF titles
        self.assertFalse(any("/4100000106" in u for u in f.calls))  # the seen job's JD is never fetched
        self.assertIn("f_AL=true", f.calls[0])
        # second run: everything is known -> nothing for the AI
        res2 = liprefetch.collect(self.conn, 2, "easy_apply", regions=["us"], fetcher=self.fetcher({"us": cards}, jds))
        self.assertEqual([(t["company"], t.get("retry")) for t in res2["targets"]], [("GoodCo", True)])  # one retry
        self.assertEqual(res2["counts"]["saved"], 0)
        res3 = liprefetch.collect(self.conn, 3, "easy_apply", regions=["us"], fetcher=self.fetcher({"us": cards}, jds))
        self.assertEqual(res3["targets"], [])

    def test_linkedin_jobs_keeps_external_and_ats_link(self):
        f = self.fetcher({"world": guest_card(4100000201, company="Atsy", loc="Worldwide")},
                         {"4100000201": jd_page("Remote worldwide.", apply_url="https%3A%2F%2Fboards.greenhouse.io%2Fatsy%2Fjobs%2F9", loc="Worldwide")})
        res = liprefetch.collect(self.conn, 1, "linkedin_jobs", regions=["world"], fetcher=f)
        self.assertEqual(len(res["targets"]), 1)
        self.assertNotIn("f_AL", f.calls[0])
        row = self.conn.execute("SELECT * FROM jobs WHERE company='Atsy'").fetchone()
        self.assertEqual((row["source"], row["apply_url"]), ("linkedin_world", "https://boards.greenhouse.io/atsy/jobs/9"))

    def test_unreachable_returns_none_and_offline(self):
        self.assertIsNone(liprefetch.collect(self.conn, 1, "easy_apply", regions=["us"], fetcher=lambda u: (999, "blocked")))
        self.assertIsNone(liprefetch.collect(self.conn, 1, "easy_apply"))       # JOBHUNTER_OFFLINE in tests

    def test_watch_cards_are_screened_without_search(self):
        calls = []

        def f(url):
            calls.append(url)
            return 200, jd_page("Remote. Apex.")
        extra = [{"ext_id": "4100000301", "title": "Salesforce Developer", "company": "WatchCo", "location": "India",
                  "url": "https://www.linkedin.com/jobs/view/4100000301/", "region": "india"}]
        res = liprefetch.collect(self.conn, 1, "easy_apply", fetcher=f, extra_cards=extra)
        self.assertEqual(len(res["targets"]), 1)
        self.assertTrue(all("seeMoreJobPostings" not in u for u in calls))

    def test_parse_jd(self):
        d = liprefetch.parse_jd(jd_page("<p>Hello <b>Apex</b></p><ul><li>LWC</li></ul>", applicants=7, loc="Pune, India"))
        self.assertIn("Apex", d["text"]); self.assertIn("LWC", d["text"])
        self.assertEqual((d["applicants"], d["location"], d["criteria"].get("Employment type")), (7, "Pune, India", "Contract"))
        self.assertIsNone(d["apply_url"])


class RunnerPrescreenTests(TempData, unittest.TestCase):
    class Eng:
        def __init__(self):
            self.calls = []

        def run_lane(self, run_id, lane, cfg, emit, stop):
            self.calls.append((lane, cfg.get("_targets")))
            return {"status": "done", "counts": {}}

    def _run(self, collect_result):
        import threading
        orig = liprefetch.collect
        liprefetch.collect = lambda *a, **k: collect_result
        try:
            eng, results = self.Eng(), {}
            em = runner._Emitter(str(self.data / "jobhunter.db"), 1) if hasattr(runner, "_Emitter") else None
            runner._run_li_lane(eng, "easy_apply", 1, dict(self.cfg), em, threading.Event(), None, results)
            return eng, results
        finally:
            liprefetch.collect = orig

    def test_no_targets_means_no_ai(self):
        from jh import config
        self.conn.execute("INSERT INTO runs (id, started_at, status) VALUES (1, '2026-10-20', 'running')")
        eng, results = self._run({"targets": [], "counts": dict.fromkeys(["cards", "not_salesforce", "seen", "screened_out", "closed", "saved", "retry"], 0), "reasons": {}})
        self.assertEqual(eng.calls, [])
        self.assertEqual(results["easy_apply"]["status"], "done")

    def test_targets_go_to_ai_and_none_falls_back(self):
        self.conn.execute("INSERT INTO runs (id, started_at, status) VALUES (1, '2026-10-20', 'running')")
        tg = [{"job_id": 5, "company": "A", "role": "Salesforce Developer", "url": "https://www.linkedin.com/jobs/view/5/", "jd": True}]
        eng, _ = self._run({"targets": tg, "counts": dict.fromkeys(["cards", "not_salesforce", "seen", "screened_out", "closed", "saved", "retry"], 0), "reasons": {}})
        self.assertEqual(eng.calls, [("easy_apply", tg)])
        eng, _ = self._run(None)
        self.assertEqual(len(eng.calls), 1)
        self.assertFalse(eng.calls[0][1])                  # old behaviour: AI searches itself


class PromptAndModelTests(TempData, unittest.TestCase):
    def test_prescreened_block(self):
        tg = [{"job_id": 7, "company": "Acme", "role": "Salesforce Developer", "location": "US",
               "url": "https://www.linkedin.com/jobs/view/123/", "jd": True},
              {"job_id": 8, "company": "Evil", "role": "x", "url": "https://evil.example/jobs/view/1/", "jd": True}]
        p = engine.compose_system_prompt("easy_apply", engine.PROMPTS_DIR / "easy_apply.md", 1, targets=tg)
        self.assertIn("PRE-SCREENED LIST", p)
        self.assertIn("7 — Salesforce Developer — Acme", p)
        self.assertNotIn("evil.example", p)
        p2 = engine.compose_system_prompt("linkedin_jobs", engine.PROMPTS_DIR / "linkedin_jobs.md", 1, targets=tg)
        self.assertIn("Meet the hiring team", p2)
        self.assertIn("PRE-SCREENED LIST", p2)

    def test_model_flag(self):
        argv, _ = engine.build_command("posts", {"engine": {"mock": False, "claude_path": sys.executable}},
                                       engine.PROMPTS_DIR / "posts.md", self.data)
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        argv, _ = engine.build_command("easy_apply", {"engine": {"claude_path": sys.executable, "model": "sonnet",
                                                                  "lane_models": {"easy_apply": "opus"}}},
                                       engine.PROMPTS_DIR / "easy_apply.md", self.data)
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        argv, _ = engine.build_command("posts", {"engine": {"claude_path": sys.executable, "model": "default"}},
                                       engine.PROMPTS_DIR / "posts.md", self.data)
        self.assertNotIn("--model", argv)
        argv, _ = engine.build_command("posts", {"engine": {"claude_path": sys.executable,
                                                             "claude_args": ["--chrome", "--model", "haiku"]}},
                                       engine.PROMPTS_DIR / "posts.md", self.data)
        self.assertEqual(argv.count("--model"), 1)

    def test_config_model_cleaning(self):
        from jh import config
        c = config.load()
        self.assertEqual(c["engine"]["model"], "sonnet")
        out = config.apply_update(c, {"engine": {"model": "bad model;rm", "lane_models": {"posts": "opus", "x": "$$"}}})
        self.assertEqual(out["engine"]["model"], "sonnet")
        self.assertEqual(out["engine"]["lane_models"], {"posts": "opus"})


def hn_fetcher(comments, hiring_title="Ask HN: Who is hiring? (October 2026)"):
    ts = int((NOW - timedelta(days=19)).timestamp())

    def f(url):
        if url == boards.HN_USER:
            return 200, {"submitted": [900, 901]}, ""
        if url == boards.HN_ITEM % 900:
            return 200, {"id": 900, "title": hiring_title, "kids": [1, 2]}, ""
        if url == boards.HN_ITEM % 901:
            return 200, {"id": 901, "title": "Ask HN: Freelancer? Seeking freelancer? (October 2026)", "kids": []}, ""
        if "algolia" in url and "story_900" in url:
            return 200, {"hits": [dict(h, parent_id=900, created_at_i=ts) for h in comments]}, ""
        if "algolia" in url:
            return 200, {"hits": []}, ""
        if "jobicy" in url:
            return 200, {"jobs": []}, ""
        if "himalayas" in url:
            return 200, {"jobs": []}, ""
        return 404, None, ""
    return f


class HackerNewsTests(TempData, unittest.TestCase):
    def test_hn_cards(self):
        comments = [
            {"objectID": "11", "comment_text": "Acme Cloud | Senior Salesforce Developer | REMOTE (Worldwide) | Contract<p>Apex, LWC. Email jobs@acmecloud.io"},
            {"objectID": "12", "comment_text": "BetaCo | Salesforce Engineer | Remote (US only)<p>Apply https://jobs.lever.co/betaco/xyz"},
            {"objectID": "13", "comment_text": "Gamma | Backend Engineer | Onsite NYC<p>We use Salesforce internally."},
            {"objectID": "14", "comment_text": "Delta | Engineers | REMOTE<p>Looking for a salesforce developer, write to hiring at delta dot dev"},
        ]
        cards = boards.hn_cards(hn_fetcher(comments))
        by = {c["company"]: c for c in cards}
        self.assertEqual(by["Acme Cloud"]["title"], "Senior Salesforce Developer")
        self.assertEqual(by["Acme Cloud"]["emails"], ["jobs@acmecloud.io"])
        self.assertTrue(by["Acme Cloud"]["open"])
        self.assertFalse(by["BetaCo"]["open"])
        self.assertEqual(by["BetaCo"]["apply_url"], "https://jobs.lever.co/betaco/xyz")
        self.assertEqual(by["Gamma"]["title"], "")
        self.assertEqual(by["Delta"]["emails"], ["hiring@delta.dev"])
        self.assertEqual(by["Delta"]["url"], "https://news.ycombinator.com/item?id=14")

    def test_collect_all_sources(self):
        comments = [{"objectID": "11", "comment_text": "Acme Cloud | Senior Salesforce Developer | REMOTE (Worldwide)<p>Email jobs@acmecloud.io"}]
        base = hn_fetcher(comments)
        gh = {"jobs": [
            {"id": 1, "title": "Salesforce Architect", "absolute_url": "https://boards.greenhouse.io/neuraflash/jobs/1",
             "location": {"name": "Remote - India"}, "content": "&lt;p&gt;Agentforce&lt;/p&gt;", "first_published": "2026-10-18T10:00:00Z"},
            {"id": 2, "title": "Salesforce Developer", "absolute_url": "https://boards.greenhouse.io/neuraflash/jobs/2",
             "location": {"name": "Boston, MA"}, "content": "Onsite", "first_published": "2026-10-18T10:00:00Z"},
        ]}
        jobicy = {"jobs": [{"id": 77, "jobTitle": "Salesforce Developer", "companyName": "RemoteCo", "jobGeo": "Anywhere",
                            "url": "https://jobicy.com/jobs/77", "pubDate": "2026-10-19 08:00:00", "jobDescription": "<p>Apex</p>"},
                           {"id": 78, "jobTitle": "Salesforce Admin", "companyName": "EuroCo", "jobGeo": "Germany",
                            "url": "https://jobicy.com/jobs/78", "pubDate": "2026-10-19 08:00:00", "jobDescription": "x"}]}

        def f(url):
            if "boards-api.greenhouse.io/v1/boards/neuraflash/" in url:
                return 200, gh, ""
            if "jobicy" in url:
                return 200, jobicy, ""
            if "greenhouse" in url or "lever" in url or "ashby" in url:
                return 404, None, ""
            return base(url)
        res = boards.collect(self.conn, 1, fetcher=f, now=NOW, pause=0)
        got = sorted((t["source"], t["company"]) for t in res["targets"])
        self.assertEqual(got, [("careers", "Neuraflash"), ("hn", "Acme Cloud"), ("jobicy", "RemoteCo")])
        hn_t = next(t for t in res["targets"] if t["source"] == "hn")
        self.assertEqual(hn_t["emails"], ["jobs@acmecloud.io"])
        row = self.conn.execute("SELECT * FROM jobs WHERE company='Acme Cloud'").fetchone()
        self.assertEqual(row["source"], "hn")
        self.assertIn("hiring email in post", row["notes"])
        # the Lever link in nothing here; seeds were added and dead boards counted
        n = self.conn.execute("SELECT COUNT(*) FROM career_boards").fetchone()[0]
        self.assertGreaterEqual(n, len(boards.SEED_BOARDS))
        # prompt shows source + email
        p = engine.compose_system_prompt("himalayas", engine.PROMPTS_DIR / "himalayas.md", 1, targets=res["targets"])
        self.assertIn("email in post: jobs@acmecloud.io", p)
        self.assertIn("— hn —", p)
        # second run: nothing new
        res2 = boards.collect(self.conn, 2, fetcher=f, now=NOW, pause=0)
        self.assertTrue(all(t.get("retry") for t in res2["targets"]))      # one contact retry, nothing re-saved
        self.assertEqual(res2["counts"]["saved"], 0)
        self.assertEqual(boards.collect(self.conn, 3, fetcher=f, now=NOW, pause=0)["targets"], [])

    def test_discover_flywheel(self):
        actions.save_job(self.conn, None, "posts", "posts", "Zeta", "Salesforce Developer",
                         apply_url="https://jobs.ashbyhq.com/zeta/123")
        boards.discover_from_db(self.conn)
        self.assertTrue(self.conn.execute("SELECT 1 FROM career_boards WHERE platform='ashby' AND token='zeta'").fetchone())
        self.assertEqual(boards.board_of("https://job-boards.greenhouse.io/acme/jobs/1"), ("greenhouse", "acme"))
        self.assertIsNone(boards.board_of("https://example.com/jobs"))

    def test_offline(self):
        res = boards.collect(self.conn, 1)
        self.assertEqual(res["targets"], [])


class ScreenJobsToolTests(TempData, unittest.TestCase):
    def test_screen_jobs(self):
        import jobhunt_mcp as m
        ctx = m.Ctx()
        ctx.ensure()
        ctx.run_id, ctx.lane = 1, "posts"
        if True:
            res, err = m.call_tool(ctx, "screen_jobs", {"source": "posts", "items": [
                {"company": "A", "role": "Salesforce Developer", "url": "https://www.linkedin.com/feed/update/1/", "text": "Remote, India. Email hr@a.com"},
                {"company": "B", "role": "Salesforce Developer", "url": "https://www.linkedin.com/feed/update/2/", "text": "Hybrid role, 3 days a week in the office in Pune"},
                {"company": "C", "role": "Salesforce Developer", "url": "https://www.linkedin.com/feed/update/3/", "text": "US citizens only.", "location": "United States"},
            ]})
        self.assertFalse(err)
        self.assertEqual([w["company"] for w in res["work"]], ["A"])
        self.assertEqual(sum(res["skipped_by_rules"].values()), 2)
        r = ctx.conn.execute("SELECT status, skip_reason FROM jobs WHERE company='B'").fetchone()
        self.assertEqual(tuple(r), ("skipped", "onsite/hybrid"))


if __name__ == "__main__":
    unittest.main()
