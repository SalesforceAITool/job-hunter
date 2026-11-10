"""auto_submit (Akshay, 29 Oct): every ready application is approved and submitted without his click."""
import unittest

from test_backend_helpers import RESUME, TempData
from jh import actions, config, watch


class FakeRuns:
    def __init__(self):
        self.calls = []

    def __call__(self, trigger, **kw):
        self.calls.append((trigger, kw))
        return len(self.calls)


class AutoSubmitTests(TempData, unittest.TestCase):
    def portal(self, ready=True, company="A", ats="linkedin_easy_apply", url=None):
        jid = actions.save_job(self.conn, None, "easy_apply", "linkedin_us", company, "Salesforce Developer")["job_id"]
        url = url or "https://www.linkedin.com/jobs/view/%d/" % (5000 + jid)
        return actions.save_portal_prefill(self.conn, None, "easy_apply", jid, ats, url, RESUME,
                                           [] if ready else ["Visa - left for you"], ready=ready)["portal_id"]

    def set_auto(self, on):
        cfg = config.load()
        cfg["auto_submit"] = on
        config.save(cfg)

    def test_default_is_on(self):
        self.assertTrue(config.DEFAULTS["auto_submit"])
        self.assertTrue(config.apply_update(config.load(), {"auto_submit": "false"})["auto_submit"] is False)

    def test_approves_only_ready_open_rows(self):
        a, b = self.portal(True, "A"), self.portal(False, "B")
        got = actions.auto_approve_ready(self.conn)
        self.assertEqual(got, [a])
        self.assertEqual([x["id"] for x in actions.submit_queue(self.conn)], [a])
        self.assertEqual(actions.auto_approve_ready(self.conn), [])          # already approved: no double
        self.assertNotIn(b, [x["id"] for x in actions.submit_queue(self.conn)])

    def test_real_failure_stays_for_him_and_not_reached_retries_twice(self):
        a, c = self.portal(True, "A"), self.portal(True, "C")
        actions.auto_approve_ready(self.conn)
        actions.mark_submit_failed(self.conn, None, "submit", a, "new required question: notice period", still_ready=True)
        actions.mark_submit_failed(self.conn, None, "submit", c, "the submit run ended before this one was sent",
                                   still_ready=True)
        self.assertEqual(actions.auto_approve_ready(self.conn), [c])          # a: real reason -> left for him
        actions.mark_submit_failed(self.conn, None, "submit", c, "the submit run ended before this one was sent",
                                   still_ready=True)
        self.assertEqual(actions.auto_approve_ready(self.conn), [])           # 2 tries used: no endless loop

    def test_wrong_chrome_failure_is_retried(self):
        a = self.portal(True, "A")
        actions.auto_approve_ready(self.conn)
        actions.mark_submit_failed(self.conn, None, "submit", a, "Chrome (Browser 2) is signed in to LinkedIn as Ravi"
                                   " Kumawat, not Akshay", still_ready=True)
        self.assertEqual(actions.auto_approve_ready(self.conn), [a])

    def test_own_company_site_is_not_auto_submitted(self):
        a = self.portal(True, "A")
        self.conn.execute("UPDATE portal_apps SET ats='own_site', apply_url=? WHERE id=?",
                          ("https://careers.acme-example.com/jobs/1", a))
        self.assertEqual(actions.auto_approve_ready(self.conn), [])

    def test_watcher_starts_submit_lane_by_itself(self):
        self.portal(True, "A")
        runs = FakeRuns()
        w = watch.Watcher(None, fetcher=lambda url: (200, ""), start_run=runs, sleep=lambda s: None)
        res = w.tick(now=1000)
        self.assertEqual(res["kind"], "submit")
        self.assertEqual(runs.calls[0][0], "approved")
        self.assertEqual(runs.calls[0][1]["lanes"], ["submit"])

    def test_watcher_waits_for_click_when_off(self):
        self.set_auto(False)
        self.portal(True, "A")
        runs = FakeRuns()
        w = watch.Watcher(None, fetcher=lambda url: (200, ""), start_run=runs, sleep=lambda s: None)
        w.tick(now=1000)
        self.assertFalse(any(c[1].get("lanes") == ["submit"] for c in runs.calls))
        self.assertEqual(actions.pending_approvals(self.conn), 0)


if __name__ == "__main__":
    unittest.main()


class AutoSubmitPromptTests(unittest.TestCase):
    def test_fill_lanes_get_submit_section_only_when_on(self):
        from jh import engine
        for lane in ("easy_apply", "portal", "board_apply"):
            t = engine.compose_system_prompt(lane, str(engine.PROMPTS_DIR / ("%s.md" % lane)), 1, auto_submit=True)
            self.assertIn("Auto-submit is ON", t)
            self.assertIn("submit_now: true", t)
        t = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), 1, auto_submit=False)
        self.assertNotIn("Auto-submit is ON", t)
        t = engine.compose_system_prompt("posts", str(engine.PROMPTS_DIR / "posts.md"), 1, auto_submit=True)
        self.assertNotIn("Auto-submit is ON", t)


class EmailRuleTests(unittest.TestCase):
    def test_every_form_lane_uses_0031_never_2783(self):
        from jh import engine
        for lane in ("easy_apply", "portal", "board_apply", "submit"):
            t = engine.compose_system_prompt(lane, str(engine.PROMPTS_DIR / ("%s.md" % lane)), 1, auto_submit=True)
            self.assertIn("akshay.verma0031@gmail.com", t)
            self.assertIn("akshay.kumar2783@gmail.com", t)


class DeveloperOnlyTests(unittest.TestCase):
    def test_role_rule(self):
        from jh.screen import role_reason, title_reason
        for t in ("Salesforce Developer", "Senior Salesforce Engineer", "Lead Salesforce Developer (LWC)", "Apex Developer",
                  "Salesforce Developer/Architect"):
            self.assertEqual(role_reason(t), "", t)
        for t in ("Salesforce Architect", "Salesforce Technical Architect", "Salesforce Administrator",
                  "Salesforce Consultant", "Salesforce Business Analyst", "Salesforce QA Tester"):
            self.assertTrue(role_reason(t), t)
            self.assertTrue(title_reason(t), t)


class DeveloperOnlySaveJobTests(TempData, unittest.TestCase):
    def test_save_job_skips_architect_whatever_the_ai_decided(self):
        r = actions.save_job(self.conn, None, "easy_apply", "linkedin_india", "Bloom Hire", "Salesforce Architect – Agentforce")
        row = self.conn.execute("SELECT status, skip_reason FROM jobs WHERE id=?", (r["job_id"],)).fetchone()
        self.assertEqual(row["status"], "skipped")
        self.assertIn("developer roles only", row["skip_reason"])
        r = actions.save_job(self.conn, None, "easy_apply", "linkedin_india", "Acme", "Salesforce Developer")
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (r["job_id"],)).fetchone()[0], "qualified")
