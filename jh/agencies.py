"""Agencies: staffing / recruitment consultancies that place India-based Salesforce contractors with US and other
foreign clients (C2C through Trysyntx IT Solution Pvt. Ltd.).

Separate from job outreach (Akshay, 2026-10-20): its own tab, its own lane ("agencies", only when ticked), its own
daily email cap. It reuses the job machinery underneath so dedupe, resume, reply/bounce detection and nudges all
work: every pitch is sent on a small "pseudo job" (jobs.source = 'agency', role = AGENCY_ROLE + month), so
  * a recruiter already emailed for any job is never pitched again (dedupe on email / LinkedIn profile);
  * a reply marks the pseudo job 'replied' -> the agency shows "replied";
  * a re-touch after 90 days uses a new month in the role, so the company+role dedupe allows it.

Where agencies come from (no AI tokens):
  1. the 237-agency sheet built on 2026-10-19 (jh/agency_seed.json): A = verified fit, B = non-US, C = US C2C vendors;
  2. companies already in Job Hunter whose posts / JDs read like an agency ("our client", "C2C", "implementation
     partner", "staffing" ...).
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

from . import actions, db, dedupe

AGENCY_ROLE = "Salesforce contract roles for your clients (C2C, remote)"
SEED_FILE = Path(__file__).with_name("agency_seed.json")
SEED_VERSION = "2026-10-19"
RETOUCH_DAYS = 90
NO_CONTACT_RETRY_DAYS = 30
STATUSES = ("new", "pitched", "replied", "no_contact", "not_fit", "paused", "manual")
USER_STATUSES = ("new", "not_fit", "paused", "manual")      # what the tab's buttons may set

SIGNALS = re.compile(r"\bour\s+(?:esteemed\s+|direct\s+)?client\b|\bend[\s-]client\b|on\s+behalf\s+of|implementation\s+partner|"
                     r"prime\s+vendor|\bc2c\b|corp[\s-]+to[\s-]+corp|\bstaffing\b|\bbench\b|vendor\s+partner|\bc2h\b|"
                     r"contract[\s-]to[\s-]hire|talent\s+solutions", re.I)
NAME_HINT = re.compile(r"staffing|recruit|talent|consult|infotech|it\s+services|resourc|\bsearch\b|solutions|technolog|"
                       r"systems|\btek\b|\bsoft|global|\blabs?\b|partners", re.I)
NOT_A_NAME = re.compile(r"confidential|undisclosed|stealth|\bclient\b|hiring company", re.I)
INDIAN_ENTITY = re.compile(r"\b(?:pvt|private\s+limited|llp|india)\b", re.I)
DIRECT_ATS = re.compile(r"greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs|workday|oraclecloud|icims", re.I)


def norm(name):
    s = re.sub(r"\(.*?\)", " ", name or "").lower()
    s = re.sub(r"\b(inc|llc|l\.l\.c|ltd|limited|pvt|private|corp|corporation|co|group|gmbh|bv|plc)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def region_of(priority, region):
    r = (region or "").lower()
    if priority == "C" or r in ("us", "usa", "united states") or r.startswith("us "):
        return "us"
    if "india" in r:
        return "india"
    return "non_us"


def _site(w):
    w = (w or "").strip()
    if not w:
        return ""
    return w if re.match(r"(?i)https?://", w) else "https://" + w


# --------------------------------------------------------------------------- seed + discovery (no AI)

def import_seed(conn, path=SEED_FILE) -> int:
    if conn.execute("SELECT 1 FROM app_meta WHERE k='agency_seed' AND v=?", (SEED_VERSION,)).fetchone():
        return 0
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    now = db.now_iso()
    n = 0
    for r in data.get("rows") or []:
        r = list(r) + [""] * (9 - len(r))
        pr, name, web, region, typ, contacts, emails, st, notes = r[:9]
        past = 1 if (st or "").lower().startswith("past client") else 0
        status = "manual" if past else "new"
        cur = conn.execute(
            "INSERT OR IGNORE INTO agencies (name, norm, priority, kind, region, website, source, known_contacts,"
            " known_emails, status, notes, created_at, updated_at, past_client) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, norm(name), pr, typ or ("US IT staffing / C2C vendor" if pr == "C" else "Recruitment / IT staffing"),
             region_of(pr, region), _site(web), "sheet " + SEED_VERSION, contacts or None, emails or None, status,
             notes or None, now, now, past))
        n += cur.rowcount
    for name, url, note in data.get("marketplaces") or []:
        cur = conn.execute(
            "INSERT OR IGNORE INTO agencies (name, norm, priority, kind, region, website, source, status, notes,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (name, norm(name), "M", "marketplace", "global", url, "checklist", "manual", note, now, now))
        n += cur.rowcount
    conn.execute("INSERT OR REPLACE INTO app_meta (k, v) VALUES ('agency_seed', ?)", (SEED_VERSION,))
    return n


def _clean_name(name):
    """'Acme (LinkedIn post via X)' / 'ACS Consultancy (client NY OMH)' -> the company name only."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "")).strip() or str(name or "").strip()


def sync_job_contacts(conn) -> None:
    """via_jobs / via_replied: how many job-application emails went to each agency, and whether one got a reply.
    An agency we are already talking to through job applications counts as approached (and isn't pitched twice)."""
    per = {}
    for r in conn.execute("SELECT j.company, j.status, COUNT(*) n FROM emails e JOIN jobs j ON j.id=e.job_id"
                          " WHERE j.source<>'agency' AND e.kind='initial' AND e.status NOT IN ('dry_run','failed','sending') GROUP BY j.id"):
        k = norm(_clean_name(r["company"]))
        cur = per.setdefault(k, [0, 0])
        cur[0] += r["n"]
        cur[1] = cur[1] or int(r["status"] == "replied")
    for a in conn.execute("SELECT id, norm, via_jobs, via_replied FROM agencies WHERE kind<>'marketplace'").fetchall():
        n, rep_ = per.get(a["norm"], (0, 0))
        if (n, rep_) != (a["via_jobs"] or 0, a["via_replied"] or 0):
            conn.execute("UPDATE agencies SET via_jobs=?, via_replied=? WHERE id=?", (n, rep_, a["id"]))


def discover_from_jobs(conn) -> int:
    """Companies already in Job Hunter that look like agencies (signals in their posts / JDs)."""
    known = {r[0] for r in conn.execute("SELECT norm FROM agencies")}
    per = {}
    emailed = {}
    for r in conn.execute("SELECT j.company, COUNT(*) n FROM emails e JOIN jobs j ON j.id=e.job_id WHERE j.source<>'agency'"
                          " AND e.kind='initial' AND e.status NOT IN ('dry_run','failed','sending') GROUP BY j.company"):
        k = norm(_clean_name(r["company"]))
        emailed[k] = emailed.get(k, 0) + r["n"]
    for r in conn.execute("SELECT company, location, jd_text, notes, apply_url, source FROM jobs"
                          " WHERE source NOT IN ('agency') AND company IS NOT NULL"):
        key = norm(_clean_name(r["company"]))
        if not key or key in known:
            continue
        text = "%s\n%s" % (r["jd_text"] or "", r["notes"] or "")
        hits = len(set(m.group(0).lower() for m in SIGNALS.finditer(text)))
        p = per.setdefault(key, {"name": r["company"], "hits": 0, "jobs": 0, "us": 0, "india": 0, "ats": 0, "hint": 0})
        p["jobs"] += 1
        p["hits"] = max(p["hits"], hits)
        p["us"] += 1 if (r["source"] == "linkedin_us" or re.search(r"united states|\bUSA?\b|\bc2c\b", text)) else 0
        p["india"] += 1 if (r["source"] == "linkedin_india" or re.search(r"\bindia\b|bengaluru|bangalore|hyderabad|pune|"
                                                                          r"chennai|noida|gurugram|gurgaon|mumbai|\bIST\b",
                                                                          "%s %s" % (r["location"] or "", text), re.I)) else 0
        p["ats"] += 1 if DIRECT_ATS.search(r["apply_url"] or "") else 0
        p["hint"] = 1 if NAME_HINT.search(r["company"]) else 0
    now = db.now_iso()
    n = 0
    for key, p in per.items():
        if p["ats"] and p["hits"] < 2:          # a direct employer with its own ATS
            continue
        if NOT_A_NAME.search(p["name"]):
            continue
        if p["hits"] >= 2 or (p["hits"] >= 1 and p["hint"]) or (emailed.get(key) and p["hint"] and not p["ats"]):
            name = _clean_name(p["name"])[:120]
            cur = conn.execute(
                "INSERT OR IGNORE INTO agencies (name, norm, priority, kind, region, source, status, notes,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, key, "D", "Staffing (seen in your jobs)",
                 "india" if (p["india"] > p["us"] or INDIAN_ENTITY.search(p["name"])) else ("us" if p["us"] else "non_us"),
                 "your jobs", "new",
                 "%d job(s) in Job Hunter with agency wording" % p["jobs"], now, now))
            n += cur.rowcount
    return n


# --------------------------------------------------------------------------- status from the pseudo jobs

def refresh_status(conn) -> None:
    today = db.today_ist()
    for a in conn.execute("SELECT a.id, a.status, a.job_id, j.status AS jst, j.skip_reason, j.updated_at AS jup FROM agencies a"
                          " LEFT JOIN jobs j ON j.id=a.job_id WHERE a.job_id IS NOT NULL").fetchall():
        sent = conn.execute("SELECT MAX(sent_at), COUNT(*) FROM emails WHERE job_id=? AND kind='initial'"
                            " AND status IN ('sent','bounced','sending')", (a["job_id"],)).fetchone()
        st, last, nxt = a["status"], None, None
        if a["status"] in ("not_fit", "paused", "manual"):
            continue
        if a["jst"] == "replied":
            st = "replied"
        elif sent[1]:
            st, last = "pitched", sent[0]
            try:
                base = date.fromisoformat((sent[0] or "")[:10])
            except ValueError:
                base = today
            nxt = base + timedelta(days=RETOUCH_DAYS)
        elif a["jst"] == "skipped":
            st = "no_contact"
            try:
                base = date.fromisoformat((a["jup"] or "")[:10])
            except ValueError:
                base = today
            nxt = base + timedelta(days=NO_CONTACT_RETRY_DAYS)
        else:
            continue
        conn.execute("UPDATE agencies SET status=?, last_touch=COALESCE(?, last_touch), next_touch=COALESCE(?, next_touch),"
                     " touches=(SELECT COUNT(*) FROM emails e JOIN jobs j ON j.id=e.job_id WHERE j.source='agency'"
                     " AND j.company=agencies.name AND e.kind='initial' AND e.status NOT IN ('dry_run','failed','sending')),"
                     " updated_at=? WHERE id=? AND (status<>? OR COALESCE(next_touch,'')<>COALESCE(?,next_touch,''))",
                     (st, last, nxt.isoformat() if nxt else None, db.now_iso(), a["id"], st, nxt.isoformat() if nxt else None))


def _engaged(conn, name):
    """Already talking to this company through job outreach (a reply or an email in the last 30 days)."""
    since = (db.now() - timedelta(days=30)).replace(microsecond=0).isoformat()
    key = norm(name)
    for r in conn.execute("SELECT j.company, j.status, MAX(e.sent_at) s FROM jobs j JOIN emails e ON e.job_id=j.id"
                          " WHERE j.source<>'agency' AND e.kind='initial' AND e.status NOT IN ('dry_run','failed','sending')"
                          " GROUP BY j.id"):
        if norm(r["company"]) == key and (r["status"] == "replied" or (r["s"] or "") >= since):
            return True
    return False


# Two tracks (Akshay, 2026-10-21): agencies in India vs outside India, tracked and pitched separately.
TRACKS = ("outside", "india", "all")
LEGACY_FOCUS = {"us_first": "outside", "us_only": "outside"}
PRIORITY_ORDER = {"outside": "CASE WHEN a.priority IN ('A','U') THEN 0 WHEN a.region='us' THEN 1 WHEN a.priority='D' THEN 2 ELSE 3 END",
                  "india": "CASE WHEN a.priority IN ('A','U') THEN 0 WHEN a.priority='D' THEN 1 ELSE 2 END",
                  "all": "CASE WHEN a.priority IN ('A','U') THEN 0 WHEN a.region='us' THEN 1 WHEN a.priority='D' THEN 2 ELSE 3 END"}


def track_of(region):
    return "india" if region == "india" else "outside"


def targets(conn, run_id, limit=5, focus="outside", lane="agencies") -> list:
    """Pick the agencies to pitch now and give each a pseudo job. Returns [{agency_id, job_id, name, ...}]."""
    import_seed(conn)
    discover_from_jobs(conn)
    sync_job_contacts(conn)
    refresh_status(conn)
    today = db.today_ist().isoformat()
    where = "a.status='new' OR (a.status IN ('pitched','no_contact') AND a.next_touch IS NOT NULL AND a.next_touch<=?)"
    focus = LEGACY_FOCUS.get(focus, focus)
    if focus == "outside":
        where = "(%s) AND COALESCE(a.region,'')<>'india'" % where
    elif focus == "india":
        where = "(%s) AND a.region='india'" % where
    rows = conn.execute("SELECT a.* FROM agencies a WHERE (%s) AND a.kind<>'marketplace' ORDER BY %s, a.id"
                        % (where, PRIORITY_ORDER.get(focus, PRIORITY_ORDER["outside"])), (today,)).fetchall()
    out = []
    month = db.today_ist().strftime("%b %Y")
    for a in rows:
        if len(out) >= limit:
            break
        if _engaged(conn, a["name"]):
            conn.execute("UPDATE agencies SET status='manual', notes=TRIM(COALESCE(notes,'') || ' · already in touch via"
                         " job outreach'), updated_at=? WHERE id=?", (db.now_iso(), a["id"]))
            continue
        role = "%s - %s" % (AGENCY_ROLE, month)
        res = actions.save_job(conn, run_id, lane, "agency", a["name"], role, location="Remote (agency)", remote=True,
                               url=a["website"] or "", jd_text="Agency profile: %s. Region: %s. %s"
                               % (a["kind"] or "", a["region"] or "", a["notes"] or ""),
                               notes="agency pitch (Agencies tab)")
        jid = res.get("job_id")
        if not jid or res.get("duplicate"):
            continue
        conn.execute("UPDATE agencies SET job_id=?, updated_at=? WHERE id=?", (jid, db.now_iso(), a["id"]))
        emails = [e.strip() for e in re.split(r"[;,\s]+", a["known_emails"] or "") if dedupe.valid_email(e.strip())]
        out.append({"agency_id": a["id"], "job_id": jid, "name": a["name"], "priority": a["priority"],
                    "region": a["region"], "track": track_of(a["region"]), "website": a["website"] or "", "kind": a["kind"] or "",
                    "known_contacts": (a["known_contacts"] or "")[:300], "known_emails": emails[:3]})
    return out


# --------------------------------------------------------------------------- tab

def list_all(conn) -> dict:
    import_seed(conn)
    discover_from_jobs(conn)
    sync_job_contacts(conn)
    refresh_status(conn)
    rows = []
    for a in conn.execute("SELECT a.*, (SELECT COUNT(*) FROM contacts c WHERE c.job_id=a.job_id) AS contacts_n FROM agencies a"
                          " ORDER BY CASE a.status WHEN 'replied' THEN 0 WHEN 'pitched' THEN 1 WHEN 'new' THEN 2 ELSE 3 END, "
                          + PRIORITY_ORDER["outside"] + ", a.name"):
        d = dict(a)
        d["recruiters"] = [dict(r) for r in conn.execute(
            "SELECT c.name, c.title, c.email, c.linkedin, (SELECT status FROM emails e WHERE e.contact_id=c.id AND"
            " e.kind='initial' ORDER BY e.id DESC LIMIT 1) AS email_status FROM contacts c JOIN jobs j ON j.id=c.job_id"
            " WHERE j.source='agency' AND j.company=?", (a["name"],))]
        rows.append(d)
    counts = {s: 0 for s in STATUSES}
    tracks = {t: {"total": 0, "to_pitch": 0, "approached": 0, "people": 0, "replied": 0, "no_contact": 0, "set_aside": 0,
                  "via_jobs": 0}
              for t in ("india", "outside")}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["kind"] == "marketplace":
            continue
        r["track"] = track_of(r["region"])
        t = tracks[r["track"]]
        t["total"] += 1
        sent = [x for x in r["recruiters"] if x.get("email_status") in ("sent", "bounced", "dry_run")]
        t["people"] += len(sent)
        via = int(r.get("via_jobs") or 0) > 0
        t["via_jobs"] += 1 if via else 0
        if r["status"] == "new" and not via:
            t["to_pitch"] += 1
        if r["status"] in ("pitched", "replied") or via:
            t["approached"] += 1
        if r["status"] == "replied" or r.get("via_replied"):
            t["replied"] += 1
        elif r["status"] == "no_contact":
            t["no_contact"] += 1
        elif r["status"] in ("not_fit", "paused", "manual"):
            t["set_aside"] += 1
    today = db.today_ist().isoformat()
    sent_today = conn.execute("SELECT COUNT(*) FROM emails e JOIN jobs j ON j.id=e.job_id WHERE j.source='agency'"
                              " AND e.kind='initial' AND e.status IN ('sent','sending','failed','bounced')"
                              " AND substr(e.sent_at,1,10)=?", (today,)).fetchone()[0]
    return {"agencies": rows, "counts": counts, "tracks": tracks, "sent_today": sent_today}


def add(conn, name, website="", region="us", kind="Staffing", notes="") -> dict:
    name = " ".join(str(name or "").split())[:120]
    if len(name) < 2:
        return {"error": "name is required"}
    region = region if region in ("us", "non_us", "india") else "us"
    now = db.now_iso()
    cur = conn.execute("INSERT OR IGNORE INTO agencies (name, norm, priority, kind, region, website, source, status, notes,"
                       " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (name, norm(name), "U", str(kind or "Staffing")[:80], region, _site(website)[:200]
                        if re.match(r"(?i)^(https?://)?[a-z0-9.-]+\.[a-z]{2,}(/\S*)?$", str(website or "").strip()) else "",
                        "added by you", "new", str(notes or "")[:300] or None, now, now))
    if not cur.rowcount:
        return {"error": "%s is already in the list" % name}
    return {"ok": True, "id": cur.lastrowid}


def set_region(conn, agency_id, region) -> dict:
    if region not in ("us", "non_us", "india"):
        return {"error": "region must be us, non_us or india"}
    cur = conn.execute("UPDATE agencies SET region=?, updated_at=? WHERE id=?", (region, db.now_iso(), int(agency_id)))
    return {"ok": True} if cur.rowcount else {"error": "agency %s not found" % agency_id}


def set_status(conn, agency_id, status) -> dict:
    if status not in USER_STATUSES:
        return {"error": "status must be one of %s" % ", ".join(USER_STATUSES)}
    row = conn.execute("SELECT past_client FROM agencies WHERE id=?", (int(agency_id),)).fetchone()
    if row and row["past_client"] and status == "new":
        return {"error": "this is a past client you email yourself; it is never auto-pitched"}
    cur = conn.execute("UPDATE agencies SET status=?, updated_at=? WHERE id=?", (status, db.now_iso(), int(agency_id)))
    return {"ok": True} if cur.rowcount else {"error": "agency %s not found" % agency_id}
