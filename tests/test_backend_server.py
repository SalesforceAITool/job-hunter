import http.client
import json
import socket
import threading
import time
import unittest

from test_backend_helpers import FIXTURE, TempData
from jh import config, events, server


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class ServerSmokeTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.data / "tracker.csv").write_bytes(FIXTURE.read_bytes())  # auto-import on first start
        self.port = free_port()
        self.web = self.data / "web_missing"
        self.srv = server.create_server(self.port, web_dir=self.web)
        self.srv.sse_heartbeat = 0.5
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
        r = c.getresponse()
        raw = r.read()
        c.close()
        ctype = r.getheader("Content-Type") or ""
        return r.status, (json.loads(raw) if "json" in ctype else raw.decode("utf-8", "replace")), r

    def test_summary_and_applications(self):
        st, s, _ = self.req("GET", "/api/summary")
        self.assertEqual(st, 200)
        for k in ("sent_today", "sent_7d", "sent_total", "replies_total", "reply_rate", "awaiting", "nudges_due",
                  "bounced", "skipped_today", "per_hour_current_run", "last_run", "running", "by_source"):
            self.assertIn(k, s)
        self.assertEqual(s["sent_total"], 24)  # auto-imported
        self.assertFalse(s["running"])
        st, rows, resp = self.req("GET", "/api/applications?limit=5")
        self.assertEqual(st, 200)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 5)
        self.assertEqual(resp.getheader("X-Total-Count"), "24")
        for k in ("contact_id", "job_id", "company", "role", "source", "contact_name", "contact_title", "email",
                  "email_source", "sent_at", "last_touch_at", "next_followup_at", "app_status", "url"):
            self.assertIn(k, rows[0])
        st, bounced, _ = self.req("GET", "/api/applications?status=bounced")
        self.assertEqual([r["email"] for r in bounced], ["hr@talentgigs.in"])
        st, q, _ = self.req("GET", "/api/applications?q=celestica")
        self.assertEqual(len(q), 2)
        st, d, _ = self.req("GET", "/api/applications/%d" % rows[0]["contact_id"])
        self.assertEqual(st, 200)
        for k in ("job", "contact", "emails", "replies", "timeline"):
            self.assertIn(k, d)
        st, skipped, _ = self.req("GET", "/api/jobs?status=skipped")
        self.assertTrue(len(skipped) >= 30)
        st, ch, _ = self.req("GET", "/api/charts")
        self.assertEqual(len(ch["daily"]), 14)
        self.assertIn("funnel", ch)
        self.assertEqual(self.req("GET", "/api/applications/99999")[0], 404)
        self.assertEqual(self.req("GET", "/api/nope")[0], 404)
        self.assertEqual(self.req("POST", "/api/summary", {})[0], 405)

    def test_placeholder_and_static(self):
        st, body, _ = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("Job Hunter", body)
        self.web.mkdir()
        (self.web / "app.css").write_text("body{}")
        st, body, r = self.req("GET", "/app.css")
        self.assertEqual(st, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("text/css"))
        self.assertEqual(self.req("GET", "/../config.json")[0], 404)

    def test_settings_masking(self):
        st, s, _ = self.req("POST", "/api/settings", {"email": {"app_password": "abcd efgh ijkl mnop"},
                                                      "max_emails_per_run": "10"})
        self.assertEqual(st, 200)
        self.assertEqual(s["email"]["app_password"], "••••")
        self.assertEqual(config.load()["email"]["app_password"], "abcdefghijklmnop")
        self.assertEqual(config.load()["max_emails_per_run"], 10)
        # posting the masked value back keeps the real password
        st, s, _ = self.req("POST", "/api/settings", {"email": s["email"], "dry_run": False})
        self.assertEqual(config.load()["email"]["app_password"], "abcdefghijklmnop")
        self.assertFalse(config.load()["dry_run"])
        st, g, _ = self.req("GET", "/api/settings")
        self.assertTrue(g["email"]["app_password"].startswith("•"))
        self.assertEqual(self.req("POST", "/api/settings", b"{not json", {"Content-Type": "application/json"})[0], 400)

    def test_cross_origin_blocked(self):
        st, _, _ = self.req("POST", "/api/run", {}, {"Origin": "https://evil.example"})
        self.assertEqual(st, 403)

    def test_csrf_null_origin_and_cross_site_fetch_blocked(self):
        """Regression: `Origin: null` (sandboxed iframe / data: page) was accepted, so any web page could POST
        /api/settings (claude_path / claude_args = code execution on the next run) or start runs."""
        evil = {"engine": {"claude_args": ["--allowedTools", "Bash"]}}
        self.assertEqual(self.req("POST", "/api/settings", evil, {"Origin": "null"})[0], 403)
        self.assertEqual(self.req("POST", "/api/run", {}, {"Origin": "null"})[0], 403)
        self.assertEqual(self.req("POST", "/api/stop", {}, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.req("POST", "/api/stop", {}, {"Origin": "http://evil.example:%d" % self.port})[0], 403)
        self.assertNotIn("Bash", config.load()["engine"]["claude_args"][:2])
        # the app's own page and non-browser clients still work
        ok = {"Origin": "http://127.0.0.1:%d" % self.port, "Sec-Fetch-Site": "same-origin"}
        self.assertEqual(self.req("POST", "/api/stop", {}, ok)[0], 200)
        self.assertEqual(self.req("POST", "/api/stop", {})[0], 200)
        self.assertEqual(self.req("GET", "/api/summary", headers={"Origin": "null"})[0], 200)
        # DNS rebinding: foreign Host header
        self.assertEqual(self.req("GET", "/api/summary", headers={"Host": "evil.example:%d" % self.port})[0], 403)

    def test_second_instance_does_not_mark_live_run_stale(self):
        """Regression: create_server ran mark_stale_runs before binding, so a second launch that lost the port
        flipped the first instance's in-progress run to 'error'."""
        from jh import db
        self.conn.execute("INSERT INTO runs (id, started_at, status, trigger) VALUES (99, ?, 'running', 'open')",
                          (db.now_iso(),))
        with self.assertRaises(OSError):
            server.create_server(self.port, web_dir=self.web, start_tailer=False)
        self.assertEqual(self.conn.execute("SELECT status FROM runs WHERE id=99").fetchone()[0], "running")

    def test_env_and_portal_and_import(self):
        st, env, _ = self.req("GET", "/api/env")
        self.assertEqual(st, 200)
        self.assertIsInstance(env, list)
        ids = {i["id"] for i in env}
        for k in ("python", "reportlab", "claude_cli", "claude_auth", "gmail_configured", "data_dir"):
            self.assertIn(k, ids)
        for i in env:
            self.assertEqual(set(i) >= {"id", "label", "ok", "detail", "fix"}, True)
        st, p, _ = self.req("GET", "/api/portal")
        self.assertEqual(p, [])
        self.assertEqual(self.req("POST", "/api/portal/42", {"status": "submitted"})[0], 404)
        # re-import through the API (multipart) is idempotent
        boundary = "XyZ"
        body = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"tracker.csv\"\r\n"
                "Content-Type: text/csv\r\n\r\n" % boundary).encode() + FIXTURE.read_bytes() + \
            ("\r\n--%s--\r\n" % boundary).encode()
        st, res, _ = self.req("POST", "/api/import", body, {"Content-Type": "multipart/form-data; boundary=" + boundary})
        self.assertEqual(st, 200, res)
        self.assertEqual(res["counts"]["contacts"], 0)
        self.assertEqual(res["counts"]["duplicates"], 60)

    def test_sse_receives_emitted_event(self):
        got, ready = [], threading.Event()

        def listen():
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/api/stream")
            r = c.getresponse()
            got.append(("status", r.status, r.getheader("Content-Type")))
            ready.set()
            buf = b""
            t_end = time.time() + 8
            while time.time() < t_end:
                line = r.fp.readline()
                if not line:
                    break
                buf += line
                if b"hello from test" in buf and b"heartbeat" in buf:
                    break
            got.append(("body", buf.decode()))
            c.close()

        th = threading.Thread(target=listen, daemon=True)
        th.start()
        self.assertTrue(ready.wait(5))
        for _ in range(50):  # wait until the handler subscribed
            if events.subscriber_count():
                break
            time.sleep(0.05)
        events.emit(self.conn, None, "posts", "step", "hello from test")
        th.join(10)
        self.assertEqual(got[0][1], 200)
        self.assertTrue(got[0][2].startswith("text/event-stream"))
        body = got[-1][1]
        self.assertIn("event: ev", body)
        self.assertIn("hello from test", body)
        self.assertIn(": heartbeat", body)
        data_line = [ln for ln in body.splitlines() if ln.startswith("data:") and "hello from test" in ln][0]
        ev = json.loads(data_line[5:])
        self.assertEqual(ev["lane"], "posts")
        self.assertEqual(ev["type"], "step")
        # backlog endpoint returns it too
        st, evs, _ = self.req("GET", "/api/events?after_id=%d" % (ev["id"] - 1))
        self.assertEqual(evs[0]["id"], ev["id"])

    def test_tailer_picks_up_other_process_events_once(self):
        q = events.subscribe()
        try:
            # simulate the MCP server process: raw insert on another connection
            from jh import db
            other = db.connect()
            other.execute("INSERT INTO events (ts, run_id, lane, level, type, message, data_json)"
                          " VALUES (?, 3, 'jobright', 'info', 'step', 'from mcp', '{}')", (db.now_iso(),))
            other.close()
            local = events.emit(self.conn, 3, "jobright", "step", "from app")
            seen = []
            t_end = time.time() + 3
            while time.time() < t_end:
                try:
                    seen.append(q.get(timeout=0.2))
                except Exception:
                    pass
            msgs = [e["message"] for e in seen if e.get("type") == "step"]
            self.assertEqual(msgs.count("from mcp"), 1)
            self.assertEqual(msgs.count("from app"), 1)
            self.assertTrue(local["id"])
        finally:
            events.unsubscribe(q)


if __name__ == "__main__":
    unittest.main()
