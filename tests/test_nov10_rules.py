"""Akshay, 10 Nov: no architect roles, only jobs asking for up to 7 years, one LinkedIn cycle every 2 hours that
also reads hiring posts, and a token-saving gap counted from the END of the previous AI cycle."""
import time
import unittest

from test_backend_helpers import TempData
from jh import config, engine, runner, screen, watch


class ScreenRuleTests(TempData, unittest.TestCase):
    def check(self, title, body, ok):
        got, why = screen.screen(title, body, "India (Remote)", india_role=True)
        self.assertEqual(got, ok, (title, body, why))
        return why

    def test_architect_titles_skipped(self):
        self.assertIn("architect", self.check("Salesforce Technical Architect", "5 years of experience", False))
        self.check("Salesforce Architect / Developer", "", False)
        self.check("Salesforce Developer", "Work with our architect on Apex.", True)

    def test_experience_cap(self):
        self.check("Salesforce Developer", "We need 5+ years of experience in Salesforce.", True)
        self.check("Salesforce Developer", "6-8 years of experience.", True)
        self.check("Salesforce Developer", "7+ years of Salesforce experience.", True)
        self.assertIn("8+", self.check("Salesforce Developer", "Minimum 8 years of experience with Apex.", False))
        self.check("Salesforce Developer", "8-10 years of relevant experience required.", False)
        self.check("Salesforce Developer", "5+ years Salesforce experience, 9+ years overall IT experience", False)
        self.check("Salesforce Developer", "Experience: 10+ years", False)
        self.check("Salesforce Developer", "10+ years of experience is a plus. 4+ years of experience required.", True)
        self.check("Salesforce Developer", "25 years in business. 3 years of Salesforce experience.", True)

    def test_cap_and_architect_are_settings(self):
        cfg = config.load(); cfg["max_years_required"] = 10; cfg["skip_architect"] = False; config.save(cfg)
        self.check("Salesforce Developer", "8-10 years of relevant experience required.", True)
        self.assertEqual(screen.architect_reason("Salesforce Architect"), "")


class CycleTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cfg = config.load()
        cfg["li_fast"] = dict(cfg["li_fast"], every_min=120, start_hour=0, end_hour=24, lanes=["easy_apply", "posts"],
                              gap_after_min=120)
        config.save(cfg)

    def test_cycle_runs_posts_quickly_and_waits_after_it_finishes(self):
        calls = []
        w = watch.Watcher(None, start_run=lambda trig, **k: calls.append((trig, k)) or 1)
        now = time.time() + 200
        res = w.li_fast_tick(self.conn, now, local_hour=10)
        self.assertEqual(res["kind"], "li_fast")
        trig, kw = calls[0]
        self.assertEqual((trig, kw["lanes"], kw["fast"], kw.get("quick")), ("li_fast", ["easy_apply", "posts"], True, True))
        self.assertTrue(w.state["ai_active"])
        # the run took 40 minutes; when the watcher sees it finished, the 2-hour gap starts from then
        orig = runner.is_running
        runner.is_running = lambda: False
        try:
            w.state["next_li_fast"] = 0
            end = now + 2400
            w.state["next_followups"] = end + 10 ** 6
            w.tick(end)
            self.assertFalse(w.state["ai_active"])
            self.assertAlmostEqual(w.state["next_ai"], end + 7200, delta=1)
            self.assertIsNone(w.li_fast_tick(self.conn, end + 3600, local_hour=11))        # still in the gap
            self.assertEqual(w.li_fast_tick(self.conn, end + 7201, local_hour=12)["kind"], "li_fast")
        finally:
            runner.is_running = orig

    def test_runner_allows_the_cycle_lanes_in_safe_mode(self):
        cfg = config.load()
        self.assertTrue(cfg["linkedin_safe"])
        self.assertEqual(cfg["li_fast"]["lanes"], ["easy_apply", "posts"])

    def test_fast_window_follows_the_interval(self):
        self.assertEqual(engine.li_fast_window(), "r7200")
        t = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), 1, li_fast=5)
        self.assertIn("f_TPR=r7200", t)
        self.assertIn("more than 7 years", t)

    def test_focus_mode_runs_only_the_linkedin_cycle(self):
        cfg = config.load(); cfg["li_fast"] = dict(cfg["li_fast"], every_min=10, gap_after_min=0)
        cfg["focus_linkedin"] = True; config.save(cfg)
        calls = []
        w = watch.Watcher(None, start_run=lambda trig, **k: calls.append((trig, k)) or 1)
        orig = runner.is_running; runner.is_running = lambda: False
        try:
            now = time.time() + 200
            w.state["next_followups"] = 0                      # follow-ups are due, but focus mode leaves them alone
            w.tick(now)
            self.assertEqual([(t, k["lanes"]) for t, k in calls], [("li_fast", ["easy_apply", "posts"])])
            w.tick(now + 300)
            self.assertEqual(len(calls), 1)                    # not before 10 minutes
            w.tick(now + 601)
            self.assertEqual(len(calls), 2)
        finally:
            runner.is_running = orig

    def test_bad_lanes_are_cleaned(self):
        cfg = config.apply_update(config.load(), {"li_fast": {"lanes": ["posts", "rm -rf", "board_apply"]}})
        self.assertEqual(cfg["li_fast"]["lanes"], ["posts"])
        raw = config.load(); raw["li_fast"]["lanes"] = ["junk"]; config.save(raw)       # hand-edited file
        calls = []
        w = watch.Watcher(None, start_run=lambda trig, **k: calls.append(k) or 1)
        w.li_fast_tick(self.conn, time.time() + 200, local_hour=10)
        self.assertEqual(calls[0]["lanes"], ["easy_apply"])


if __name__ == "__main__":
    unittest.main()
