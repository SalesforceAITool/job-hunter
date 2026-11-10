"""Autopilot: the watcher starts the cheap checks and LinkedIn Jobs by itself, and stops AI work at the daily budget."""
import time
import unittest

from test_backend_helpers import TempData
from jh import actions, config, events, watch


class AutopilotTests(TempData, unittest.TestCase):
    def watcher(self, calls):
        def start(trig, **k):
            calls.append((trig, tuple(k.get("lanes") or ()), tuple(k.get("regions") or ())))
            return len(calls)
        w = watch.Watcher(None, fetcher=lambda u, t=20: (200, ""), start_run=start, sleep=lambda s: None, jobboard=True)
        w.jobboard_tick = lambda conn: None          # no background refresh in this test
        return w

    def test_followups_then_linkedin_jobs_on_schedule(self):
        self.cfg["watch"]["posts"] = False
        config.save(self.cfg)
        calls = []
        w = self.watcher(calls)
        now = time.time()
        w.tick(now)                                   # nothing due yet (first checks a minute after start)
        self.assertEqual(calls, [])
        w.tick(now + 61)
        self.assertEqual(calls[-1][:2], ("auto", ("followups",)))
        w.tick(now + 62)
        self.assertEqual(len(calls), 1)               # follow-ups not again for 15 min
        w.tick(now + 301)
        self.assertEqual(calls[-1], ("auto", ("linkedin_jobs",), ("india", "world")))
        self.assertTrue(w.status()["autopilot"])

    def test_over_budget_only_free_checks(self):
        self.cfg["watch"]["posts"] = False
        self.cfg["budget"]["daily_usd"] = 5
        config.save(self.cfg)
        events.emit(self.conn, None, "posts", "lane_finished", "x", "info", {"cost_usd": 6})
        calls = []
        w = self.watcher(calls)
        now = time.time()
        w.tick(now + 61)
        w.tick(now + 400)
        self.assertEqual([c[1] for c in calls], [("followups",)])     # no LinkedIn Jobs run after the budget
        self.assertTrue(w.status()["over_budget"])

    def test_real_app_watcher_has_autopilot_and_job_board(self):
        """Regression: the default fetcher was filled in before the "was a fetcher injected?" check, so the real app's
        watcher thought it was a test and never ran the Job board or Autopilot."""
        w = watch.Watcher(None)
        self.assertFalse(w.state["no_jobboard"])
        self.assertTrue(w.autopilot().get("on"))

    def test_switch_off_turns_everything_off(self):
        calls = []
        w = self.watcher(calls)
        st = w.set({"autopilot": False})
        self.assertFalse(st["autopilot"])
        self.assertFalse(st["on"])
        w.tick(time.time() + 1000)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()


class LinkedInSafeModeTests(TempData, unittest.TestCase):
    def test_real_app_makes_no_linkedin_requests_and_no_linkedin_runs(self):
        from jh import ligate, liprefetch, runner
        self.assertTrue(config.load()["linkedin_safe"])
        import os
        old = os.environ.pop("JOBHUNTER_OFFLINE", None)
        try:
            self.assertEqual(ligate.get("https://www.linkedin.com/jobs-guest/x")[0], 0)        # no request made
            self.assertIsNone(liprefetch.collect(self.conn, 1, "easy_apply"))                   # AI browses as a person
        finally:
            if old is not None:
                os.environ["JOBHUNTER_OFFLINE"] = old
        cfg = config.load(); cfg["li_fast"] = dict(cfg["li_fast"], on=False); config.save(cfg)
        calls = []
        w = watch.Watcher(None, start_run=lambda trig, **k: calls.append((trig, tuple(k.get("lanes") or ()))) or 1)
        w.jobboard_tick = lambda conn: None
        now = time.time()
        for dt in (61, 400, 4000):
            w.tick(now + dt)
        self.assertTrue(all(c[1] == ("followups",) for c in calls), calls)                  # never a LinkedIn lane
        self.assertTrue(w.status()["linkedin_safe"])
        self.assertFalse(w.status()["on"])

    def test_li_fast_applies_to_new_linkedin_jobs_by_itself(self):
        """Akshay, 30 Oct: new LinkedIn jobs are found and applied to without his click (his own Chrome, capped)."""
        from jh import runner
        calls = []
        w = watch.Watcher(None, start_run=lambda trig, **k: calls.append((trig, k)) or 1)
        now = time.time() + 200                                                        # first check ~2 min after start
        res = w.li_fast_tick(self.conn, now, local_hour=10)
        self.assertEqual(res["kind"], "li_fast")
        trig, kw = calls[0]
        self.assertEqual((trig, kw["lanes"], kw["fast"]), ("li_fast", ["easy_apply"], True))
        self.assertIsNone(w.li_fast_tick(self.conn, now + 60, local_hour=10))             # not due again yet
        self.assertIsNone(w.li_fast_tick(self.conn, now + 3600, local_hour=3))            # night: sleeps
        cfg = config.load(); cfg["li_fast"] = dict(cfg["li_fast"], daily_cap=1); config.save(cfg)
        jid = actions.save_job(self.conn, None, "easy_apply", "linkedin_india", "A", "Salesforce Developer")["job_id"]
        actions.save_portal_prefill(self.conn, None, "easy_apply", jid, "linkedin_easy_apply",
                                    "https://www.linkedin.com/jobs/view/4100000001/", {}, [], ready=True)
        self.assertIsNone(w.li_fast_tick(self.conn, now + 7200, local_hour=11))           # daily cap reached
        # the prompt asks for jobs from the past hour, capped
        from jh import engine
        t = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), 1, li_fast=5)
        self.assertIn("f_TPR=r3600", t)
        self.assertNotIn("f_TPR=r86400", t)
        self.assertIn("At most 5 applications", t)

    def test_li_fast_trigger_runs_only_easy_apply(self):
        import types
        from jh import runner
        seen = []
        orig = runner._load_engine
        self.addCleanup(setattr, runner, "_load_engine", orig)
        runner._load_engine = lambda: types.SimpleNamespace(run_lane=lambda r, lane, *a: seen.append(lane) or {"status": "done"})
        runner.start_run("li_fast", cfg=config.load(), lanes=["easy_apply", "posts"], fast=True)
        self.assertTrue(runner.wait(30))
        self.assertIn("easy_apply", seen)
        self.assertNotIn("posts", seen)

    def test_app_open_run_skips_linkedin_lanes_button_run_keeps_them(self):
        import types
        from jh import runner
        seen = []
        orig = runner._load_engine
        self.addCleanup(setattr, runner, "_load_engine", orig)
        runner._load_engine = lambda: types.SimpleNamespace(run_lane=lambda r, lane, *a: seen.append(lane) or {"status": "done"})
        runner.start_run("open", cfg=config.load())
        self.assertTrue(runner.wait(30))
        self.assertNotIn("easy_apply", seen)
        self.assertNotIn("posts", seen)
        self.assertNotIn("linkedin_jobs", seen)
        seen.clear()
        runner.start_run("button", cfg=config.load(), lanes=["posts"])
        self.assertTrue(runner.wait(30))
        self.assertEqual(seen, ["posts"])
