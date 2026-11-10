"""Team review (forms area): question bank, submit flow, watch mode, run selection.

Each test states the CORRECT behaviour. On the unpatched code the tests marked "BUG" fail.
"""
import csv
import http.client
import importlib.util
import io
import json
import socket
import unittest
from datetime import timedelta
from unittest import mock

from test_backend_helpers import ROOT, TempData
from test_watch_submit_qa import EngineStubMixin, PortalMixin
from jh import actions, db, qa, runner, server


def _load_mcp():
    spec = importlib.util.spec_from_file_location("jobhunt_mcp_team", str(ROOT / "engine" / "jobhunt_mcp.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- best_option: wrong dropdown picks

class BestOptionTeam(TempData, unittest.TestCase):
    # BUG: a unitless "15" (notice period in days) lands in "3+ months" because 15 >= 3.
    def test_notice_days_never_maps_to_months_range(self):
        qa.record(self.conn, None, "easy_apply", None, [
            {"question": "What is your notice period?", "answer": "15", "field_type": "number", "source": "standard"}])
        r = qa.lookup(self.conn, [{"question": "What is your notice period?", "field_type": "choice",
                                   "options": ["0-1 month", "1-2 months", "2-3 months", "3+ months"]}])["results"][0]
        self.assertTrue(r["found"])
        self.assertIn(r["option"], (None, "0-1 month"))
        self.assertEqual(qa.best_option("15", ["0-1 month", "1-2 months", "3+ months"], "Notice period"), "")

    # BUG: 7 (years, seeded) -> "6-12 months".
    def test_years_answer_never_maps_to_months_range(self):
        qa.seed_once(self.conn)
        r = qa.lookup(self.conn, [{"question": "How many years of total work experience do you have?", "field_type": "choice",
                                   "options": ["0-6 months", "6-12 months", "12+ months"]}])["results"][0]
        self.assertIn(r["option"], (None, "12+ months"))

    # BUG: thousands separators break range parsing; $45,000 picks "$100,000+" via the "000+" fragment.
    def test_thousands_separators(self):
        self.assertEqual(qa.best_option("45000", ["$10,000 - $50,000", "$50,000 - $100,000", "$100,000+"]),
                         "$10,000 - $50,000")

    # BUG: an INR (LPA) answer must never be mapped onto USD options.
    def test_lpa_answer_never_picks_usd_option(self):
        qa.add_question(self.conn, "What is your expected salary?", "28 LPA")
        r = qa.lookup(self.conn, [{"question": "What is your expected salary?", "field_type": "choice",
                                   "options": ["$10,000 - $20,000", "$20,000 - $50,000", "$100,000+"]}])["results"][0]
        self.assertIsNone(r["option"])

    # BUG (informational): answers.md: "3+ years" -> pick the range that contains 3+ ("3-5 years"), never "1-3".
    def test_three_plus_picks_range_starting_at_three(self):
        self.assertEqual(qa.best_option("3+ years", ["0-1 years", "1-3 years", "3-5 years", "5+ years"]), "3-5 years")
        self.assertEqual(qa.best_option("3", ["0-1 years", "1-3 years", "3-5 years", "5+ years"],
                                        "How many years of experience do you have with Apex?"), "3-5 years")

    def test_yes_no_polarity_holds_for_authorization_options(self):   # passes: polarity guard works
        self.assertEqual(qa.best_option("No", ["I will require sponsorship", "I do not require sponsorship"]),
                         "I do not require sponsorship")
        self.assertEqual(qa.best_option("Yes", ["I am a US citizen", "I have a green card", "I need sponsorship"]), "")


# --------------------------------------------------------------------------- learning / matching

class LearningTeam(TempData, unittest.TestCase):
    # BUG: per_job() doesn't cover authorization/sponsorship/visa/citizenship; a country-agnostic auth question
    # answered "Yes" on an India job is learned and replayed confidently on a US job.
    def test_country_ambiguous_auth_answer_not_learned(self):
        q = "Are you legally authorized to work in the country where this job is located?"
        res = qa.record(self.conn, None, "easy_apply", None, [{"question": q, "answer": "Yes", "field_type": "yes_no",
                                                                "source": "profile"}])
        self.assertEqual(res["learned"], 0)
        self.assertFalse(qa.lookup(self.conn, [q])["results"][0]["found"])

    # BUG: the agent flips the seeded OPEN sponsorship question to answered without Akshay.
    def test_agent_cannot_answer_open_sensitive_question(self):
        qa.seed_once(self.conn)
        q = "Will you now or in the future require sponsorship for employment?"
        qa.record(self.conn, None, "easy_apply", None, [{"question": q, "answer": "No", "source": "standard"}])
        st = self.conn.execute("SELECT status FROM questions WHERE norm=?", (qa.normalize(q),)).fetchone()[0]
        self.assertEqual(st, "open")

    # BUG: "U. S." / "U.K." lose the country (single letters dropped) and match a generic question at 0.99.
    def test_spaced_country_abbreviation_keeps_country(self):
        qa.add_question(self.conn, "Are you legally authorized to work?", "Yes")
        for q in ("Are you legally authorized to work in the U. S.?", "Are you legally authorized to work in the U.K.?"):
            self.assertFalse(qa.lookup(self.conn, [q])["results"][0]["found"], q)

    # Passes: inputs are .strip()ped before storage, so a leading TAB/CR never reaches _csv (guard test).
    def test_csv_export_neutralises_tab_and_cr(self):
        for i, bad in enumerate(("\t=HYPERLINK(\"http://x\")", "\r=1+1", "=1+1", "@SUM(1)")):
            qa.add_question(self.conn, "Question %d %s" % (i, "abcdefgh"[i]), bad)
        rows = list(csv.reader(io.StringIO(qa.export_bank_csv(self.conn).lstrip("﻿"))))[1:]
        for r in rows:
            self.assertTrue(r[1].startswith("'"), repr(r[1]))


# --------------------------------------------------------------------------- submit state machine

class SubmitTeam(PortalMixin, TempData, unittest.TestCase):
    def row(self, pid):
        return dict(self.conn.execute("SELECT * FROM portal_apps WHERE id=?", (pid,)).fetchone())

    # BUG (double submit): approval lapses while the submit lane is on the form -> mark_submitted is refused after the
    # real submit -> row stays "Ready" -> Akshay approves again -> second submission.
    def test_approval_expiring_mid_lane_cannot_cause_second_submit(self):
        _, a, _ = self.portal(True)
        almost = (db.now() - timedelta(hours=actions.APPROVAL_HOURS) + timedelta(seconds=30)).replace(microsecond=0)
        actions.approve_submit(self.conn, [a])
        self.conn.execute("UPDATE portal_apps SET approved_at=? WHERE id=?", (almost.isoformat(), a))
        m = _load_mcp()
        ctx = m.Ctx()
        ctx.lane = "submit"
        res, err = m.call_tool(ctx, "get_submit_queue", {})                            # lane picks it up
        self.assertEqual([q["id"] for q in res["queue"]], [a])
        later = db.now() + timedelta(minutes=3)                                       # lane takes 3 min
        with mock.patch.object(db, "now", lambda: later):
            res = actions.mark_submitted(self.conn, None, "submit", a, "Your application was sent")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(actions.approve_submit(self.conn, [a])["approved"], [])

    # BUG: POST /api/portal/<id> {"status":"prefilled"} reopens a submitted row with ready=1 -> re-approvable.
    def test_submitted_row_cannot_be_reopened_and_reapproved(self):
        _, a, _ = self.portal(True)
        actions.approve_submit(self.conn, [a])
        actions.mark_submitted(self.conn, None, "submit", a, "sent")
        actions.set_portal_status(self.conn, a, "prefilled")
        self.assertEqual(actions.approve_submit(self.conn, [a])["approved"], [])

    # BUG (race): the click approves whatever form is stored NOW. A re-prefill between page load and click is
    # approved unseen. approve_submit must accept the version the UI showed and refuse a newer one.
    def test_approval_bound_to_version_seen(self):
        jid, a, url = self.portal(True)
        self.conn.execute("UPDATE portal_apps SET updated_at='2000-01-01T00:00:00+05:30' WHERE id=?", (a,))
        seen = self.row(a)["updated_at"]
        actions.save_portal_prefill(self.conn, None, "easy_apply", jid, "linkedin_easy_apply", url,
                                    {"headline": "x"}, [], ready=True)                # watcher re-prefill
        res = actions.approve_submit(self.conn, [a], seen={a: seen})
        self.assertEqual(res["approved"], [])

    def test_reprefill_after_approval_voids_it(self):   # passes
        jid, a, url = self.portal(True)
        actions.approve_submit(self.conn, [a])
        actions.save_portal_prefill(self.conn, None, "easy_apply", jid, "linkedin_easy_apply", url, {}, [], ready=True)
        self.assertEqual(actions.submit_queue(self.conn), [])

    # Defence in depth: with auto-submit OFF only the submit lane's MCP process may confirm a submission.
    def test_only_submit_lane_may_mark_submitted(self):
        from jh import config
        cfg = config.load(); cfg["auto_submit"] = False; config.save(cfg)
        _, a, _ = self.portal(True)
        actions.approve_submit(self.conn, [a])
        m = _load_mcp()
        ctx = m.Ctx()
        ctx.lane = "easy_apply"
        res, err = m.call_tool(ctx, "mark_submitted", {"portal_id": a, "confirmation": "sent"})
        self.assertTrue(err, res)
        self.assertEqual(self.row(a)["status"], "prefilled")

    # Auto-submit ON (Akshay, 30 Oct): the fill lane submits the form it just filled, in the same pass.
    def test_auto_submit_fill_lane_submits_in_same_pass(self):
        jid, _, url = self.portal(False)
        m = _load_mcp()
        ctx = m.Ctx()
        ctx.lane = "easy_apply"
        res, err = m.call_tool(ctx, "save_portal_prefill", {"job_id": jid, "ats": "linkedin_easy_apply",
                                                            "apply_url": url, "questions_left": [], "ready": True})
        self.assertFalse(err, res)
        self.assertTrue(res.get("submit_now"), res)
        pid = res["portal_id"]
        res, err = m.call_tool(ctx, "mark_submitted", {"portal_id": pid, "confirmation": "Your application was sent"})
        self.assertFalse(err, res)
        self.assertEqual(self.row(pid)["status"], "submitted")
        # a form with blanks is never submitted
        jid2, _, url2 = self.portal(False)
        res, err = m.call_tool(ctx, "save_portal_prefill", {"job_id": jid2, "ats": "linkedin_easy_apply", "apply_url": url2,
                                                            "questions_left": ["Visa - left for you"], "ready": False})
        self.assertFalse(res.get("submit_now"))
        self.assertTrue(m.call_tool(ctx, "get_submit_queue", {})[1])          # still submit-lane only


# --------------------------------------------------------------------------- HTTP: run selection, watch, questions

class HttpTeam(EngineStubMixin, TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.stub = self.stub_engine()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.srv = server.create_server(self.port, web_dir=self.data / "nope", start_tailer=False, start_watch=False)
        server.serve_in_thread(self.srv)

    def tearDown(self):
        server.shutdown_server(self.srv)
        runner.wait(30)
        super().tearDown()

    def req(self, method, path, raw):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=raw, headers={"Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    # BUG: unknown lanes are filtered to nothing -> normalize_selection returns None -> FULL default run.
    def test_run_unknown_lanes_is_400_not_full_run(self):
        for body in (b'{"lanes": ["bogus"]}', b'{"lanes": ""}', b'{"lanes": ["Submit"]}'):
            self.assertEqual(self.req("POST", "/api/run", body), 400, body)
        runner.wait(30)
        self.assertEqual(self.stub.calls, [])

    def test_run_unhashable_lanes_not_500(self):
        self.assertNotEqual(self.req("POST", "/api/run", b'{"lanes": [{}]}'), 500)

    def test_watch_overflow_not_500(self):
        self.assertNotEqual(self.req("POST", "/api/watch", b'{"interval_min": 1e999}'), 500)


if __name__ == "__main__":
    unittest.main()
