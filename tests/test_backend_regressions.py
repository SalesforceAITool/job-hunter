"""Regression tests from the staff/CSO review (mailer, IMAP matching, config, Windows launch details)."""
import logging
import smtplib
import subprocess
import sys
import unittest
from unittest import mock

from test_backend_helpers import TempData
from jh import config, envcheck, importer, mailer, resume


class _FakeSMTP:
    """smtplib.SMTP_SSL stand-in: the message is accepted, then QUIT fails (timeout / bad reply)."""
    sent = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        raise smtplib.SMTPServerDisconnected("connection reset during QUIT")

    def login(self, *a):
        pass

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


class MailerTests(TempData, unittest.TestCase):
    def test_error_after_server_accepted_message_counts_as_sent(self):
        """Regression: an exception from QUIT after DATA was accepted was reported as a failed send, so the
        contact was not deduped and got the same application again on a later run."""
        cfg = dict(self.cfg, email={"backend": "smtp", "address": "me@gmail.com", "app_password": "x" * 16})
        _FakeSMTP.sent = []
        with mock.patch.object(mailer.smtplib, "SMTP_SSL", _FakeSMTP):
            mid = mailer.send(cfg, "hr@acme.io", "Role", "body")
        self.assertEqual(len(_FakeSMTP.sent), 1)
        self.assertEqual(mid, _FakeSMTP.sent[0]["Message-ID"])

    def _imap_box(self, results):
        box = object.__new__(mailer._ImapBox)  # skip the network login
        box.cfg = self.cfg
        box._search = lambda folder, criteria, max_n=50: list(results)
        return box

    def test_imap_from_substring_matches_are_dropped(self):
        """Regression: IMAP FROM/TO are substring searches - mail from joann@acme.io counted as a reply from
        ann@acme.io (nudges stopped), and a Sent message to joann@ could become the nudge's thread parent."""
        msgs = [{"from_addr": "joann@acme.io", "to": ["me@gmail.com"], "subject": "Re: role", "date": "2026-10-19"},
                {"from_addr": "ann@acme.io", "to": ["me@gmail.com"], "subject": "Re: role", "date": "2026-10-19"}]
        got = self._imap_box(msgs).find_replies("Ann@acme.io", "2026-10-18")
        self.assertEqual([m["from_addr"] for m in got], ["ann@acme.io"])
        sent = [{"from_addr": "me@gmail.com", "to": ["joann@acme.io"], "subject": "Role", "date": "2026-10-19",
                 "message_id": "<wrong@x>"}]
        self.assertIsNone(self._imap_box(sent).find_sent_message("ann@acme.io", "Role"))
        sent.append({"from_addr": "me@gmail.com", "to": ["ann@acme.io"], "subject": "Role", "date": "2026-10-18",
                     "message_id": "<right@x>"})
        self.assertEqual(self._imap_box(sent).find_sent_message("ann@acme.io", "Role")["message_id"], "<right@x>")


class ConfigTests(TempData, unittest.TestCase):
    def test_windows_args_quotes_stripped_and_port_clamped(self):
        self.assertEqual(config._unquote('"mcp__jobhunt__*"'), "mcp__jobhunt__*")
        self.assertEqual(config._unquote("'a b'"), "a b")
        self.assertEqual(config._unquote('C:\\Tools\\claude.exe'), 'C:\\Tools\\claude.exe')
        cfg = config.load()
        with mock.patch.object(config.os, "name", "nt"):  # shlex posix=False path, as on Windows
            out = config.apply_update(cfg, {"engine": {"claude_args": '--allowedTools "mcp__jobhunt__*"'},
                                            "port": 70000})
        self.assertEqual(out["engine"]["claude_args"], ["--allowedTools", "mcp__jobhunt__*"])
        self.assertEqual(out["port"], 65535)


class SubprocessEncodingTests(TempData, unittest.TestCase):
    """Regression: text=True without encoding decodes child output as cp1252 on Windows; a strict decode of
    UTF-8 bytes raised UnicodeDecodeError (resume build crashed / /api/env returned 500)."""

    def test_resume_builder_uses_utf8(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(kw)
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        with mock.patch.object(resume.subprocess, "run", fake_run):
            with self.assertRaises(resume.ResumeError):
                resume.build_pdf({"headline": "x"}, self.data / "r.pdf")
        self.assertEqual(calls[0]["encoding"], "utf-8")
        self.assertEqual(calls[0]["errors"], "replace")
        self.assertEqual(calls[0]["env"]["PYTHONIOENCODING"], "utf-8")

    def test_envcheck_uses_utf8(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(kw)
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch.object(envcheck.subprocess, "run", fake_run):
            envcheck._run(["claude", "--version"])
        self.assertEqual((calls[0]["encoding"], calls[0]["errors"]), ("utf-8", "replace"))


class ImporterLockTests(TempData, unittest.TestCase):
    def test_import_takes_write_lock_up_front(self):
        """A deferred BEGIN that reads first fails with SQLITE_BUSY (no retry) if the MCP process writes
        between the read and the first write; BEGIN IMMEDIATE waits on busy_timeout instead."""
        stmts = []
        self.conn.set_trace_callback(stmts.append)
        importer.import_text(self.conn, "date,company,role,contact,title,email,status,next_step\n"
                                        "2026-10-01,Acme,Salesforce Developer,Ann,HR,ann@acme.io,sent,\n")
        self.conn.set_trace_callback(None)
        self.assertIn("BEGIN IMMEDIATE", stmts)


class AppLoggingTests(TempData, unittest.TestCase):
    def test_pythonw_stdio_does_not_hold_app_log_open(self):
        """Regression: under pythonw (stdout/stderr None) app.log was opened a second time for stdio, which makes
        RotatingFileHandler's rename fail on Windows (log never rotates, logging errors on every record)."""
        import app
        root = logging.getLogger()
        before = list(root.handlers)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = None
        try:
            app.setup_logging()
            stream_path = sys.stdout.name
        finally:
            if sys.stdout is not None and sys.stdout is not old_out:
                sys.stdout.close()
            sys.stdout, sys.stderr = old_out, old_err
            for h in [h for h in root.handlers if h not in before]:
                root.removeHandler(h)
                h.close()
        self.assertTrue(stream_path.endswith("stdio.log"), stream_path)


class EngineDrainTests(unittest.TestCase):
    def test_slow_consumer_still_gets_last_line_after_exit(self):
        """Regression: after the child exited the stdout reader got only 5 s; a backlog of buffered lines (each
        one becomes a DB event) lost the final `result` line and a finished lane was reported as an error."""
        import time
        from jh import engine
        code = "import sys\nfor i in range(600): print('line %d' % i)\nprint('LAST')\n"
        seen = []

        def slow(line):
            time.sleep(0.01)
            seen.append(line.strip())
        rc, reason, _ = engine._run_stream([sys.executable, "-c", code], None, ".", slow, timeout_s=120)
        self.assertEqual((rc, reason), (0, None))
        self.assertEqual(seen[-1], "LAST")


if __name__ == "__main__":
    unittest.main()
