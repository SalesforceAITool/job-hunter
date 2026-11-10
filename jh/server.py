"""HTTP API + SSE + static UI (SPEC §6). Bound to 127.0.0.1 only.

Usage:
    srv = create_server(port)        # inits DB, auto-import, event tailer, stats pump
    serve_in_thread(srv)  /  srv.serve_forever()
    shutdown_server(srv)
"""
import json
import logging
import mimetypes
import os
import queue
import re
import threading
import time
import traceback
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import actions, config, db, envcheck, events, importer, mailer, qa, runner, watch

log = logging.getLogger("jobhunter.server")
MAX_BODY = 20 * 1024 * 1024

PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>Job Hunter</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:14px system-ui,sans-serif;background:#0d0f12;color:#e6e6e6;margin:0;padding:24px}
pre{background:#16191e;padding:12px;border-radius:8px;max-height:50vh;overflow:auto;font-size:12px}
button{background:#5b8cff;color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer}</style></head>
<body><h1>Job Hunter</h1><p>The UI (web/index.html) is not installed yet. The API is running.</p>
<p><button onclick="fetch('/api/run',{method:'POST'}).then(r=>r.json()).then(j=>log(JSON.stringify(j)))">Run now</button>
<button onclick="fetch('/api/stop',{method:'POST'})">Stop</button></p>
<h3>Summary</h3><pre id="s"></pre><h3>Live events</h3><pre id="e"></pre>
<script>
const e=document.getElementById('e');function log(t){e.textContent+=t+"\\n";e.scrollTop=1e9}
function s(){fetch('/api/summary').then(r=>r.json()).then(j=>document.getElementById('s').textContent=JSON.stringify(j,null,2))}
s();const es=new EventSource('/api/stream');
es.addEventListener('ev',m=>{const v=JSON.parse(m.data);if(v.type==='stats'){s();return}
log((v.ts||'').slice(11,19)+' ['+(v.lane||'-')+'] '+v.type+': '+v.message)});
</script></body></html>"""


class JobHunterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # on Windows SO_REUSEADDR would allow double binds

    def __init__(self, addr, handler, db_path=None, web_dir=None):
        super().__init__(addr, handler)
        self.db_path = db_path
        self.web_dir = Path(web_dir) if web_dir else config.app_root() / "web"
        self.stopping = threading.Event()
        self.sse_heartbeat = 15.0
        self.pump = None
        self.watcher = watch.Watcher(db_path)


class Handler(BaseHTTPRequestHandler):
    server_version = "JobHunter/1.0"
    protocol_version = "HTTP/1.0"

    # ------------------------------------------------------------- plumbing
    def log_message(self, fmt, *args):  # never write to stderr (None under pythonw)
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _conn(self):
        # ?profile=<id>: read that person's own database (Ravi's applications are not in Akshay's)
        return db.connect(getattr(self, "_profile_db", None) or self.server.db_path)

    def _send(self, status, body: bytes, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=200, extra=None):
        self._send(status, json.dumps(obj, default=str).encode("utf-8"), "application/json; charset=utf-8", extra)

    def _error(self, status, msg):
        self._json({"error": msg}, status)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("request body too large")
        return self.rfile.read(n) if n > 0 else b""

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("invalid JSON body")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _allowed_origin(self, method="GET") -> bool:
        """Block cross-site requests and DNS rebinding: Host/Origin must be this local server.

        State-changing requests (POST) additionally refuse `Origin: null` (sandboxed iframes, data: and
        file: pages send it - a classic CSRF bypass) and browsers' `Sec-Fetch-Site: cross-site/same-site`.
        Non-browser clients (app.py, tests) send neither header and are allowed.
        """
        port = self.server.server_address[1]
        ok_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        host = (self.headers.get("Host") or "").lower()
        if host and host not in ok_hosts:
            return False
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin == "null":
            return method == "GET"
        if origin and (not origin.startswith("http://") or origin[len("http://"):] not in ok_hosts):
            return False
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if method != "GET":
            if fetch_site and fetch_site not in ("same-origin", "none"):
                return False
        elif fetch_site == "cross-site" and urlparse(self.path).path.startswith("/api/"):
            return False          # <img src=http://127.0.0.1:8765/api/env> from any website
        return True

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        qs = {k: v[-1] for k, v in parse_qs(url.query).items()}
        self._profile_db = None
        if qs.get("profile") and path.startswith("/api/") and path not in ("/api/run", "/api/settings"):
            from . import profiles
            if profiles.is_known(config.load(), qs["profile"]):
                self._profile_db = profiles.db_path(qs["profile"])
        if not self._allowed_origin(method):
            return self._error(403, "forbidden origin")
        try:
            for m, pattern, fn in ROUTES:
                if m != method:
                    continue
                match = re.fullmatch(pattern, path)
                if match:
                    return fn(self, qs, *match.groups())
            if method == "GET" and not path.startswith("/api/"):
                return self._static(url.path)
            if any(re.fullmatch(p, path) for _, p, _ in ROUTES):
                return self._error(405, "method not allowed")
            return self._error(404, "not found: %s" % path)
        except ValueError as e:
            return self._error(400, str(e))
        except OverflowError:                 # an id beyond SQLite's 64-bit range: nothing can match it
            return self._error(400, "number out of range")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return None
        except Exception as e:
            log.error("error on %s %s: %s\n%s", method, self.path, e, traceback.format_exc())
            try:
                return self._error(500, "%s: %s" % (type(e).__name__, e))
            except Exception:
                return None

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # ------------------------------------------------------------- static
    def _static(self, raw_path):
        web = self.server.web_dir.resolve()
        rel = raw_path.lstrip("/") or "index.html"
        target = (web / rel).resolve()
        try:
            target.relative_to(web)
        except ValueError:
            return self._error(404, "not found")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            if rel in ("index.html",) or raw_path in ("/", ""):
                return self._send(200, PLACEHOLDER.encode("utf-8"), "text/html; charset=utf-8")
            return self._error(404, "not found: %s" % raw_path)
        ctype = {".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                 ".json": "application/json"}.get(target.suffix.lower()) or \
            mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ------------------------------------------------------------- API: reads
    def summary(self, qs):
        conn = self._conn()
        try:
            self._json(build_summary(conn))
        finally:
            conn.close()

    def applications(self, qs):
        limit = _int(qs.get("limit"), 200, 1, 5000)
        offset = _int(qs.get("offset"), 0, 0, 10 ** 9)
        conn = self._conn()
        try:
            rows, total = db.applications(conn, qs.get("status"), qs.get("source"), qs.get("q"), limit, offset)
        finally:
            conn.close()
        self._json(rows, extra={"X-Total-Count": str(total if total is not None else len(rows))})

    def application_detail(self, qs, cid):
        conn = self._conn()
        try:
            d = db.application_detail(conn, int(cid))
        finally:
            conn.close()
        if not d:
            return self._error(404, "application %s not found" % cid)
        self._json(d)

    def jobs(self, qs):
        conn = self._conn()
        try:
            rows = db.jobs_list(conn, qs.get("status"), qs.get("run_id"), qs.get("source"), qs.get("q"),
                                _int(qs.get("limit"), 200, 1, 5000), _int(qs.get("offset"), 0, 0, 10 ** 9))
        finally:
            conn.close()
        self._json(rows)

    def runs(self, qs):
        conn = self._conn()
        try:
            self._json(db.runs_list(conn, _int(qs.get("limit"), 50, 1, 1000)))
        finally:
            conn.close()

    def run_detail(self, qs, rid):
        conn = self._conn()
        try:
            d = db.run_detail(conn, int(rid))
        finally:
            conn.close()
        if not d:
            return self._error(404, "run %s not found" % rid)
        d["running"] = runner.current_run_id() == d["id"]
        self._json(d)

    def events_backlog(self, qs):
        conn = self._conn()
        try:
            self._json(db.events_list(conn, qs.get("run_id"), qs.get("after_id"), _int(qs.get("limit"), 500, 1, 5000)))
        finally:
            conn.close()

    def charts(self, qs):
        conn = self._conn()
        try:
            self._json(db.charts(conn))
        finally:
            conn.close()

    def portal(self, qs):
        conn = self._conn()
        try:
            self._json(db.portal_list(conn, qs.get("status")))
        finally:
            conn.close()

    def settings_get(self, qs):
        self._json(config.masked(config.load()))

    def profiles_get(self, qs):
        from . import profiles
        self._json({"profiles": profiles.listing(config.load())})

    def version(self, qs):
        from . import BUILD
        self._json({"build": BUILD})

    def env(self, qs):
        login = str(qs.get("login") or qs.get("gmail_login") or "").lower() in ("1", "true", "yes")
        self._json(envcheck.run_checks(config.load(), check_login=login))

    # ------------------------------------------------------------- API: SSE
    def stream(self, qs):
        q = events.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(b"retry: 3000\n: connected\n\n")
            self.wfile.flush()
            sent_max = 0
            after = self.headers.get("Last-Event-ID") or qs.get("after_id")
            if after and str(after).isdigit():
                conn = self._conn()
                try:
                    backlog = db.events_list(conn, after_id=int(after), limit=2000)
                finally:
                    conn.close()
                for ev in backlog:
                    self._sse_write(ev)
                    sent_max = max(sent_max, ev["id"])
            last_beat = time.time()
            while not self.server.stopping.is_set():
                try:
                    ev = q.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - last_beat >= self.server.sse_heartbeat:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        last_beat = time.time()
                    continue
                if ev.get("id") and ev["id"] <= sent_max:
                    continue
                self._sse_write(ev)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass  # client went away
        finally:
            events.unsubscribe(q)

    def _sse_write(self, ev):
        payload = json.dumps(ev, default=str)
        chunk = ("id: %s\n" % ev["id"] if ev.get("id") else "") + "event: ev\ndata: %s\n\n" % payload
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    # ------------------------------------------------------------- API: writes
    def run_start(self, qs):
        body = self._json_body() if self.headers.get("Content-Length") else {}
        trigger = body.get("trigger") if body.get("trigger") in ("open", "button") else "button"
        try:
            lanes = body.get("lanes") if isinstance(body.get("lanes"), (list, str)) else None
            if isinstance(lanes, str):
                lanes = [x.strip() for x in lanes.split(",")]
            if lanes is not None:
                lanes = [x for x in lanes if isinstance(x, str) and x != "submit"]   # submit only via /api/portal/submit
                if not lanes or runner.normalize_selection(lanes)[0] is None:
                    return self._error(400, "pick at least one lane (submitting is started from Ready to submit)")
            regions = body.get("regions") if isinstance(body.get("regions"), (list, str)) else None
            from . import profiles
            pid = str(body.get("profile") or profiles.DEFAULT_ID)
            cfg = config.load()
            if not profiles.is_known(cfg, pid):
                return self._error(400, "unknown profile: %s" % pid[:40])
            if profiles.get(cfg, pid):
                # another person: application-only run in his own database, with his facts, resume and Chrome
                rid = runner.start_run(trigger, cfg=profiles.run_config(cfg, pid), db_path=profiles.db_path(pid),
                                       lanes=list(profiles.PROFILE_LANES), regions=regions)
                return self._json({"run_id": rid, "profile": pid})
            rid = runner.start_run(trigger, db_path=self.server.db_path, lanes=lanes, regions=regions)
        except runner.AlreadyRunning as e:
            return self._json({"error": str(e), "run_id": runner.current_run_id()}, 409)
        self._json({"run_id": rid})

    def run_stop(self, qs):
        rid = runner.current_run_id()
        ok = runner.stop_run()
        self._json({"ok": ok, "run_id": rid, "message": "stopping" if ok else "no run in progress"})

    def settings_post(self, qs):
        body = self._json_body()
        if runner.is_running():
            return self._error(409, "Settings are locked while a run is going — stop it or wait until it ends.")
        old = config.load()
        new = config.apply_update(old, body, from_api=True)
        config.save(new)
        out = config.masked(new)
        out["_restart_needed"] = new.get("port") != old.get("port")
        self._json(out)

    def test_email(self, qs):
        body = self._json_body()
        cfg = config.apply_update(config.load(), body, from_api=True) if body else config.load()
        ok, err = mailer.test_login(cfg)
        self._json({"ok": ok, "error": err})

    def engine_test(self, qs):
        try:
            engine = runner._load_engine()
            fn = getattr(engine, "engine_smoke_test")
        except Exception as e:
            return self._json({"ok": False, "checks": [{"id": "engine", "ok": False,
                                                        "detail": "AI engine not available: %s" % e}]})
        self._json(fn(config.load()))

    def portal_update(self, qs, pid):
        body = self._json_body()
        conn = self._conn()
        try:
            res = actions.set_portal_status(conn, int(pid), body.get("status"))
        finally:
            conn.close()
        if res.get("error"):
            return self._error(404 if "not found" in res["error"] else 400, res["error"])
        events.stats_dirty.set()
        self._json(res)

    # ------------------------------------------------------------- watch mode + submit selected
    def watch_get(self, qs):
        self._json(self.server.watcher.status())

    def watch_post(self, qs):
        body = self._json_body()
        self._json(self.server.watcher.set(body if isinstance(body, dict) else {}))

    def portal_submit(self, qs):
        body = self._json_body()
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        if not ids:
            return self._error(400, "ids must be a non-empty list of Ready-to-submit items")
        conn = self._conn()
        try:
            res = actions.approve_submit(conn, ids, seen=body.get("seen"))
        finally:
            conn.close()
        res["run_id"] = None
        if res["approved"]:
            try:
                res["run_id"] = runner.start_run("approved", db_path=self.server.db_path, lanes=["submit"])
            except runner.AlreadyRunning:
                res["queued"] = True   # the dispatcher starts the submit lane as soon as the current run ends
        events.stats_dirty.set()
        self._json(res)

    # ------------------------------------------------------------- question bank
    # ------------------------------------------------------------- agencies
    def agencies_get(self, qs):
        from . import agencies
        conn = self._conn()
        try:
            out = agencies.list_all(conn)
            out["settings"] = config.load().get("agencies") or {}
            self._json(out)
        finally:
            conn.close()

    def agencies_add(self, qs):
        from . import agencies
        body = self._json_body()
        conn = self._conn()
        try:
            res = agencies.add(conn, body.get("name"), body.get("website") or "", body.get("region") or "us",
                               body.get("kind") or "Staffing", body.get("notes") or "")
        finally:
            conn.close()
        if res.get("error"):
            return self._error(400, res["error"])
        self._json(res)

    def agency_update(self, qs, aid):
        from . import agencies
        body = self._json_body()
        conn = self._conn()
        try:
            if body.get("region") is not None:
                res = agencies.set_region(conn, int(aid), str(body.get("region") or ""))
            else:
                res = agencies.set_status(conn, int(aid), str(body.get("status") or ""))
        finally:
            conn.close()
        if res.get("error"):
            return self._error(404 if "not found" in res["error"] else 400, res["error"])
        self._json(res)

    # ------------------------------------------------------------- job board
    def jobboard_get(self, qs):
        from . import jobboard
        try:
            hours = int(qs.get("hours") or 48)
        except ValueError:
            hours = 48
        conn = self._conn()
        try:
            self._json(jobboard.list_listings(conn, hours=hours))
        finally:
            conn.close()

    def jobboard_refresh(self, qs):
        from . import jobboard
        body = self._json_body()

        def after(c):
            jobboard.auto_pick(c)
        started = jobboard.refresh_in_thread(self.server.db_path, li=True if body.get("linkedin") else None, on_done=after)
        self._json({"started": started, "refreshing": True})

    def jobboard_apply(self, qs):
        from . import jobboard
        body = self._json_body()
        ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        if not ids:
            return self._error(400, "ids must be a non-empty list of Job board rows")
        conn = self._conn()
        try:
            res = jobboard.queue(conn, ids, by="you")
        finally:
            conn.close()
        res["run_id"] = None
        if res["queued"]:
            try:
                res["run_id"] = runner.start_run("jobboard", db_path=self.server.db_path, lanes=["board_apply"])
            except runner.AlreadyRunning:
                res["waiting"] = True     # the dispatcher starts it as soon as the current run ends
        events.stats_dirty.set()
        self._json(res)

    def jobboard_update(self, qs, lid):
        from . import jobboard
        body = self._json_body()
        conn = self._conn()
        try:
            res = jobboard.set_status(conn, int(lid), str(body.get("status") or ""))
        finally:
            conn.close()
        if res.get("error"):
            return self._error(404 if "not found" in res["error"] else 400, res["error"])
        self._json(res)

    def jobboard_settings(self, qs):
        """Job board settings only (allowed during a run: they never change a running AI lane)."""
        body = self._json_body()
        patch = {k: body[k] for k in ("on", "auto_fill", "per_run", "daily_cap", "daily_usd", "li_regions",
                                      "fast_minutes", "li_every_hours") if k in body}
        cfg = config.load()
        cur = dict(cfg.get("jobboard") or {})
        cur.update(patch)
        new = config.apply_update(cfg, {"jobboard": cur})
        config.save(new)
        self._json(new["jobboard"])

    def questions(self, qs):
        conn = self._conn()
        try:
            self._json({"questions": qa.list_questions(conn, qs.get("status"), (qs.get("q") or "").strip() or None),
                        "counts": qa.counts(conn)})
        finally:
            conn.close()

    def questions_export(self, qs):
        kind = "log" if qs.get("kind") == "log" else "bank"
        conn = self._conn()
        try:
            text = qa.export_log_csv(conn) if kind == "log" else qa.export_bank_csv(conn)
        finally:
            conn.close()
        name = "form_answers_log.csv" if kind == "log" else "answers_bank.csv"
        self._send(200, text.encode("utf-8"), "text/csv; charset=utf-8",
                   {"Content-Disposition": 'attachment; filename="%s"' % name})

    def question_add(self, qs):
        body = self._json_body()
        conn = self._conn()
        try:
            row = qa.add_question(conn, body.get("question"), body.get("answer"), body.get("status"),
                                  body.get("field_type") or "text")
        except ValueError as e:
            return self._error(400, str(e))
        finally:
            conn.close()
        events.stats_dirty.set()
        self._json(row)

    def question_update(self, qs, qid):
        body = self._json_body()
        conn = self._conn()
        try:
            if body.get("delete") is True:
                ok = qa.delete_question(conn, int(qid))
                if not ok:
                    return self._error(404, "question not found")
                res = {"ok": True, "deleted": int(qid)}
            else:
                res = qa.save_answer(conn, int(qid), answer=body.get("answer"), status=body.get("status"),
                                     question=body.get("question"))
        except KeyError:
            return self._error(404, "question not found")
        except ValueError as e:
            return self._error(400, str(e))
        finally:
            conn.close()
        events.stats_dirty.set()
        self._json(res)

    def import_tracker(self, qs):
        ctype = self.headers.get("Content-Type") or ""
        raw = self._body()
        conn = self._conn()
        try:
            if ctype.startswith("multipart/form-data"):
                text = _multipart_file(ctype, raw)
                if text is None:
                    raise ValueError("no file in upload")
                counts = importer.import_text(conn, text)
            elif ctype.startswith("application/json") or raw[:1] == b"{":
                body = json.loads(raw.decode("utf-8") or "{}")
                if body.get("csv"):
                    counts = importer.import_text(conn, body["csv"])
                else:
                    p = Path(body.get("path") or (config.data_dir() / "tracker.csv"))
                    if not p.is_file():
                        raise ValueError("file not found: %s" % p)
                    counts = importer.import_csv(conn, p)
            elif raw.strip():
                counts = importer.import_text(conn, raw.decode("utf-8-sig", "replace"))
            else:
                p = config.data_dir() / "tracker.csv"
                if not p.is_file():
                    raise ValueError("no file uploaded and %s not found" % p)
                counts = importer.import_csv(conn, p)
            events.emit(conn, None, None, "step", "Imported tracker: %(contacts)d contacts, %(skipped_jobs)d skipped jobs, "
                        "%(duplicates)d already present" % counts, "success", counts)
        finally:
            conn.close()
        events.stats_dirty.set()
        self._json({"ok": True, "counts": counts})


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _multipart_file(ctype, raw):
    msg = BytesParser(policy=default_policy).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
    if not msg.is_multipart():
        return None
    first_text = None
    for part in msg.iter_parts():
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8-sig", "replace")
        if part.get_filename():
            return text
        if first_text is None and part.get_param("name", header="content-disposition") in ("file", "csv"):
            first_text = text
    return first_text


def build_summary(conn) -> dict:
    s = db.summary(conn, running=runner.is_running())
    cfg = config.load()
    s["dry_run"] = bool(cfg.get("dry_run"))
    s["gmail_configured"] = config.email_configured(cfg)
    s["current_run_id"] = runner.current_run_id()
    w = cfg.get("watch") or {}
    s["watch_on"] = bool(w.get("on"))
    s["submit_pending"] = actions.pending_approvals(conn)
    return s


ROUTES = [
    ("GET", r"/api/summary", Handler.summary),
    ("GET", r"/api/applications", Handler.applications),
    ("GET", r"/api/applications/(\d+)", Handler.application_detail),
    ("GET", r"/api/jobs", Handler.jobs),
    ("GET", r"/api/runs", Handler.runs),
    ("GET", r"/api/runs/(\d+)", Handler.run_detail),
    ("GET", r"/api/events", Handler.events_backlog),
    ("GET", r"/api/stream", Handler.stream),
    ("GET", r"/api/charts", Handler.charts),
    ("GET", r"/api/settings", Handler.settings_get),
    ("GET", r"/api/env", Handler.env),
    ("GET", r"/api/version", Handler.version),
    ("GET", r"/api/profiles", Handler.profiles_get),
    ("GET", r"/api/portal", Handler.portal),
    ("POST", r"/api/run", Handler.run_start),
    ("POST", r"/api/stop", Handler.run_stop),
    ("POST", r"/api/settings", Handler.settings_post),
    ("POST", r"/api/settings/test-email", Handler.test_email),
    ("POST", r"/api/engine-test", Handler.engine_test),
    ("POST", r"/api/portal/submit", Handler.portal_submit),
    ("POST", r"/api/portal/(\d+)", Handler.portal_update),
    ("GET", r"/api/watch", Handler.watch_get),
    ("POST", r"/api/watch", Handler.watch_post),
    ("POST", r"/api/import", Handler.import_tracker),
    ("GET", r"/api/questions", Handler.questions),
    ("GET", r"/api/questions/export\.csv", Handler.questions_export),
    ("POST", r"/api/questions", Handler.question_add),
    ("POST", r"/api/questions/(\d+)", Handler.question_update),
    ("GET", r"/api/agencies", Handler.agencies_get),
    ("POST", r"/api/agencies", Handler.agencies_add),
    ("POST", r"/api/agencies/(\d+)", Handler.agency_update),
    ("GET", r"/api/jobboard", Handler.jobboard_get),
    ("POST", r"/api/jobboard/refresh", Handler.jobboard_refresh),
    ("POST", r"/api/jobboard/apply", Handler.jobboard_apply),
    ("POST", r"/api/jobboard/settings", Handler.jobboard_settings),
    ("POST", r"/api/jobboard/(\d+)", Handler.jobboard_update),
]


def _stats_pump(srv):
    """Publish a `stats` event (debounced) after KPI-relevant events."""
    while not srv.stopping.is_set():
        if not events.stats_dirty.wait(1.0):
            continue
        time.sleep(0.4)
        events.stats_dirty.clear()
        if not events.subscriber_count():
            continue
        try:
            conn = db.connect(srv.db_path)
            try:
                events.publish_stats(build_summary(conn))
            finally:
                conn.close()
        except Exception as e:
            log.warning("stats pump: %s", e)


def create_server(port=None, host="127.0.0.1", db_path=None, web_dir=None, start_tailer=True,
                  start_watch=True) -> JobHunterServer:
    """Init the DB (stale runs, first-start import), start the event tailer + stats pump, bind the socket."""
    cfg = config.load()
    port = cfg.get("port", 8765) if port is None else port
    # Bind first: if another instance owns the port, fail BEFORE mark_stale_runs flips its live run to 'error'.
    srv = JobHunterServer((host, int(port)), Handler, db_path=db_path, web_dir=web_dir)
    try:
        conn = db.connect(db_path)
        try:
            db.mark_stale_runs(conn)
            try:
                from . import jobboard
                jobboard.release_working(conn)      # applications a crashed run left half-done go back to the queue
            except Exception as e:
                log.warning("job board release: %s", e)
            counts = importer.maybe_auto_import(conn, config.data_dir())
            if counts:
                log.info("auto-imported tracker.csv: %s", counts)
                events.emit(conn, None, None, "step", "Imported existing tracker.csv: %d contacts, %d skipped jobs"
                            % (counts["contacts"], counts["skipped_jobs"]), "success", counts)
            try:
                qa.renormalize(conn)
                qa.seed_once(conn)
            except Exception as e:  # never block startup on the question-bank seed
                log.warning("question bank seed failed: %s", e)
        finally:
            conn.close()
    except BaseException:
        srv.server_close()
        raise
    if start_tailer:
        events.start_db_tailer(db_path)
    srv.pump = threading.Thread(target=_stats_pump, args=(srv,), name="jh-stats", daemon=True)
    srv.pump.start()
    if start_watch:
        srv.watcher.start()   # dispatcher: approvals -> submit lane; watch mode polling when switched on
    return srv


def serve_in_thread(srv) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.25}, name="jh-http", daemon=True)
    t.start()
    return t


def shutdown_server(srv):
    srv.stopping.set()
    srv.watcher.shutdown()
    if srv.watcher.thread is not None:
        srv.watcher.thread.join(2.0)
    try:
        srv.shutdown()
    finally:
        srv.server_close()
        events.stop_db_tailer()
