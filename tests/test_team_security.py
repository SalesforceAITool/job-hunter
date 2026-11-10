"""gstack /cso regression tests: settings API can't repoint the engine, cross-site GETs refused, secrets masked."""
import http.client
import json
import socket
import sys
import unittest

from test_backend_helpers import TempData
from jh import actions, config, engine, runner, server


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


class _Srv(TempData):
    def setUp(self):
        super().setUp()
        self.port = free_port()
        self.srv = server.create_server(self.port, web_dir=self.data / "web_missing", start_watch=False)
        server.serve_in_thread(self.srv)

    def tearDown(self):
        server.shutdown_server(self.srv)
        super().tearDown()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        h = {"Content-Type": "application/json"} if isinstance(body, dict) else {}
        h.update(headers or {})
        c.request(method, path, body=data, headers=h)
        r = c.getresponse(); raw = r.read(); c.close()
        try:
            return r.status, json.loads(raw or b"{}")
        except ValueError:
            return r.status, {}

    def same_origin(self):
        return {"Origin": "http://127.0.0.1:%d" % self.port, "Sec-Fetch-Site": "same-origin"}


class SettingsApi(_Srv, unittest.TestCase):
    def test_api_cannot_repoint_engine_or_paths(self):
        before = config.load()
        evil = sys.executable
        st, _ = self.req("POST", "/api/settings", {
            "max_emails_per_run": 40, "data_dir": "C:/attacker", "db_path": "C:/attacker/x.db",
            "engine": {"claude_path": evil, "python_path": evil, "model": "opus",
                       "claude_args": ["--mcp-config=C:/x/evil.json"]}}, self.same_origin())
        self.assertEqual(st, 200)
        cfg = config.load()
        self.assertEqual(cfg["engine"]["claude_path"], before["engine"]["claude_path"])
        self.assertEqual(cfg["engine"].get("python_path"), before["engine"].get("python_path"))
        self.assertEqual(cfg["engine"]["claude_args"], before["engine"]["claude_args"])
        self.assertNotIn("db_path", {k for k, v in cfg.items() if v == "C:/attacker/x.db"})
        self.assertEqual((cfg["max_emails_per_run"], cfg["engine"]["model"]), (40, "opus"))   # normal settings still save

    def test_settings_locked_during_run(self):
        orig = runner.is_running
        runner.is_running = lambda: True
        try:
            st, _ = self.req("POST", "/api/settings", {"dry_run": False}, self.same_origin())
        finally:
            runner.is_running = orig
        self.assertEqual(st, 409)

    def test_equals_form_flag_is_stripped(self):
        argv, _ = engine.build_command("posts", {"engine": {"claude_path": sys.executable,
                                                             "claude_args": ["--chrome", "--mcp-config=C:/x/evil.json"]}},
                                       engine.lane_prompt_path("posts"), self.data, run_id=1)
        self.assertFalse(any(a.startswith("--mcp-config=") for a in argv))

    def test_cross_site_get_refused_post_refused_rebinding_refused(self):
        st, _ = self.req("GET", "/api/env", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "image"})
        self.assertEqual(st, 403)
        st, _ = self.req("POST", "/api/run", {}, {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)
        st, _ = self.req("GET", "/api/settings", headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(st, 403)


class Secrets(TempData, unittest.TestCase):
    def test_mask_and_filter(self):
        cfg = config.load()
        cfg["email"]["app_password"] = "abcdefghijklmnop"
        self.assertNotIn("op", config.masked(cfg)["email"]["app_password"])
        self.assertTrue(actions._leaks_secret("code: a.b.c.d.e.f.g.h.i.j.k.l.m.n.o.p", cfg))
        self.assertTrue(actions._leaks_secret("ponmlkjihgfedcba", cfg))
        self.assertFalse(actions._leaks_secret("Hello, I am a Salesforce developer", cfg))

    def test_password_not_in_engine_env_argv_or_prompt(self):
        cfg = config.load()
        cfg["email"]["app_password"] = "zzsecretzzsecret"
        config.save(cfg)
        argv, env = engine.build_command("posts", config.load(), engine.lane_prompt_path("posts"), self.data, run_id=1)
        blob = json.dumps(argv) + json.dumps(env) + (self.data / "engine" / "posts.system.md").read_text()
        self.assertNotIn("zzsecretzzsecret", blob)

    def test_non_http_urls_not_stored(self):
        r = actions.save_job(self.conn, None, "posts", "posts", "Acme", "Salesforce Developer",
                             url="javascript:alert(1)", apply_url="file:///c:/x", decision="qualified")
        row = self.conn.execute("SELECT url, apply_url FROM jobs WHERE id=?", (r["job_id"],)).fetchone()
        self.assertEqual((row["url"], row["apply_url"]), ("", None))


if __name__ == "__main__":
    unittest.main()
