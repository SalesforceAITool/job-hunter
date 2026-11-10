import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from test_engine_support import write_stub_root, load_stub_modules, real_backend_available, db_rows  # noqa: E402

MCP = APP_ROOT / "engine" / "jobhunt_mcp.py"


def load_mcp_module():
    spec = importlib.util.spec_from_file_location("jobhunt_mcp_under_test", str(MCP))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class McpSession:
    """Spawn the MCP server as a subprocess and talk newline-delimited JSON-RPC."""

    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, str(MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env)
        self.n = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        if notify:
            return None
        line = self.p.stdout.readline()
        resp = json.loads(line.decode("utf-8"))
        assert resp.get("id") == self.n, resp
        return resp

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        finally:
            for s in (self.p.stdout, self.p.stderr):
                try:
                    s.close()
                except Exception:
                    pass


def _env(root, db, lane="posts", run_id="5"):
    env = dict(os.environ)
    env.update({"JOBHUNTER_APP_ROOT": str(root), "JOBHUNTER_DB": str(db), "JOBHUNTER_LANE": lane,
                "JOBHUNTER_RUN_ID": run_id, "JOBHUNTER_CONFIG": str(Path(db).parent / "config.json"),
                "PYTHONIOENCODING": "utf-8"})
    return env


def _call(sess, name, args):
    r = sess.send("tools/call", {"name": name, "arguments": args})
    res = r["result"]
    return json.loads(res["content"][0]["text"]), res.get("isError", False)


class McpProtocolSubprocessTest(unittest.TestCase):
    """Protocol round-trip against a stub jh package (always runs)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = write_stub_root(Path(self.td.name) / "root")
        self.db = Path(self.td.name) / "data" / "jobhunter.db"
        self.db.parent.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_roundtrip(self):
        s = McpSession(_env(self.root, self.db))
        try:
            r = s.send("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})
            self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")
            self.assertEqual(r["result"]["serverInfo"]["name"], "jobhunt")
            self.assertIn("tools", r["result"]["capabilities"])
            s.send("notifications/initialized", notify=True)
            self.assertEqual(s.send("ping")["result"], {})
            tools = s.send("tools/list")["result"]["tools"]
            names = {t["name"] for t in tools}
            for n in ("log_step", "already_contacted", "save_job", "save_contact", "send_application",
                      "save_portal_prefill", "get_rules", "build_resume", "list_portal_candidates"):
                self.assertIn(n, names)
            for t in tools:
                self.assertEqual(t["inputSchema"]["type"], "object")
            res, err = _call(s, "log_step", {"message": "Opening LinkedIn — café ✓"})
            self.assertFalse(err)
            self.assertEqual(res, {"ok": True})
            # tool errors are isError results, not protocol errors
            res, err = _call(s, "log_step", {})
            self.assertTrue(err)
            res, err = _call(s, "nope", {})
            self.assertTrue(err)
            # unknown method -> JSON-RPC error
            self.assertEqual(s.send("does/not/exist")["error"]["code"], -32601)
            # full flow
            job, err = _call(s, "save_job", {"source": "posts", "company": "Acme", "role": "Salesforce Developer",
                                             "decision": "qualified", "fit_score": 150})
            self.assertFalse(err)
            c, err = _call(s, "save_contact", {"job_id": job["job_id"], "name": "Jane", "title": "Recruiter",
                                               "email": "jane@acme.example", "email_source": "post"})
            self.assertFalse(err)
            resume = {"headline": "h", "summary": "s", "skills": [["a", "b"]] * 6,
                      "project_order": ["bcbsm", "amkor", "bogus"]}
            sent, err = _call(s, "send_application", {"job_id": job["job_id"], "contact_id": c["contact_id"],
                                                      "subject": "Hi", "body": "Body", "resume": resume})
            self.assertFalse(err)
            self.assertTrue(sent["ok"])
            ac, _ = _call(s, "already_contacted", {"company": "Acme", "role": "Salesforce Developer", "emails": []})
            self.assertTrue(ac.get("contacted"), ac)
            dup, err = _call(s, "save_contact", {"job_id": job["job_id"], "name": "Jane", "title": "Recruiter",
                                                 "email": "jane@acme.example", "email_source": "post"})
            self.assertTrue(err)
            skipped, err = _call(s, "save_job", {"source": "jobright", "company": "Beta", "role": "SF Dev",
                                                 "decision": "skipped", "fit_score": 40})
            self.assertTrue(err)  # skip_reason required
            j2, _ = _call(s, "save_job", {"source": "jobright", "company": "Beta", "role": "SF Dev",
                                          "decision": "skipped", "skip_reason": "no email found", "fit_score": 80,
                                          "apply_url": "https://jobs.lever.co/beta/123"})
            cands, err = _call(s, "list_portal_candidates", {})
            self.assertFalse(err)
            self.assertEqual(cands["jobs"][0]["id"], j2["job_id"])
            self.assertTrue(cands["jobs"][0]["ats_link"])
            br, err = _call(s, "build_resume", {"job_id": j2["job_id"], "resume": resume})
            self.assertFalse(err)
            self.assertTrue(br["resume_path"].endswith(".pdf"))
            pp, err = _call(s, "save_portal_prefill", {"job_id": j2["job_id"], "ats": "lever",
                                                       "apply_url": "https://jobs.lever.co/beta/123",
                                                       "resume": resume, "questions_left": ["Work authorization"]})
            self.assertFalse(err)
            self.assertIn("portal_id", pp)
            rules, err = _call(s, "get_rules", {})
            self.assertIn("Never invent", rules["rules"])
        finally:
            s.close()
        ev = db_rows(self.db, "SELECT * FROM events WHERE type='step'")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["lane"], "posts")
        self.assertEqual(ev[0]["run_id"], 5)
        self.assertEqual(ev[0]["message"], "Opening LinkedIn — café ✓")
        jobs = db_rows(self.db, "SELECT * FROM jobs ORDER BY id")
        self.assertEqual(jobs[0]["fit_score"], 100)  # clamped
        emails = db_rows(self.db, "SELECT * FROM emails")
        self.assertEqual(len(emails), 1)

    def test_parse_error_and_missing_backend(self):
        env = _env(Path(self.td.name) / "empty_root", self.db)
        s = McpSession(env)
        try:
            s.p.stdin.write(b"this is not json\n")
            s.p.stdin.flush()
            err = json.loads(s.p.stdout.readline())
            self.assertEqual(err["error"]["code"], -32700)
            s.send("initialize", {})
            self.assertTrue(s.send("tools/list")["result"]["tools"])
            res, is_err = _call(s, "log_step", {"message": "x"})
            self.assertTrue(is_err)
            self.assertIn("cannot import", res["error"])
            # get_rules works without backend
            res, is_err = _call(s, "get_rules", {})
            self.assertFalse(is_err)
        finally:
            s.close()


class McpInProcessStubTest(unittest.TestCase):
    """Dispatch via handle()/serve() with stub modules injected through sys.modules."""

    def test_serve_with_sys_modules_stub(self):
        m = load_mcp_module()
        with tempfile.TemporaryDirectory() as td:
            mods = load_stub_modules(write_stub_root(Path(td) / "root"))
            db = str(Path(td) / "t.db")
            with mock.patch.dict(sys.modules, mods), \
                 mock.patch.dict(os.environ, {"JOBHUNTER_DB": db, "JOBHUNTER_LANE": "portal", "JOBHUNTER_RUN_ID": ""}):
                ctx = m.Ctx()
                msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "log_step", "arguments": {"message": "hello", "level": "bogus"}}},
                        [{"jsonrpc": "2.0", "id": 3, "method": "ping"}]]
                inp = io.BytesIO(b"".join(json.dumps(x).encode() + b"\n" for x in msgs))
                out = io.BytesIO()
                m.serve(inp, out, ctx)
                lines = [json.loads(l) for l in out.getvalue().splitlines()]
                self.assertEqual(lines[0]["result"]["protocolVersion"], m.DEFAULT_PROTOCOL)
                self.assertFalse(lines[1]["result"]["isError"])
                self.assertEqual(lines[2], [{"jsonrpc": "2.0", "id": 3, "result": {}}])
                self.assertIsNone(ctx.run_id)
            rows = db_rows(db, "SELECT lane, level, message, run_id FROM events")
            self.assertEqual(rows, [{"lane": "portal", "level": "info", "message": "hello", "run_id": None}])


@unittest.skipUnless(real_backend_available(), "real jh.actions/jh.db not present yet")
class McpRealBackendIntegrationTest(unittest.TestCase):
    def test_log_step_and_save_job_with_real_backend(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "jobhunter.db"
            env = _env(APP_ROOT, db, lane="jobright", run_id="")
            env["JOBHUNTER_DATA"] = td
            s = McpSession(env)
            try:
                s.send("initialize", {"protocolVersion": "2025-06-18"})
                s.send("notifications/initialized", notify=True)
                res, err = _call(s, "log_step", {"message": "integration hello"})
                self.assertFalse(err, res)
                res, err = _call(s, "already_contacted", {"company": "Zeta", "role": "Salesforce Developer", "emails": []})
                self.assertFalse(err, res)
                self.assertFalse(res["contacted"])
                res, err = _call(s, "save_job", {"source": "jobright", "company": "Zeta", "role": "Salesforce Developer",
                                                 "decision": "skipped", "skip_reason": "W2 only", "fit_score": 20})
                self.assertFalse(err, res)
                self.assertIn("job_id", res)
            finally:
                s.close()
            rows = db_rows(db, "SELECT message FROM events WHERE type='step'")
            self.assertIn("integration hello", [r["message"] for r in rows])


if __name__ == "__main__":
    unittest.main()
