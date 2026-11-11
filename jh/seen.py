""""Have we already worked on this?" checks, so the AI never re-opens a job page or a LinkedIn profile it
already handled in an earlier run (each re-open costs Claude tokens, LinkedIn page loads and SalesQL credits).

filter_jobs(items)      -> each item is new / seen / retry
    seen  : a job with the same company+role, the same LinkedIn job id or the same URL is already saved
            (any status: qualified, skipped, contacted, replied...) -> don't open it again
    retry : saved earlier but no contact was found ("no email found" / "no email in post"), no contact
            saved since, saved in the last 14 days, and not retried yet -> one contact-lookup retry
            (the job is NOT re-read; its job_id is returned). Handed out once per job.
filter_profiles(urls)   -> LinkedIn profiles never looked up before
record_profile(url, found)
filter_posts(run_id, urls) -> LinkedIn posts never read before (Akshay, 11 Nov: every post the posts lane has
    looked at is remembered, hiring or not, so a 10-minute cycle never re-reads the same posts; a search page with
    nothing new is skipped without reading it). A post counts as read once the posts lane that saw it FINISHED
    ('done'); a lane that crashed or was stopped gives its posts back.
"""
import re
from datetime import timedelta

from . import db, dedupe

_LI_JOB = re.compile(r"(?:/jobs/view/(?:[^/?#\s]*-)?|[?&]currentJobId=)(\d{6,15})(?:[/?#&]|$)")
# query params that ARE the job's identity (HN item?id=, Greenhouse ?gh_jid=, Workday/iCIMS ?jobId=...)
_ID_PARAM = re.compile(r"[?&](id|gh_jid|jobid|job_id|jid|jk|token)=([\w-]{1,64})", re.I)
_JR_JOB = re.compile(r"jobright\.ai/jobs/info/([0-9a-f]{12,40})", re.I)
RETRY_REASONS = ("no email found", "no email in post", "recruiter found, no email")
RETRY_DAYS = 14


def url_key(url: str) -> str:
    """Stable key for a job URL: LinkedIn job id, JobRight id, else host+path without query."""
    u = (url or "").strip()
    if not u:
        return ""
    m = _LI_JOB.search(u)
    if m:
        return "li:" + m.group(1)
    m = _JR_JOB.search(u)
    if m:
        return "jr:" + m.group(1).lower()
    q = _ID_PARAM.search(u.split("#")[0])
    u = re.sub(r"^https?://(www\.)?", "", u.split("#")[0].split("?")[0]).rstrip("/").lower()
    return "u:" + u + ("?%s=%s" % (q.group(1).lower(), q.group(2).lower()) if q else "")


def _find_job(conn, company, role, url):
    key = dedupe.norm_key(company or "", role or "")
    if company and role and not key.startswith("|") and not key.endswith("|"):
        r = conn.execute("SELECT * FROM jobs WHERE norm_key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        if r:
            return r
    uk = url_key(url)
    if uk:
        needle = uk.split(":", 1)[1]
        for r in conn.execute("SELECT * FROM jobs WHERE url LIKE ? OR apply_url LIKE ? ORDER BY id DESC LIMIT 20",
                              ("%" + needle.split("/")[-1] + "%",) * 2):
            if url_key(r["url"]) == uk or url_key(r["apply_url"]) == uk:
                return r
    return None


def filter_jobs(conn, items) -> dict:
    out = {"new": [], "seen": [], "retry": []}
    cutoff = (db.now() - timedelta(days=RETRY_DAYS)).replace(microsecond=0).isoformat()
    for it in (items or [])[:200]:
        if isinstance(it, str):
            it = {"url": it}
        if not isinstance(it, dict):
            continue
        company, role, url = (str(it.get(k) or "").strip() for k in ("company", "role", "url"))
        if not (url or (company and role)):
            continue
        r = _find_job(conn, company, role, url)
        echo = {"company": company, "role": role, "url": url}
        if r is None:
            out["new"].append(echo)
            continue
        has_contact = conn.execute("SELECT 1 FROM contacts WHERE job_id=? LIMIT 1", (r["id"],)).fetchone()
        reason = (r["skip_reason"] or "").lower()
        if (not has_contact and r["status"] in ("qualified", "skipped")
                and (r["status"] == "qualified" or any(reason.startswith(x) for x in RETRY_REASONS))
                and (r["lookup_tries"] or 0) < 1 and (r["created_at"] or "") >= cutoff):
            conn.execute("UPDATE jobs SET lookup_tries=COALESCE(lookup_tries,0)+1 WHERE id=?", (r["id"],))
            out["retry"].append(dict(echo, job_id=r["id"], why="no contact found last time"))
            continue
        out["seen"].append(dict(echo, job_id=r["id"], status=r["status"], reason=r["skip_reason"],
                                when=(r["updated_at"] or r["created_at"] or "")[:10]))
    out["counts"] = {k: len(out[k]) for k in ("new", "seen", "retry")}
    return out


def _slug(url):
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url or "", re.I)
    return m.group(1).lower().strip("/") if m else ""


def filter_profiles(conn, urls) -> dict:
    new, seen = [], []
    for u in (urls or [])[:100]:
        s = _slug(str(u or ""))
        if not s:
            continue
        r = conn.execute("SELECT found, last_ts FROM profile_lookups WHERE slug=?", (s,)).fetchone()
        c = conn.execute("SELECT email FROM contacts WHERE lower(linkedin) LIKE ? LIMIT 1",
                         ("%/in/" + s + "%",)).fetchone()
        if r or c:
            seen.append({"url": u, "found": bool((r and r["found"]) or c), "email_known": bool(c),
                         "when": (r["last_ts"] if r else "")[:10]})
        else:
            new.append(u)
    return {"new": new, "seen": seen}


def record_profile(conn, url, found=False, lane=None) -> dict:
    s = _slug(url)
    if not s:
        return {"error": "not a LinkedIn /in/ profile URL"}
    now = db.now_iso()
    conn.execute("INSERT INTO profile_lookups (slug, url, found, lane, first_ts, last_ts) VALUES (?,?,?,?,?,?)"
                 " ON CONFLICT(slug) DO UPDATE SET found=MAX(found, excluded.found), last_ts=excluded.last_ts,"
                 " tries=tries+1", (s, url, 1 if found else 0, lane, now, now))
    return {"ok": True}


# --------------------------------------------------------------------------- LinkedIn posts already read

_POST_ID = re.compile(r"(?:urn:li:|urn%3Ali%3A)(?:activity|share|ugcPost)(?::|%3A)(\d{10,25})|activity-(\d{10,25})", re.I)


def post_id(url: str) -> str:
    """LinkedIn post id from a feed/update URN link or a /posts/...-activity-<id>- link ("" if none)."""
    m = _POST_ID.search(str(url or ""))
    return (m.group(1) or m.group(2)) if m else ""


def filter_posts(conn, run_id, urls) -> dict:
    """-> {"new": [urls to read], "seen": n, "no_id": n}. New posts are remembered for this run at once (so a
    post found by two searches is read once); they count as read for later runs when this lane finishes."""
    new, seen, no_id, now, done = [], 0, 0, db.now_iso(), set()
    rid = run_id if isinstance(run_id, int) else None
    for u in (urls or [])[:100]:
        u = str(u or "").strip()[:300]
        pid = post_id(u)
        if not pid:
            if u:
                no_id += 1
                new.append(u)                       # can't tell: let the AI check it (screen_jobs dedupes saved ones)
            continue
        if pid in done:
            seen += 1
            continue
        done.add(pid)
        r = conn.execute("SELECT run_id, status FROM post_seen WHERE post_id=?", (pid,)).fetchone()
        saved = conn.execute("SELECT 1 FROM jobs WHERE url LIKE ? LIMIT 1", ("%" + pid + "%",)).fetchone()
        if saved or (r and (r["status"] == "done" or (rid is not None and r["run_id"] == rid))):
            seen += 1
            continue
        conn.execute("INSERT INTO post_seen (post_id, url, run_id, status, first_seen, updated_at) VALUES (?,?,?,?,?,?)"
                     " ON CONFLICT(post_id) DO UPDATE SET run_id=excluded.run_id, status='pending',"
                     " updated_at=excluded.updated_at", (pid, u, rid, "pending", now, now))
        new.append(u)
    return {"new": new, "seen": seen, "no_id": no_id}


def close_posts(conn, run_id, ok: bool) -> int:
    """Posts lane finished: its posts count as read ('done'), or go back to unread if the lane failed/stopped."""
    if not isinstance(run_id, int):
        return 0
    if ok:
        return conn.execute("UPDATE post_seen SET status='done', updated_at=? WHERE run_id=? AND status='pending'",
                            (db.now_iso(), run_id)).rowcount
    return conn.execute("DELETE FROM post_seen WHERE run_id=? AND status='pending'", (run_id,)).rowcount
