"""Profiles (Akshay, 30 Oct): Run asks "apply with which profile?" — Akshay or Ravi — and a Ravi run uses only Ravi's
facts, resume, database and Chrome, and never sends email in his name."""
import json
import os
import socket
import threading
import types
import unittest
import urllib.request
from pathlib import Path

from test_backend_helpers import TempData
from jh import actions, config, db, engine, profiles, runner

RAVI = {"name": "Ravi Kumawat", "email": "ravikumawat171477@gmail.com", "phone": "+91 94793 02096",
        "linkedin": "https://www.linkedin.com/in/ravi-kumawat-01954932a/", "linkedin_name": "Ravi Kumawat",
        "resume_file": "Ravi_Kumawat_Resume.pdf", "answers": {"total_years": "5", "notice": "Immediate joiner"},
        "resume_text": "Ravi Kumawat - Salesforce Developer, LWC, Apex, CPQ"}


class ProfileTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cfg = config.load()
        cfg["profiles"] = {"ravi": dict(RAVI)}
        config.save(cfg)
        d = profiles.profile_dir("ravi")
        (d / "resumes" / "Ravi_Kumawat_Resume.pdf").write_bytes(b"%PDF-1.4 ravi")

    def test_listing_and_run_config(self):
        ids = [p["id"] for p in profiles.listing()]
        self.assertEqual(ids, ["akshay", "ravi"])
        rc = profiles.run_config(config.load(), "ravi")
        self.assertEqual(rc["profile"]["email"], "ravikumawat171477@gmail.com")
        self.assertTrue(rc["dry_run"])                                   # never email in his name
        self.assertEqual(rc["email"]["app_password"], "")
        self.assertFalse(rc["li_fast"]["on"])
        self.assertTrue(rc["db_path"].endswith(os.path.join("profiles", "ravi", "jobhunter.db")))
        self.assertTrue(rc["resume_pdf"].endswith("Ravi_Kumawat_Resume.pdf"))
        self.assertTrue(Path(rc["config_path"]).is_file())
        self.assertNotIn("profiles", json.loads(Path(rc["config_path"]).read_text()))
        self.assertIsNone(profiles.get(config.load(), "nobody"))
        self.assertFalse(profiles.is_known(config.load(), "../etc"))

    def test_prompt_is_ravis_not_akshays(self):
        rc = profiles.run_config(config.load(), "ravi")
        t = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), 1, auto_submit=True,
                                         prof_cfg=rc)
        self.assertIn("THIS RUN APPLIES FOR RAVI KUMAWAT", t)
        self.assertIn("ravikumawat171477@gmail.com", t)
        self.assertNotIn("akshay.verma0031@gmail.com", t)
        self.assertNotIn("+91 75664 42162", t)
        self.assertIn('starts with **"Ravi Kumawat"**', t)
        self.assertNotIn("current CTC 20 LPA", t)                       # Akshay's standard answers left out
        self.assertIn("Auto-submit is ON", t)
        # Akshay's own runs are unchanged
        t2 = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), 1, auto_submit=True)
        self.assertIn("akshay.verma0031@gmail.com", t2)
        self.assertNotIn("RAVI", t2)

    def test_engine_uses_his_database_and_config(self):
        rc = profiles.run_config(config.load(), "ravi")
        argv, env = engine.build_command("easy_apply", dict(rc, engine=dict(rc["engine"], mock=True)),
                                         str(engine.PROMPTS_DIR / "easy_apply.md"), engine.data_dir_for(rc), run_id=1)
        self.assertEqual(env["JOBHUNTER_DB"], rc["db_path"])
        self.assertEqual(env["JOBHUNTER_CONFIG"], rc["config_path"])
        self.assertEqual(env["JOBHUNTER_DATA"], rc["data_dir"])

    def test_resume_is_his_own_pdf_unchanged(self):
        rc = profiles.run_config(config.load(), "ravi")
        os.environ["JOBHUNTER_CONFIG"] = rc["config_path"]
        os.environ["JOBHUNTER_DATA"] = rc["data_dir"]
        try:
            conn = db.connect(rc["db_path"])
            jid = actions.save_job(conn, None, "easy_apply", "linkedin_india", "Acme", "Salesforce Developer")["job_id"]
            p = actions.build_resume_only(conn, jid, {"headline": "anything"})
            self.assertEqual(Path(p).read_bytes(), b"%PDF-1.4 ravi")
            self.assertTrue(p.endswith("Ravi_Kumawat_Resume.pdf"))
            conn.close()
        finally:
            os.environ["JOBHUNTER_DATA"] = self._tmp
            os.environ.pop("JOBHUNTER_CONFIG", None)

    def test_api_run_with_profile_and_view(self):
        from jh import server
        seen = []
        orig = runner._load_engine
        self.addCleanup(setattr, runner, "_load_engine", orig)
        runner._load_engine = lambda: types.SimpleNamespace(
            run_lane=lambda r, lane, cfg, *a: seen.append((lane, cfg.get("profile_id"), cfg.get("db_path"))) or {"status": "done"})
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        srv = server.create_server(port, web_dir=self.data / "nope", start_tailer=False, start_watch=False)
        server.serve_in_thread(srv)
        self.addCleanup(server.shutdown_server, srv)
        base = "http://127.0.0.1:%d" % port
        lst = json.loads(urllib.request.urlopen(base + "/api/profiles").read())
        self.assertEqual([p["id"] for p in lst["profiles"]], ["akshay", "ravi"])
        req = urllib.request.Request(base + "/api/run", data=json.dumps({"trigger": "button", "profile": "ravi"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        res = json.loads(urllib.request.urlopen(req).read())
        self.assertEqual(res["profile"], "ravi")
        self.assertTrue(runner.wait(30))
        self.assertEqual([x[0] for x in seen], ["easy_apply"])           # application-only
        self.assertEqual(seen[0][1], "ravi")
        self.assertTrue(seen[0][2].endswith(os.path.join("profiles", "ravi", "jobhunter.db")))
        # his run is in his database, not Akshay's
        runs_r = json.loads(urllib.request.urlopen(base + "/api/runs?profile=ravi").read())
        runs_a = json.loads(urllib.request.urlopen(base + "/api/runs").read())
        ids = lambda x: [r["id"] for r in (x.get("runs") if isinstance(x, dict) else x)]
        self.assertIn(res["run_id"], ids(runs_r))
        self.assertEqual(ids(runs_a), [])                                # nothing in Akshay's database


if __name__ == "__main__":
    unittest.main()


class ProfileChromeTests(TempData, unittest.TestCase):
    """Akshay, 30 Oct: Ravi picked -> LinkedIn only in Ravi's Chrome; Akshay picked -> only in Akshay's."""
    AK = "3b7d6d57-059f-4afc-aa29-2371bfb387e2"
    RV = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        super().setUp()
        cfg = config.load()
        cfg["engine"]["chrome_device_id"] = self.AK
        cfg["profiles"] = {"ravi": dict(RAVI)}
        config.save(cfg)

    def test_ravi_run_excludes_akshays_chrome_and_locks_his_own(self):
        import sys
        sys.path.insert(0, str(Path(engine.__file__).resolve().parent.parent / "engine"))
        import browser_guard as g
        rc = profiles.run_config(config.load(), "ravi")
        self.assertEqual(rc["engine"]["chrome_device_id"], "")
        self.assertEqual(rc["engine"]["exclude_device_ids"], [self.AK])
        ev = lambda dev: {"session_id": "s", "tool_name": "mcp__claude-in-chrome__select_browser", "tool_input": {"deviceId": dev}}
        self.assertFalse(g.decide(ev(self.AK), rc["config_path"], rc)[0])         # Akshay's Chrome: blocked
        self.assertTrue(g.decide(ev(self.RV), rc["config_path"], rc)[0])          # first run: finds his own
        # the lane locks Ravi's Chrome in Ravi's config; Akshay's can never be saved as Ravi's
        import importlib.util
        spec = importlib.util.spec_from_file_location("jhmcp", str(Path(engine.__file__).resolve().parent.parent / "engine" / "jobhunt_mcp.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        os.environ["JOBHUNTER_CONFIG"] = rc["config_path"]
        os.environ["JOBHUNTER_DB"] = rc["db_path"]
        try:
            ctx = m.Ctx()
            self.assertTrue(m.call_tool(ctx, "set_preferred_browser", {"device_id": self.AK})[1])
            self.assertFalse(m.call_tool(ctx, "set_preferred_browser", {"device_id": self.RV, "name": "Ravi"})[1])
        finally:
            os.environ.pop("JOBHUNTER_CONFIG", None)
            os.environ.pop("JOBHUNTER_DB", None)
        self.assertEqual(profiles.device_of(config.load(), "ravi"), self.RV)
        rc2 = profiles.run_config(config.load(), "ravi")
        self.assertEqual(rc2["engine"]["chrome_device_id"], self.RV)              # next Ravi run: locked to his
        self.assertFalse(g.decide(ev(self.AK), rc2["config_path"], rc2)[0])
        self.assertTrue(g.decide(ev(self.RV), rc2["config_path"], rc2)[0])
        # Akshay's own runs stay locked to Akshay's Chrome
        main = config.load()
        self.assertFalse(g.decide(ev(self.RV), str(config.config_path()), main)[0])
        self.assertTrue(g.decide(ev(self.AK), str(config.config_path()), main)[0])
        self.assertEqual(profiles.other_devices(config.load(), "akshay"), [self.RV])
