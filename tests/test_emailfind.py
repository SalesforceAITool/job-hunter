"""Email fallback when SalesQL finds nothing: only verified addresses, and save_contact accepts only what find_email returned."""
import unittest

from test_backend_helpers import TempData, write_inbox
from jh import actions, config, emailfind


def fake_api(answers):
    calls = []

    def fetch(url, headers=None, data=None, timeout=20):
        calls.append((url, data))
        for k, v in answers.items():
            if k in url:
                return v
        return 404, None
    fetch.calls = calls
    return fetch


class EmailFindTests(TempData, unittest.TestCase):
    def job(self, company="Acme Cloud"):
        return actions.save_job(self.conn, None, "linkedin_jobs", "linkedin_world", company, "Salesforce Developer")["job_id"]

    def test_gmail_history_then_save(self):
        jid = self.job()
        write_inbox(self.data, "Priya Shah <priya@acmecloud.com>", "Re: Salesforce roles", "Hi Akshay")
        write_inbox(self.data, "no-reply@acmecloud.com", "Newsletter", "x")
        r = emailfind.find(self.conn, self.cfg, jid, "Acme Cloud", "https://www.acmecloud.com/careers")
        self.assertEqual([c["email"] for c in r["candidates"]], ["priya@acmecloud.com"])
        self.assertEqual(r["candidates"][0]["source"], "gmail")
        ok = actions.save_contact(self.conn, None, "linkedin_jobs", jid, "Priya Shah", "Talent Acquisition", "priya@acmecloud.com", "gmail")
        self.assertIn("contact_id", ok)

    def test_invented_address_with_verified_source_is_refused(self):
        jid = self.job()
        bad = actions.save_contact(self.conn, None, "linkedin_jobs", jid, "Ann", "Recruiter", "ann@acmecloud.com", "hunter")
        self.assertIn("not returned by find_email", bad["error"])

    def test_hunter_only_valid_and_hr(self):
        self.cfg["email_finder"]["hunter_api_key"] = "testkey12345"
        config.save(self.cfg)
        f = fake_api({"email-finder": (200, {"data": {"email": "rahul.k@acmecloud.com", "verification": {"status": "accept_all"}}}),
                      "domain-search": (200, {"data": {"emails": [
                          {"value": "meera@acmecloud.com", "position": "Talent Acquisition Lead", "verification": {"status": "valid"}},
                          {"value": "dev@acmecloud.com", "position": "Software Engineer", "verification": {"status": "valid"}},
                          {"value": "hr@other.com", "position": "HR", "verification": {"status": "valid"}},
                          {"value": "sam@acmecloud.com", "position": "Recruiter", "verification": {"status": "unknown"}}]}})})
        jid = self.job()
        r = emailfind.find(self.conn, config.load(), jid, "Acme Cloud", "acmecloud.com", "Rahul", "Kumar", fetch=f)
        self.assertEqual([c["email"] for c in r["candidates"]], ["meera@acmecloud.com"])   # not unverified / not other domain
        self.assertIn("hunter", r["tried"])

    def test_apollo_verified_only_and_daily_cap(self):
        self.cfg["email_finder"].update(apollo_api_key="apollokey123", daily_cap=1)
        config.save(self.cfg)
        f = fake_api({"people/match": (200, {"person": {"email": "j.doe@acmecloud.com", "email_status": "verified", "title": "Director"}})})
        jid = self.job()
        r = emailfind.find(self.conn, config.load(), jid, "Acme Cloud", "acmecloud.com", "Jane", "Doe", fetch=f)
        self.assertEqual(r["candidates"][0]["source"], "apollo")
        r2 = emailfind.find(self.conn, config.load(), jid, "Acme Cloud", "acmecloud.com", "John", "Roe", fetch=f)
        self.assertIn("daily lookup cap reached (1)", r2["tried"])

    def test_domain_cleaning_and_keys_masked(self):
        self.assertEqual(emailfind.clean_domain("https://www.Acme.com/jobs?x=1"), "acme.com")
        self.assertEqual(emailfind.clean_domain("linkedin.com"), "")
        self.assertEqual(emailfind.clean_domain("gmail.com"), "")
        self.cfg["email_finder"]["hunter_api_key"] = "secretkey999"
        m = config.masked(self.cfg)
        self.assertNotIn("secretkey999", str(m))
        self.assertTrue(m["email_finder"]["hunter_api_key_set"])
        kept = config.apply_update(self.cfg, {"email_finder": {"hunter_api_key": m["email_finder"]["hunter_api_key"]}}, from_api=True)
        self.assertEqual(kept["email_finder"]["hunter_api_key"], "secretkey999")

    def test_secret_filter_covers_finder_keys(self):
        self.cfg["email_finder"]["hunter_api_key"] = "abcd1234efgh"
        self.assertTrue(actions._leaks_secret("my key is abcd-1234-efgh", self.cfg))


if __name__ == "__main__":
    unittest.main()
