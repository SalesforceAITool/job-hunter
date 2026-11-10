"""Shared helpers for the engine tests: a tiny stand-in `jh` package (db + actions)
written to a temp dir, used when the real backend modules are not available
(or to keep engine tests independent of backend behaviour)."""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, trigger TEXT, stats_json TEXT);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, run_id INTEGER, source TEXT, company TEXT, role TEXT, location TEXT,
  remote INTEGER, url TEXT, posted TEXT, jd_text TEXT, status TEXT, skip_reason TEXT, notes TEXT, norm_key TEXT,
  fit_score INTEGER, apply_url TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS portal_apps (id INTEGER PRIMARY KEY, job_id INTEGER, ats TEXT, apply_url TEXT, status TEXT,
  resume_path TEXT, questions_json TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, job_id INTEGER, name TEXT, title TEXT, email TEXT,
  email_source TEXT, linkedin TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS emails (id INTEGER PRIMARY KEY, job_id INTEGER, contact_id INTEGER, kind TEXT, subject TEXT,
  body TEXT, message_id TEXT, in_reply_to TEXT, resume_path TEXT, status TEXT, error TEXT, sent_at TEXT);
CREATE TABLE IF NOT EXISTS replies (id INTEGER PRIMARY KEY, contact_id INTEGER, job_id INTEGER, from_addr TEXT,
  subject TEXT, snippet TEXT, received_at TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT, run_id INTEGER, lane TEXT, level TEXT, type TEXT,
  message TEXT, data_json TEXT);
"""

STUB_DB = '''
import os, sqlite3, datetime
SCHEMA = %r
def now_iso():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).isoformat(timespec="seconds")
def connect(path=None):
    path = path or os.environ.get("JOBHUNTER_DB")
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c
'''

STUB_ACTIONS = '''
import json, os
from jh import db
def _ev(conn, run_id, lane, type, message, level="info", data=None):
    conn.execute("INSERT INTO events(ts,run_id,lane,level,type,message,data_json) VALUES(?,?,?,?,?,?,?)",
                 (db.now_iso(), run_id, lane, level, type, message, json.dumps(data or {})))
    conn.commit()
def log_step(conn, run_id, lane, message, level="info"):
    _ev(conn, run_id, lane, "step", message, level); return {"ok": True}
def already_contacted(conn, company, role, emails, linkedin=None):
    for e in emails or []:
        if conn.execute("SELECT 1 FROM contacts WHERE lower(email)=lower(?)", (e,)).fetchone():
            return {"contacted": True, "reason": "email " + e}
    if conn.execute("SELECT 1 FROM jobs WHERE lower(company)=lower(?) AND lower(role)=lower(?) AND status IN ('contacted','replied','bounced','closed')",
                    (company, role)).fetchone():
        return {"contacted": True, "reason": "company+role"}
    return {"contacted": False, "reason": ""}
def save_job(conn, run_id, lane, source, company, role, location="", remote=True, url="", posted="", jd_text="",
             decision="qualified", skip_reason=None, notes=None, fit_score=None, apply_url=None):
    st = "qualified" if decision == "qualified" else "skipped"
    cur = conn.execute("INSERT INTO jobs(run_id,source,company,role,location,remote,url,posted,jd_text,status,skip_reason,notes,fit_score,apply_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, source, company, role, location, int(bool(remote)), url, posted, jd_text, st, skip_reason, notes, fit_score, apply_url, db.now_iso(), db.now_iso()))
    conn.commit()
    _ev(conn, run_id, lane, "job_qualified" if st == "qualified" else "job_skipped", company + " - " + role)
    return {"job_id": cur.lastrowid}
def save_contact(conn, run_id, lane, job_id, name, title, email, email_source, linkedin=None):
    if conn.execute("SELECT 1 FROM contacts WHERE lower(email)=lower(?)", (email,)).fetchone():
        return {"error": "duplicate email"}
    t = (title or "").lower()
    if ("developer" in t or "qa" in t.split()) and not any(k in t for k in ("lead", "manager", "director", "architect")):
        return {"error": "developer/QA title"}
    cur = conn.execute("INSERT INTO contacts(job_id,name,title,email,email_source,linkedin,created_at) VALUES(?,?,?,?,?,?,?)",
                       (job_id, name, title, email, email_source, linkedin, db.now_iso()))
    conn.commit()
    _ev(conn, run_id, lane, "contact_found", name)
    return {"contact_id": cur.lastrowid}
def build_resume_only(conn, job_id, resume):
    d = os.path.join(os.path.dirname(os.environ.get("JOBHUNTER_DB", ".")), "resumes")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "job_%s.pdf" % job_id)
    open(p, "wb").write(b"%PDF-1.4 stub")
    return p
def send_application(conn, run_id, lane, job_id, contact_id, subject, body, resume, cfg):
    assert (cfg.get("email") or {}).get("backend", "file") == "file" or os.environ.get("JOBHUNTER_ALLOW_SMTP")
    p = build_resume_only(conn, job_id, resume)
    cur = conn.execute("INSERT INTO emails(job_id,contact_id,kind,subject,body,resume_path,status,sent_at) VALUES(?,?,?,?,?,?,?,?)",
                       (job_id, contact_id, "initial", subject, body, p, "dry_run", db.now_iso()))
    conn.execute("UPDATE jobs SET status='contacted' WHERE id=?", (job_id,))
    conn.commit()
    _ev(conn, run_id, lane, "email_sent", subject, "success")
    return {"ok": True, "email_id": cur.lastrowid, "error": None, "dry_run": True}
def save_portal_prefill(conn, run_id, lane, job_id, ats, apply_url, resume, questions_left, cfg, ready=False):
    p = build_resume_only(conn, job_id, resume)
    st = "needs_input" if questions_left else "prefilled"
    cur = conn.execute("INSERT INTO portal_apps(job_id,ats,apply_url,status,resume_path,questions_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (job_id, ats, apply_url, st, p, json.dumps(questions_left), db.now_iso(), db.now_iso()))
    conn.commit()
    _ev(conn, run_id, lane, "portal_ready", ats)
    return {"portal_id": cur.lastrowid, "resume_path": p}
'''


def write_stub_root(root):
    """Create <root>/jh/{__init__,db,actions}.py. Returns root as Path."""
    root = Path(root)
    (root / "jh").mkdir(parents=True, exist_ok=True)
    (root / "jh" / "__init__.py").write_text("", encoding="utf-8")
    (root / "jh" / "db.py").write_text(STUB_DB % SCHEMA, encoding="utf-8")
    (root / "jh" / "actions.py").write_text(textwrap.dedent(STUB_ACTIONS), encoding="utf-8")
    return root


def load_stub_modules(root):
    """Import the stub modules under the names jh, jh.db, jh.actions without touching sys.path.
    Returns a dict suitable for unittest.mock.patch.dict(sys.modules, ...)."""
    import types
    pkg = types.ModuleType("jh")
    pkg.__path__ = [str(Path(root) / "jh")]
    mods = {"jh": pkg}
    saved = {k: sys.modules.get(k) for k in ("jh", "jh.db", "jh.actions")}
    try:
        sys.modules["jh"] = pkg
        for name in ("db", "actions"):
            spec = importlib.util.spec_from_file_location("jh." + name, str(Path(root) / "jh" / (name + ".py")))
            m = importlib.util.module_from_spec(spec)
            sys.modules["jh." + name] = m
            spec.loader.exec_module(m)
            setattr(pkg, name, m)
            mods["jh." + name] = m
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mods


def real_backend_available():
    """True if the real backend jh.actions + jh.db exist in the repo."""
    return (APP_ROOT / "jh" / "actions.py").is_file() and (APP_ROOT / "jh" / "db.py").is_file()


def db_rows(db_path, sql, args=()):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


class StubSanityTest(unittest.TestCase):
    def test_stub_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = write_stub_root(Path(td) / "root")
            mods = load_stub_modules(root)
            conn = mods["jh.db"].connect(str(Path(td) / "x.db"))
            a = mods["jh.actions"]
            j = a.save_job(conn, 1, "posts", "posts", "Acme", "Salesforce Developer")
            c = a.save_contact(conn, 1, "posts", j["job_id"], "Jane", "Recruiter", "jane@acme.example", "post")
            self.assertIn("contact_id", c)
            self.assertIn("error", a.save_contact(conn, 1, "posts", j["job_id"], "Bob", "Salesforce Developer", "bob@acme.example", "post"))
            self.assertTrue(a.already_contacted(conn, "x", "y", ["JANE@acme.example"])["contacted"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
