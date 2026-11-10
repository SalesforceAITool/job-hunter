"""Replies -> scheduled interviews: classification, Gmail drafts (never sent), inbound mail about portal/Easy Apply."""
import os
import unittest
from datetime import date

from test_backend_helpers import RESUME, TempData, write_inbox
from jh import actions, db, followups, replies


class ClassifyTests(unittest.TestCase):
    CASES = [
        ("Interview invitation - Salesforce Developer", "Please pick a slot: calendly.com/acme/30min", "interview"),
        ("Re: Salesforce role", "Can we have a quick call tomorrow? What is your best number?", "call_request"),
        ("Re: Salesforce role", "Please share your current CTC, expected CTC and notice period.", "info_request"),
        ("Re: Salesforce role", "Are you comfortable with C2C? What is your hourly rate?", "info_request"),
        ("Next step: assessment", "Please complete the HackerRank test within 48 hours.", "assessment"),
        ("Re: Salesforce role", "Unfortunately we decided to move forward with other candidates.", "rejection"),
        ("Thank you for applying to Acme", "We have received your application and will review it.", "ack"),
        ("Re: Salesforce role", "Thanks Akshay.", "other"),
    ]

    def test_classify(self):
        got = [(s[:30], replies.classify(s, t)) for s, t, _ in self.CASES]
        self.assertEqual(got, [(s[:30], want) for s, _, want in self.CASES])

    def test_draft_has_facts_and_slots_and_no_us_rate_for_india(self):
        us = replies.draft_text("info_request", "Maria Lopez", "Acme", "Salesforce Developer")
        self.assertTrue(us.startswith("Hi Maria,"))
        self.assertIn("C2C through my company Trysyntx", us)
        self.assertIn("9:30 AM - 12:30 PM US Eastern", us)
        ind = replies.draft_text("call_request", "Neha", "Codiot", "Salesforce Developer", india=True)
        self.assertNotIn("USD", ind)
        self.assertIn("+91 75664 42162", ind)

    def test_match_company(self):
        comps = [(1, "NeuraFlash, an Accenture company"), (2, "Acme Health Inc")]
        self.assertEqual(replies.match_company({"from_addr": "talent@neuraflash.com", "subject": "x", "text": ""}, comps), 1)
        self.assertEqual(replies.match_company({"from_addr": "no-reply@greenhouse-mail.io",
                                                "subject": "Interview with Acme Health", "text": ""}, comps), 2)
        self.assertIsNone(replies.match_company({"from_addr": "a@b.io", "subject": "Hello", "text": ""}, comps))


class FollowupReplyDraftTests(TempData, unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("JOBHUNTER_TODAY")
        os.environ["JOBHUNTER_TODAY"] = "2026-10-14"
        super().setUp()

    def tearDown(self):
        super().tearDown()
        if self._old is None:
            os.environ.pop("JOBHUNTER_TODAY", None)
        else:
            os.environ["JOBHUNTER_TODAY"] = self._old

    def test_reply_gets_category_and_gmail_draft_never_sent(self):
        job = actions.save_job(self.conn, None, "posts", "posts", "Acme", "Salesforce Developer", jd_text="JD")["job_id"]
        cid = actions.save_contact(self.conn, None, "posts", job, "Maria Lopez", "Recruiter", "maria@acme.io", "post")["contact_id"]
        self.assertTrue(actions.send_application(self.conn, None, "posts", job, cid, "Salesforce Developer - Akshay",
                                                 "Hi Maria,\n\nbody\n\nThanks,\nAkshay Verma", RESUME, self.cfg)["ok"])
        outbox_before = len(list((self.data / "outbox").glob("*.eml")))
        write_inbox(self.data, "Maria Lopez <maria@acme.io>", "Re: Salesforce Developer - Akshay",
                    "Hi Akshay, can we have a quick call? What's your availability this week?")
        out, emit = self.emitted()
        counts = followups.run_followups(self.conn, None, self.cfg, emit, None, today=date(2026, 10, 14))
        r = self.conn.execute("SELECT category, draft FROM replies").fetchone()
        self.assertEqual((r["category"], r["draft"]), ("call_request", 1))
        self.assertEqual(len(list((self.data / "drafts").glob("*.eml"))), 1)
        self.assertEqual(len(list((self.data / "outbox").glob("*.eml"))), outbox_before)   # nothing was sent
        ev = [e for e in out if e["type"] == "reply_found"][0]
        self.assertEqual(ev["data"]["category"], "call_request")
        self.assertEqual(counts["replies"], 1)

    def test_inbound_mail_about_a_portal_application(self):
        job = actions.save_job(self.conn, None, "board_apply", "careers", "NeuraFlash", "Salesforce Developer")["job_id"]
        self.conn.execute("INSERT INTO portal_apps (job_id, ats, status, created_at, updated_at) VALUES (?, 'greenhouse',"
                          " 'submitted', ?, ?)", (job, db.now_iso(), db.now_iso()))
        write_inbox(self.data, "Priya <priya@neuraflash.com>", "Your application - Salesforce Developer",
                    "Hi Akshay, we'd like to schedule an interview. Please book a slot.")
        write_inbox(self.data, "no-reply@greenhouse-mail.io", "Thank you for applying to NeuraFlash",
                    "We have received your application.")
        write_inbox(self.data, "jobs-noreply@linkedin.com", "Your application was viewed", "NeuraFlash viewed it")
        out, emit = self.emitted()
        counts = followups.run_followups(self.conn, None, self.cfg, emit, None, today=date(2026, 10, 14))
        rows = self.conn.execute("SELECT job_id, kind, category, draft FROM replies").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(job, "inbound", "interview", 1)])
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0], "replied")
        self.assertEqual(counts.get("inbound"), 1)
        # running again doesn't duplicate
        followups.run_followups(self.conn, None, self.cfg, emit, None, today=date(2026, 10, 14))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
