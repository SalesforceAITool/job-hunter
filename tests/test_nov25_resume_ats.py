"""25 Nov (ideas from ai-job-search-dev/ai-job-search): ATS keyword check after build_resume, echo-trap rule."""
import sys
import unittest
from pathlib import Path

from test_backend_helpers import TempData
from jh import actions, ats, config, engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

JD = ("Senior Salesforce Consultant. 7+ years. Strong Sales Cloud and Case Management: Email-to-Case, Web-to-Case, "
      "escalation rules, queues and SLAs. Apex, LWC, Salesforce Flow, REST APIs. Reports and dashboards. "
      "Experience with MuleSoft is a plus.")


class AtsTests(unittest.TestCase):
    def test_terms_and_coverage(self):
        t = ats.terms_in(JD)
        for k in ("Sales Cloud", "Case Management", "Email-to-Case", "Web-to-Case", "Escalation Rules", "Queues",
                  "SLAs", "Apex", "LWC", "Salesforce Flow", "REST API", "Reports and Dashboards", "MuleSoft"):
            self.assertIn(k, t)
        resume = "Apex, LWC, Flows, REST integrations, Sales Cloud and Service Cloud"
        cov = ats.coverage(JD, resume, {})
        self.assertEqual(set(cov["matched"]), {"Apex", "LWC", "Salesforce Flow", "REST API", "Sales Cloud"})
        self.assertIn("Email-to-Case", cov["can_add"])
        self.assertIn("Queues", cov["can_add"])
        # not confirmed by Akshay: never offered for adding
        for k in ("MuleSoft", "Escalation Rules", "Reports and Dashboards"):
            self.assertIn(k, cov["missing"])
            self.assertNotIn(k, cov["can_add"])
        self.assertEqual(cov["score"], round(100 * 5 / cov["jd_terms"]))

    def test_config_can_confirm_more(self):
        cov = ats.coverage(JD, "Apex", {"resume_confirmed_skills": ["Escalation Rules"]})
        self.assertIn("Escalation Rules", cov["can_add"])

    def test_no_terms(self):
        self.assertEqual(ats.coverage("Great team, great pay", "Apex", {})["score"], None)


class BuildResumeToolTests(TempData, unittest.TestCase):
    def test_build_resume_returns_the_check(self):
        try:
            import reportlab  # noqa: F401
        except Exception:
            self.skipTest("reportlab not installed")
        import jobhunt_mcp as m
        job = actions.save_job(self.conn, 1, "easy_apply", "linkedin_india", "Strong Point",
                               "Senior Salesforce Developer - Case Management", url="https://www.linkedin.com/jobs/view/4493406778/",
                               jd_text=JD)["job_id"]
        ctx = m.Ctx()
        ctx.ensure()
        ctx.lane = "easy_apply"
        res, err = m.call_tool(ctx, "build_resume", {"job_id": job, "resume": {
            "headline": "Salesforce Developer (7+ years) | Service Cloud, Apex, LWC",
            "summary": "Salesforce developer with 7+ years.",
            "skills": [["Development", "Apex, LWC, Flows"]] * 6}})
        self.assertFalse(err, res)
        self.assertTrue(Path(res["resume_path"]).is_file())
        self.assertTrue(Path(res["resume_path"][:-4] + ".txt").is_file())
        cov = res["ats"]
        self.assertIn("Apex", cov["matched"])
        self.assertIn("MuleSoft", cov["missing"])
        self.assertNotIn("MuleSoft", cov["can_add"])
        msgs = [r[0] for r in self.conn.execute("SELECT message FROM events WHERE message LIKE 'Resume keywords:%'")]
        self.assertEqual(len(msgs), 1)

    def test_prompt_rules(self):
        t = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), 1)
        self.assertIn("ATS keyword check", t)
        self.assertIn("Echo traps", t)
        self.assertIn("window.__jh.resumeInput()", t)
        self.assertIn("resumeInput = async function", t)       # the helper itself is in the prompt


if __name__ == "__main__":
    unittest.main()
