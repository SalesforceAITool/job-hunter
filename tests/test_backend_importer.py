import unittest

from test_backend_helpers import FIXTURE, TempData
from jh import db, importer


class ImporterTests(TempData, unittest.TestCase):
    def test_fixture_counts(self):
        c = importer.import_csv(self.conn, FIXTURE)
        self.assertEqual(c["rows"], 60)
        self.assertEqual(c["contacts"], 24)          # every sent/bounced row with an email
        self.assertEqual(c["emails"], 24)
        self.assertEqual(c["nudges"], 3)             # "nudge 1 sent 2026-10-08" x3
        self.assertEqual(c["bounced"], 1)            # Talentgigs
        self.assertEqual(c["errors"], 0)
        self.assertGreaterEqual(c["skipped_jobs"], 30)
        n = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        self.assertEqual(n("SELECT COUNT(*) FROM contacts"), 24)
        self.assertEqual(n("SELECT COUNT(*) FROM emails WHERE kind='initial'"), 24)
        self.assertEqual(n("SELECT COUNT(*) FROM emails WHERE kind='nudge1'"), 3)
        self.assertEqual(n("SELECT COUNT(*) FROM emails WHERE message_id IS NOT NULL"), 0)
        self.assertEqual(n("SELECT COUNT(*) FROM jobs WHERE source!='import'"), 0)
        # two Celestica contacts share one job; Touchpoints + Touchpoints (JobRight) share one job
        self.assertEqual(n("SELECT COUNT(*) FROM jobs WHERE company LIKE 'Celestica%'"), 1)
        self.assertEqual(n("SELECT COUNT(DISTINCT job_id) FROM contacts WHERE email LIKE '%touchpointsinc.com'"), 1)
        # times are 10:00 IST on the tracker date
        first = self.conn.execute("SELECT sent_at FROM emails e JOIN contacts c ON c.id=e.contact_id"
                                  " WHERE c.email='kmartino@crosscountry-consulting.com' AND kind='initial'").fetchone()[0]
        self.assertEqual(first, "2026-10-06T10:00:00+05:30")
        skipped = self.conn.execute("SELECT skip_reason FROM jobs WHERE company='Cherokee Federal'").fetchone()[0]
        self.assertEqual(skipped, "US citizenship + Public Trust required")
        tg = self.conn.execute("SELECT j.status FROM jobs j WHERE company LIKE 'Talentgigs%'").fetchone()[0]
        self.assertEqual(tg, "bounced")
        s = db.summary(self.conn)
        self.assertEqual(s["sent_total"], 24)
        self.assertEqual(s["bounced"], 1)
        self.assertEqual(s["by_source"]["import"], 24)

    def test_idempotent(self):
        importer.import_csv(self.conn, FIXTURE)
        before = [self.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ("jobs", "contacts", "emails", "replies")]
        c2 = importer.import_csv(self.conn, FIXTURE)
        after = [self.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                 for t in ("jobs", "contacts", "emails", "replies")]
        self.assertEqual(before, after)
        self.assertEqual(c2["contacts"], 0)
        self.assertEqual(c2["duplicates"], 60)

    def test_replied_and_stop_rows(self):
        importer.import_text(self.conn, "date,company,role,contact,title,email,status,next_step\n"
                                        "2026-10-07,Delta,Salesforce Developer,Di D,Recruiter,di@delta.com,replied,call booked\n"
                                        "2026-10-07,Gamma,Salesforce Developer,Cy C,HR,cy@gamma.com,sent,hold\n")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replies WHERE kind='reply'").fetchone()[0], 1)
        st = dict(self.conn.execute("SELECT company, status FROM jobs").fetchall())
        self.assertEqual(st, {"Delta": "replied", "Gamma": "closed"})

    def test_auto_import_on_empty_db_only(self):
        (self.data / "tracker.csv").write_bytes(FIXTURE.read_bytes())
        c = importer.maybe_auto_import(self.conn, self.data)
        self.assertEqual(c["contacts"], 24)
        self.assertIsNone(importer.maybe_auto_import(self.conn, self.data))


if __name__ == "__main__":
    unittest.main()
