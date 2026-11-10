import json
import threading
import time
import types
import unittest

from test_backend_helpers import FIXTURE, RESUME, TempData
from jh import actions, db, importer, runner


def events_of(conn, run_id):
    return [db.event_row_to_dict(r) for r in conn.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]


class RunnerTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._orig_loader = runner._load_engine

    def tearDown(self):
        runner.stop_run()
        runner.wait(10)
        runner._load_engine = self._orig_loader
        super().tearDown()

    def test_engine_missing_lanes_skipped_followups_run(self):
        def missing():
            raise ImportError("No module named 'jh.engine'")
        runner._load_engine = missing
        importer.import_csv(self.conn, FIXTURE)
        rid = runner.start_run("button", cfg=self.cfg)
        with self.assertRaises(runner.AlreadyRunning):
            runner.start_run("button", cfg=self.cfg)
        self.assertTrue(runner.wait(30))
        self.assertFalse(runner.is_running())
        evs = events_of(self.conn, rid)
        types_ = [e["type"] for e in evs]
        self.assertEqual(types_[0], "run_started")
        self.assertEqual(types_[-1], "run_finished")
        fin = {e["lane"]: e["data"] for e in evs if e["type"] == "lane_finished"}
        for lane in ("posts", "jobright", "linkedin_jobs", "outreach", "portal", "easy_apply"):
            self.assertEqual(fin[lane]["status"], "skipped", lane)
        self.assertTrue(any(e["type"] == "warning" and "AI engine not available" in e["message"] for e in evs))
        self.assertEqual(fin["followups"]["status"], "done")
        self.assertEqual(fin["followups"]["counts"]["checked"], 23)  # every open imported thread
        self.assertTrue(any(e["type"] == "lane_started" and e["lane"] == "followups" for e in evs))
        run = self.conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        self.assertEqual(run["status"], "done")
        self.assertTrue(run["finished_at"])
        stats = json.loads(run["stats_json"])
        self.assertIn("lanes", stats)
        self.assertEqual(stats["sent"], 0)

    def test_fake_engine_sequential_lanes(self):
        self.cfg["linkedin_safe"] = False; __import__("jh.config").config.save(self.cfg)   # LinkedIn behaviour under test
        calls = []

        def run_lane(run_id, lane, cfg, emit, stop):
            calls.append(lane)
            conn = db.connect()
            emit(lane, "lane_started", "engine tries to start lane")   # must be swallowed
            emit(lane, "step", "working on %s" % lane)
            if lane == "jobright":
                job = actions.save_job(conn, run_id, lane, "jobright", "Acme", "Salesforce Developer")["job_id"]
                cid = actions.save_contact(conn, run_id, lane, job, "Ann", "Recruiter", "ann@acme.io", "SalesQL")["contact_id"]
                actions.send_application(conn, run_id, lane, job, cid, "subj", "body", RESUME, cfg)
                actions.save_job(conn, run_id, lane, "jobright", "Beta", "Salesforce Developer", decision="skipped",
                                 skip_reason="W2 only")
            emit(lane, "lane_finished", "engine result")                  # converted to a step
            conn.close()
            return {"status": "done", "counts": {"turns": 3}, "cost_usd": 0.12, "error": None}

        runner._load_engine = lambda: types.SimpleNamespace(run_lane=run_lane)
        rid = runner.start_run("open", cfg=self.cfg, lanes=["followups", "posts", "jobright", "linkedin_jobs", "himalayas",
                                                             "easy_apply", "portal"])
        self.assertTrue(runner.wait(30))
        # Easy Apply first (freshness), then the source lanes; portal picked -> it runs too
        self.assertEqual(calls, ["easy_apply", "posts", "jobright", "linkedin_jobs", "portal"])
        evs = events_of(self.conn, rid)
        fins = [e for e in evs if e["type"] == "lane_finished"]
        self.assertEqual(sorted(e["lane"] for e in fins),
                         sorted(["followups", "posts", "jobright", "linkedin_jobs", "himalayas", "outreach", "agencies", "portal", "easy_apply",
                             "board_apply", "submit"]))
        jr = [e for e in fins if e["lane"] == "jobright"][0]["data"]
        self.assertEqual(jr["status"], "done")
        self.assertEqual(jr["counts"]["sent"], 1)
        self.assertEqual(jr["counts"]["qualified"], 1)
        self.assertEqual(jr["counts"]["skipped"], 1)
        self.assertEqual(jr["cost_usd"], 0.12)
        out = [e for e in fins if e["lane"] == "outreach"][0]["data"]
        self.assertEqual(out["counts"]["sent"], 1)
        starts = [e["lane"] for e in evs if e["type"] == "lane_started"]
        self.assertEqual(starts.count("jobright"), 1)
        stats = json.loads(self.conn.execute("SELECT stats_json FROM runs WHERE id=?", (rid,)).fetchone()[0])
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(stats["qualified"], 1)
        s = db.summary(self.conn)
        self.assertEqual(s["sent_today"], 1)
        self.assertGreater(s["per_hour_current_run"], 0)

    def test_selected_lanes_only(self):
        calls = []

        def run_lane(run_id, lane, cfg, emit, stop):
            calls.append((lane, tuple(cfg.get("_regions") or ())))
            return {"status": "done", "counts": {}}

        runner._load_engine = lambda: types.SimpleNamespace(run_lane=run_lane)
        rid = runner.start_run("button", cfg=dict(self.cfg, easy_apply=False),
                               lanes=["easy_apply", "posts", "bogus"], regions=["india", "mars"])
        self.assertTrue(runner.wait(30))
        # Easy Apply runs because it was picked, even with the Settings toggle off; order stays posts -> easy_apply
        self.assertEqual(calls, [("easy_apply", ("india",)), ("posts", ("india",))])
        evs = events_of(self.conn, rid)
        fin = {e["lane"]: e["data"] for e in evs if e["type"] == "lane_finished"}
        for lane in ("followups", "jobright", "linkedin_jobs", "himalayas", "portal"):
            self.assertEqual(fin[lane]["status"], "skipped", lane)
            self.assertEqual(fin[lane]["reason"], "not selected for this run", lane)
        self.assertEqual(fin["outreach"]["status"], "done")
        started = [e for e in evs if e["type"] == "run_started"][0]["data"]
        self.assertEqual(started["lanes"], ["posts", "easy_apply"])
        self.assertEqual(started["regions"], ["india"])

    def test_portal_only_runs_when_picked(self):
        calls = []
        runner._load_engine = lambda: types.SimpleNamespace(
            run_lane=lambda run_id, lane, cfg, emit, stop: calls.append(lane) or {"status": "done"})
        rid = runner.start_run("button", cfg=self.cfg, lanes=["portal"])
        self.assertTrue(runner.wait(30))
        self.assertEqual(calls, ["portal"])  # no ATS jobs this run, but it was picked
        fin = {e["lane"]: e["data"] for e in events_of(self.conn, rid) if e["type"] == "lane_finished"}
        self.assertEqual(fin["outreach"]["reason"], "no source lane selected")

    def test_default_run_skips_low_yield_lanes(self):
        self.cfg["linkedin_safe"] = False; __import__("jh.config").config.save(self.cfg)   # LinkedIn behaviour under test
        calls = []

        def run_lane(run_id, lane, cfg, emit, stop):
            calls.append(lane)
            return {"status": "done", "counts": {}}
        runner._load_engine = lambda: types.SimpleNamespace(run_lane=run_lane)
        rid = runner.start_run("open", cfg=self.cfg)
        self.assertTrue(runner.wait(30))
        self.assertEqual(calls, ["easy_apply", "posts", "linkedin_jobs"])     # no JobRight, no Remote-boards emails
        started = [e for e in events_of(self.conn, rid) if e["type"] == "run_started"][0]["data"]
        self.assertEqual(started["regions"], ["india", "world"])              # default regions: no US

    def test_budget_stops_ai_lanes_not_submit(self):
        from jh import events as ev
        ev.emit(self.conn, None, "posts", "lane_finished", "x", "info", {"cost_usd": 50})
        calls = []
        runner._load_engine = lambda: types.SimpleNamespace(run_lane=lambda *a, **k: calls.append(a[1]) or {"status": "done"})
        rid = runner.start_run("button", cfg=self.cfg, lanes=["posts"])
        self.assertTrue(runner.wait(30))
        self.assertEqual(calls, [])
        fin = {e["lane"]: e["data"] for e in events_of(self.conn, rid) if e["type"] == "lane_finished"}
        self.assertEqual(fin["posts"]["reason"], "daily AI budget reached")

    def test_normalize_selection(self):
        self.assertEqual(runner.normalize_selection(None, None), (None, ["us", "india", "world"]))
        self.assertEqual(runner.normalize_selection([], []), (None, ["us", "india", "world"]))
        self.assertEqual(runner.normalize_selection("easy_apply, followups", "world,us"),
                         (["followups", "easy_apply"], ["us", "world"]))
        self.assertEqual(runner.normalize_selection(["nope"], None)[0], None)

    def test_stop(self):
        started = threading.Event()

        def run_lane(run_id, lane, cfg, emit, stop):
            started.set()
            while not stop.is_set():
                time.sleep(0.05)
            return {"status": "stopped", "counts": {}}

        runner._load_engine = lambda: types.SimpleNamespace(run_lane=run_lane)
        rid = runner.start_run("button", cfg=self.cfg)
        self.assertTrue(started.wait(10))
        self.assertTrue(runner.stop_run())
        self.assertTrue(runner.wait(15))
        run = self.conn.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()
        self.assertEqual(run["status"], "stopped")
        fin = {e["lane"]: e["data"]["status"] for e in events_of(self.conn, rid) if e["type"] == "lane_finished"}
        self.assertEqual(fin["easy_apply"], "stopped")      # Easy Apply runs first now
        self.assertEqual(fin["posts"], "skipped")
        self.assertFalse(runner.stop_run())


if __name__ == "__main__":
    unittest.main()
