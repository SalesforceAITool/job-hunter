import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(APP_ROOT))
from test_engine_support import write_stub_root, real_backend_available, db_rows  # noqa: E402
from jh import engine  # noqa: E402


class Collector:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, lane, type, message, level="info", data=None):
        with self.lock:
            self.events.append((lane, type, message, level, data or {}))

    def types(self):
        return [e[1] for e in self.events]


class RunLaneMockTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.data = Path(self.td.name) / "data"
        self.cfg = {"data_dir": str(self.data), "db_path": str(self.data / "jobhunter.db"),
                    "engine": {"mock": True, "lane_timeout_min": 2}}
        self.envp = mock.patch.dict(os.environ, {"JOBHUNTER_MOCK_SPEED": "0.01", "JOBHUNTER_MOCK_SEED": "42"})
        self.envp.start()
        for k in ("JOBHUNTER_DB", "JOBHUNTER_CONFIG", "JOBHUNTER_APP_ROOT", "JOBHUNTER_MOCK_FAIL",
                  "JOBHUNTER_MOCK_HANG", "JOBHUNTER_MOCK_NO_WRITES", "JOBHUNTER_MOCK_ENGINE"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.envp.stop()
        self.td.cleanup()

    def test_no_writes_lane_done(self):
        os.environ["JOBHUNTER_MOCK_NO_WRITES"] = "1"
        col = Collector()
        res = engine.run_lane(3, "posts", self.cfg, col, threading.Event())
        self.assertEqual(res["status"], "done", res)
        self.assertIsNone(res["error"])
        self.assertIsInstance(res["cost_usd"], float)
        self.assertGreater(res["turns"], 5)
        self.assertGreaterEqual(res["counts"]["sent"], 1)
        self.assertGreaterEqual(res["counts"]["skipped"], 1)
        t = col.types()
        self.assertIn("tool", t)
        self.assertIn("step", t)
        self.assertNotIn("lane_finished", t)  # runner owns lane_finished
        self.assertTrue(all(e[0] == "posts" for e in col.events))
        msgs = [e[2] for e in col.events]
        self.assertTrue(any(m.startswith("Opening linkedin.com/search") for m in msgs), msgs[:10])
        self.assertTrue(any(m.startswith("Sending application to ") for m in msgs))
        self.assertTrue(any(m.startswith("Engine started") for m in msgs))
        logs = list((self.data / "logs").glob("engine_3_posts_*.jsonl"))
        self.assertEqual(len(logs), 1)

    def test_stub_backend_all_lanes_write_db(self):
        root = write_stub_root(Path(self.td.name) / "stubroot")
        os.environ["JOBHUNTER_APP_ROOT"] = str(root)
        for lane in ("posts", "jobright", "linkedin_jobs", "portal"):
            col = Collector()
            res = engine.run_lane(9, lane, self.cfg, col, threading.Event())
            self.assertEqual(res["status"], "done", (lane, res, col.events[-5:]))
        db = self.data / "jobhunter.db"
        sources = {r["source"] for r in db_rows(db, "SELECT DISTINCT source FROM jobs")}
        self.assertTrue({"posts", "jobright", "linkedin_us", "linkedin_world"} <= sources, sources)
        self.assertGreaterEqual(len(db_rows(db, "SELECT * FROM emails")), 4)
        self.assertEqual(len(db_rows(db, "SELECT * FROM portal_apps")), 1)
        steps = db_rows(db, "SELECT lane FROM events WHERE type='step' AND run_id=9")
        self.assertTrue({"posts", "jobright", "linkedin_jobs", "portal"} <= {r["lane"] for r in steps})
        # the mock never uses real addresses
        for r in db_rows(db, "SELECT email FROM contacts"):
            self.assertTrue(r["email"].endswith(".example"), r)

    def test_engine_error_captures_stderr_tail(self):
        os.environ["JOBHUNTER_MOCK_FAIL"] = "1"
        col = Collector()
        res = engine.run_lane(1, "jobright", self.cfg, col, threading.Event())
        self.assertEqual(res["status"], "error")
        self.assertIn("exited with code 3", res["error"])
        self.assertIn("simulated engine crash", res["error"])
        err = [e for e in col.events if e[1] == "error"]
        self.assertEqual(len(err), 1)
        self.assertLessEqual(len(err[0][4]["stderr_tail"]), 20)
        self.assertTrue(any(e[3] == "debug" for e in col.events))

    def test_stop_kills_process(self):
        os.environ["JOBHUNTER_MOCK_HANG"] = "1"
        col = Collector()
        stop = threading.Event()
        threading.Timer(1.5, stop.set).start()
        t0 = time.time()
        res = engine.run_lane(1, "posts", self.cfg, col, stop)
        self.assertEqual(res["status"], "stopped")
        self.assertLess(time.time() - t0, 15)
        self.assertIn("Stopped by user", [e[2] for e in col.events])

    def test_timeout(self):
        os.environ["JOBHUNTER_MOCK_HANG"] = "1"
        cfg = dict(self.cfg, engine={"mock": True, "lane_timeout_min": 0.03})  # ~1.8 s
        col = Collector()
        res = engine.run_lane(1, "posts", cfg, col, threading.Event())
        self.assertEqual(res["status"], "error")
        self.assertIn("Timed out", res["error"])

    def test_missing_claude_reports_error(self):
        cfg = {"data_dir": str(self.data), "engine": {"mock": False, "claude_path": str(self.data / "nope")}}
        col = Collector()
        with mock.patch.object(engine, "resolve_claude", return_value=None):
            res = engine.run_lane(1, "posts", cfg, col, threading.Event())
        self.assertEqual(res["status"], "error")
        self.assertIn("not found", res["error"])

    def test_smoke_test_mock(self):
        root = write_stub_root(Path(self.td.name) / "stubroot")
        os.environ["JOBHUNTER_APP_ROOT"] = str(root)
        r = engine.engine_smoke_test(self.cfg)
        ids = {c["id"]: c for c in r["checks"]}
        self.assertTrue(r["ok"], r)
        for k in ("claude_found", "run", "mcp_jobhunt", "log_step", "chrome"):
            self.assertTrue(ids[k]["ok"], ids[k])


@unittest.skipUnless(real_backend_available(), "real jh.actions/jh.db not present yet")
class RunLaneRealBackendTest(unittest.TestCase):
    def test_mock_lane_with_real_actions(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            cfg = {"data_dir": str(data), "db_path": str(data / "jobhunter.db"),
                   "dry_run": True, "email": {"backend": "file"}, "max_emails_per_run": 25,
                   "engine": {"mock": True, "lane_timeout_min": 3}}
            env = {"JOBHUNTER_MOCK_SPEED": "0.01", "JOBHUNTER_DATA": str(data), "JOBHUNTER_DB": str(data / "jobhunter.db")}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("JOBHUNTER_APP_ROOT", None)
                os.environ.pop("JOBHUNTER_MOCK_NO_WRITES", None)
                col = Collector()
                res = engine.run_lane(None, "jobright", cfg, col, threading.Event())
            warn = [e for e in col.events if e[3] in ("warn", "error")]
            self.assertEqual(res["status"], "done", (res, warn))
            self.assertGreaterEqual(res["counts"]["found"], 3, (res, warn))
            jobs = db_rows(data / "jobhunter.db", "SELECT * FROM jobs")
            self.assertGreaterEqual(len(jobs), 3)


if __name__ == "__main__":
    unittest.main()
