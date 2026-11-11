"""Akshay, 11 Nov: a daily email with what Job Hunter did (applications submitted end to end, recruiter emails)."""
import email
import email.policy
import time
import unittest
from datetime import datetime, timedelta

from test_backend_helpers import TempData
from jh import actions, config, db, report


def ist(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=db.IST)


class DailyReportTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        report._next_try["at"] = 0.0
        cfg = config.load()
        cfg["email"]["address"] = "apply@example.com"
        config.save(cfg)

    def outbox(self):
        out = []
        for f in sorted((self.data / "outbox").glob("*.eml")) if (self.data / "outbox").exists() else []:
            out.append(email.message_from_bytes(f.read_bytes(), policy=email.policy.default))
        return out

    def add_job(self, company, source="posts", url=None):
        return actions.save_job(self.conn, 1, "posts", source, company, "Salesforce Developer",
                                url=url or "https://www.linkedin.com/feed/update/urn:li:activity:7393%012d/" % abs(hash(company) % 10**12))["job_id"]

    def add_form(self, company, status, when, ats="linkedin_easy_apply", qs='[]'):
        j = self.add_job(company, "linkedin_india", "https://www.linkedin.com/jobs/view/44%08d/" % (abs(hash(company)) % 10**8))
        self.conn.execute("INSERT INTO portal_apps (job_id, ats, apply_url, status, questions_json, created_at, updated_at,"
                          " submitted_at, ready) VALUES (?,?,?,?,?,?,?,?,1)",
                          (j, ats, "https://www.linkedin.com/jobs/view/%d/" % j, status, qs, when, when,
                           when if status == "submitted" else None))

    def add_mail(self, company, when, kind="initial", status="sent", error=None, source="posts"):
        j = self.add_job(company, source)
        c = self.conn.execute("INSERT INTO contacts (job_id, name, email, created_at) VALUES (?,?,?,?)",
                              (j, "Recruiter", "hr@%s.com" % company.lower(), when)).lastrowid
        self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, status, error, sent_at) VALUES (?,?,?,?,?,?,?)",
                          (j, c, kind, "Application", status, error, when))

    def fill_day(self):
        self.add_form("Alpha", "submitted", "2026-11-11T10:00:00+05:30")
        self.add_form("Beta", "submitted", "2026-11-11T15:30:00+05:30")
        self.add_form("Gamma", "submitted", "2026-11-11T12:00:00+05:30", ats="greenhouse")
        self.add_form("Delta", "submitted", "2026-11-10T22:00:00+05:30")            # before the window
        self.add_form("Eps", "needs_input", "2026-11-11T13:00:00+05:30", qs='["How many years of Vlocity?"]')
        self.add_mail("Posta", "2026-11-11T09:00:00+05:30")
        self.add_mail("Postb", "2026-11-11T11:00:00+05:30")
        self.add_mail("Jobc", "2026-11-11T11:30:00+05:30", source="linkedin_us")
        self.add_mail("Nudged", "2026-11-11T12:30:00+05:30", kind="nudge1")
        self.add_mail("Limited", "2026-11-11T14:00:00+05:30",
                      error="SMTP send failed: (550, b'5.4.5 Daily user sending limit exceeded.')")

    def test_counts_and_lists(self):
        self.fill_day()
        s = report.collect(self.conn, ist(2026, 11, 11, 0), ist(2026, 11, 11, 23))
        self.assertEqual([r["company"] for r in s["easy_apply"]], ["Alpha", "Beta"])
        self.assertEqual([r["company"] for r in s["portals"]], ["Gamma"])
        self.assertEqual(len(s["emails"]), 3)                                  # the Gmail-refused one is not counted
        self.assertEqual({k: len(v) for k, v in s["by_source"].items()},
                         {"LinkedIn hiring posts": 2, "LinkedIn job listings": 1})
        self.assertEqual(len(s["nudges"]), 1)
        self.assertEqual([m["why"] for m in s["not_sent"]], ["Gmail daily sending limit reached"])
        self.assertEqual([r["company"] for r in s["waiting"]], ["Eps"])
        subject, text, html = report.render(s)
        self.assertIn("3 applied (2 Easy Apply, 1 portal), 3 recruiter emails", subject)
        self.assertIn("hr@posta.com", text)
        self.assertIn("https://www.linkedin.com/feed/update/", text)            # the post link for each email
        self.assertIn("How many years of Vlocity?", text)
        self.assertIn("Since Job Hunter started: 3 Easy Apply submitted", text)
        self.assertIn("<table", html)

    def test_bounce_notices_are_not_replies(self):
        for frm in ("mailer-daemon@googlemail.com", "Priya <priya@acme.com>"):
            self.conn.execute("INSERT INTO replies (from_addr, subject, received_at) VALUES (?,?,?)",
                              (frm, "Re: Salesforce Developer", "2026-11-11T10:00:00+05:30"))
        s = report.collect(self.conn, ist(2026, 11, 11, 0), ist(2026, 11, 11, 23))
        self.assertEqual([r["from_addr"] for r in s["replies"]], ["Priya <priya@acme.com>"])

    def test_sent_once_a_day_at_the_set_hour(self):
        self.fill_day()
        cfg = config.load()
        # first time, before 11 PM: yesterday's slot has passed with no report yet -> today so far goes out
        res = report.tick(self.conn, cfg, now_dt=ist(2026, 11, 11, 18), now_s=1)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.outbox()[-1]["To"], "apply@example.com")
        self.assertIsNone(report.tick(self.conn, cfg, now_dt=ist(2026, 11, 11, 22, 59), now_s=2))
        res = report.tick(self.conn, cfg, now_dt=ist(2026, 11, 11, 23, 0), now_s=3)   # 11 PM: since 6 PM
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], 0)
        self.assertIsNone(report.tick(self.conn, cfg, now_dt=ist(2026, 11, 12, 9), now_s=4))
        self.assertEqual(len(self.outbox()), 2)

    def test_laptop_off_at_report_time_sends_when_back(self):
        cfg = config.load()
        self.conn.execute("INSERT INTO app_meta (k, v) VALUES (?, ?)", (report.META_KEY, "2026-11-10T23:00:00+05:30"))
        self.fill_day()
        res = report.tick(self.conn, cfg, now_dt=ist(2026, 11, 12, 8), now_s=1)       # missed 11 Nov 11 PM
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], 3)                   # whole gap covered: 10 Nov 11 PM -> 12 Nov 8 AM
        msg = self.outbox()[-1]
        self.assertIn("Easy Apply", msg["Subject"])
        self.assertEqual(msg.get_content_type(), "multipart/alternative")

    def test_settings_address_hour_off_and_dry_run(self):
        cfg = config.apply_update(config.load(), {"daily_report": {"to": " me@example.org ", "hour": "21"}})
        self.assertEqual(cfg["daily_report"], {"on": True, "hour": 21, "to": "me@example.org"})
        self.assertEqual(config.apply_update(cfg, {"daily_report": {"to": "not an email", "hour": 99}})["daily_report"],
                         {"on": True, "hour": 23, "to": ""})
        config.save(cfg)
        res = report.tick(self.conn, config.load(), now_dt=ist(2026, 11, 11, 21, 5), now_s=1)
        self.assertEqual(self.outbox()[-1]["To"], "me@example.org")
        off = config.apply_update(config.load(), {"daily_report": {"on": False}})
        self.conn.execute("DELETE FROM app_meta")
        self.assertIsNone(report.tick(self.conn, off, now_dt=ist(2026, 11, 12, 22), now_s=2))
        dry = dict(config.load(), dry_run=True)
        self.assertIsNone(report.tick(self.conn, dry, now_dt=ist(2026, 11, 12, 22), now_s=3))

    def test_failed_send_retries_later_and_keeps_the_window(self):
        cfg = config.load()
        cfg["email"]["backend"] = "smtp"
        cfg["email"]["app_password"] = ""
        res = report.tick(self.conn, cfg, now_dt=ist(2026, 11, 11, 23, 5), now_s=100)
        self.assertFalse(res["ok"])
        self.assertIsNone(report.tick(self.conn, cfg, now_dt=ist(2026, 11, 11, 23, 10), now_s=200))   # waits 30 min
        self.assertIsNone(self.conn.execute("SELECT v FROM app_meta WHERE k=?", (report.META_KEY,)).fetchone())
        res = report.tick(self.conn, config.load(), now_dt=ist(2026, 11, 11, 23, 40), now_s=100 + report.RETRY_S)
        self.assertTrue(res["ok"])

    def test_send_now_does_not_move_the_schedule(self):
        self.fill_day()
        res = report.send_now(self.conn)
        self.assertTrue(res["ok"], res)
        self.assertIn("today so far", res["subject"])
        self.assertIsNone(self.conn.execute("SELECT v FROM app_meta WHERE k=?", (report.META_KEY,)).fetchone())


if __name__ == "__main__":
    unittest.main()
