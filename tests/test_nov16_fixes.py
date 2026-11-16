"""16 Nov fixes, from the laptop's day: 4 Easy Apply + 10 recruiter emails went out, but
* the first daily report went out at start-up (after 5 days off) with all zeros and read as "not applying";
* run 221's engine printed its result at 15:03 and never exited -> no cycle until 17:25;
* while LinkedIn's public list paused us (HTTP 429) every cycle fell back to a $0.37 AI search;
* the AI was sent Pune / hybrid jobs from the public list and paid to skip them;
* LinkedIn's post search page has no post ids, so the read-posts memory never filled.
"""
import sys
import time
import unittest
from datetime import datetime

from test_backend_helpers import TempData
from jh import config, db, engine, liprefetch, report, screen, seen, watch


def ist(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=db.IST)


class EngineExitTests(unittest.TestCase):
    def test_engine_that_hangs_after_its_result_is_ended(self):
        code = ("import sys,time; print('{\"type\": \"result\", \"subtype\": \"success\"}'); sys.stdout.flush(); "
                "time.sleep(120)")
        lines = []
        old = engine.RESULT_EXIT_GRACE_S
        engine.RESULT_EXIT_GRACE_S = 1
        t0 = time.monotonic()
        try:
            rc, reason, _ = engine._run_stream([sys.executable, "-c", code], None, ".", lines.append, timeout_s=120)
        finally:
            engine.RESULT_EXIT_GRACE_S = old
        self.assertLess(time.monotonic() - t0, 30)
        self.assertIsNone(reason)                         # a normal finish, not a timeout
        self.assertTrue(any('"result"' in x for x in lines))


class ProbePauseTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cfg = config.load()
        cfg["li_fast"] = dict(cfg["li_fast"], every_min=10, lanes=["easy_apply", "posts"], start_hour=0, end_hour=24)
        config.save(cfg)
        self.calls, self.status = [], [200]
        self.w = watch.Watcher(None, start_run=lambda trig, **k: self.calls.append(k) or len(self.calls),
                               probe_fetch=lambda u: (self.status[0], ""), sleep=lambda s: None)

    def tick(self, t):
        self.w.state["next_li_fast"] = 0
        return self.w.li_fast_tick(self.conn, t, local_hour=12)

    def test_waits_without_ai_while_linkedin_pauses_us(self):
        t = time.time()
        self.tick(t)                                         # good check, nothing new
        self.status[0] = 429
        self.tick(t + 600)
        self.assertEqual(self.calls[-1]["lanes"], ["posts"])  # no $0.37 AI search for Easy Apply
        n = self.conn.execute("SELECT COUNT(*) FROM events WHERE message LIKE 'LinkedIn''s public job list is pausing%'")
        self.assertEqual(n.fetchone()[0], 1)
        # unreadable for over 2 hours: the AI searches itself, at most once an hour
        self.tick(t + 2 * 3600 + 700)
        self.assertEqual(self.calls[-1]["lanes"], ["easy_apply", "posts"])
        self.tick(t + 2 * 3600 + 1300)
        self.assertEqual(self.calls[-1]["lanes"], ["posts"])
        self.tick(t + 3 * 3600 + 800)
        self.assertEqual(self.calls[-1]["lanes"], ["easy_apply", "posts"])

    def test_first_check_after_start_failing_still_searches(self):
        self.status[0] = 429
        self.tick(time.time())
        self.assertEqual(self.calls[-1]["lanes"], ["easy_apply", "posts"])


class GuestJobScreenTests(TempData, unittest.TestCase):
    def test_hybrid_titles_skipped_without_ai(self):
        self.assertEqual(screen.screen("SENIOR JAVA SALESFORCE DEVELOPER | HYBRID | R1.6M PA")[1], "onsite/hybrid")
        self.assertEqual(screen.screen("Senior Salesforce Engineer (Fulltime and Hybrid)")[1], "onsite/hybrid")
        self.assertTrue(screen.screen("Salesforce Developer (Remote/Hybrid)")[0])

    def test_probe_cards_get_the_city_location_check(self):
        cards = [{"ext_id": "4401000001", "title": "Salesforce Developer", "company": "Viraaj HR",
                  "location": "Pune, Maharashtra, India", "url": "https://www.linkedin.com/jobs/view/4401000001/",
                  "region": "india"},
                 {"ext_id": "4401000002", "title": "Salesforce Developer", "company": "Remote Co",
                  "location": "India", "url": "https://www.linkedin.com/jobs/view/4401000002/", "region": "india"}]
        jd = ('<section><div class="show-more-less-html__markup">We build Salesforce apps with Apex and LWC. '
              '3+ years.</div></section>')
        res = liprefetch.collect(self.conn, 1, "easy_apply", regions=["india"], fetcher=lambda u, timeout=20.0: (200, jd),
                                 extra_cards=cards)
        self.assertEqual([t["company"] for t in res["targets"]], ["Remote Co"])
        self.assertIn("onsite/hybrid (city location, JD never says remote)", str(res["reasons"]) + str(
            self.conn.execute("SELECT skip_reason FROM jobs WHERE company='Viraaj HR'").fetchone()[0]))


class PostKeyTests(TempData, unittest.TestCase):
    def test_text_keys_remember_posts(self):
        k1 = ("Divya Chandila | Hiring Salesforce Experience Cloud Lead, 9+ years, Remote, Contract. Strong Apex, LWC"
              " and Experience Cloud skills needed. Resume to divya")
        k1_full = k1 + ".c@ekloudservices.com with your notice period and current CTC please"
        self.assertTrue(seen.post_id(k1).startswith("k"))
        self.assertEqual(seen.post_id(k1), seen.post_id(k1_full))     # "…more" expanded or not: same post
        self.assertEqual(seen.post_id("post:ekloud-divya-20261116"), "")
        r1 = self.conn.execute("INSERT INTO runs (started_at, status, trigger, stats_json) VALUES (?, 'running', 'li_fast',"
                               " '{}')", (db.now_iso(),)).lastrowid
        self.assertEqual(seen.filter_posts(self.conn, r1, [k1])["new"], [k1])
        seen.close_posts(self.conn, r1, True)
        r2 = r1 + 1
        self.assertEqual(seen.filter_posts(self.conn, r2, [k1_full, "Naveen kumar | We are hiring Salesforce "
                                                                    "Developers with 4-6 years of experience"])["seen"], 1)

    def test_prompt_has_the_key_js(self):
        t = engine.compose_system_prompt("posts", str(engine.PROMPTS_DIR / "posts.md"), 1, quick=True)
        self.assertIn("Feed post(?: number", t)
        self.assertIn("new_posts", t)


class ReportStartupTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        report._next_try["at"] = 0.0
        cfg = config.load()
        cfg["email"]["address"] = "apply@example.com"
        config.save(cfg)

    def run_at(self, ts):
        self.conn.execute("INSERT INTO runs (started_at, status, trigger, stats_json) VALUES (?, 'done', 'li_fast', '{}')",
                          (ts,))

    def test_no_empty_report_when_the_laptop_comes_back(self):
        self.conn.execute("INSERT INTO app_meta (k, v) VALUES (?, ?)", (report.META_KEY, "2026-11-11T23:00:00+05:30"))
        # laptop off 12-16 Nov; Job Hunter starts at 12:44 -> nothing ran since the last report: wait for 11 PM
        self.assertIsNone(report.tick(self.conn, config.load(), now_dt=ist(2026, 11, 16, 12, 44), now_s=1))
        self.run_at("2026-11-16T12:46:00+05:30")
        res = report.tick(self.conn, config.load(), now_dt=ist(2026, 11, 16, 13, 0), now_s=2)
        self.assertTrue(res["ok"])                        # something ran since: the late report goes out

    def test_gaps_are_listed(self):
        for ts in ("2026-11-16T12:46:00+05:30", "2026-11-16T15:03:00+05:30", "2026-11-16T17:25:00+05:30"):
            self.conn.execute("INSERT INTO events (ts, run_id, lane, type, level, message, data_json) VALUES (?,?,?,?,?,?,?)",
                              (ts, None, "watch", "step", "info", "x", "{}"))
        s = report.collect(self.conn, ist(2026, 11, 16, 12, 44), ist(2026, 11, 16, 18, 0))
        self.assertEqual(s["gaps"], [("2026-11-16T12:46:00+05:30", "2026-11-16T15:03:00+05:30"),
                                     ("2026-11-16T15:03:00+05:30", "2026-11-16T17:25:00+05:30")])
        text = report.render(s)[1]
        self.assertIn("JOB HUNTER WAS NOT RUNNING", text)
        self.assertIn("2 h 22 min", text)


if __name__ == "__main__":
    unittest.main()
