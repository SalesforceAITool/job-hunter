import email
import os
import email.policy
import unittest
from datetime import date

from test_backend_helpers import RESUME, TempData, write_inbox
from jh import actions, db, followups, importer

FRI, SAT, SUN, MON, TUE, WED, THU = (date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19),
                                     date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22))


class ScheduleTests(unittest.TestCase):
    def test_weekdays_sanity(self):
        self.assertEqual(FRI.weekday(), 4)
        self.assertEqual(MON.weekday(), 0)

    def test_nudge1_one_business_day(self):
        self.assertEqual(followups.nudge_due(MON), ("nudge1", TUE))
        self.assertEqual(followups.nudge_due(THU), ("nudge1", date(2026, 10, 23)))
        # Friday -> Monday; weekend sends are read on Monday, so nudge1 goes Tuesday
        self.assertEqual(followups.nudge_due(FRI), ("nudge1", MON))
        self.assertEqual(followups.nudge_due(SAT), ("nudge1", TUE))
        self.assertEqual(followups.nudge_due(SUN), ("nudge1", TUE))

    def test_nudge2_three_business_days_after_nudge1(self):
        self.assertEqual(followups.nudge_due(date(2026, 10, 7), date(2026, 10, 8)), ("nudge2", date(2026, 10, 13)))
        self.assertEqual(followups.nudge_due(THU, FRI), ("nudge2", WED))          # Fri -> Wed
        self.assertEqual(followups.nudge_due(FRI, MON), ("nudge2", THU))          # Mon -> Thu
        self.assertEqual(followups.nudge_due(MON, TUE), ("nudge2", date(2026, 10, 23)))

    def test_sequence_done(self):
        self.assertEqual(followups.nudge_due(MON, TUE, WED), (None, None))

    def test_business_days_between(self):
        self.assertEqual(followups.business_days_between(FRI, MON), 1)
        self.assertEqual(followups.business_days_between(FRI, SUN), 0)
        self.assertEqual(followups.business_days_between(MON, THU), 3)

    def test_auto_reply_classification(self):
        self.assertTrue(followups.is_auto_reply({"subject": "Automatic reply: Salesforce Developer"}))
        self.assertTrue(followups.is_auto_reply({"subject": "Out of Office: back Monday"}))
        self.assertTrue(followups.is_auto_reply({"subject": "Re: role", "auto_submitted": "auto-replied"}))
        self.assertFalse(followups.is_auto_reply({"subject": "Re: role", "auto_submitted": "no"}))
        self.assertFalse(followups.is_auto_reply({"subject": "Re: Salesforce Developer role"}))

    def test_templates(self):
        self.assertGreaterEqual(len(followups.NUDGE1_TEMPLATES), 3)
        self.assertGreaterEqual(len(followups.NUDGE2_TEMPLATES), 3)
        t = followups.nudge_text("nudge1", 5, "Katie Martino", "Senior Salesforce Developer", "Tekion (LinkedIn)")
        self.assertTrue(t.startswith("Hi Katie,"))
        self.assertTrue(t.endswith("Thanks,\nAkshay Verma"))
        self.assertNotIn("(LinkedIn)", t)
        self.assertTrue(followups.nudge_text("nudge2", 1, "HR inbox", "Dev", "X").startswith("Hi there,"))
        texts = {followups.nudge_text("nudge1", i, "A B", "r", "c") for i in range(4)}
        self.assertEqual(len(texts), 4)  # rotates


class RunFollowupsTests(TempData, unittest.TestCase):
    """Runs on a fixed Wednesday so results don't depend on the weekday the suite runs on."""

    def setUp(self):
        self._old_today = os.environ.get("JOBHUNTER_TODAY")
        os.environ["JOBHUNTER_TODAY"] = "2026-10-14"
        super().setUp()

    def tearDown(self):
        super().tearDown()
        if self._old_today is None:
            os.environ.pop("JOBHUNTER_TODAY", None)
        else:
            os.environ["JOBHUNTER_TODAY"] = self._old_today

    def _sent_app(self, email_addr="maria.hr@acme.io", company="Acme", role="Salesforce Developer"):
        job = actions.save_job(self.conn, None, "jobright", "jobright", company, role, jd_text="JD")["job_id"]
        cid = actions.save_contact(self.conn, None, "jobright", job, "Maria Lopez", "Talent Acquisition Manager",
                                   email_addr, "SalesQL")["contact_id"]
        res = actions.send_application(self.conn, None, "jobright", job, cid, "Salesforce Developer role - Akshay Verma",
                                       "Hi Maria,\n\nbody\n\nThanks,\nAkshay Verma", RESUME, self.cfg)
        self.assertTrue(res["ok"], res)
        return job, cid

    def _run(self, today, cfg=None):
        out, emit = self.emitted()
        counts = followups.run_followups(self.conn, None, cfg or self.cfg, emit, None, today=today)
        return counts, out

    def _outbox(self):
        return sorted((self.data / "outbox").glob("*.eml"))

    def test_real_send_after_dry_run_is_tracked_and_followed_up(self):
        """Regression: thread_state took the first 'initial' row (the dry run), so a contact really emailed
        in a later run showed as dry_run, was never nudged and was missing from the KPIs."""
        job = actions.save_job(self.conn, None, "jobright", "jobright", "Acme", "Salesforce Developer")["job_id"]
        cid = actions.save_contact(self.conn, None, "jobright", job, "Maria Lopez", "Talent Acquisition Manager",
                                   "maria.hr@acme.io", "SalesQL")["contact_id"]
        for rid in (1, 2):
            self.conn.execute("INSERT INTO runs (id, started_at, status, trigger) VALUES (?, ?, 'done', 'button')",
                              (rid, db.now_iso()))
        dry = actions.send_application(self.conn, 1, "jobright", job, cid, "Role", "body", RESUME,
                                       dict(self.cfg, dry_run=True))
        self.assertTrue(dry["ok"] and dry["dry_run"])
        real = actions.send_application(self.conn, 2, "jobright", job, cid, "Role", "body", RESUME, self.cfg)
        self.assertTrue(real["ok"] and not real["dry_run"], real)
        t = db.load_threads(self.conn)[cid]
        self.assertEqual(t["app_status"], "sent")
        self.assertTrue(t["initial_sent"])
        self.assertEqual(t["initial"]["id"], real["email_id"])
        self.assertIsNotNone(t["next_followup"])
        s = db.summary(self.conn)
        self.assertEqual((s["sent_total"], s["awaiting"]), (1, 1))
        counts, _ = self._run(followups.add_business_days(db.today_ist(), 2))
        self.assertEqual(counts["nudges_sent"], 1)

    def test_precedence_auto_reply_is_auto(self):
        self.assertTrue(followups.is_auto_reply({"subject": "Re: role", "precedence": "auto_reply"}))
        self.assertFalse(followups.is_auto_reply({"subject": "Re: role", "precedence": ""}))

    def test_nudges_in_thread_then_stop(self):
        job, cid = self._sent_app()
        initial = self.conn.execute("SELECT * FROM emails WHERE contact_id=? AND kind='initial'", (cid,)).fetchone()
        today = db.today_ist()
        counts, _ = self._run(today)
        self.assertEqual(counts["nudges_sent"], 0)  # not due on the day of sending
        d1 = followups.add_business_days(today, 1)
        counts, evs = self._run(d1)
        self.assertEqual(counts["nudges_sent"], 1, evs)
        self.assertTrue(any(e["type"] == "nudge_sent" for e in evs))
        n1 = self.conn.execute("SELECT * FROM emails WHERE contact_id=? AND kind='nudge1'", (cid,)).fetchone()
        self.assertEqual(n1["in_reply_to"], initial["message_id"])
        self.assertEqual(n1["subject"], "Re: " + initial["subject"])
        msg = email.message_from_bytes(self._outbox()[-1].read_bytes(), policy=email.policy.default)
        self.assertEqual(msg["In-Reply-To"], initial["message_id"])
        self.assertEqual(msg["References"], initial["message_id"])
        self.assertFalse(any(p.get_filename() for p in msg.iter_attachments()))
        # same day again: nothing more
        self.assertEqual(self._run(d1)[0]["nudges_sent"], 0)
        # nudge2 is due 3 business days after nudge1 (nudge1 was really sent today)
        d2 = followups.add_business_days(today, 3)
        self.assertEqual(self._run(followups.add_business_days(today, 2))[0]["nudges_sent"], 0)
        self.assertEqual(self._run(d2)[0]["nudges_sent"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM emails WHERE contact_id=? AND kind='nudge2'",
                                           (cid,)).fetchone()[0], 1)
        # sequence done
        self.assertEqual(self._run(followups.add_business_days(today, 20))[0]["nudges_sent"], 0)
        self.assertEqual(db.load_threads(self.conn, [cid])[cid]["app_status"], "nudge2")

    def test_auto_reply_does_not_stop_but_real_reply_does(self):
        job, cid = self._sent_app()
        write_inbox(self.data, "Maria Lopez <maria.hr@acme.io>", "Automatic reply: Salesforce Developer role",
                    "I am out of the office until Monday.")
        counts, evs = self._run(followups.add_business_days(db.today_ist(), 1))
        self.assertEqual(counts["auto_replies"], 1)
        self.assertEqual(counts["nudges_sent"], 1)
        self.assertEqual(self.conn.execute("SELECT kind FROM replies WHERE contact_id=?", (cid,)).fetchone()[0], "auto")
        write_inbox(self.data, "maria.hr@acme.io", "Re: Salesforce Developer role - Akshay Verma",
                    "Hi Akshay, thanks - are you free for a call Tuesday?")
        counts, evs = self._run(followups.add_business_days(db.today_ist(), 5))
        self.assertEqual(counts["replies"], 1)
        self.assertEqual(counts["nudges_sent"], 0)
        self.assertTrue(any(e["type"] == "reply_found" for e in evs))
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0], "replied")
        th = db.load_threads(self.conn, [cid])[cid]
        self.assertEqual(th["app_status"], "replied")
        self.assertIsNone(th["next_followup"])
        # re-running does not duplicate replies
        self._run(followups.add_business_days(db.today_ist(), 6))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replies WHERE contact_id=?", (cid,)).fetchone()[0], 2)

    def test_bounce_marks_email_and_job(self):
        job, cid = self._sent_app("wrong@acme.io")
        write_inbox(self.data, "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                    "Delivery Status Notification (Failure)",
                    "Address not found\n\nYour message wasn't delivered to wrong@acme.io because the address "
                    "couldn't be found.", headers={"X-Failed-Recipients": "wrong@acme.io"})
        counts, evs = self._run(followups.add_business_days(db.today_ist(), 1))
        self.assertEqual(counts["bounces"], 1)
        self.assertEqual(counts["nudges_sent"], 0)
        self.assertTrue(any(e["type"] == "bounce_found" for e in evs))
        self.assertEqual(self.conn.execute("SELECT status FROM emails WHERE contact_id=?", (cid,)).fetchone()[0], "bounced")
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0], "bounced")
        self.assertEqual(db.load_threads(self.conn, [cid])[cid]["app_status"], "bounced")

    def test_bounce_address_parsed_from_body(self):
        from jh import mailer
        info = {"from_addr": "mailer-daemon@googlemail.com", "subject": "Undeliverable: hello", "x_failed_recipients": "",
                "text": "Final-Recipient: rfc822; hr@talentgigs.in\nAction: failed"}
        self.assertTrue(mailer.is_bounce(info))
        self.assertEqual(mailer.failed_address(info, "akshay.verma0031@gmail.com"), "hr@talentgigs.in")

    def test_dry_run_sends_no_nudges(self):
        self._sent_app()
        cfg = dict(self.cfg, dry_run=True)
        before = len(self._outbox())
        counts, evs = self._run(followups.add_business_days(db.today_ist(), 1), cfg)
        self.assertEqual(counts["nudges_sent"], 0)
        self.assertEqual(counts["nudges_due"], 1)
        self.assertEqual(len(self._outbox()), before)
        self.assertTrue(any("[dry run]" in e["message"] for e in evs))

    def test_gmail_not_configured_skips(self):
        self._sent_app()
        cfg = dict(self.cfg, email={"backend": "smtp", "address": "a@gmail.com", "app_password": ""})
        counts, evs = self._run(followups.add_business_days(db.today_ist(), 1), cfg)
        self.assertEqual(counts.get("skipped_reason"), "gmail not configured")
        self.assertEqual(counts["nudges_due"], 1)

    def test_imported_history(self):
        csv_text = ("date,company,role,contact,title,email,status,next_step\n"
                    "2026-10-06,Alpha,Salesforce Developer,Ann A,HR,ann@alpha.com,sent,nudge 1 sent 2026-10-08; nudge 2 due 2026-10-13\n"
                    "2026-10-06,Beta,Salesforce Developer,Bob B,HR,bob@beta.com,sent,nudge 2 sent 2026-10-13; sequence done (no reply)\n"
                    "2026-10-07,Gamma,Salesforce Developer,Cy C,HR,cy@gamma.com,sent,hold\n"
                    "2026-10-07,Delta,Salesforce Developer,Di D,Recruiter,di@delta.com,replied,replied 2026-10-09\n"
                    "2026-10-16,Eps,Salesforce Developer,Ed E,HR,ed@eps.com,sent,nudge 1 due 2026-10-19\n")
        importer.import_text(self.conn, csv_text)
        th = {t["email"]: t for t in db.load_threads(self.conn).values()}
        self.assertEqual(th["ann@alpha.com"]["next_followup"], date(2026, 10, 13))
        self.assertEqual(th["ann@alpha.com"]["next_kind"], "nudge2")
        self.assertIsNone(th["bob@beta.com"]["next_followup"])
        self.assertIsNone(th["cy@gamma.com"]["next_followup"])
        self.assertEqual(th["di@delta.com"]["app_status"], "replied")
        self.assertEqual(th["ed@eps.com"]["next_followup"], MON)  # Friday send -> Monday
        # imported rows have no Message-ID and nothing is in Sent Mail -> nudge skipped with a warning
        counts, evs = self._run(MON)
        self.assertEqual(counts["nudges_sent"], 0)
        self.assertEqual(counts["nudges_skipped"], 2)  # ann (nudge2) + ed (nudge1)
        self.assertTrue(any(e["type"] == "warning" and "Sent Mail" in e["message"] for e in evs))

    def test_imported_history_found_in_sent_mail(self):
        from jh import mailer
        importer.import_text(self.conn, "date,company,role,contact,title,email,status,next_step\n"
                                        "2026-10-16,Eps,Salesforce Developer,Ed E,HR,ed@eps.com,sent,nudge 1 due 2026-10-19\n")
        orig = mailer.send(self.cfg, "ed@eps.com", "Salesforce Developer at Eps - Akshay Verma", "hello")
        counts, evs = self._run(MON)
        self.assertEqual(counts["nudges_sent"], 1, evs)
        row = self.conn.execute("SELECT * FROM emails WHERE kind='nudge1'").fetchone()
        self.assertEqual(row["in_reply_to"], orig)
        self.assertEqual(row["subject"], "Re: Salesforce Developer at Eps - Akshay Verma")


if __name__ == "__main__":
    unittest.main()
