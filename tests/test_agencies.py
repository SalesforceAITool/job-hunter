"""Agencies feature: seed import, discovery, picking, separate caps, status, nudges, lane, prompt, API."""
import http.client
import json
import socket
import threading
import unittest
from datetime import timedelta

from test_backend_helpers import TempData
from jh import actions, agencies, db, engine, followups, runner, server


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


class AgencyData(TempData, unittest.TestCase):
    def test_seed_import_once(self):
        n = agencies.import_seed(self.conn)
        self.assertEqual(n, 237 + 8)
        self.assertEqual(agencies.import_seed(self.conn), 0)
        ttc = self.conn.execute("SELECT * FROM agencies WHERE name LIKE 'TTC Group%'").fetchone()
        self.assertEqual(ttc["status"], "manual")                       # past client: Akshay emails TTC himself
        self.assertIn("error", agencies.set_status(self.conn, ttc["id"], "new"))
        mf = self.conn.execute("SELECT * FROM agencies WHERE name='Mason Frank International'").fetchone()
        self.assertIn("j.eliot@masonfrank.com", mf["known_emails"])
        self.assertEqual(mf["website"], "https://masonfrank.com")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agencies WHERE region='us' AND priority='C'").fetchone()[0], 120)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agencies WHERE kind='marketplace'").fetchone()[0], 8)

    def test_discover_from_jobs(self):
        actions.save_job(self.conn, None, "posts", "posts", "Acme Staffing Solutions", "Salesforce Developer",
                         jd_text="Our client in Texas needs a Salesforce developer. C2C ok.", decision="skipped",
                         skip_reason="onsite/hybrid")
        actions.save_job(self.conn, None, "posts", "posts", "BigCo", "Salesforce Engineer",
                         jd_text="Join our team.", apply_url="https://boards.greenhouse.io/bigco/jobs/1")
        actions.save_job(self.conn, None, "posts", "posts", "Mason Frank International", "Salesforce Developer",
                         jd_text="our client C2C staffing")
        agencies.import_seed(self.conn)
        n = agencies.discover_from_jobs(self.conn)
        names = {r[0] for r in self.conn.execute("SELECT name FROM agencies WHERE priority='D'")}
        self.assertEqual(names, {"Acme Staffing Solutions"})
        self.assertEqual(n, 1)

    def test_targets_order_pseudo_jobs_and_engaged(self):
        rid = self.conn.execute("INSERT INTO runs (started_at, status) VALUES ('2026-10-21', 'running')").lastrowid
        # already talking to Synapri through job outreach -> not pitched again
        j = actions.save_job(self.conn, rid, "posts", "posts", "Synapri", "Salesforce Developer")["job_id"]
        c = self.conn.execute("INSERT INTO contacts (job_id, name, title, email, created_at) VALUES (?,?,?,?,?)",
                              (j, "Mike", "Recruiter", "mike@synapri.com", db.now_iso())).lastrowid
        self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, body, status, sent_at, run_id)"
                          " VALUES (?,?,'initial','s','b','sent',?,?)", (j, c, db.now_iso(), rid))
        tg = agencies.targets(self.conn, rid, limit=4, focus="outside")
        names = [t["name"] for t in tg]
        self.assertEqual(names[0], "Mason Frank International")         # A first, TTC (manual) skipped
        self.assertNotIn("Synapri", names)
        self.assertEqual(self.conn.execute("SELECT status FROM agencies WHERE name='Synapri'").fetchone()[0], "manual")
        self.assertEqual(tg[0]["known_emails"], ["j.taylor3@masonfrank.com", "j.eliot@masonfrank.com"])
        job = self.conn.execute("SELECT * FROM jobs WHERE id=?", (tg[0]["job_id"],)).fetchone()
        self.assertEqual((job["source"], job["status"]), ("agency", "qualified"))
        self.assertTrue(job["role"].startswith(agencies.AGENCY_ROLE))
        agencies.add(self.conn, "Desi Consultancy Pvt Ltd", "desi.in", region="india")
        ind = agencies.targets(self.conn, rid, limit=50, focus="india")
        self.assertEqual([t["name"] for t in ind], ["Desi Consultancy Pvt Ltd"])
        out = agencies.targets(self.conn, rid, limit=50, focus="outside")
        self.assertTrue(out and all(t["track"] == "outside" for t in out))
        lst = agencies.list_all(self.conn)
        self.assertEqual(lst["tracks"]["india"]["total"], 1)
        self.assertGreater(lst["tracks"]["outside"]["total"], 200)

    def test_separate_caps_and_status(self):
        rid = self.conn.execute("INSERT INTO runs (started_at, status) VALUES ('2026-10-21', 'running')").lastrowid
        cfg = dict(self.cfg, max_emails_per_run=1, max_emails_per_day=1, agencies={"daily_cap": 1})
        # one job email uses the job cap
        j = actions.save_job(self.conn, rid, "posts", "posts", "Acme", "Salesforce Developer")["job_id"]
        c = actions.save_contact(self.conn, rid, "posts", j, "Ann HR", "Talent Acquisition", "ann@acme.io", "SalesQL")
        self.assertTrue(actions.send_application(self.conn, rid, "posts", j, c["contact_id"], "Hi", "Body", {}, cfg)["ok"])
        # agency pitch still goes out (own cap)
        tg = agencies.targets(self.conn, rid, limit=1)
        aj = tg[0]["job_id"]
        c2 = actions.save_contact(self.conn, rid, "agencies", aj, "Jimmy Taylor", "Senior Manager, Salesforce Contract",
                                  "j.taylor3@masonfrank.com", "sheet")
        r2 = actions.send_application(self.conn, rid, "agencies", aj, c2["contact_id"], "Hi", "Pitch", {}, cfg)
        self.assertTrue(r2["ok"], r2)
        # second agency email today hits the agency cap
        c3 = actions.save_contact(self.conn, rid, "agencies", aj, "Joshua Eliot", "Principal Recruitment Consultant",
                                  "j.eliot@masonfrank.com", "sheet")
        r3 = actions.send_application(self.conn, rid, "agencies", aj, c3["contact_id"], "Hi", "Pitch", {}, cfg)
        self.assertIn("agency daily cap", r3["error"])
        # status: pitched, next touch in 90 days
        agencies.refresh_status(self.conn)
        a = self.conn.execute("SELECT * FROM agencies WHERE id=?", (tg[0]["agency_id"],)).fetchone()
        self.assertEqual(a["status"], "pitched")
        self.assertEqual(a["next_touch"], (db.today_ist() + timedelta(days=90)).isoformat())
        self.assertEqual(a["touches"], 1)
        # reply -> replied
        self.conn.execute("UPDATE jobs SET status='replied' WHERE id=?", (aj,))
        agencies.refresh_status(self.conn)
        self.assertEqual(self.conn.execute("SELECT status FROM agencies WHERE id=?", (tg[0]["agency_id"],)).fetchone()[0], "replied")

    def test_no_contact_and_user_status(self):
        rid = self.conn.execute("INSERT INTO runs (started_at, status) VALUES ('2026-10-21', 'running')").lastrowid
        tg = agencies.targets(self.conn, rid, limit=1)
        actions.set_job_status(self.conn, rid, "agencies", tg[0]["job_id"], "skipped", "no email found")
        agencies.refresh_status(self.conn)
        a = self.conn.execute("SELECT * FROM agencies WHERE id=?", (tg[0]["agency_id"],)).fetchone()
        self.assertEqual(a["status"], "no_contact")
        self.assertEqual(agencies.set_status(self.conn, a["id"], "not_fit"), {"ok": True})
        self.assertIn("error", agencies.set_status(self.conn, a["id"], "pitched"))
        self.assertIn("error", agencies.add(self.conn, "Mason Frank International"))
        self.assertTrue(agencies.add(self.conn, "New Agency LLC", "newagency.com")["ok"])

    def test_agency_nudge_text(self):
        t = followups.nudge_text("nudge1", 0, "Jimmy Taylor", agencies.AGENCY_ROLE + " - Oct 2026", "Mason Frank")
        self.assertIn("Salesforce reqs", t)
        self.assertNotIn("Salesforce contract roles for your clients", t)

    def test_prompt_block(self):
        tg = [{"job_id": 5, "name": "Mason Frank International", "region": "non_us", "kind": "Salesforce recruitment",
               "website": "https://masonfrank.com", "known_contacts": "Jimmy Taylor (Senior Manager)",
               "known_emails": ["j.taylor3@masonfrank.com", "not an email"]}]
        p = engine.compose_system_prompt("agencies", engine.PROMPTS_DIR / "agencies.md", 1, targets=tg)
        self.assertIn("job_id 5 — Mason Frank International — UK/EU/other", p)
        self.assertIn("known emails: j.taylor3@masonfrank.com", p)
        self.assertNotIn("not an email", p)
        self.assertIn("Trysyntx", p)


class AgencyTracking(TempData, unittest.TestCase):
    def test_in_touch_via_job_emails(self):
        rid = self.conn.execute("INSERT INTO runs (started_at, status) VALUES ('2026-10-21', 'running')").lastrowid
        j = actions.save_job(self.conn, rid, "linkedin_india", "linkedin_india", "Codiot Technologies LLP (LinkedIn post)",
                             "Salesforce Developer", jd_text="Remote, India")["job_id"]
        c = self.conn.execute("INSERT INTO contacts (job_id, name, title, email, created_at) VALUES (?,?,?,?,?)",
                              (j, "HR", "HR", "hr@codiot.com", db.now_iso())).lastrowid
        self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, body, status, sent_at, run_id)"
                          " VALUES (?,?,'initial','s','b','replied',?,?)", (j, c, db.now_iso(), rid))
        self.conn.execute("UPDATE jobs SET status='replied' WHERE id=?", (j,))
        lst = agencies.list_all(self.conn)
        a = next(x for x in lst["agencies"] if x["name"] == "Codiot Technologies LLP")
        self.assertEqual((a["region"], a["via_jobs"], a["via_replied"]), ("india", 1, 1))
        self.assertEqual((lst["tracks"]["india"]["approached"], lst["tracks"]["india"]["replied"]), (1, 1))
        self.assertEqual(lst["tracks"]["india"]["to_pitch"], 0)


class AgencyLane(TempData, unittest.TestCase):
    class Eng:
        def __init__(self):
            self.calls = []

        def run_lane(self, run_id, lane, cfg, emit, stop):
            self.calls.append((lane, cfg.get("_targets")))
            return {"status": "done", "counts": {}}

    def test_lane_runs_only_when_ticked(self):
        rid = self.conn.execute("INSERT INTO runs (started_at, status) VALUES ('2026-10-21', 'running')").lastrowid
        em = runner._Emitter(str(self.data / "jobhunter.db"), rid)
        eng, results = self.Eng(), {}
        runner._run_agencies(eng, rid, dict(self.cfg, agencies={"per_run": 2, "focus": "outside"}), em,
                             threading.Event(), None, results)
        self.assertEqual(len(eng.calls), 1)
        self.assertEqual(eng.calls[0][0], "agencies")
        self.assertEqual(len(eng.calls[0][1]), 2)
        self.assertIn("agencies", runner.SELECTABLE_LANES)
        self.assertEqual(runner.normalize_selection(["agencies"])[0], ["agencies"])


class AgencyApi(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.port = free_port()
        self.srv = server.create_server(self.port, web_dir=self.data / "web_missing", start_watch=False)
        server.serve_in_thread(self.srv)

    def tearDown(self):
        server.shutdown_server(self.srv)
        super().tearDown()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json"} if body is not None else {})
        r = c.getresponse(); raw = r.read(); c.close()
        return r.status, json.loads(raw or b"{}")

    def test_endpoints(self):
        st, d = self.req("GET", "/api/agencies")
        self.assertEqual(st, 200)
        self.assertEqual(len(d["agencies"]), 245)
        self.assertIn("tracks", d)
        self.assertEqual(d["settings"], {"per_run": 5, "daily_cap": 15, "focus": "all"})
        st, d2 = self.req("POST", "/api/agencies", {"name": "Brand New Staffing", "website": "brandnew.io", "region": "us"})
        self.assertEqual(st, 200)
        st, _ = self.req("POST", "/api/agencies/%d" % d2["id"], {"status": "paused"})
        self.assertEqual(st, 200)
        st, _ = self.req("POST", "/api/agencies/%d" % d2["id"], {"status": "pitched"})
        self.assertEqual(st, 400)
        st, _ = self.req("POST", "/api/agencies/%d" % d2["id"], {"region": "india"})
        self.assertEqual(st, 200)
        st, _ = self.req("POST", "/api/agencies/%d" % d2["id"], {"region": "mars"})
        self.assertEqual(st, 400)
        st, _ = self.req("POST", "/api/agencies/99999", {"status": "paused"})
        self.assertEqual(st, 404)
        st, _ = self.req("POST", "/api/agencies", {"name": ""})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
