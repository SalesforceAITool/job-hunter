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
