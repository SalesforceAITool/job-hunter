"""SQLite schema, connection helper, timestamps and read queries for the API.

All timestamps are ISO 8601 in Asia/Kolkata (+05:30), so `substr(ts,1,10)` is the IST date.
Connections use autocommit (isolation_level=None) so other processes (the MCP server)
see writes immediately; multi-row writers wrap their work in BEGIN/COMMIT themselves.
"""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30), "IST")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
  status TEXT, trigger TEXT, stats_json TEXT);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, run_id INTEGER, source TEXT,
  company TEXT, role TEXT, location TEXT, remote INTEGER, url TEXT, posted TEXT,
  jd_text TEXT, status TEXT, skip_reason TEXT, notes TEXT, norm_key TEXT, fit_score INTEGER,
  apply_url TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS portal_apps (id INTEGER PRIMARY KEY, job_id INTEGER, ats TEXT, apply_url TEXT,
  status TEXT, resume_path TEXT, questions_json TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, job_id INTEGER, name TEXT, title TEXT,
  email TEXT, email_source TEXT, linkedin TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS emails (id INTEGER PRIMARY KEY, job_id INTEGER, contact_id INTEGER,
  kind TEXT, subject TEXT, body TEXT, message_id TEXT, in_reply_to TEXT, resume_path TEXT,
  status TEXT, error TEXT, sent_at TEXT);
CREATE TABLE IF NOT EXISTS replies (id INTEGER PRIMARY KEY, contact_id INTEGER, job_id INTEGER,
  from_addr TEXT, subject TEXT, snippet TEXT, received_at TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT, run_id INTEGER, lane TEXT,
  level TEXT, type TEXT, message TEXT, data_json TEXT);
CREATE INDEX IF NOT EXISTS ix_contacts_email ON contacts(lower(email));
CREATE INDEX IF NOT EXISTS ix_jobs_norm ON jobs(norm_key);
CREATE INDEX IF NOT EXISTS ix_emails_contact ON emails(contact_id);
CREATE INDEX IF NOT EXISTS ix_replies_contact ON replies(contact_id);
CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY, question TEXT, norm TEXT UNIQUE,
  field_type TEXT, options_json TEXT, answer TEXT, status TEXT, times_seen INTEGER DEFAULT 0,
  times_used INTEGER DEFAULT 0, jobs_json TEXT, last_company TEXT, last_lane TEXT, source TEXT,
  created_at TEXT, updated_at TEXT, answered_at TEXT);
CREATE INDEX IF NOT EXISTS ix_questions_status ON questions(status);
CREATE TABLE IF NOT EXISTS form_answers (id INTEGER PRIMARY KEY, job_id INTEGER, run_id INTEGER, lane TEXT,
  question TEXT, norm TEXT, answer TEXT, field_type TEXT, source TEXT, learned INTEGER DEFAULT 0, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_form_answers_job ON form_answers(job_id);
CREATE TABLE IF NOT EXISTS app_meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS profile_lookups (slug TEXT PRIMARY KEY, url TEXT, found INTEGER DEFAULT 0, lane TEXT,
  first_ts TEXT, last_ts TEXT, tries INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS watch_seen (ext_id TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT, region TEXT,
  url TEXT, listed TEXT, status TEXT, reason TEXT, run_id INTEGER, first_seen TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_watch_status ON watch_seen(status);
CREATE TABLE IF NOT EXISTS post_seen (post_id TEXT PRIMARY KEY, url TEXT, run_id INTEGER, status TEXT DEFAULT 'pending',
  first_seen TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS agencies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, norm TEXT UNIQUE,
  priority TEXT, kind TEXT, region TEXT, website TEXT, linkedin TEXT, source TEXT, known_contacts TEXT, known_emails TEXT,
  status TEXT DEFAULT 'new', notes TEXT, job_id INTEGER, touches INTEGER DEFAULT 0, last_touch TEXT, next_touch TEXT,
  created_at TEXT, updated_at TEXT, via_jobs INTEGER DEFAULT 0, via_replied INTEGER DEFAULT 0, past_client INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS career_boards (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, token TEXT NOT NULL,
  added_at TEXT, origin TEXT, status TEXT DEFAULT 'active', last_ok TEXT, misses INTEGER DEFAULT 0, jobs INTEGER,
  sf_jobs INTEGER, UNIQUE(platform, token));
CREATE TABLE IF NOT EXISTS listings (id INTEGER PRIMARY KEY AUTOINCREMENT, norm_key TEXT, source TEXT, sources TEXT,
  platform TEXT, company TEXT, title TEXT, location TEXT, region TEXT, url TEXT, apply_url TEXT, apply_kind TEXT,
  posted_at TEXT, first_seen TEXT, last_seen TEXT, salary TEXT, jd_text TEXT, jd_checked INTEGER DEFAULT 0,
  applicants INTEGER, elig TEXT, reason TEXT, emails TEXT, status TEXT DEFAULT 'new', job_id INTEGER, queued_at TEXT,
  queued_by TEXT, tries INTEGER DEFAULT 0, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS email_candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, email TEXT,
  source TEXT, name TEXT, title TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_email_cand ON email_candidates(job_id, email);
CREATE TABLE IF NOT EXISTS listing_ids (ext_id TEXT PRIMARY KEY, listing_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_listings_norm ON listings(norm_key);
CREATE INDEX IF NOT EXISTS ix_listings_posted ON listings(posted_at);
CREATE INDEX IF NOT EXISTS ix_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS ix_listings_job ON listings(job_id);
CREATE INDEX IF NOT EXISTS ix_listing_ids_listing ON listing_ids(listing_id);
CREATE INDEX IF NOT EXISTS ix_emails_job ON emails(job_id);
CREATE INDEX IF NOT EXISTS ix_portal_job ON portal_apps(job_id);
CREATE INDEX IF NOT EXISTS ix_replies_job ON replies(job_id);
"""

# Additive columns beyond SPEC §4 (backend-internal; safe for other readers to ignore).
EXTRA_COLUMNS = {
    "contacts": [("stop_nudges", "INTEGER DEFAULT 0"), ("notes", "TEXT")],
    "jobs": [("lookup_tries", "INTEGER DEFAULT 0")],   # contact-lookup retries handed out by seen.filter_jobs
    "emails": [("run_id", "INTEGER")],
    # category = jh.replies.classify (interview / call_request / ...); draft = a reply draft was saved in Gmail
    "replies": [("category", "TEXT"), ("draft", "INTEGER DEFAULT 0"), ("message_id", "TEXT")],
    # ready = the engine reached the final Review page with every required field answered
    # approved_at = Akshay clicked "Submit selected" for this row (his per-batch approval)
    "portal_apps": [("ready", "INTEGER DEFAULT 0"), ("approved_at", "TEXT"), ("submitted_at", "TEXT"),
                    ("submit_note", "TEXT"), ("claimed_at", "TEXT")],
}

SENT_STATUSES = ("sent", "bounced")  # an email that actually left the mailbox
SOURCES = ("posts", "jobright", "linkedin_us", "linkedin_world", "linkedin_india", "himalayas", "jobicy", "hn", "careers", "board",
           "agency", "import", "manual")


def _fake_today():
    """Test/QA clock seam: env JOBHUNTER_TODAY=YYYY-MM-DD moves "now" to that IST date (time of day kept)."""
    raw = (os.environ.get("JOBHUNTER_TODAY") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def now() -> datetime:
    real = datetime.now(IST)
    fake = _fake_today()
    return real.replace(year=fake.year, month=fake.month, day=fake.day) if fake else real


def now_iso() -> str:
    """Current time, ISO 8601 with +05:30."""
    return now().replace(microsecond=0).isoformat()


def today_ist() -> date:
    return now().date()


def parse_ts(ts):
    """Parse an ISO timestamp (or YYYY-MM-DD) into an aware datetime in IST, or None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(str(ts).strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def ts_date(ts):
    dt = parse_ts(ts)
    return dt.date() if dt else None


def default_db_path() -> Path:
    env = os.environ.get("JOBHUNTER_DB")
    if env:
        return Path(env)
    from . import config
    return config.data_dir() / "jobhunter.db"


def connect(path=None) -> sqlite3.Connection:
    """Open the DB (creating schema), WAL mode, Row factory, autocommit."""
    p = Path(path) if path else default_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


NORM_VERSION = 1  # bump when dedupe.norm_key changes: stored jobs.norm_key values are recomputed once


def _renormalize(conn):
    """Recompute jobs.norm_key after a normalization change (PRAGMA user_version tracks the version)."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= NORM_VERSION:
        return
    from . import dedupe
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return  # someone else is migrating/writing; the next connect retries
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] < NORM_VERSION:
            rows = conn.execute("SELECT id, company, role, norm_key FROM jobs").fetchall()
            for r in rows:
                key = dedupe.norm_key(r["company"], r["role"])
                if key != r["norm_key"]:
                    conn.execute("UPDATE jobs SET norm_key=? WHERE id=?", (key, r["id"]))
            conn.execute("PRAGMA user_version=%d" % NORM_VERSION)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _migrate(conn):
    _renormalize(conn)
    for table, cols in EXTRA_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError:
                    pass  # another process added it concurrently


def db_file(conn) -> Path:
    """Filesystem path of the main database of a connection."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2])
    return default_db_path()


def row_dict(row):
    return dict(row) if row is not None else None


def event_row_to_dict(row) -> dict:
    d = dict(row)
    raw = d.pop("data_json", None)
    try:
        d["data"] = json.loads(raw) if raw else {}
    except ValueError:
        d["data"] = {}
    return d


def mark_stale_runs(conn):
    """Runs left 'running' by a crashed process become 'error'."""
    conn.execute("UPDATE runs SET status='error', finished_at=? WHERE status='running'", (now_iso(),))


# ---------------------------------------------------------------- contact threads

def load_threads(conn, contact_ids=None) -> dict:
    """Return {contact_id: thread} with contact+job fields, emails and replies, plus derived state."""
    from . import followups
    where, args = "", []
    if contact_ids is not None:
        ids = [int(i) for i in contact_ids]
        if not ids:
            return {}
        where = "WHERE c.id IN (%s)" % ",".join("?" * len(ids))
        args = ids
    rows = conn.execute(
        "SELECT c.id AS contact_id, c.job_id, c.name, c.title, c.email, c.email_source, c.linkedin,"
        " c.created_at AS contact_created_at, COALESCE(c.stop_nudges,0) AS stop_nudges, c.notes AS contact_notes,"
        " j.company, j.role, j.source, j.url, j.status AS job_status, j.fit_score"
        " FROM contacts c LEFT JOIN jobs j ON j.id = c.job_id " + where, args).fetchall()
    threads = {r["contact_id"]: dict(r, emails=[], replies=[]) for r in rows}
    if not threads:
        return {}
    idset = "(%s)" % ",".join(str(i) for i in threads)
    for e in conn.execute("SELECT * FROM emails WHERE contact_id IN %s ORDER BY sent_at, id" % idset):
        threads[e["contact_id"]]["emails"].append(dict(e))
    for r in conn.execute("SELECT * FROM replies WHERE contact_id IN %s ORDER BY received_at, id" % idset):
        threads[r["contact_id"]]["replies"].append(dict(r))
    for t in threads.values():
        t.update(followups.thread_state(t))
    return threads


# ---------------------------------------------------------------- API queries

def summary(conn, running=False) -> dict:
    """KPI object for /api/summary and `stats` events."""
    today = today_ist()
    t_s = today.isoformat()
    d7 = (today - timedelta(days=6)).isoformat()
    sent_q = "SELECT COUNT(*) FROM emails WHERE kind='initial' AND status IN ('sent','bounced')"
    sent_total = conn.execute(sent_q).fetchone()[0]
    sent_today = conn.execute(sent_q + " AND substr(sent_at,1,10)=?", (t_s,)).fetchone()[0]
    sent_7d = conn.execute(sent_q + " AND substr(sent_at,1,10)>=?", (d7,)).fetchone()[0]
    dry_today = conn.execute("SELECT COUNT(*) FROM emails WHERE kind='initial' AND status='dry_run'"
                             " AND substr(sent_at,1,10)=?", (t_s,)).fetchone()[0]
    threads = load_threads(conn)
    contacted = [t for t in threads.values() if t["initial_sent"]]
    replied = sum(1 for t in contacted if t["app_status"] == "replied")
    awaiting = sum(1 for t in contacted if t["app_status"] in ("sent", "nudge1", "nudge2"))
    bounced = sum(1 for t in contacted if t["app_status"] == "bounced")
    nudges_due = sum(1 for t in contacted if t["next_followup"] and t["next_followup"] <= today)
    skipped_today = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='skipped' AND substr(updated_at,1,10)=?",
                                 (t_s,)).fetchone()[0]
    by_source = {s: 0 for s in ("posts", "jobright", "linkedin_us", "linkedin_world", "linkedin_india", "himalayas", "jobicy",
                                "hn", "careers", "import")}
    for r in conn.execute("SELECT j.source, COUNT(*) n FROM emails e JOIN jobs j ON j.id=e.job_id"
                          " WHERE e.kind='initial' AND e.status IN ('sent','bounced') GROUP BY j.source"):
        by_source[r["source"] or "manual"] = r["n"]
    last = conn.execute("SELECT id, started_at, finished_at, status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "sent_today": sent_today, "sent_7d": sent_7d, "sent_total": sent_total,
        "replies_total": replied,
        "reply_rate": round(replied / len(contacted), 4) if contacted else 0.0,
        "awaiting": awaiting, "nudges_due": nudges_due, "bounced": bounced,
        "skipped_today": skipped_today, "dry_run_today": dry_today,
        "per_hour_current_run": per_hour(conn, last["id"]) if last else 0.0,
        "last_run": row_dict(last), "running": bool(running), "by_source": by_source,
        "questions_open": conn.execute("SELECT COUNT(*) FROM questions WHERE status='open'").fetchone()[0],
    }


def per_hour(conn, run_id) -> float:
    """Initial emails (sent or dry-run) per hour for a run, elapsed floored at 10 minutes."""
    run = conn.execute("SELECT started_at, finished_at FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        return 0.0
    n = conn.execute("SELECT COUNT(*) FROM emails WHERE run_id=? AND kind='initial'"
                     " AND status IN ('sent','bounced','dry_run')", (run_id,)).fetchone()[0]
    start = parse_ts(run["started_at"])
    end = parse_ts(run["finished_at"]) or now()
    hours = max((end - start).total_seconds() / 3600.0, 1 / 6) if start else 1.0
    return round(n / hours, 1)


def _app_row(t) -> dict:
    nf = t["next_followup"]
    return {
        "contact_id": t["contact_id"], "job_id": t["job_id"], "company": t["company"], "role": t["role"],
        "source": t["source"], "contact_name": t["name"], "contact_title": t["title"], "email": t["email"],
        "email_source": t["email_source"], "sent_at": t["sent_at"], "last_touch_at": t["last_touch_at"],
        "next_followup_at": nf.isoformat() if nf else None, "app_status": t["app_status"],
        "url": t["url"], "fit_score": t.get("fit_score"),
    }


def applications(conn, status=None, source=None, q=None, limit=200, offset=0):
    """Rows for the applications table. status: awaiting|replied|bounced|sent|nudge1|nudge2|dry_run|failed|
    pending|skipped|all. 'skipped' returns skipped jobs in the same row shape (no contact)."""
    status = (status or "").strip().lower() or None
    if status == "skipped":
        rows = jobs_list(conn, status="skipped", source=source, q=q, limit=limit, offset=offset)
        return [{"contact_id": None, "job_id": j["id"], "company": j["company"], "role": j["role"],
                 "source": j["source"], "contact_name": None, "contact_title": None, "email": None,
                 "email_source": None, "sent_at": None, "last_touch_at": j["updated_at"],
                 "next_followup_at": None, "app_status": "skipped", "url": j["url"],
                 "skip_reason": j["skip_reason"], "fit_score": j["fit_score"]} for j in rows], None
    threads = list(load_threads(conn).values())
    if status and status != "all":
        if status == "awaiting":
            threads = [t for t in threads if t["app_status"] in ("sent", "nudge1", "nudge2")]
        else:
            threads = [t for t in threads if t["app_status"] == status]
    if source:
        threads = [t for t in threads if (t["source"] or "") == source]
    if q:
        ql = q.lower()
        threads = [t for t in threads if any(ql in (t.get(k) or "").lower()
                                             for k in ("company", "role", "name", "title", "email"))]
    threads.sort(key=lambda t: (t["sent_at"] or t["contact_created_at"] or "", t["contact_id"]), reverse=True)
    total = len(threads)
    return [_app_row(t) for t in threads[offset:offset + limit]], total


def application_detail(conn, contact_id):
    th = load_threads(conn, [contact_id]).get(int(contact_id))
    if not th:
        return None
    job = row_dict(conn.execute("SELECT * FROM jobs WHERE id=?", (th["job_id"],)).fetchone())
    contact = row_dict(conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone())
    timeline = []
    for e in th["emails"]:
        label = {"initial": "Application sent", "nudge1": "Nudge 1 sent", "nudge2": "Nudge 2 sent"}.get(e["kind"], e["kind"])
        if e["status"] != "sent":
            label += " (%s)" % {"dry_run": "dry run, not sent"}.get(e["status"], e["status"])
        timeline.append({"ts": e["sent_at"], "kind": e["kind"], "status": e["status"], "label": label})
    for r in th["replies"]:
        label = {"reply": "Reply received", "auto": "Auto-reply", "bounce": "Bounced"}.get(r["kind"], r["kind"])
        timeline.append({"ts": r["received_at"], "kind": r["kind"], "status": r["kind"], "label": label})
    timeline.sort(key=lambda x: x["ts"] or "")
    if th["next_followup"]:
        nxt = "nudge1" if th["app_status"] == "sent" else "nudge2"
        timeline.append({"ts": th["next_followup"].isoformat(), "kind": nxt, "status": "due",
                         "label": ("Nudge 1" if nxt == "nudge1" else "Nudge 2") + " due"})
    portal = [row_dict(r) for r in conn.execute("SELECT * FROM portal_apps WHERE job_id=?", (th["job_id"],))]
    return {"job": job, "contact": contact, "emails": th["emails"], "replies": th["replies"],
            "timeline": timeline, "app_status": th["app_status"],
            "next_followup_at": th["next_followup"].isoformat() if th["next_followup"] else None,
            "portal": portal}


def jobs_list(conn, status=None, run_id=None, source=None, q=None, limit=200, offset=0):
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if run_id:
        sql += " AND run_id=?"
        args.append(int(run_id))
    if source:
        sql += " AND source=?"
        args.append(source)
    if q:
        sql += " AND (lower(company) LIKE ? OR lower(role) LIKE ? OR lower(COALESCE(skip_reason,'')) LIKE ?)"
        args += ["%" + q.lower() + "%"] * 3
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
    args += [int(limit), int(offset)]
    return [dict(r) for r in conn.execute(sql, args)]


def _run_out(r) -> dict:
    d = dict(r)
    try:
        d["stats"] = json.loads(d.pop("stats_json") or "{}")
    except ValueError:
        d["stats"] = {}
    return d


def runs_list(conn, limit=50):
    return [_run_out(r) for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),))]


def run_detail(conn, run_id):
    r = conn.execute("SELECT * FROM runs WHERE id=?", (int(run_id),)).fetchone()
    if not r:
        return None
    d = _run_out(r)
    lanes = {}
    for ev in conn.execute("SELECT * FROM events WHERE run_id=? AND type IN ('lane_started','lane_finished')"
                           " ORDER BY id", (int(run_id),)):
        e = event_row_to_dict(ev)
        ln = lanes.setdefault(e["lane"], {"lane": e["lane"], "status": "running", "started_at": None,
                                          "finished_at": None, "counts": {}})
        if e["type"] == "lane_started":
            ln["started_at"] = e["ts"]
        else:
            ln["finished_at"] = e["ts"]
            ln["status"] = e["data"].get("status", "done")
            ln["counts"] = e["data"].get("counts", {})
            if e["data"].get("error"):
                ln["error"] = e["data"]["error"]
    d["lanes"] = list(lanes.values())
    d["per_hour"] = per_hour(conn, run_id)
    return d


def events_list(conn, run_id=None, after_id=None, limit=500):
    limit = max(1, min(int(limit or 500), 5000))
    sql, args = "SELECT * FROM events WHERE 1=1", []
    if run_id:
        sql += " AND run_id=?"
        args.append(int(run_id))
    if after_id is not None and str(after_id) != "":
        sql += " AND id>? ORDER BY id ASC LIMIT ?"
        args += [int(after_id), limit]
        rows = conn.execute(sql, args).fetchall()
    else:
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = list(reversed(conn.execute(sql, args).fetchall()))
    return [event_row_to_dict(r) for r in rows]


def charts(conn) -> dict:
    today = today_ist()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    daily = {d: {"date": d, **{s: 0 for s in SOURCES}, "total": 0, "replies": 0, "nudges": 0} for d in days}
    for r in conn.execute("SELECT substr(e.sent_at,1,10) d, COALESCE(j.source,'manual') s, e.kind k, COUNT(*) n"
                          " FROM emails e LEFT JOIN jobs j ON j.id=e.job_id"
                          " WHERE e.status IN ('sent','bounced') AND substr(e.sent_at,1,10)>=?"
                          " GROUP BY d, s, k", (days[0],)):
        if r["d"] not in daily:
            continue
        if r["k"] == "initial":
            daily[r["d"]][r["s"] if r["s"] in SOURCES else "manual"] += r["n"]
            daily[r["d"]]["total"] += r["n"]
        else:
            daily[r["d"]]["nudges"] += r["n"]
    for r in conn.execute("SELECT substr(received_at,1,10) d, COUNT(*) n FROM replies WHERE kind='reply'"
                          " AND substr(received_at,1,10)>=? GROUP BY d", (days[0],)):
        if r["d"] in daily:
            daily[r["d"]]["replies"] = r["n"]

    def cnt(where):
        return conn.execute("SELECT COUNT(*) FROM jobs WHERE " + where).fetchone()[0]
    funnel = {
        "found": cnt("1=1"),
        "qualified": cnt("status IN ('qualified','contacted','replied','bounced','closed')"),
        "contacted": cnt("status IN ('contacted','replied','bounced','closed')"),
        "replied": cnt("status='replied'"),
    }
    latest = [dict(r) for r in conn.execute(
        "SELECT r.id, r.contact_id, r.job_id, r.from_addr, r.subject, r.snippet, r.received_at,"
        " c.name AS contact_name, j.company, j.role FROM replies r LEFT JOIN contacts c ON c.id=r.contact_id"
        " LEFT JOIN jobs j ON j.id=r.job_id WHERE r.kind='reply' ORDER BY r.received_at DESC LIMIT 8")]
    return {"daily": [daily[d] for d in days], "funnel": funnel, "latest_replies": latest}


def portal_list(conn, status=None):
    try:
        from . import actions
        actions.expire_approvals(conn)
    except Exception:
        pass
    sql = ("SELECT p.*, j.company, j.role, j.url AS job_url, j.fit_score FROM portal_apps p"
           " LEFT JOIN jobs j ON j.id=p.job_id")
    args = []
    if status:
        sql += " WHERE p.status=?"
        args.append(status)
    sql += " ORDER BY CASE WHEN p.status IN ('prefilled','needs_input') THEN 0 ELSE 1 END, p.id DESC"
    out = []
    for r in conn.execute(sql, args):
        d = dict(r)
        try:
            d["questions"] = json.loads(d.pop("questions_json") or "[]")
        except ValueError:
            d["questions"] = []
        d["job"] = {"id": d["job_id"], "company": d.get("company"), "role": d.get("role"),
                    "url": d.get("job_url"), "fit_score": d.get("fit_score")}
        d["answers"] = [dict(a) for a in conn.execute(
            "SELECT question, answer, source FROM form_answers WHERE id IN (SELECT MAX(id) FROM form_answers"
            " WHERE job_id=? GROUP BY norm) ORDER BY id", (d["job_id"],))] if d.get("job_id") else []
        out.append(d)
    return out
