"""Import the existing tracker.csv history (SPEC §10). Idempotent: re-importing adds nothing.

Columns: date, company, role, contact, title, email, status, next_step.
* status sent*/replied*/bounced* + an email -> job (source 'import') + contact + initial email
  (sent_at = date 10:00 IST, message_id NULL); 'replied' adds a replies row; 'bounced' marks it bounced.
* next_step "nudge 1 sent YYYY-MM-DD" / "nudge 2 sent YYYY-MM-DD" -> nudge email rows on that date.
* next_step "sequence done" / "hold" / "replied" -> no further nudges (job closed unless replied).
* status skipped (and anything else, e.g. "no email found") -> a skipped job with the reason.
"""
import csv
import io
import re
from datetime import datetime
from pathlib import Path

from . import db, dedupe

NUDGE_RE = re.compile(r"nudge\s*([12])\s*sent\s*(?:on\s*)?(\d{4}-\d{2}-\d{2})", re.I)
STOP_RE = re.compile(r"sequence done|\bhold\b|\breplied\b", re.I)
STATUS_DATE_RE = re.compile(r"^(sent|replied|bounced)\s*(?:on\s*)?(\d{4}-\d{2}-\d{2})", re.I)


def _ts(day: str, default=None) -> str:
    """'YYYY-MM-DD' -> that day 10:00 IST; anything else (incl. impossible dates like 2026-13-45) -> default/now."""
    day = (day or "").strip()[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return default or db.now_iso()
    return day + "T10:00:00+05:30"


def _clean(v) -> str:
    v = (v or "").strip()
    return "" if v in ("-", "—", "n/a", "N/A") else v


def _get_job(conn, company, role, status, created, notes, skip_reason=None):
    """Existing job id for the normalized company+role, or a new one."""
    key = dedupe.norm_key(company, role)
    row = conn.execute("SELECT id FROM jobs WHERE norm_key=? ORDER BY id LIMIT 1", (key,)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute("INSERT INTO jobs (run_id, source, company, role, location, remote, url, posted, jd_text, status,"
                       " skip_reason, notes, norm_key, created_at, updated_at) VALUES"
                       " (NULL, 'import', ?, ?, '', 1, '', '', '', ?, ?, ?, ?, ?, ?)",
                       (company, role, status, skip_reason, notes, key, created, created))
    return cur.lastrowid, True


def import_rows(conn, rows) -> dict:
    counts = {"rows": 0, "jobs": 0, "contacts": 0, "emails": 0, "nudges": 0, "replies": 0, "bounced": 0,
              "skipped_jobs": 0, "duplicates": 0, "errors": 0}
    touched_jobs = set()
    conn.execute("BEGIN IMMEDIATE")  # take the write lock up front (no read->write upgrade BUSY under WAL)
    try:
        for raw in rows:
            r = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
            if not any(v.strip() for v in r.values()):
                continue
            counts["rows"] += 1
            company, role = _clean(r.get("company")), _clean(r.get("role"))
            if not company:
                counts["errors"] += 1
                continue
            role = role or "Salesforce role"
            status_raw = (r.get("status") or "").strip()
            status = status_raw.lower()
            next_step = (r.get("next_step") or "").strip()
            email = dedupe.normalize_email(_clean(r.get("email")))
            created = _ts(r.get("date"))
            # "sent 2026-10-18 (...)": the real send date (the date column is when the job was found);
            # "replied 2026-10-12": when the reply came in
            sd = STATUS_DATE_RE.match(status)
            sent_at = _ts(sd.group(2), created) if sd and sd.group(1).lower() == "sent" else created
            reply_at = _ts(sd.group(2), created) if sd and sd.group(1).lower() == "replied" else created
            is_contact_row = status.startswith(("sent", "replied", "bounced")) and dedupe.valid_email(email)
            if is_contact_row:
                if conn.execute("SELECT 1 FROM contacts WHERE lower(trim(email))=?", (email,)).fetchone():
                    counts["duplicates"] += 1
                    continue
                job_id, new = _get_job(conn, company, role, "contacted", created, None)
                counts["jobs"] += new
                stop = 1 if (STOP_RE.search(next_step) or status.startswith("replied")) else 0
                cur = conn.execute("INSERT INTO contacts (job_id, name, title, email, email_source, linkedin, created_at,"
                                   " stop_nudges, notes) VALUES (?,?,?,?, 'import', NULL, ?, ?, ?)",
                                   (job_id, _clean(r.get("contact")), _clean(r.get("title")), email, created, stop,
                                    ("%s | %s" % (status_raw, next_step)).strip(" |")))
                contact_id = cur.lastrowid
                counts["contacts"] += 1
                bounced = status.startswith("bounced")
                conn.execute("INSERT INTO emails (job_id, contact_id, kind, subject, body, message_id, status, error,"
                             " sent_at) VALUES (?,?, 'initial', NULL, NULL, NULL, ?, ?, ?)",
                             (job_id, contact_id, "bounced" if bounced else "sent",
                              next_step if bounced else None, sent_at))
                counts["emails"] += 1
                for n, day in NUDGE_RE.findall(next_step):
                    conn.execute("INSERT INTO emails (job_id, contact_id, kind, status, sent_at) VALUES (?,?,?, 'sent', ?)",
                                 (job_id, contact_id, "nudge" + n, _ts(day, sent_at)))  # bad date: keep order
                    counts["nudges"] += 1
                if status.startswith("replied"):
                    conn.execute("INSERT INTO replies (contact_id, job_id, from_addr, subject, snippet, received_at, kind)"
                                 " VALUES (?,?,?,?,?,?, 'reply')", (contact_id, job_id, email, "(imported)",
                                                                    next_step[:300], reply_at))
                    counts["replies"] += 1
                if bounced:
                    conn.execute("UPDATE contacts SET stop_nudges=1 WHERE id=?", (contact_id,))
                    conn.execute("INSERT INTO replies (contact_id, job_id, from_addr, subject, snippet, received_at, kind)"
                                 " VALUES (?,?, 'mailer-daemon', '(imported bounce)', ?, ?, 'bounce')",
                                 (contact_id, job_id, next_step[:300], created))
                    counts["bounced"] += 1
                touched_jobs.add(job_id)
            else:
                reason = next_step if status.startswith("skip") else ("%s: %s" % (status_raw or "not contacted",
                                                                                  next_step)).strip(": ")
                key = dedupe.norm_key(company, role)
                if conn.execute("SELECT 1 FROM jobs WHERE norm_key=?", (key,)).fetchone():
                    counts["duplicates"] += 1
                    continue
                _get_job(conn, company, role, "skipped", created,
                         "imported: %s" % status_raw if not status.startswith("skip") else None, reason or "skipped")
                counts["jobs"] += 1
                counts["skipped_jobs"] += 1
        for job_id in touched_jobs:
            _settle_job_status(conn, job_id)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return counts


def _settle_job_status(conn, job_id):
    """replied > contacted (some contact still in sequence) > closed > bounced."""
    th = db.load_threads(conn, [r["id"] for r in conn.execute("SELECT id FROM contacts WHERE job_id=?", (job_id,))])
    states = [t["app_status"] for t in th.values()]
    if "replied" in states:
        st = "replied"
    elif any(s in ("sent", "nudge1") and not t["stop_nudges"] for s, t in zip(states, th.values())):
        st = "contacted"
    elif all(s == "bounced" for s in states):
        st = "bounced"
    else:
        st = "closed"
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (st, job_id))


REQUIRED_COLUMNS = ("company", "email", "status")


def import_text(conn, text: str) -> dict:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    have = {(f or "").strip().lower() for f in (reader.fieldnames or [])}
    missing = [c for c in REQUIRED_COLUMNS if c not in have]
    if have and missing:  # e.g. a different CSV: refuse instead of importing addresses as "skipped" jobs
        raise ValueError("tracker.csv is missing column(s) %s; expected date, company, role, contact, title, email, "
                         "status, next_step" % ", ".join(missing))
    return import_rows(conn, reader)


def import_csv(conn, path) -> dict:
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", "replace")
    return import_text(conn, text)


def maybe_auto_import(conn, data_dir) -> dict:
    """Import <data>/tracker.csv on first start (empty DB). Returns counts or None."""
    p = Path(data_dir) / "tracker.csv"
    if not p.exists():
        return None
    if conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] or conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]:
        return None
    return import_csv(conn, p)
