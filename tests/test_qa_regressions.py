"""QA pass regressions (2026-10-18): each test failed before its fix."""
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from test_backend_helpers import RESUME as RESUME_OK, ROOT, TempData
from jh import actions, db, dedupe, engine, followups, importer

SEED = ROOT / "seed" / "tracker.csv"


class FrozenTodayTests(TempData, unittest.TestCase):
    """Real tracker history + the JOBHUNTER_TODAY clock seam: exactly the expected people are due."""

    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"JOBHUNTER_TODAY": "2026-10-19"})
        self._env.start()
        importer.import_csv(self.conn, SEED)

    def tearDown(self):
        self._env.stop()
        super().tearDown()

    def test_clock_seam(self):
        self.assertEqual(db.today_ist(), date(2026, 10, 19))
        self.assertTrue(db.now_iso().startswith("2026-10-19T"))

    def test_seed_counts(self):
        s = db.summary(self.conn)
        self.assertEqual(s["sent_total"], 44)
        self.assertEqual(s["replies_total"], 2)
        self.assertEqual(s["bounced"], 2)
        names = {r[0] for r in self.conn.execute(
            "SELECT c.name FROM replies r JOIN contacts c ON c.id=r.contact_id WHERE r.kind='reply'")}
        self.assertEqual(names, {"Patrick Soule", "Nicola Bryan"})
        bounced = {r[0] for r in self.conn.execute(
            "SELECT c.email FROM replies r JOIN contacts c ON c.id=r.contact_id WHERE r.kind='bounce'")}
        self.assertEqual(bounced, {"hr@talentgigs.in", "kavya.malik@samsonsoft.com"})

    def test_due_on_monday_1019(self):
        due = {t["name"]: t["next_kind"] for t in db.load_threads(self.conn).values()
               if t["next_followup"] and t["next_followup"] <= db.today_ist()}
        # Monday 10-19: only the nudge2s; the weekend (10-17/18) sends get nudge1 on Tuesday 10-20.
        want = {n: "nudge2" for n in ("George Josten", "Matthew Kelly", "David Kim", "Vishnu Dommaraju")}
        self.assertEqual(due, want)
        tue = {t["name"]: t["next_kind"] for t in db.load_threads(self.conn).values()
               if t["next_followup"] and t["next_followup"] <= date(2026, 10, 20)}
        want.update({n: "nudge1" for n in ("HR inbox (from post)", "Sneha Sambaraju", "Cailyn Comeau",
                                            "Senthu Sivasambu", "Kathryn Peluchette", "Michael Clark",
                                            "Veronica Arevalo")})
        self.assertEqual(tue, want)

    def test_stopped_threads_never_due(self):
        for t in db.load_threads(self.conn).values():
            notes = (t.get("contact_notes") or "").lower()
            if "sequence done" in notes or "hold" in notes or t["replied"] or t["bounced"]:
                self.assertIsNone(t["next_followup"], t["name"])

    def test_imported_rows_without_message_id_are_skipped_with_warning(self):
        out, emit = self.emitted()
        counts = followups.run_followups(self.conn, None, self.cfg, emit, None)
        self.assertEqual(counts["nudges_sent"], 0)
        self.assertEqual(counts["nudges_skipped"], 4)  # the 4 nudge2s due on Monday 10-19
        warns = [e["message"] for e in out if e["type"] == "warning"]
        self.assertTrue(all("original email not found in Sent Mail" in w for w in warns), warns)


class ImporterSentDateTests(TempData, unittest.TestCase):
    def test_status_sent_date_wins_over_found_date(self):
        """'sent 2026-10-18 (...)' in the status column is the real send date (the date column is when the job
        was found): Monogram/Equality/fusionSpan were shown as sent on 10-17."""
        importer.import_csv(self.conn, SEED)
        sent = self.conn.execute("SELECT e.sent_at FROM emails e JOIN contacts c ON c.id=e.contact_id"
                                 " WHERE c.email='kathryn.peluchette@monogramhealth.com' AND e.kind='initial'"
                                 ).fetchone()[0]
        self.assertEqual(sent, "2026-10-18T10:00:00+05:30")
        plain = self.conn.execute("SELECT e.sent_at FROM emails e JOIN contacts c ON c.id=e.contact_id"
                                  " WHERE c.email='cailyn.comeau@versapay.com' AND e.kind='initial'").fetchone()[0]
        self.assertEqual(plain, "2026-10-17T10:00:00+05:30")


class NudgeTextTests(unittest.TestCase):
    def test_role_annotations_dropped(self):
        """Imported roles carry tracker notes: 'Salesforce Developer (US remote; public-sector consulting)'."""
        t = followups.nudge_text("nudge1", 0, "Sneha Sambaraju", "Salesforce Developer (US remote; public-sector consulting)",
                                 "Capio Group (JobRight)")
        self.assertIn("the Salesforce Developer role", t)
        self.assertNotIn("public-sector", t)
        t = followups.nudge_text("nudge1", 3, "HR inbox (from post)", "Salesforce Developer (Remote; 8 PM-2:30 AM IST)",
                                 "Codiot Technologies LLP (LinkedIn post via Salesforce Jungle)")
        self.assertNotIn("(", t)
        self.assertIn("Codiot Technologies LLP", t)
        self.assertTrue(t.startswith("Hi there,"))


class RoleParentheticalDedupeTests(TempData, unittest.TestCase):
    def test_norm_key_ignores_role_notes(self):
        self.assertEqual(dedupe.norm_key("Capio Group (JobRight)", "Salesforce Developer (US remote; public-sector consulting)"),
                         dedupe.norm_key("Capio Group", "Salesforce Developer"))
        self.assertEqual(dedupe.normalize_role("(Remote)"), "remote")  # never empty

    def test_capio_same_job_not_contacted_again(self):
        """Live QA: a second and third Capio recruiter were emailed for the job Sneha got on 10-18."""
        importer.import_csv(self.conn, SEED)
        r = dedupe.already_contacted(self.conn, "Capio Group", "Salesforce Developer", ["someone.else@capiogroup.com"])
        self.assertTrue(r["contacted"], r)
        job = actions.save_job(self.conn, None, "jobright", "jobright", "Capio Group", "Salesforce Developer")
        self.assertTrue(job.get("duplicate"), job)

    def test_existing_db_norm_keys_are_recomputed(self):
        self.conn.execute("INSERT INTO jobs (company, role, status, norm_key) VALUES"
                          " ('Capio Group (JobRight)', 'Salesforce Developer (US remote)', 'contacted', 'stale')")
        self.conn.execute("PRAGMA user_version=0")
        conn2 = db.connect(db.db_file(self.conn))
        try:
            self.assertEqual(conn2.execute("SELECT norm_key FROM jobs").fetchone()[0], "capio group|salesforce developer")
        finally:
            conn2.close()


class DuplicateVisibilityTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        importer.import_csv(self.conn, SEED)

    def _events(self, after):
        return [dict(r) for r in self.conn.execute("SELECT type, level, message, data_json FROM events WHERE id>?",
                                                   (after,))]

    def _max(self):
        return self.conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]

    def test_duplicate_contact_is_reported(self):
        job = actions.save_job(self.conn, 1, "posts", "posts", "Capio Group", "Salesforce Admin Developer")["job_id"]
        m = self._max()
        res = actions.save_contact(self.conn, 1, "posts", job, "Sneha Sambaraju", "Recruitment Specialist",
                                   "Sneha.Sambaraju@capiogroup.com", "post")
        self.assertIn("duplicate", res["error"])
        ev = self._events(m)
        self.assertEqual(len(ev), 1, ev)
        self.assertEqual(ev[0]["type"], "warning")
        self.assertIn("Duplicate", ev[0]["message"])
        self.assertTrue(json.loads(ev[0]["data_json"]).get("duplicate"))

    def test_duplicate_job_is_reported_as_skipped(self):
        m = self._max()
        res = actions.save_job(self.conn, 1, "posts", "posts", "Celestica Inc.", "Sr. Salesforce Developer / Admin")
        self.assertTrue(res["duplicate"])
        ev = self._events(m)
        self.assertEqual([e["type"] for e in ev], ["job_skipped"])
        self.assertIn("already contacted", ev[0]["message"])
        self.assertTrue(json.loads(ev[0]["data_json"]).get("duplicate"))

    def test_engine_does_not_echo_duplicate_errors_as_raw_json(self):
        st = engine.StreamState()
        engine.parse_stream_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__jobhunt__save_contact", "input": {"name": "S"}}]}}), st)
        out = engine.parse_stream_line(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": [
                {"type": "text", "text": '{"error": "duplicate: s@x.com was already emailed on 2026-10-18 (X)"}'}]}]}}), st)
        self.assertEqual(out, [])


class EngineSummaryOnceTests(unittest.TestCase):
    def test_final_summary_emitted_once(self):
        """The final assistant text and result.result are the same summary: it was shown twice in the feed."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        data = Path(td.name)
        cfg = {"data_dir": str(data), "db_path": str(data / "jobhunter.db"), "engine": {"mock": True, "lane_timeout_min": 2}}
        env = {"JOBHUNTER_MOCK_SPEED": "0.01", "JOBHUNTER_MOCK_SEED": "3", "JOBHUNTER_MOCK_NO_WRITES": "1"}
        events = []
        with mock.patch.dict(os.environ, env):
            for k in ("JOBHUNTER_DB", "JOBHUNTER_CONFIG", "JOBHUNTER_APP_ROOT", "JOBHUNTER_MOCK_FAIL", "JOBHUNTER_MOCK_HANG"):
                os.environ.pop(k, None)
            res = engine.run_lane(1, "posts", cfg, lambda *a, **k: events.append(a), threading.Event())
        self.assertEqual(res["status"], "done")
        msgs = [e[2] for e in events if e[1] == "step"]
        summary = [m for m in msgs if m.startswith("Lane posts finished")]
        self.assertEqual(len(summary), 1, msgs[-5:])
        for a, b in zip(msgs, msgs[1:]):
            self.assertNotEqual(a, b)


class ImporterMalformedTests(TempData, unittest.TestCase):
    HEAD = "date,company,role,contact,title,email,status,next_step\n"

    def test_impossible_dates_are_not_stored(self):
        """'nudge 1 sent 2026-99-99' was stored verbatim; parse_ts() then returned None and nudge1 was re-sent."""
        importer.import_text(self.conn, self.HEAD + "2026-10-12,NgCo,Dev,G H,HR,g@ngco.com,sent,nudge 1 sent 2026-99-99\n"
                                                    "2026-13-45,BadDate,Dev,B C,HR,b@bad.com,sent,\n")
        for (ts,) in self.conn.execute("SELECT sent_at FROM emails"):
            self.assertIsNotNone(db.parse_ts(ts), ts)
        t = [t for t in db.load_threads(self.conn).values() if t["email"] == "g@ngco.com"][0]
        self.assertEqual(t["app_status"], "nudge1")
        self.assertEqual(t["next_kind"], "nudge2")

    def test_missing_required_columns_rejected(self):
        with self.assertRaises(ValueError) as cm:
            importer.import_text(self.conn, "date,company,email\n2026-10-10,Foo,foo@x.com\n")
        self.assertIn("status", str(cm.exception))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)


class SendFailureTests(TempData, unittest.TestCase):
    def _contact(self, addr="a@acme.io"):
        job = actions.save_job(self.conn, None, "posts", "posts", "Acme", "Salesforce Developer")["job_id"]
        cid = actions.save_contact(self.conn, None, "posts", job, "A B", "HR", addr, "post")["contact_id"]
        return job, cid

    def test_smtp_connection_error_marks_failed_and_retry_possible(self):
        from jh import mailer
        cfg = dict(self.cfg, email={"backend": "smtp", "address": "a@gmail.com", "app_password": "abcdabcdabcdabcd"})
        job, cid = self._contact()

        def boom(*a, **k):
            raise OSError("Network is unreachable")
        with mock.patch.object(mailer.smtplib, "SMTP_SSL", boom):
            r = actions.send_application(self.conn, None, "posts", job, cid, "s", "b", RESUME_OK, cfg)
        self.assertFalse(r["ok"])
        self.assertIn("SMTP send failed", r["error"])
        self.assertEqual(self.conn.execute("SELECT status FROM emails WHERE id=?", (r["email_id"],)).fetchone()[0], "failed")
        self.assertFalse(dedupe.already_contacted(self.conn, "x", "y", ["a@acme.io"])["contacted"])
        self.assertEqual(db.load_threads(self.conn)[cid]["app_status"], "failed")
        r2 = actions.send_application(self.conn, None, "posts", job, cid, "s", "b", RESUME_OK, self.cfg)  # file backend
        self.assertTrue(r2["ok"], r2)

    def test_failure_before_resume_build_leaves_no_sending_claim(self):
        """resume_dir() ran outside the try: an exception there left a 'sending' row = 'contacted' forever."""
        job, cid = self._contact()
        with mock.patch.object(actions, "resume_dir", side_effect=OSError("disk full")):
            r = actions.send_application(self.conn, None, "posts", job, cid, "s", "b", RESUME_OK, self.cfg)
        self.assertFalse(r["ok"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM emails WHERE status='sending'").fetchone()[0], 0)
        self.assertFalse(dedupe.already_contacted(self.conn, "x", "y", ["a@acme.io"])["contacted"])

    def test_missing_reportlab_message(self):
        import subprocess
        from jh import resume
        tb = ("Traceback (most recent call last):\n  File \"resume2.py\", line 6, in <module>\n"
              "    from reportlab.lib import colors\nModuleNotFoundError: No module named 'reportlab'\n")
        fake = subprocess.CompletedProcess([], 1, "", tb)
        with mock.patch.object(resume.subprocess, "run", return_value=fake):
            with self.assertRaises(resume.ResumeError) as cm:
                resume.build_pdf(RESUME_OK, self.data / "x.pdf")
        self.assertIn("reportlab is not installed", str(cm.exception))
        self.assertIn("install_windows.bat", str(cm.exception))

    def test_engine_does_not_echo_email_failed(self):
        st = engine.StreamState()
        engine.parse_stream_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t9", "name": "mcp__jobhunt__send_application", "input": {"contact_id": 3}}]}}), st)
        out = engine.parse_stream_line(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t9", "is_error": True, "content": [
                {"type": "text", "text": '{"ok": false, "email_id": 97, "error": "resume build failed: x"}'}]}]}}), st)
        self.assertEqual(out, [])
        self.assertEqual(st.counts["failed"], 1)


if __name__ == "__main__":
    unittest.main()
