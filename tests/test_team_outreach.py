"""Team review (outreach + resume): caps, double sends, header injection, secrets, nudges, resume content."""
import email
import email.policy
import importlib
import json
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_backend_helpers import RESUME, TempData, write_inbox
from jh import actions, db, dedupe, followups, mailer, resume

ROOT = Path(__file__).resolve().parent.parent
RES_DIR = ROOT / "engine" / "resume"


def _pdftext(path):
    try:
        return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                              timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("pdftotext not available")


class _Base(TempData):
    def _run_row(self, rid):
        self.conn.execute("INSERT OR IGNORE INTO runs (id, started_at, status, trigger) VALUES (?, ?, 'done', 'button')",
                          (rid, db.now_iso()))

    def _job(self, company="Acme", role="Salesforce Developer", rid=None):
        return actions.save_job(self.conn, rid, "jobright", "jobright", company, role, jd_text="JD",
                                fit_score=80)["job_id"]

    def _contact(self, job, addr, title="Talent Acquisition Manager", linkedin=None, rid=None, source="SalesQL"):
        res = actions.save_contact(self.conn, rid, "jobright", job, "Pat Doe", title, addr, source, linkedin=linkedin)
        self.assertIn("contact_id", res, res)
        return res["contact_id"]

    def _send(self, job, cid, rid=None, cfg=None, resume_cfg=None):
        return actions.send_application(self.conn, rid, "jobright", job, cid, "Salesforce Developer - Akshay Verma",
                                        "Hi Pat,\n\nbody\n\nThanks,\nAkshay Verma", resume_cfg or RESUME, cfg or self.cfg)

    def _outbox(self):
        return sorted((self.data / "outbox").glob("*.eml"))


class CapsAndDoubleSends(_Base, unittest.TestCase):
    def test_daily_cap_holds_across_runs(self):
        """Watch mode starts a posts run every 30 min; each run got a fresh 25-email budget (no daily cap)."""
        cfg = dict(self.cfg, max_emails_per_run=2)
        sent = 0
        for rid in (1, 2, 3):
            self._run_row(rid)
            for i in range(2):
                job = self._job("Co%d%d" % (rid, i), rid=rid)
                cid = self._contact(job, "hr%d%d@co%d%d.io" % (rid, i, rid, i), rid=rid)
                sent += bool(self._send(job, cid, rid=rid, cfg=cfg)["ok"])
        self.assertLessEqual(sent, 2, "%d initial emails went out today with a cap of 2" % sent)

    def _smtp_fails_after_data(self):
        """An SMTP server that takes the DATA and then times out before the 250 reply (message may be delivered)."""
        calls = []

        class FakeSMTP:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, *a):
                pass

            def send_message(self, msg):
                calls.append(msg["To"])
                raise socket.timeout("timed out waiting for 250 after DATA")

        return calls, FakeSMTP

    def _smtp_cfg(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["email"].update(backend="smtp", address="akshay.verma0031@gmail.com", app_password="abcd efgh ijkl mnop")
        return cfg

    def test_ambiguous_smtp_failure_is_never_retried(self):
        calls, Fake = self._smtp_fails_after_data()
        cfg = self._smtp_cfg()
        self._run_row(1)
        self._run_row(2)
        job = self._job(rid=1)
        cid = self._contact(job, "maria@acme.io", rid=1)
        with mock.patch("smtplib.SMTP_SSL", Fake):
            first = self._send(job, cid, rid=1, cfg=cfg)
            self.assertFalse(first["ok"])
            # the AI (or a later run) tries again
            again_contact = actions.save_contact(self.conn, 2, "jobright", job, "Maria", "HR", "maria@acme.io", "SalesQL")
            second = self._send(job, cid, rid=2, cfg=cfg)
        self.assertEqual(len(calls), 1, "message handed to Gmail %d times for one person (%s / %s)"
                         % (len(calls), again_contact, second))

    def test_failed_attempts_count_toward_the_run_cap(self):
        calls, Fake = self._smtp_fails_after_data()
        cfg = dict(self._smtp_cfg(), max_emails_per_run=2)
        self._run_row(1)
        with mock.patch("smtplib.SMTP_SSL", Fake):
            for i in range(6):
                job = self._job("Fail%d" % i, rid=1)
                cid = self._contact(job, "hr@fail%d.io" % i, rid=1)
                self._send(job, cid, rid=1, cfg=cfg)
        self.assertLessEqual(len(calls), 2, "%d SMTP attempts in one run with cap 2" % len(calls))

    def test_same_linkedin_profile_not_emailed_twice_at_send_time(self):
        """Both contacts saved before either was emailed: save_contact's LinkedIn check had nothing to see."""
        li = "https://www.linkedin.com/in/jane-doe-123/"
        a_job = self._job("Alpha", "Salesforce Developer")
        b_job = self._job("Beta", "Salesforce Architect")
        a = self._contact(a_job, "jane@alpha.io", linkedin=li)
        b = self._contact(b_job, "jane.doe@beta.io", linkedin="/in/Jane-Doe-123")
        self.assertTrue(self._send(a_job, a)["ok"])
        second = self._send(b_job, b)
        self.assertFalse(second["ok"], "same person emailed twice: %s" % second)
        self.assertEqual(len(self._outbox()), 1)

    def test_secret_in_resume_is_refused(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["email"]["app_password"] = "abcd efgh ijkl mnop"
        job = self._job()
        cid = self._contact(job, "maria@acme.io")
        evil = dict(RESUME, summary="Salesforce developer. abcdefghijklmnop")
        res = self._send(job, cid, cfg=cfg, resume_cfg=evil)
        self.assertFalse(res["ok"], "the Gmail app password went out inside the resume PDF")

    def test_header_injection_in_subject_is_neutralised(self):
        job = self._job()
        cid = self._contact(job, "maria@acme.io")
        res = actions.send_application(self.conn, None, "jobright", job, cid,
                                       "Salesforce role\r\nBcc: evil@attacker.io X-Evil: 1", "Hi,\n\nbody",
                                       RESUME, self.cfg)
        self.assertTrue(res["ok"], res)
        msg = email.message_from_bytes(self._outbox()[0].read_bytes(), policy=email.policy.default)
        self.assertIsNone(msg["Bcc"])
        self.assertIsNone(msg["X-Evil"])
        self.assertEqual(msg["To"], "maria@acme.io")
        self.assertNotIn("\n", msg["Subject"])

    def test_dry_run_never_touches_smtp(self):
        cfg = dict(self._smtp_cfg(), dry_run=True)
        job = self._job()
        cid = self._contact(job, "maria@acme.io")
        with mock.patch("smtplib.SMTP_SSL", side_effect=AssertionError("SMTP used in dry run")):
            res = self._send(job, cid, cfg=cfg)
        self.assertTrue(res["ok"] and res["dry_run"], res)


class ContactRules(_Base, unittest.TestCase):
    def test_developer_titles_with_hr_words_are_refused(self):
        for title in ("Salesforce Developer - HR Systems", "QA Engineer, Talent Platform",
                      "Senior Software Engineer, People Team", "Salesforce Developer (Partner Success)"):
            ok, _ = actions.title_allowed(title)
            self.assertFalse(ok, "developer/QA title allowed: %r" % title)
        for title in ("Lead Salesforce Developer", "Engineering Manager", "Technical Recruiter",
                      "HR Business Partner", "Salesforce Architect", "Talent Acquisition Partner"):
            self.assertTrue(actions.title_allowed(title)[0], title)

    def test_malformed_emails_rejected(self):
        for bad in ("a..b@acme.io", ".a@acme.io", "a.@acme.io", "a@acme..io", "a@-acme.io", "a@acme.io\nBcc:x@y.io"):
            self.assertFalse(dedupe.valid_email(bad), bad)
        self.assertTrue(dedupe.valid_email("o'neil.j+jobs@sub.acme-corp.co.uk"))

    def test_free_mail_only_when_written_in_post(self):
        job = self._job()
        r = actions.save_contact(self.conn, None, "jobright", job, "Pat", "Recruiter", "pat.recruits@gmail.com", "SalesQL")
        self.assertIn("error", r, "a personal gmail from SalesQL was accepted as a work email")
        ok = actions.save_contact(self.conn, None, "jobright", job, "Pat", "Recruiter", "pat.recruits@gmail.com", "post")
        self.assertIn("contact_id", ok)


class Nudges(_Base, unittest.TestCase):
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

    def _run(self, today):
        out, emit = self.emitted()
        return followups.run_followups(self.conn, None, self.cfg, emit, None, today=today), out

    def test_no_nudge_to_colleague_after_someone_on_the_job_replied(self):
        self._run_row(1)
        job = self._job(rid=1)
        hr = self._contact(job, "maria@acme.io", rid=1)
        mgr = self._contact(job, "sam@acme.io", title="Engineering Manager", rid=1)
        self.assertTrue(self._send(job, hr, rid=1)["ok"])
        self.assertTrue(self._send(job, mgr, rid=1)["ok"])
        write_inbox(self.data, "maria@acme.io", "Re: Salesforce Developer - Akshay Verma", "Let's talk Tuesday.")
        counts, evs = self._run(followups.add_business_days(db.today_ist(), 1))
        self.assertEqual(counts["replies"], 1)
        self.assertEqual(counts["nudges_sent"], 0, "the hiring manager was nudged after HR already replied")

    def test_bounce_processing_is_idempotent(self):
        job = self._job()
        cid = self._contact(job, "wrong@acme.io")
        self.assertTrue(self._send(job, cid)["ok"])
        write_inbox(self.data, "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                    "Delivery Status Notification (Failure)", "Your message wasn't delivered to wrong@acme.io")
        for d in (1, 2, 5):
            counts, _ = self._run(followups.add_business_days(db.today_ist(), d))
            self.assertEqual(counts["nudges_sent"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM replies WHERE kind='bounce'").fetchone()[0], 1)
        self.assertEqual(len(self._outbox()), 1)


class ResumeContent(TempData, unittest.TestCase):
    def test_default_pdf_matches_original_layout(self):
        out = resume.build_pdf({}, self.data / "r.pdf")
        txt = _pdftext(out)
        pages = txt.split("\f")
        pages = [p for p in pages if p.strip()]
        self.assertGreater(len(pages), 1)
        for p in pages:
            self.assertEqual(p.strip().splitlines()[0].strip(), "Akshay Kumar Verma")
        self.assertRegex(txt, r"Forcesap IT Solution / Salesforce Consultant\s+Oct 2023 – Present")
        pos = [txt.find(h) for h in ("Sales Coach Agent for NISSAN", "Customer Service Agent for ASDA",
                                         "Agentforce for SINGAPORE AIRLINES", "Amkor Technology — Salesforce CPQ",
                                         "Salesforce OmniStudio for BLUE CROSS")]
        self.assertTrue(all(p > 0 for p in pos), pos)
        self.assertEqual(pos, sorted(pos), "Agentforce projects + Amkor are not the first four projects")
        low = txt.lower()
        for bad in ("team of 17", "team of 7", "led a team", "managed a team", "team lead"):
            self.assertNotIn(bad, low)
        fonts = subprocess.run(["pdffonts", out], capture_output=True, text=True).stdout
        self.assertRegex(fonts, r"(Calibri|Carlito)-Bold")

    def test_tailored_cfg_markup_is_inert(self):
        evil = {"summary": "Apex & LWC </b></para><font name='Nope'>x</font> <a href='javascript:x'>y</a>",
                "skills": [["A<b>", "B</b> & <br/>"]] * 6, "project_order": ["bcbsm"]}
        txt = _pdftext(resume.build_pdf(evil, self.data / "e.pdf"))
        self.assertIn("</para><font name='Nope'>", txt)

    def test_tailored_cfg_cannot_claim_team_leadership_or_missing_skills(self):
        lie = dict(RESUME, summary="Salesforce developer with 7+ years. Led a team of 17 developers at Deloitte. "
                                    "Built Agentforce agents for Nissan.",
                   skills=[["Integration", "REST, SOAP, MuleSoft development, platform events"],
                           ["Marketing", "Pardot, Marketing Cloud"], ["Dev", "Apex, LWC"], ["CPQ", "SBQQ"],
                           ["Clouds", "Sales, Service, nCino"], ["DevOps", "Git"]])
        low = _pdftext(resume.build_pdf(lie, self.data / "l.pdf")).lower()
        for bad in ("team of 17", "mulesoft", "pardot", "ncino"):
            self.assertNotIn(bad, low, "resume claims %r" % bad)
        self.assertIn("agentforce agents for nissan", low)
        self.assertIn("platform events", low)

    def test_pdf_still_built_without_any_font_files(self):
        sys.path.insert(0, str(RES_DIR))
        try:
            classic = importlib.import_module("classic")
            with mock.patch.object(classic, "_FONT_DIRS", []):
                out = classic.build({"summary": "Ünïcode ≥ → test"}, str(self.data / "nofont.pdf"))
        finally:
            sys.path.remove(str(RES_DIR))
        fonts = subprocess.run(["pdffonts", out], capture_output=True, text=True).stdout
        self.assertIn("Helvetica-Bold", fonts)
        self.assertTrue(Path(out).read_bytes().startswith(b"%PDF"))

    def test_docx_matches_layout(self):
        try:
            import docx  # noqa
        except ImportError:
            raise unittest.SkipTest("python-docx missing")
        out = self.data / "r.docx"
        subprocess.run([sys.executable, str(RES_DIR / "classic_docx.py"), str(out)], cwd=str(RES_DIR), check=True,
                       capture_output=True, timeout=120)
        import docx
        d = docx.Document(str(out))
        hp = d.sections[0].header.paragraphs[0]
        self.assertEqual(hp.text, "Akshay Kumar Verma")
        self.assertEqual((hp.runs[0].font.name, hp.runs[0].font.size.pt, hp.runs[0].bold), ("Calibri", 22.0, True))
        contact = d.paragraphs[0].runs
        self.assertEqual((contact[0].font.name, contact[0].font.size.pt), ("Calibri", 11.0))
        self.assertEqual(len(d.tables), 3)
        self.assertTrue(d.tables[0].rows[0].cells[0].text.startswith("Forcesap IT Solution / Salesforce Consultant"))
        self.assertIn("Oct 2023 – Present", d.tables[0].rows[0].cells[1].text)
        body = [p for p in d.paragraphs if p.style.name == "List Bullet"][0].runs[0].font
        self.assertEqual((body.name, body.size.pt), ("Century Gothic", 10.0))
        alltext = "\n".join(p.text for p in d.paragraphs).lower()
        self.assertNotIn("team of", alltext)

    def test_docx_unescapes_tailored_text(self):
        sys.path.insert(0, str(RES_DIR))
        try:
            classic_docx = importlib.import_module("classic_docx")
            cfg = resume.clean_config({"summary": "Apex <5 yrs & LWC > Aura"})
            out = classic_docx.build(str(self.data / "t.docx"), cfg)
        finally:
            sys.path.remove(str(RES_DIR))
        import docx
        txt = "\n".join(p.text for p in docx.Document(out).paragraphs)
        self.assertIn("Apex <5 yrs & LWC > Aura", txt)


if __name__ == "__main__":
    unittest.main()
