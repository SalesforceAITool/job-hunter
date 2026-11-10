import unittest

from test_backend_helpers import TempData  # noqa: F401  (sets sys.path)
from jh import actions, dedupe


class NormalizeTests(unittest.TestCase):
    def test_company_suffixes_and_markers(self):
        cases = {
            "CGI (LinkedIn)": "cgi",
            "CGI Inc.": "cgi",
            "Touchpoints (JobRight)": "touchpoints",
            "DX Future Tech Pvt. Ltd.": "dx future tech",
            "GranularTek LLC": "granulartek",
            "GBG Plc": "gbg",
            "entero AG (JobRight)": "entero",
            "Fresh Tech IT (LinkedIn post)": "fresh tech it",
            "  Vibrant   Emotional  Health ": "vibrant emotional health",
            "Peak & Peak": "peak and peak",
        }
        for raw, want in cases.items():
            self.assertEqual(dedupe.normalize_company(raw), want, raw)

    def test_role(self):
        self.assertEqual(dedupe.normalize_role("Sr. Salesforce Developer (Remote)"), "senior salesforce developer")
        self.assertEqual(dedupe.normalize_role("Senior Salesforce Developer"), "senior salesforce developer")
        self.assertEqual(dedupe.normalize_role("Salesforce Developer (m/f/d)"), "salesforce developer")
        self.assertEqual(dedupe.normalize_role("Senior Engineer, Salesforce"), "senior engineer salesforce")
        self.assertEqual(dedupe.normalize_role("CPQ Salesforce Developer"), "cpq salesforce developer")

    def test_norm_key_matches_variants(self):
        a = dedupe.norm_key("Touchpoints (JobRight)", "Senior Salesforce Developer")
        b = dedupe.norm_key("TOUCHPOINTS, Inc.", "Sr. Salesforce Developer")
        self.assertEqual(a, b)
        self.assertNotEqual(a, dedupe.norm_key("Touchpoints", "Salesforce Architect"))

    def test_email_normalization(self):
        self.assertEqual(dedupe.normalize_email("  <Jane.Doe@Example.COM> "), "jane.doe@example.com")
        self.assertTrue(dedupe.valid_email("jo.mitra@celestica.com"))
        self.assertFalse(dedupe.valid_email("-"))


class TitleRuleTests(unittest.TestCase):
    def test_refused(self):
        for t in ["Senior Salesforce Developer", "Software Engineer", "QA Engineer", "Tester", "Salesforce Developers",
                  "SDET"]:
            self.assertFalse(actions.title_allowed(t)[0], t)

    def test_allowed(self):
        for t in ["Salesforce Architect", "Lead Salesforce Developer", "Engineering Manager", "Director of Software Engineering",
                  "Senior IT Recruiter", "HR", "Talent Acquisition Partner", "CEO", "CTO", "Co-Founder",
                  "Principal Engineer", "Technical Lead", "Head of Engineering", "", "-", "Hiring inbox",
                  "VP Engineering", "Partner & CEO"]:
            self.assertTrue(actions.title_allowed(t)[0], t)


class AlreadyContactedTests(TempData, unittest.TestCase):
    def test_by_email_and_company_role(self):
        from jh import importer
        importer.import_csv(self.conn, str(__import__("test_backend_helpers").FIXTURE))
        r = dedupe.already_contacted(self.conn, "Other Co", "Dev", ["JO.MITRA@celestica.com"])
        self.assertTrue(r["contacted"])
        self.assertIn("jo.mitra@celestica.com", r["reason"])
        r = dedupe.already_contacted(self.conn, "Celestica Inc.", "Sr. Salesforce Developer / Admin", [])
        self.assertTrue(r["contacted"])
        self.assertIn("company+role", r["reason"])
        # skipped jobs are not "contacted"
        r = dedupe.already_contacted(self.conn, "Docker", "Senior Salesforce Developer", ["new@docker.com"])
        self.assertFalse(r["contacted"])

    def test_by_linkedin_profile(self):
        c = self.conn
        c.execute("INSERT INTO jobs (id, company, role, status) VALUES (900, 'Acme', 'SF Dev', 'contacted')")
        c.execute("INSERT INTO contacts (id, job_id, name, email, linkedin) VALUES (900, 900, 'Rita', 'rita@acme.com',"
                  " 'https://www.linkedin.com/in/rita-r-123/')")
        c.execute("INSERT INTO emails (job_id, contact_id, kind, status, sent_at) VALUES (900, 900, 'initial', 'sent',"
                  " '2026-10-10T10:00:00+05:30')")
        c.commit()
        r = dedupe.already_contacted(c, "New Co", "Architect", [], linkedin="linkedin.com/in/Rita-R-123?trk=x")
        self.assertTrue(r["contacted"])
        self.assertIn("LinkedIn", r["reason"])
        r = actions.already_contacted(c, "New Co", "Architect", [], linkedin="https://www.linkedin.com/in/someone-else/")
        self.assertFalse(r["contacted"])
        # dry-run sends do not count
        c.execute("UPDATE emails SET status='dry_run'")
        c.commit()
        self.assertFalse(dedupe.already_contacted(c, "New Co", "Architect", [],
                                                  linkedin="/in/rita-r-123/")["contacted"])


if __name__ == "__main__":
    unittest.main()
