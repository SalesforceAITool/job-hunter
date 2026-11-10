import json
import unittest
from datetime import datetime, timedelta, timezone

from test_backend_helpers import TempData
from jh import actions, himalayas, seen

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def job(i, title="Senior Salesforce Developer", company="Acme", restr=None, days=1, apply="https://boards.greenhouse.io/acme/jobs/1", **kw):
    d = {"title": title, "companyName": company, "companySlug": company.lower(), "guid": "https://himalayas.app/companies/%s/jobs/sf-%d" % (company.lower(), i),
         "locationRestrictions": restr if restr is not None else [], "timezoneRestrictions": [-5, 5.5],
         "minSalary": 60000, "maxSalary": 90000, "currency": "USD", "salaryPeriod": "annual",
         "employmentType": "Contractor", "description": "<p>Apex, LWC, <b>Agentforce</b>.</p><ul><li>Remote</li></ul>",
         "pubDate": int((NOW - timedelta(days=days)).timestamp()), "expiryDate": int((NOW + timedelta(days=30)).timestamp()),
         "applicationLink": apply}
    d.update(kw)
    return d


def fetcher_for(pages):
    calls = []

    def f(url):
        calls.append(url)
        n = len(calls)
        if n <= len(pages):
            return 200, pages[n - 1], json.dumps(pages[n - 1])
        return 200, {"jobs": []}, '{"jobs": []}'
    f.calls = calls
    return f


class HimalayasTests(TempData, unittest.TestCase):
    def test_collect_filters_and_saves(self):
        page1 = {"jobs": [
            job(1),                                                     # keep (worldwide)
            job(2, company="Beta", restr=["India", "Singapore"]),       # keep
            job(3, company="Gamma", restr=["United States"]),           # US only -> drop
            job(4, title="Python Engineer", company="Delta"),           # not Salesforce
            job(5, title="Salesforce Account Executive", company="Eps"),  # sales role
            job(1),                                                     # duplicate guid
        ]}
        page2 = {"jobs": [job(6, company="Old", days=40)]}             # too old -> stop
        f = fetcher_for([page1, page2])
        res = himalayas.collect(self.conn, None, fetcher=f, now=NOW)
        c = res["counts"]
        self.assertEqual((c["saved"], c["not_india"], c["not_salesforce"], c["old"]), (2, 1, 2, 1))
        self.assertEqual([t["company"] for t in res["targets"]], ["Acme", "Beta"])
        self.assertEqual(len(f.calls), 2)                               # stopped at the old job
        self.assertIn("country=India", f.calls[0])
        row = self.conn.execute("SELECT * FROM jobs WHERE company='Acme'").fetchone()
        self.assertEqual(row["source"], "himalayas")
        self.assertEqual(row["apply_url"], "https://boards.greenhouse.io/acme/jobs/1")
        self.assertIn("Agentforce", row["jd_text"])
        self.assertIn("via Himalayas", row["notes"])
        self.assertIn("USD 60000-90000", row["notes"])
        # second run: nothing new, nothing re-saved
        res2 = himalayas.collect(self.conn, None, fetcher=fetcher_for([page1]), now=NOW)
        self.assertEqual(res2["counts"]["saved"], 0)
        self.assertEqual(res2["counts"]["seen"], 0 + res2["counts"]["seen"])
        self.assertEqual(res2["targets"], [t for t in res2["targets"] if t.get("retry")])  # only one-time retries

    def test_errors_and_odd_envelopes(self):
        res = himalayas.collect(self.conn, None, fetcher=lambda u: (429, None, ""), now=NOW)
        self.assertIn("429", res["error"])
        res = himalayas.collect(self.conn, None, fetcher=lambda u: (200, {"weird": 1}, '{"weird":1}'), now=NOW)
        self.assertIn("format", res["error"])
        self.assertTrue((self.data / "logs" / "himalayas_last.txt").exists())
        self.assertEqual(himalayas.jobs_of([{"a": 1}, 2]), [{"a": 1}])
        self.assertEqual(himalayas.jobs_of({"data": {"jobs": [{"x": 1}]}}), [{"x": 1}])
        self.assertTrue(himalayas.india_ok([]))
        self.assertTrue(himalayas.india_ok([{"name": "India"}]))
        self.assertFalse(himalayas.india_ok(["Canada"]))
        self.assertIsNotNone(himalayas._ts("2026-10-19T10:00:00Z"))
        self.assertIsNotNone(himalayas._ts(1760000000000))

    def test_offline_in_tests(self):
        st, p, raw = himalayas.fetch_json("https://himalayas.app/jobs/api/search?q=x")
        self.assertEqual((st, p), (0, None))


class SeenTests(TempData, unittest.TestCase):
    def test_filter_jobs_new_seen_retry(self):
        c = self.conn
        a = actions.save_job(c, None, "jobright", "jobright", "Acme", "Salesforce Developer",
                             url="https://jobright.ai/jobs/info/6ad6d38d67bec1d0d9c72238")["job_id"]
        actions.save_job(c, None, "linkedin_jobs", "linkedin_us", "Beta", "SF Admin", decision="skipped",
                         skip_reason="requires US work authorization", url="https://www.linkedin.com/jobs/view/4011111111/")
        b = actions.save_job(c, None, "linkedin_jobs", "linkedin_us", "Gamma", "SF Architect", decision="skipped",
                             skip_reason="no email found")["job_id"]
        items = [
            {"company": "Acme", "role": "Salesforce Developer", "url": "x"},                     # same company+role
            {"company": "Other", "role": "Other", "url": "https://jobright.ai/jobs/info/6ad6d38d67bec1d0d9c72238?x=1"},  # same JobRight id
            {"company": "Z", "role": "Y", "url": "https://in.linkedin.com/jobs/view/sf-admin-4011111111?trk=1"},  # same LI id (slug url)
            {"company": "Gamma", "role": "SF Architect", "url": ""},                              # retry once
            {"company": "New Co", "role": "Salesforce Developer", "url": "https://www.linkedin.com/jobs/view/4099999999/"},
            "junk", {"company": "", "role": "", "url": ""},
        ]
        r = seen.filter_jobs(c, items)
        # Acme (qualified, no contact yet) and Gamma ("no email found") get ONE contact retry each;
        # the second Acme card (same JobRight id) is then seen; Beta (US-only skip) is seen; "junk" is a url-only new item
        self.assertEqual(r["counts"], {"new": 2, "seen": 2, "retry": 2})
        self.assertEqual(sorted(x["job_id"] for x in r["retry"]), sorted([a, b]))
        self.assertIn(a, [x["job_id"] for x in r["seen"]])
        r2 = seen.filter_jobs(c, [{"company": "Gamma", "role": "SF Architect"}])
        self.assertEqual(r2["counts"]["seen"], 1)                              # retry handed out only once

    def test_profiles(self):
        c = self.conn
        jid = actions.save_job(c, None, "posts", "posts", "Acme", "SF Dev")["job_id"]
        actions.save_contact(c, None, "posts", jid, "Jane", "Recruiter", "jane@acme.io", "SalesQL",
                             linkedin="https://www.linkedin.com/in/jane-doe-1/")
        seen.record_profile(c, "https://www.linkedin.com/in/no-email-guy/?trk=x", found=False)
        r = seen.filter_profiles(c, ["https://www.linkedin.com/in/Jane-Doe-1", "https://www.linkedin.com/in/no-email-guy",
                                     "https://www.linkedin.com/in/fresh-person/", "not a url"])
        self.assertEqual(r["new"], ["https://www.linkedin.com/in/fresh-person/"])
        self.assertEqual(len(r["seen"]), 2)
        self.assertIn("error", seen.record_profile(c, "https://example.com"))


if __name__ == "__main__":
    unittest.main()
