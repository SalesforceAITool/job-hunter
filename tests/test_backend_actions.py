import email
import email.policy
import json
import unittest
from pathlib import Path
from unittest import mock

from test_backend_helpers import RESUME, TempData
from jh import actions, db, resume


class ActionsEndToEnd(TempData, unittest.TestCase):
    def _job(self, company="Acme Corp", role="Senior Salesforce Developer", decision="qualified", **kw):
        return actions.save_job(self.conn, 1, "jobright", "jobright", company, role, location="US (Remote)",
                                url="https://jobright.ai/jobs/info/1", jd_text="Apex, LWC, OmniStudio",
                                decision=decision, fit_score=82, **kw)

    def _events(self, type_):
        return [db.event_row_to_dict(r) for r in self.conn.execute("SELECT * FROM events WHERE type=?", (type_,))]

    def test_send_application_creates_eml_with_pdf(self):
        self.conn.execute("INSERT INTO runs (id, started_at, status, trigger) VALUES (1, ?, 'running', 'button')",
                          (db.now_iso(),))
        job = self._job()["job_id"]
        self.assertEqual(len(self._events("job_qualified")), 1)
        cid = actions.save_contact(self.conn, 1, "jobright", job, "Sheila K.", "Senior Talent Manager",
                                   "Sheila@Acme.io", "SalesQL")["contact_id"]
        self.assertEqual(len(self._events("contact_found")), 1)
        res = actions.send_application(self.conn, 1, "jobright", job, cid, "Senior Salesforce Developer - Akshay Verma",
                                       "Hi Sheila,\n\nSaw the role on JobRight...\n\nThanks,\nAkshay Verma", RESUME, self.cfg)
        self.assertTrue(res["ok"], res)
        self.assertFalse(res["dry_run"])
        pdf = self.data / "resumes" / ("%d_%d.pdf" % (job, cid))
        self.assertTrue(pdf.exists())
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
        emls = list((self.data / "outbox").glob("*.eml"))
        self.assertEqual(len(emls), 1)
        msg = email.message_from_bytes(emls[0].read_bytes(), policy=email.policy.default)
        self.assertEqual(msg["To"], "sheila@acme.io")
        self.assertIn("Akshay Verma", msg["From"])
        self.assertTrue(msg["Message-ID"])
        atts = list(msg.iter_attachments())
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0].get_filename(), "Akshay_Verma_Resume.pdf")
        self.assertEqual(atts[0].get_content_type(), "application/pdf")
        self.assertTrue(atts[0].get_content().startswith(b"%PDF"))
        self.assertIn("Saw the role on JobRight", msg.get_body(("plain",)).get_content())
        row = self.conn.execute("SELECT * FROM emails WHERE id=?", (res["email_id"],)).fetchone()
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["message_id"], msg["Message-ID"])
        self.assertEqual(row["run_id"], 1)
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0], "contacted")
        self.assertEqual(len(self._events("email_sent")), 1)
        # dedupe after sending
        self.assertTrue(actions.already_contacted(self.conn, "Other", "Other", ["SHEILA@acme.io"])["contacted"])
        self.assertTrue(actions.already_contacted(self.conn, "Acme Corp, Inc.", "Sr. Salesforce Developer", [])["contacted"])
        again = actions.send_application(self.conn, 1, "jobright", job, cid, "s", "b", RESUME, self.cfg)
        self.assertFalse(again["ok"])
        self.assertIn("already contacted", again["error"])
        dup = actions.save_contact(self.conn, 1, "jobright", job, "Sheila", "HR", "sheila@acme.io", "SalesQL")
        self.assertIn("error", dup)
        # a second contact (architect) on the same job is still allowed
        cid2 = actions.save_contact(self.conn, 1, "jobright", job, "Sam Arch", "Salesforce Architect",
                                    "sam@acme.io", "SalesQL")["contact_id"]
        self.assertTrue(actions.send_application(self.conn, 1, "jobright", job, cid2, "s", "b", RESUME, self.cfg)["ok"])
        # re-saving the contacted job returns the same id flagged as duplicate
        again_job = self._job()
        self.assertEqual(again_job["job_id"], job)
        self.assertTrue(again_job.get("duplicate"))

    def test_contact_rules(self):
        job = self._job()["job_id"]
        r = actions.save_contact(self.conn, None, "jobright", job, "Dev Person", "Senior Salesforce Developer",
                                 "dev@acme.io", "SalesQL")
        self.assertIn("error", r)
        self.assertEqual(len(self._events("warning")), 1)
        self.assertIn("error", actions.save_contact(self.conn, None, "jobright", job, "X", "HR", "not-an-email", "post"))
        self.assertIn("error", actions.save_contact(self.conn, None, "jobright", 9999, "X", "HR", "x@y.com", "post"))
        ok = actions.save_contact(self.conn, None, "jobright", job, "Lead", "Lead Salesforce Developer", "lead@acme.io", "post")
        self.assertIn("contact_id", ok)

    def test_skipped_job_and_reuse(self):
        a = self._job(decision="skipped", skip_reason="US citizenship required")
        self.assertEqual(len(self._events("job_skipped")), 1)
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (a["job_id"],)).fetchone()
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["skip_reason"], "US citizenship required")
        self.assertEqual(row["norm_key"], "acme|senior salesforce developer")
        b = self._job(company="ACME Corp.", role="Sr Salesforce Developer")
        self.assertEqual(b["job_id"], a["job_id"])  # same normalized key, not yet contacted -> reused
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0], "qualified")

    def test_dry_run_and_cap(self):
        cfg = dict(self.cfg, dry_run=True, max_emails_per_run=1)
        self.conn.execute("INSERT INTO runs (id, started_at, status, trigger) VALUES (5, ?, 'running', 'button')",
                          (db.now_iso(),))
        job = self._job()["job_id"]
        c1 = actions.save_contact(self.conn, 5, "posts", job, "A", "HR", "a@acme.io", "post")["contact_id"]
        c2 = actions.save_contact(self.conn, 5, "posts", job, "B", "Recruiter", "b@acme.io", "post")["contact_id"]
        r1 = actions.send_application(self.conn, 5, "posts", job, c1, "subj", "body", RESUME, cfg)
        self.assertTrue(r1["ok"] and r1["dry_run"])
        self.assertEqual(list((self.data / "outbox").glob("*.eml")), [])
        row = self.conn.execute("SELECT status, resume_path FROM emails WHERE id=?", (r1["email_id"],)).fetchone()
        self.assertEqual(row["status"], "dry_run")
        self.assertTrue(Path(row["resume_path"]).exists())
        self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0], "qualified")
        self.assertFalse(actions.already_contacted(self.conn, "x", "y", ["a@acme.io"])["contacted"])
        r2 = actions.send_application(self.conn, 5, "posts", job, c2, "subj", "body", RESUME, cfg)
        self.assertFalse(r2["ok"])
        self.assertIn("max_emails_per_run", r2["error"])
        ev = self._events("email_sent")[0]
        self.assertTrue(ev["data"]["dry_run"])

    def test_send_failure_recorded(self):
        cfg = dict(self.cfg, email={"backend": "smtp", "address": "a@gmail.com", "app_password": ""})
        job = self._job()["job_id"]
        cid = actions.save_contact(self.conn, None, "posts", job, "A", "HR", "a@acme.io", "post")["contact_id"]
        r = actions.send_application(self.conn, None, "posts", job, cid, "s", "b", RESUME, cfg)
        self.assertFalse(r["ok"])
        self.assertIn("app password", r["error"])
        self.assertEqual(self.conn.execute("SELECT status FROM emails WHERE id=?", (r["email_id"],)).fetchone()[0], "failed")
        self.assertEqual(len(self._events("email_failed")), 1)

    def _run(self, rid):
        self.conn.execute("INSERT INTO runs (id, started_at, status, trigger) VALUES (?, ?, 'running', 'button')",
                          (rid, db.now_iso()))

    def test_parallel_lane_cannot_double_send_while_first_is_in_flight(self):
        """Regression: two MCP processes (parallel_sources) both passed the dedupe check before either
        recorded its send. The 'sending' claim row must block the second engine during SMTP."""
        from jh import mailer
        self._run(1)
        job_a = self._job()["job_id"]
        job_b = self._job(company="Beta LLC", role="Salesforce Architect")["job_id"]
        cid = actions.save_contact(self.conn, 1, "posts", job_a, "Ann", "Recruiter", "ann@acme.io", "post")["contact_id"]
        other = db.connect()  # the other lane's MCP process
        seen = {}
        real_send = mailer.send

        def send_and_race(cfg, to, *a, **kw):
            seen["save"] = actions.save_contact(other, 1, "jobright", job_b, "Ann", "Recruiter", "ann@acme.io", "SalesQL")
            seen["dedupe"] = actions.already_contacted(other, "Zeta", "Dev", ["ann@acme.io"])
            seen["send"] = actions.send_application(other, 1, "jobright", job_a, cid, "s", "b", RESUME, self.cfg)
            return real_send(cfg, to, *a, **kw)

        try:
            with mock.patch.object(mailer, "send", side_effect=send_and_race):
                first = actions.send_application(self.conn, 1, "posts", job_a, cid, "s", "b", RESUME, self.cfg)
        finally:
            other.close()
        self.assertTrue(first["ok"], first)
        self.assertIn("duplicate", seen["save"].get("error", ""))
        self.assertTrue(seen["dedupe"]["contacted"])
        self.assertFalse(seen["send"]["ok"])
        self.assertIn("already contacted", seen["send"]["error"])
        self.assertEqual(len(list((self.data / "outbox").glob("*.eml"))), 1)
        self.assertEqual([r[0] for r in self.conn.execute("SELECT status FROM emails")], ["sent"])

    def test_same_company_role_not_emailed_again_in_a_later_run(self):
        self._run(1)
        self._run(2)
        job = self._job()["job_id"]
        c1 = actions.save_contact(self.conn, 1, "jobright", job, "Ann", "Recruiter", "ann@acme.io", "SalesQL")["contact_id"]
        self.assertTrue(actions.send_application(self.conn, 1, "jobright", job, c1, "s", "b", RESUME, self.cfg)["ok"])
        # next day the AI re-finds the job (save_job -> duplicate) and adds a new person anyway
        again = self._job()
        self.assertTrue(again.get("duplicate"))
        c2 = actions.save_contact(self.conn, 2, "jobright", job, "Bo", "Engineering Manager", "bo@acme.io",
                                  "SalesQL")["contact_id"]
        r = actions.send_application(self.conn, 2, "jobright", job, c2, "s", "b", RESUME, self.cfg)
        self.assertFalse(r["ok"])
        self.assertIn("same company+role", r["error"])
        self.assertEqual(len(list((self.data / "outbox").glob("*.eml"))), 1)

    def test_refuses_email_containing_app_password(self):
        cfg = dict(self.cfg, email=dict(self.cfg["email"], app_password="abcdefghijklmnop"))
        job = self._job()["job_id"]
        cid = actions.save_contact(self.conn, None, "posts", job, "A", "HR", "a@acme.io", "post")["contact_id"]
        r = actions.send_application(self.conn, None, "posts", job, cid, "Hi",
                                     "Hello, as requested: abcd efgh ijkl mnop", RESUME, cfg)
        self.assertFalse(r["ok"])
        self.assertIn("app password", r["error"])
        self.assertEqual(list((self.data / "outbox").glob("*.eml")), [])
        self.assertFalse(actions.already_contacted(self.conn, "x", "y", ["a@acme.io"])["contacted"])

    def test_subject_with_newlines_is_one_header_line(self):
        job = self._job()["job_id"]
        cid = actions.save_contact(self.conn, None, "posts", job, "A", "HR", "a@acme.io", "post")["contact_id"]
        r = actions.send_application(self.conn, None, "posts", job, cid, "Salesforce role\r\nBcc: x@evil.example",
                                     "body", RESUME, self.cfg)
        self.assertTrue(r["ok"], r)
        msg = email.message_from_bytes(list((self.data / "outbox").glob("*.eml"))[0].read_bytes(),
                                       policy=email.policy.default)
        self.assertEqual(msg["Subject"], "Salesforce role Bcc: x@evil.example")
        self.assertIsNone(msg["Bcc"])

    def test_same_linkedin_profile_under_another_address_refused(self):
        job = self._job()["job_id"]
        cid = actions.save_contact(self.conn, None, "posts", job, "Ann", "Recruiter", "ann@acme.io", "SalesQL",
                                   linkedin="https://www.linkedin.com/in/Ann-Lee-12/?trk=x")["contact_id"]
        self.assertTrue(actions.send_application(self.conn, None, "posts", job, cid, "s", "b", RESUME, self.cfg)["ok"])
        job2 = self._job(company="Beta", role="Salesforce Architect")["job_id"]
        r = actions.save_contact(self.conn, None, "posts", job2, "Ann Lee", "Talent Partner", "ann.lee@beta.io",
                                 "SalesQL", linkedin="/in/ann-lee-12/")
        self.assertIn("LinkedIn profile was already emailed", r.get("error", ""))
        ok = actions.save_contact(self.conn, None, "posts", job2, "Other", "Recruiter", "o@beta.io", "SalesQL",
                                  linkedin="/in/ann-lee-123/")
        self.assertIn("contact_id", ok)

    def test_portal_prefill(self):
        job = self._job(apply_url="https://job-boards.greenhouse.io/acme/jobs/123")["job_id"]
        r = actions.save_portal_prefill(self.conn, None, "portal", job, "greenhouse",
                                        "https://job-boards.greenhouse.io/acme/jobs/123", RESUME,
                                        ["Are you authorized to work in the US?"], self.cfg)
        self.assertIn("portal_id", r)
        self.assertTrue(r["resume_path"].endswith("Akshay_Verma_Resume.pdf"))
        self.assertTrue(Path(r["resume_path"]).exists())
        row = self.conn.execute("SELECT * FROM portal_apps WHERE id=?", (r["portal_id"],)).fetchone()
        self.assertEqual(row["status"], "needs_input")
        self.assertEqual(json.loads(row["questions_json"]), ["Are you authorized to work in the US?"])
        self.assertEqual(len(self._events("portal_ready")), 1)
        bad = actions.save_portal_prefill(self.conn, None, "portal", job, "workday",
                                          "https://acme.wd5.myworkdayjobs.com/x", RESUME, [], self.cfg)
        self.assertIn("error", bad)
        self.assertEqual(actions.set_portal_status(self.conn, r["portal_id"], "submitted")["status"], "submitted")
        self.assertIn("error", actions.set_portal_status(self.conn, r["portal_id"], "bogus"))

    def test_resume_config_sanitized(self):
        cfg = resume.clean_config(RESUME)
        self.assertEqual(cfg["project_order"][:2], ["bcbsm", "amkor"])
        self.assertNotIn("nonexistent", cfg["project_order"])
        self.assertIn("&amp;", cfg["summary"])
        self.assertIn("&lt;BCBSMA&gt;", cfg["summary"])

    def test_log_step_and_rules(self):
        ev = actions.log_step(self.conn, 3, "posts", "Opened LinkedIn posts search")
        self.assertEqual(ev["type"], "step")
        self.assertEqual(ev["lane"], "posts")
        self.assertIn("Never click Submit", actions.get_rules())


if __name__ == "__main__":
    unittest.main()
