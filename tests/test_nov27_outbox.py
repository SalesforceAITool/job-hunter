"""27 Nov (Akshay: "solve all those bounces ... agency apne aap, follow up ... improve daily"):
* Gmail refused everything for a day after a 90-second burst ("550 5.4.5 Daily user sending limit exceeded");
* Gmail's own "limit" notices were stored as dead addresses;
* SalesQL guesses bounced 29%;
* follow-ups and agency pitches never ran by themselves in LinkedIn focus mode;
* the tailored resume never uploaded (Read denied for the engine).
"""
import smtplib
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from test_backend_helpers import RESUME, TempData
from jh import actions, config, db, engine, followups, mailer, outbox, runner, selfupdate, sendguard, watch

A1, A2 = "jobs.one@example.com", "jobs.two@example.com"


def smtp_cfg(base, **email):
    em = {"backend": "smtp", "address": A1, "app_password": "a" * 16, "from_name": "Akshay Verma",
          "accounts": [{"address": A2, "app_password": "b" * 16}], "verify_recipients": False}
    em.update(email)
    return dict(base, email=em)


class SendGuardTests(TempData, unittest.TestCase):
    def test_accounts_and_pick_spreads_and_spaces(self):
        cfg = smtp_cfg(self.cfg)
        self.assertEqual([a["address"] for a in sendguard.accounts(cfg)], [A1, A2])
        now = time.time()
        a, why = sendguard.pick(self.conn, cfg, now=now)
        self.assertIsNotNone(a, why)
        sendguard.mark_sent(self.conn, a["address"], now)
        b, why = sendguard.pick(self.conn, cfg, now=now + 10)
        self.assertIsNone(b)                                   # min gap between any two sends
        self.assertIn("spacing", why)
        b, _ = sendguard.pick(self.conn, cfg, now=now + 100)
        self.assertNotEqual(b["address"], a["address"])        # the other account (the first one sent a moment ago)

    def test_limit_pauses_the_account_for_a_day(self):
        cfg = smtp_cfg(self.cfg)
        err = smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded.")
        self.assertEqual(sendguard.on_send_error(self.conn, cfg, A1, "SMTP send failed: %s" % err), "limit")
        a, _ = sendguard.pick(self.conn, cfg, prefer=A1, now=time.time() + 3600)
        self.assertIsNone(a)
        a, _ = sendguard.pick(self.conn, cfg, prefer=A1, now=time.time() + 25 * 3600)
        self.assertEqual(a["address"], A1)
        self.assertEqual(sendguard.on_send_error(self.conn, cfg, A2, "535 5.7.8 Username and Password not accepted"),
                         "auth")
        self.assertEqual(sendguard.on_send_error(self.conn, cfg, A2, "550 5.1.1 The email account does not exist"), "dead")

    def test_daily_cap_and_warmup(self):
        cfg = smtp_cfg(self.cfg, warmup_daily=2, accounts=[])
        acct = sendguard.accounts(cfg)[0]
        self.assertEqual(sendguard.daily_cap(self.conn, cfg, acct), 2)          # young account
        for i in range(2):
            self.conn.execute("INSERT INTO emails (kind, status, sent_at, sender) VALUES ('initial','sent',?,?)",
                              (db.now_iso(), A1))
        a, why = sendguard.pick(self.conn, cfg, ignore_gap=True)
        self.assertIsNone(a)
        self.assertIn("daily cap", why)

    def test_daily_cap_is_a_rolling_24h_window(self):
        # 28 Nov: 12 sends in the evening, then midnight reset the count and 11 more went out -> Gmail 5.4.5
        from datetime import timedelta
        cfg = smtp_cfg(self.cfg, warmup_daily=3, per_account_hourly=50, accounts=[])
        for h in (5, 4, 3):     # yesterday evening (hours ago), possibly another calendar date
            self.conn.execute("INSERT INTO emails (kind, status, sent_at, sender) VALUES ('initial','sent',?,?)",
                              ((db.now() - timedelta(hours=h)).replace(microsecond=0).isoformat(), A1))
        self.conn.execute("INSERT INTO emails (kind, status, sent_at, sender) VALUES ('initial','sent',?,?)",
                          ((db.now() - timedelta(hours=30)).replace(microsecond=0).isoformat(), A1))
        self.assertEqual(sendguard.sent_today(self.conn, A1), 3)      # 30 h ago does not count
        a, why = sendguard.pick(self.conn, cfg, ignore_gap=True)
        self.assertIsNone(a)
        self.assertIn("daily cap", why)

    def test_hourly_cap_spans_midnight(self):
        from datetime import timedelta
        for m in (50, 40, 30):
            self.conn.execute("INSERT INTO emails (kind, status, sent_at, sender) VALUES ('initial','sent',?,?)",
                              ((db.now() - timedelta(minutes=m)).replace(microsecond=0).isoformat(), A1))
        self.assertEqual(sendguard.sent_last_hour(self.conn, A1), 3)

    def test_recipient_checks(self):
        cfg = smtp_cfg(self.cfg, verify_recipients=True)
        ok, why = sendguard.check_recipient(self.conn, cfg, A2, "post")
        self.assertFalse(ok)                                  # his own address
        dns = {"MX": {"Status": 3}, "A": {"Status": 3}}
        fetch = lambda url: dns["MX" if "type=MX" in url else "A"]          # noqa: E731
        ok, why = sendguard.check_recipient(self.conn, cfg, "hr@nowhere-domain.example", "post", fetch=fetch)
        self.assertFalse(ok)
        self.assertIn("no mail server", why)
        good = lambda url: {"Status": 0, "Answer": [{"type": 15}]}          # noqa: E731
        self.assertTrue(sendguard.check_recipient(self.conn, cfg, "hr@acme.example", "post", fetch=good)[0])
        # a SalesQL guess: only when a verifier says it exists
        ok, why = sendguard.check_recipient(self.conn, cfg, "ann@acme2.example", "SalesQL", fetch=good, verify=lambda c, a: None)
        self.assertFalse(ok)
        self.assertIn("unverified", why)
        self.assertTrue(sendguard.check_recipient(self.conn, cfg, "bob@acme2.example", "SalesQL", fetch=good,
                                                  verify=lambda c, a: "valid")[0])
        ok, why = sendguard.check_recipient(self.conn, cfg, "cy@acme2.example", "SalesQL", fetch=good,
                                            verify=lambda c, a: "invalid")
        self.assertFalse(ok)
        self.assertFalse(sendguard.check_recipient(self.conn, cfg, "cy@acme2.example", "post", fetch=good)[0])  # dead now

    def test_limit_notice_bursts_are_not_dead_addresses(self):
        j = actions.save_job(self.conn, 1, "posts", "posts", "Acme", "Salesforce Developer")["job_id"]
        ids = []
        for i in range(6):
            c = self.conn.execute("INSERT INTO contacts (job_id, email, created_at) VALUES (?,?,?)",
                                  (j, "p%d@acme.example" % i, db.now_iso())).lastrowid
            self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, status, sent_at) VALUES (?,?,'initial','sent',?)",
                              (j, c, "2026-11-20T10:00:00+05:30"))
            self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, status, sent_at) VALUES (?,?,'nudge1','bounced',?)",
                              (j, c, "2026-11-26T14:39:00+05:30"))
            self.conn.execute("UPDATE emails SET status='bounced' WHERE contact_id=? AND kind='initial'", (c,))
            self.conn.execute("INSERT INTO replies (contact_id, job_id, from_addr, received_at, kind) VALUES (?,?,"
                              "'mailer-daemon', ?, 'bounce')", (c, j, "2026-11-26T14:%02d:00+05:30" % (39 + i % 2)))
            self.conn.execute("UPDATE contacts SET stop_nudges=1 WHERE id=?", (c,))
            ids.append(c)
        self.assertEqual(sendguard.fix_limit_bounces(self.conn), 6)
        th = db.load_threads(self.conn, ids)
        self.assertTrue(all(t["app_status"] == "sent" for t in th.values()), [t["app_status"] for t in th.values()])
        self.assertEqual(sendguard.fix_limit_bounces(self.conn), 0)                   # once


class FakeBox:
    def __init__(self, cfg):
        self.addr = cfg["email"]["address"]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def find_bounces(self, days=7):
        return []

    def find_replies(self, addr, since):
        return []

    def recent_inbox(self, days=4):
        return []

    def find_sent_message(self, to, hint=None):
        return None


class OutboxTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(mailer, "mailbox", FakeBox)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = smtp_cfg(self.cfg)
        config.save(self.cfg)
        self.job = actions.save_job(self.conn, 1, "posts", "posts", "Acme Inc", "Salesforce Developer")["job_id"]
        self.cid = actions.save_contact(self.conn, 1, "posts", self.job, "Ann Lee", "Recruiter", "ann@acme.example",
                                        "post")["contact_id"]
        self.sent = []

    def send(self, cfg, to, subject, body, attach=None, in_reply_to=None, references=None, html=None):
        self.sent.append((cfg["email"]["address"], to, subject, attach, in_reply_to))
        return "<m%d@x>" % len(self.sent)

    def test_application_is_queued_then_sent_from_the_outbox(self):
        r = actions.send_application(self.conn, 1, "posts", self.job, self.cid, "Salesforce Developer", "Hi Ann",
                                     RESUME, self.cfg)
        self.assertTrue(r["ok"] and r["queued"], r)
        row = self.conn.execute("SELECT * FROM emails WHERE id=?", (r["email_id"],)).fetchone()
        self.assertEqual((row["status"], row["queued"]), ("sending", 1))
        self.assertTrue(Path(row["resume_path"]).is_file())
        # counts as contacted at once (no second email to the same person)
        r2 = actions.send_application(self.conn, 1, "posts", self.job, self.cid, "x", "y", RESUME, self.cfg)
        self.assertFalse(r2["ok"])
        res = outbox.tick(self.conn, self.cfg, send_fn=self.send)
        self.assertEqual(res, {"sent": r["email_id"]})
        row = self.conn.execute("SELECT * FROM emails WHERE id=?", (r["email_id"],)).fetchone()
        self.assertEqual((row["status"], row["queued"]), ("sent", 0))
        self.assertIn(row["sender"], (A1, A2))
        self.assertTrue(self.sent[0][3])                       # the tailored PDF is attached
        self.assertEqual(outbox.tick(self.conn, self.cfg, send_fn=self.send), {})

    def test_limit_waits_instead_of_failing(self):
        r = actions.send_application(self.conn, 1, "posts", self.job, self.cid, "s", "b", RESUME, self.cfg)

        def limit(cfg, *a, **k):
            raise mailer.MailError("SMTP send failed: (550, b'5.4.5 Daily user sending limit exceeded.')")
        res = outbox.tick(self.conn, self.cfg, send_fn=limit)
        self.assertIn("paused", res["waiting"])
        row = self.conn.execute("SELECT status, queued FROM emails WHERE id=?", (r["email_id"],)).fetchone()
        self.assertEqual((row["status"], row["queued"]), ("sending", 1))        # waits, not 'failed'
        paused = [a for a in (A1, A2) if sendguard.paused_until(self.conn, a) > time.time()]
        self.assertEqual(len(paused), 1)

    def test_dead_address_fails_and_stops_nudges(self):
        r = actions.send_application(self.conn, 1, "posts", self.job, self.cid, "s", "b", RESUME, self.cfg)

        def dead(cfg, *a, **k):
            raise mailer.MailError("SMTP send failed: {'ann@acme.example': (550, b'5.1.1 user unknown')}")
        self.assertEqual(outbox.tick(self.conn, self.cfg, send_fn=dead), {"failed": r["email_id"]})
        self.assertTrue(sendguard.bounced_before(self.conn, "ann@acme.example"))

    def test_nudge_goes_from_the_thread_account(self):
        self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, status, sent_at, sender, message_id)"
                          " VALUES (?,?, 'initial', 'Salesforce Developer', 'sent', ?, ?, '<orig@x>')",
                          (self.job, self.cid, "2026-11-20T10:00:00+05:30", A2))
        counts = followups.run_followups(self.conn, 1, self.cfg, emit_fn=lambda *a, **k: None, today=db.today_ist())
        self.assertEqual(counts.get("nudges_queued"), 1, counts)
        res = outbox.tick(self.conn, self.cfg, send_fn=self.send)
        self.assertIn("sent", res)
        self.assertEqual(self.sent[-1][0], A2)                  # same account as the first email
        self.assertEqual(self.sent[-1][4], "<orig@x>")          # threaded under it
        # nothing queued twice
        followups.run_followups(self.conn, 1, self.cfg, emit_fn=lambda *a, **k: None, today=db.today_ist())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM emails WHERE kind='nudge1'").fetchone()[0], 1)

    def test_threads_from_an_unreadable_account_wait(self):
        self.cfg["email"]["legacy_address"] = "old.inbox@example.com"
        self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, status, sent_at, message_id)"
                          " VALUES (?,?, 'initial', 'Salesforce Developer', 'sent', ?, '<o@x>')",
                          (self.job, self.cid, "2026-10-20T10:00:00+05:30"))
        out = []
        counts = followups.run_followups(self.conn, 1, self.cfg, emit_fn=lambda *a, **k: out.append(a),
                                         today=db.today_ist())
        self.assertEqual(counts["nudges_skipped"], 1)
        self.assertTrue(any("old.inbox@example.com" in str(a) for a in out))


class LimitBurstTests(TempData, unittest.TestCase):
    def test_a_burst_of_failures_is_gmail_limit_not_dead_addresses(self):
        cfg = smtp_cfg(self.cfg)
        job = actions.save_job(self.conn, 1, "posts", "posts", "Acme Inc", "Salesforce Developer")["job_id"]
        stamps = ["2026-11-26T14:39:%02d+05:30" % (10 * i) for i in range(6)]
        bounces = [{"failed_addr": "p%d@acme.example" % i, "subject": "Delivery Status Notification (Failure)",
                    "date": ts, "limit": False} for i, ts in enumerate(stamps)]
        bounces.append({"failed_addr": "solo@acme.example", "subject": "Undeliverable", "date": "2026-11-20T10:00:00+05:30",
                        "limit": False})
        self.assertEqual(followups._limit_bursts(bounces), set(range(6)))
        for b in bounces:
            c = self.conn.execute("INSERT INTO contacts (job_id, email, created_at) VALUES (?,?,?)",
                                  (job, b["failed_addr"], db.now_iso())).lastrowid
            self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, status, sent_at, sender) VALUES"
                              " (?,?,'initial','sent','2026-11-20T09:00:00+05:30',?)", (job, c, A1))

        class Box(FakeBox):
            def find_bounces(self, days=7):
                return bounces
        with mock.patch.object(mailer, "mailbox", Box):
            counts = followups.run_followups(self.conn, 1, cfg, emit_fn=lambda *a, **k: None, today=db.today_ist())
        self.assertEqual(counts["limit_notices"], 6)
        self.assertEqual(counts["bounces"], 1)                                  # only the lone one is a real bounce
        self.assertFalse(sendguard.bounced_before(self.conn, "p0@acme.example"))
        self.assertTrue(sendguard.bounced_before(self.conn, "solo@acme.example"))
        # an old notice pauses nothing now
        self.assertEqual(sendguard.paused_until(self.conn, A1), 0)


class AutoLanesTests(TempData, unittest.TestCase):
    def test_followups_and_agencies_by_themselves_in_focus_mode(self):
        cfg = config.load()
        cfg["focus_linkedin"] = True
        cfg["li_fast"] = dict(cfg["li_fast"], on=False)
        cfg["agencies"] = dict(cfg["agencies"], daily_cap=15, every_min=120, start_hour=13, end_hour=24)
        cfg["email"]["backend"] = "file"
        config.save(cfg)
        calls = []
        w = watch.Watcher(None, start_run=lambda trig, **k: calls.append((trig, k["lanes"])) or len(calls))
        orig = runner.is_running
        runner.is_running = lambda: False
        try:
            now = time.time()
            w.auto_lanes_tick(self.conn, now, local_hour=10)
            self.assertEqual(calls, [("auto", ["followups"])])
            self.assertIsNone(w.auto_lanes_tick(self.conn, now + 60, local_hour=10))       # agencies: outside hours
            w.auto_lanes_tick(self.conn, now + 120, local_hour=14)
            self.assertEqual(calls[-1], ("auto", ["agencies"]))
            self.assertIsNone(w.auto_lanes_tick(self.conn, now + 600, local_hour=15))      # every 2 h
        finally:
            runner.is_running = orig


class EngineReadTests(TempData, unittest.TestCase):
    def test_resume_folder_readable_secrets_denied_and_empty_cwd(self):
        d = engine.engine_cwd(self.data)
        self.assertEqual(d.name, "engine_cwd")
        self.assertEqual(list(d.iterdir()), [])
        rule = engine._abs_rule(Path("C:\\Users\\X\\data\\resumes"))
        self.assertEqual(rule, "//C:/Users/X/data/resumes")


class SelfUpdateTests(unittest.TestCase):
    def test_pending_and_imports(self):
        import tempfile
        root = Path(tempfile.mkdtemp())
        (root / "jh").mkdir()
        (root / "jh" / "__init__.py").write_text('BUILD = "2099-01-01.1"\n', encoding="utf-8")
        self.assertEqual(selfupdate.pending(root), "2099-01-01.1")
        ok, err = selfupdate.imports_ok(sys.executable, root)
        self.assertFalse(ok)                                   # a half-copied build is never started
        here = Path(__file__).resolve().parents[1]
        self.assertIsNone(selfupdate.pending(here))
        self.assertTrue(selfupdate.imports_ok(sys.executable, here)[0])


if __name__ == "__main__":
    unittest.main()
