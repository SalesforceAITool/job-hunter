"""Review findings for the Agencies feature (each test proves one finding; red = bug present)."""
import os
import sys
import unittest
from datetime import timedelta

from test_backend_helpers import TempData, ROOT
from jh import actions, agencies, config, db

sys.path.insert(0, str(ROOT / "engine"))


def _run(conn):
    return conn.execute("INSERT INTO runs (started_at, status) VALUES ('2026-10-21', 'running')").lastrowid


class _Clock:
    def set(self, d):
        os.environ["JOBHUNTER_TODAY"] = d

    def tearDown(self):
        os.environ.pop("JOBHUNTER_TODAY", None)
        super().tearDown()


class AgencyCapZero(TempData, unittest.TestCase):
    def test_daily_cap_zero_blocks_agency_emails(self):
        cfg = config.apply_update(dict(self.cfg), {"agencies": {"daily_cap": 0}}, from_api=True)
        self.assertEqual(cfg["agencies"]["daily_cap"], 0)            # the validator keeps 0 ("stop agency emails")
        cfg["email"]["backend"], cfg["dry_run"] = "file", False
        rid = _run(self.conn)
        tg = agencies.targets(self.conn, rid, limit=1)
        c = actions.save_contact(self.conn, rid, "agencies", tg[0]["job_id"], "Jimmy Taylor", "Senior Manager",
                                 "j.taylor3@masonfrank.com", "sheet")
        r = actions.send_application(self.conn, rid, "agencies", tg[0]["job_id"], c["contact_id"], "Hi", "Pitch", {}, cfg)
        self.assertFalse(r["ok"], "daily_cap=0 still sent an agency email: `or 15` turns 0 into 15")


class AgencyRetouch(_Clock, TempData, unittest.TestCase):
    def _pitch(self, rid, email):
        tg = agencies.targets(self.conn, rid, limit=1)
        self.assertTrue(tg and tg[0]["name"] == "Mason Frank International", tg)
        c = actions.save_contact(self.conn, rid, "agencies", tg[0]["job_id"], "P", "Recruiter", email, "sheet")
        self.assertTrue(actions.send_application(self.conn, rid, "agencies", tg[0]["job_id"], c["contact_id"],
                                                 "Hi", "Pitch", {}, self.cfg)["ok"])
        agencies.refresh_status(self.conn)
        return tg[0]["agency_id"]

    def test_second_pitch_advances_next_touch(self):
        self.set("2026-10-21")
        aid = self._pitch(_run(self.conn), "j.taylor3@masonfrank.com")
        self.set("2027-01-20")                                        # 91 days later: re-touch is due
        self._pitch(_run(self.conn), "j.eliot@masonfrank.com")
        nt = self.conn.execute("SELECT next_touch FROM agencies WHERE id=?", (aid,)).fetchone()[0]
        self.assertGreater(nt, "2027-01-20", "next_touch not advanced after the re-touch (stays %s)" % nt)
        self.set("2027-02-03")                                        # next month: must NOT be due again
        names = [t["name"] for t in agencies.targets(self.conn, _run(self.conn), limit=3)]
        self.assertNotIn("Mason Frank International", names, "agency re-pitched every month after first re-touch")

    def test_no_contact_retry_not_every_run(self):
        self.set("2026-10-21")
        rid = _run(self.conn)
        tg = agencies.targets(self.conn, rid, limit=1)
        actions.set_job_status(self.conn, rid, "agencies", tg[0]["job_id"], "skipped", "no email found")
        agencies.refresh_status(self.conn)
        self.set("2026-11-21")
        rid = _run(self.conn)
        tg2 = agencies.targets(self.conn, rid, limit=1)
        self.assertEqual(tg2[0]["agency_id"], tg[0]["agency_id"])
        actions.set_job_status(self.conn, rid, "agencies", tg2[0]["job_id"], "skipped", "no email found")
        agencies.refresh_status(self.conn)
        nt = self.conn.execute("SELECT next_touch FROM agencies WHERE id=?", (tg[0]["agency_id"],)).fetchone()[0]
        self.assertGreater(nt, "2026-11-21", "no_contact agency stays due -> AI re-searches it every run")


class AgencyDryRun(TempData, unittest.TestCase):
    def test_dry_run_does_not_burn_agency_for_90_days(self):
        cfg = dict(self.cfg, dry_run=True)
        rid = _run(self.conn)
        tg = agencies.targets(self.conn, rid, limit=1)
        c = actions.save_contact(self.conn, rid, "agencies", tg[0]["job_id"], "P", "Recruiter",
                                 "j.taylor3@masonfrank.com", "sheet")
        self.assertTrue(actions.send_application(self.conn, rid, "agencies", tg[0]["job_id"], c["contact_id"],
                                                 "Hi", "Pitch", {}, cfg)["dry_run"])
        agencies.refresh_status(self.conn)
        st = self.conn.execute("SELECT status FROM agencies WHERE id=?", (tg[0]["agency_id"],)).fetchone()[0]
        self.assertNotEqual(st, "pitched", "a dry run marked the agency pitched (skipped for 90 days, nothing sent)")


class AgencySourceSpoof(TempData, unittest.TestCase):
    def test_job_lane_cannot_save_agency_source(self):
        import jobhunt_mcp as m
        ctx = m.Ctx(); ctx.ensure(); ctx.run_id, ctx.lane = _run(self.conn), "posts"
        res, err = m.call_tool(ctx, "save_job", {"source": "agency", "company": "Acme", "role": "Salesforce Dev",
                                                 "decision": "qualified", "fit_score": 80})
        src = ctx.conn.execute("SELECT source FROM jobs WHERE company='Acme'").fetchone()
        self.assertTrue(err or src is None or src[0] != "agency",
                        "AI saved a job as source='agency': its emails skip the job per-run/daily caps")


class AgencyDirectEmployer(TempData, unittest.TestCase):
    def test_emailed_india_employer_not_pitched_as_agency(self):
        rid = _run(self.conn)
        j = actions.save_job(self.conn, rid, "linkedin_india", "linkedin_india", "Zeta Health", "Salesforce Developer",
                             jd_text="Remote, India. Join our product team.")["job_id"]
        c = self.conn.execute("INSERT INTO contacts (job_id, name, title, email, created_at) VALUES (?,?,?,?,?)",
                              (j, "HR", "HR", "hr@zeta.com", db.now_iso())).lastrowid
        old = (db.now() - timedelta(days=40)).replace(microsecond=0).isoformat()
        self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, body, status, sent_at, run_id)"
                          " VALUES (?,?,'initial','s','b','sent',?,?)", (j, c, old, rid))
        names = [t["name"] for t in agencies.targets(self.conn, _run(self.conn), limit=50, focus="india")]
        self.assertNotIn("Zeta Health", names, "a direct employer with no agency wording got a C2C bench pitch")


if __name__ == "__main__":
    unittest.main()
