"""More ways to get a VERIFIED work email when SalesQL finds none (Akshay, 22 Oct).

find(conn, cfg, job_id, company, domain, first, last) tries, cheapest first, and returns only verified addresses:
  1. replies   — people at that company who already wrote to him (Job Hunter's own replies table)
  2. gmail     — real senders at that company domain in his Gmail inbox (last 12 months, no robots)
  3. hunter    — Hunter.io (optional API key in Settings): the named person's address if Hunter verifies it
                 ("valid"), else verified HR / recruiting addresses from Hunter's domain search
  4. apollo    — Apollo.io (optional API key): the named person, only when Apollo marks the email "verified"
Every candidate is remembered in email_candidates for that job, and save_contact only accepts these sources for
an address this tool returned (the AI can't invent one and label it "hunter").

Not here (they need no code): the address printed in the post (email_source "post") and a hiring inbox printed on
the company's own careers/contact page (email_source "website") — the lane prompts cover those.
Never guessed, never "probably first.last@": an address is used only when a source verified it.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from . import db, dedupe

log = logging.getLogger("jobhunter.emailfind")

HUNTER_FINDER = "https://api.hunter.io/v2/email-finder?%s"
HUNTER_DOMAIN = "https://api.hunter.io/v2/domain-search?%s"
APOLLO_MATCH = "https://api.apollo.io/api/v1/people/match"
HR_WORDS = re.compile(r"recruit|talent|hr\b|human resources|people|hiring|acquisition|staffing|delivery|account manager|"
                      r"salesforce|crm|founder|ceo|cto|director|head", re.I)
NOREPLY = re.compile(r"^(?:no-?reply|noreply|do-?not-?reply|notifications?|mailer|bounce|alerts?|news|newsletter|marketing|"
                     r"support|billing|invoice|info)[^@]*@", re.I)
BLOCKED_DOMAINS = {"linkedin.com", "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
                   "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "myworkdayjobs.com", "indeed.com"}
SOURCES = ("replies", "gmail", "hunter", "apollo")


def clean_domain(d):
    d = (d or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split("?")[0].split(":")[0]
    d = d[4:] if d.startswith("www.") else d
    if not re.fullmatch(r"[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63}){1,4}", d) or d in BLOCKED_DOMAINS or d in dedupe.FREE_MAIL:
        return ""
    return d


def _finder_cfg(cfg):
    f = (cfg or {}).get("email_finder") or {}
    return {"hunter": str(f.get("hunter_api_key") or "").strip(), "apollo": str(f.get("apollo_api_key") or "").strip(),
            "daily_cap": int(f.get("daily_cap") or 25)}


def _used_today(conn):
    k = "finder_" + db.now_iso()[:10]
    r = conn.execute("SELECT v FROM app_meta WHERE k=?", (k,)).fetchone()
    return int(r[0]) if r and str(r[0]).isdigit() else 0


def _use(conn):
    k = "finder_" + db.now_iso()[:10]
    conn.execute("INSERT OR REPLACE INTO app_meta (k, v) VALUES (?, ?)", (k, str(_used_today(conn) + 1)))


def _get_json(url, headers=None, data=None, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers=dict({"Accept": "application/json", "User-Agent": "JobHunter/1.0"}, **(headers or {})),
                                 method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read(2_000_000).decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:      # network / bad JSON: the lane just moves on
        return 0, {"error": str(e)}


def hunter(cfg_key, domain, first="", last="", company="", fetch=None):
    """Verified addresses from Hunter.io: the named person (email-finder), else HR/recruiting people (domain search)."""
    fetch = fetch or _get_json
    out = []
    if first and last:
        q = {"first_name": first, "last_name": last, "api_key": cfg_key}
        q.update({"domain": domain} if domain else {"company": company})
        st, doc = fetch(HUNTER_FINDER % urllib.parse.urlencode(q))
        d = (doc or {}).get("data") or {}
        if st == 200 and d.get("email") and ((d.get("verification") or {}).get("status") == "valid"):
            out.append({"email": d["email"], "name": "%s %s" % (first, last), "title": d.get("position") or "",
                        "source": "hunter", "how": "Hunter verified the address of %s %s" % (first, last)})
    if not out:
        q = {"type": "personal", "limit": 10, "api_key": cfg_key}
        q.update({"domain": domain} if domain else {"company": company})
        st, doc = fetch(HUNTER_DOMAIN % urllib.parse.urlencode(q))
        for e in ((doc or {}).get("data") or {}).get("emails") or []:
            if not isinstance(e, dict) or (e.get("verification") or {}).get("status") != "valid":
                continue
            title = e.get("position") or e.get("department") or ""
            if not HR_WORDS.search("%s %s" % (title, e.get("department") or "")):
                continue
            out.append({"email": e.get("value"), "name": " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x),
                        "title": title, "source": "hunter", "how": "Hunter domain search (verified)"})
    return out[:3]


def apollo(cfg_key, domain, first, last, company="", fetch=None):
    """The named person's email from Apollo.io, only when Apollo marks it verified."""
    if not (first and last):
        return []
    fetch = fetch or _get_json
    body = {"first_name": first, "last_name": last, "reveal_personal_emails": False}
    body.update({"domain": domain} if domain else {"organization_name": company})
    st, doc = fetch(APOLLO_MATCH, headers={"X-Api-Key": cfg_key, "Cache-Control": "no-cache"}, data=body)
    p = (doc or {}).get("person") or {}
    if st == 200 and p.get("email") and p.get("email_status") == "verified":
        return [{"email": p["email"], "name": p.get("name") or "%s %s" % (first, last), "title": p.get("title") or "",
                 "source": "apollo", "how": "Apollo verified"}]
    return []


def from_replies(conn, domain):
    if not domain:
        return []
    rows = conn.execute("SELECT DISTINCT lower(from_addr) AS a FROM replies WHERE lower(from_addr) LIKE ? AND kind!='auto'",
                        ("%@" + domain,)).fetchall()
    return [{"email": r["a"], "name": "", "title": "", "source": "replies", "how": "wrote to you before"}
            for r in rows if not NOREPLY.match(r["a"])]


def from_gmail(cfg, domain, mailbox_factory=None):
    if not domain:
        return []
    from . import mailer
    try:
        with (mailbox_factory or mailer.mailbox)(cfg) as mb:
            if not hasattr(mb, "senders_at"):
                return []
            got = mb.senders_at(domain)
    except Exception as e:
        log.info("gmail lookup for %s: %s", domain, e)
        return []
    return [{"email": a, "name": n, "title": "", "source": "gmail", "how": "a real person at %s emailed you" % domain}
            for a, n in got if not NOREPLY.match(a)][:3]


def find(conn, cfg, job_id, company, domain, first="", last="", fetch=None, mailbox_factory=None) -> dict:
    """Verified candidate addresses for a job, cheapest source first. Remembers them for save_contact."""
    domain = clean_domain(domain)
    first, last = (first or "").strip()[:40], (last or "").strip()[:40]
    fc = _finder_cfg(cfg)
    tried, cands = [], []
    for name, fn in (("replies", lambda: from_replies(conn, domain)),
                     ("gmail", lambda: from_gmail(cfg, domain, mailbox_factory))):
        tried.append(name)
        cands = fn()
        if cands:
            break
    paid = [(n, k) for n, k in (("hunter", fc["hunter"]), ("apollo", fc["apollo"])) if k]
    if not cands and paid and (domain or company):
        if _used_today(conn) >= fc["daily_cap"]:
            tried.append("daily lookup cap reached (%d)" % fc["daily_cap"])
        else:
            for name, key in paid:
                if name == "apollo" and not (first and last):
                    continue
                tried.append(name)
                _use(conn)
                cands = hunter(key, domain, first, last, company, fetch) if name == "hunter" else \
                    apollo(key, domain, first, last, company, fetch)
                if cands:
                    break
    out, seen = [], set()
    for c in cands:
        addr = dedupe.normalize_email(c.get("email"))
        if not dedupe.valid_email(addr) or addr in seen or addr.split("@")[-1] in dedupe.FREE_MAIL:
            continue
        if domain and not (addr.endswith("@" + domain) or addr.endswith("." + domain)):
            continue                     # only the company's own domain
        seen.add(addr)
        hit = dedupe.email_contacted(conn, addr)
        c = dict(c, email=addr, already_emailed=bool(hit))
        out.append(c)
        conn.execute("INSERT INTO email_candidates (job_id, email, source, name, title, created_at) VALUES (?,?,?,?,?,?)",
                     (job_id, addr, c["source"], c.get("name") or "", c.get("title") or "", db.now_iso()))
    hint = ""
    if not out and not paid:
        hint = ("No verified email from your mailbox. Next: a hiring inbox printed on the company's own careers/contact"
                " page (email_source 'website'); otherwise leave the job for a portal application.")
    return {"candidates": out, "tried": tried, "domain": domain, "hint": hint}


def returned_for(conn, job_id, email, source) -> bool:
    """save_contact check: this verified source really returned this address for this job (last 2 days)."""
    since = db.now_iso()[:10]
    return bool(conn.execute("SELECT 1 FROM email_candidates WHERE job_id=? AND email=? AND source=? AND"
                             " substr(created_at,1,10)>=date(?, '-1 day')",
                             (job_id, dedupe.normalize_email(email), source, since)).fetchone())
