"""LinkedIn pre-screen for the Easy Apply and LinkedIn Jobs lanes — plain HTTP, no AI, no login.

Before: the AI opened LinkedIn search pages, scrolled them, opened every job and read every JD, and then
skipped ~80% of them (onsite, US-only, not Salesforce, already handled). That reading was most of the bill.

Now, before the AI starts:
  1. LinkedIn's PUBLIC job list (the same guest endpoint watch mode uses) for each picked region
     (remote filter on; Easy Apply filter for the easy_apply lane), newest first;
  2. Salesforce-title filter, then `seen.filter_jobs` -> jobs handled in any earlier run are dropped;
  3. the public JD page of each new job (`jobs-guest/jobs/api/jobPosting/<id>`) -> skip rules in Python
     (jh/screen.py). Skipped jobs are saved as skipped right here (zero tokens, never looked at again);
  4. the survivors are saved as qualified (with JD text, so build_resume works) and handed to the AI as a
     short list of job_ids. The AI never opens a search page or reads a JD to decide.
If LinkedIn's public list can't be read (blocked / rate limited), collect() returns None and the lane runs
the old way (the AI searches itself), so nothing breaks.
"""
import html as htmllib
import logging
import os
import random
import re
import time
import urllib.parse

from . import actions, db, dedupe, screen, seen
from .watch import GEO, GUEST_URL, fetch as http_get, parse_guest_html, title_ok

log = logging.getLogger("jobhunter.liprefetch")

JD_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/%s"
REGION_SOURCE = {"us": "linkedin_us", "india": "linkedin_india", "world": "linkedin_world"}
PAGES = {"us": 3, "india": 2, "world": 2}
CAP = {"easy_apply": 20, "linkedin_jobs": 16}
MAX_JD_FETCH = 45
KEYWORDS = "salesforce"


def search_url(region, lane, lookback="24h", start=0):
    q = {"keywords": KEYWORDS, "geoId": GEO[region], "f_WT": "2", "sortBy": "DD", "start": str(int(start)),
         "f_TPR": "r604800" if lookback == "week" else ("r43200" if lane == "easy_apply" else "r86400")}
    if lane == "easy_apply":
        q["f_AL"] = "true"
    return GUEST_URL + "?" + urllib.parse.urlencode(q)


def _strip(h):
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", h or "")
    t = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/ul)\s*/?>", "\n", t)
    t = htmllib.unescape(re.sub(r"<[^>]{0,2000}>", " ", t))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t)).strip()


def parse_jd(html):
    """{text, apply_url, applicants, criteria, location, closed} from the public job page (best effort)."""
    h = html or ""
    out = {"text": "", "apply_url": None, "applicants": None, "criteria": {}, "location": "", "closed": False}
    m = re.search(r"class=\"show-more-less-html__markup[^\"]*\"[^>]*>(.*?)(?:<button[^>]+show-more-less-html__button|</section>)",
                  h, re.S | re.I)
    if not m:
        m = re.search(r"class=\"description__text[^\"]*\"[^>]*>(.*?)</section>", h, re.S | re.I)
    if m:
        out["text"] = _strip(m.group(1))[:12000]
    a = re.search(r"<code[^>]*id=\"applyUrl\"[^>]*>\s*<!--\s*\"?(.*?)\"?\s*-->", h, re.S | re.I)
    if a:
        u = htmllib.unescape(a.group(1)).strip()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        out["apply_url"] = (q.get("url") or [u])[0][:500] if u.startswith("http") else None
    n = re.search(r"(Over\s+)?([\d,]+)\s+applicants", h, re.I)
    if n:
        out["applicants"] = int(n.group(2).replace(",", "")) + (1 if n.group(1) else 0)
    for k, v in re.findall(r"description__job-criteria-subheader[^>]*>\s*(.*?)\s*</h3>\s*<span[^>]*>\s*(.*?)\s*</span>", h, re.S | re.I):
        out["criteria"][_strip(k)] = _strip(v)
    loc = re.search(r"topcard__flavor--bullet[^>]*>\s*(.*?)\s*</span>", h, re.S | re.I)
    if loc:
        out["location"] = _strip(loc.group(1))[:150]
    out["closed"] = bool(re.search(r"No longer accepting applications", h, re.I))
    return out


def _sleep(a=1.2, b=2.6):
    if os.environ.get("JOBHUNTER_OFFLINE") == "1":
        return
    time.sleep(random.uniform(a, b))


def collect(conn, run_id, lane, regions=("us", "india", "world"), lookback="24h", fetcher=None, emit=None,
            cap=None, extra_cards=None):
    """-> {"targets": [...], "counts": {...}} or None when LinkedIn's public list could not be read at all.

    extra_cards: watch-mode cards [{ext_id,title,company,location,url}] screened instead of searching."""
    if os.environ.get("JOBHUNTER_OFFLINE") == "1" and fetcher is None:
        return None
    if fetcher is None:
        from . import ligate
        if ligate.safe_mode() and not extra_cards:
            return None                 # account safety: no LinkedIn public-page fetching; the AI searches as a person would
        fetcher = ligate.get            # shared LinkedIn spacing + rate-limit pause
    get = fetcher
    cap = cap or CAP.get(lane, 16)
    say = emit or (lambda *a, **k: None)
    counts = {"cards": 0, "not_salesforce": 0, "seen": 0, "screened_out": 0, "closed": 0, "saved": 0,
              "retry": 0, "jd_missing": 0, "regions_ok": 0}
    reasons = {}
    cards, ok_any = [], False
    if extra_cards:
        ok_any = True
        for c in extra_cards:
            cards.append(dict(c, region=c.get("region") or "world"))
    else:
        for region in [r for r in ("us", "india", "world") if r in (regions or GEO)]:
            got = 0
            for page in range(PAGES[region]):
                st, body = get(search_url(region, lane, lookback, start=25 * page))
                if st != 200:
                    if page == 0:
                        say("LinkedIn public list %s: HTTP %s" % (region, st or body[:80]))
                    break
                ok_any = True
                cs = parse_guest_html(body)
                for c in cs:
                    c["region"] = region
                cards += cs
                got += len(cs)
                if len(cs) < 10:
                    break
                _sleep()
            if got:
                counts["regions_ok"] += 1
    if not ok_any:
        return None
    # unique, Salesforce titles only
    uniq, seen_ids, keys = [], set(), set()
    for c in cards:
        k = dedupe.norm_key(c.get("company") or "", c.get("title") or "")
        if c["ext_id"] in seen_ids or (c.get("company") and k in keys):
            continue                    # same job listed under several ids (regions / locations)
        seen_ids.add(c["ext_id"])
        keys.add(k)
        counts["cards"] += 1
        if not title_ok(c.get("title")):
            counts["not_salesforce"] += 1
            continue
        uniq.append(c)
    if not uniq:
        return {"targets": [], "counts": counts, "reasons": reasons}
    flt = seen.filter_jobs(conn, [{"company": c.get("company") or "", "role": c["title"], "url": c["url"]} for c in uniq])
    by_url = {x["url"]: x for x in flt["retry"]}
    new_urls = {x["url"] for x in flt["new"]}
    counts["seen"] = len(flt["seen"])
    targets, fetched = [], 0
    for c in uniq:
        if len(targets) >= cap:
            break
        src = REGION_SOURCE[c["region"]]
        if c["url"] in by_url:
            r = by_url[c["url"]]
            if lane == "easy_apply" and conn.execute(
                    "SELECT 1 FROM jobs j WHERE j.id=? AND (EXISTS (SELECT 1 FROM portal_apps p WHERE p.job_id=j.id)"
                    " OR j.notes LIKE '%external apply%')", (r["job_id"],)).fetchone():
                continue
            counts["retry"] += 1
            targets.append({"job_id": r["job_id"], "company": c.get("company"), "role": c["title"], "url": c["url"],
                            "location": c.get("location"), "retry": True, "jd": True})
            continue
        if c["url"] not in new_urls or not c.get("company"):
            continue
        jd = {"text": "", "apply_url": None, "applicants": None, "location": "", "closed": False}
        if fetched < MAX_JD_FETCH:
            _sleep(1.0, 2.2)
            st, body = get(JD_URL % c["ext_id"])
            fetched += 1
            if st == 200:
                jd = parse_jd(body)
            elif st == 429:
                fetched = MAX_JD_FETCH          # rate limited: stop fetching, the AI reads the rest itself
        loc = c.get("location") or jd["location"] or ""
        if jd["closed"]:
            counts["closed"] += 1
            actions.save_job(conn, run_id, lane, src, c["company"], c["title"], location=loc, url=c["url"],
                             decision="skipped", skip_reason="posting closed", jd_text=jd["text"][:4000])
            continue
        ok, why = screen.screen(c["title"], jd["text"], loc, india_role=(c["region"] == "india") or None)
        if ok and not extra_cards:            # guest search cards (watch-mode cards came from the Easy Apply filter too)
            ok, why = screen.guest_remote_check(loc, jd["text"])
        if not ok:
            counts["screened_out"] += 1
            key = why.split(" (")[0]
            reasons[key] = reasons.get(key, 0) + 1
            actions.save_job(conn, run_id, lane, src, c["company"], c["title"], location=loc, url=c["url"],
                             decision="skipped", skip_reason=why, jd_text=jd["text"][:4000],
                             notes="screened by Job Hunter (no AI)", apply_url=_ats(jd["apply_url"]))
            continue
        if lane == "easy_apply" and jd["apply_url"]:
            # the public page shows an external apply link -> not an Easy Apply job, but it passed every rule:
            # keep it qualified so the LinkedIn Jobs (contacts) and Portal lanes still pick it up
            counts["screened_out"] += 1
            reasons["external apply"] = reasons.get("external apply", 0) + 1
            actions.save_job(conn, run_id, lane, src, c["company"], c["title"], location=loc, url=c["url"],
                             jd_text=jd["text"][:6000], notes="external apply (no Easy Apply)",
                             apply_url=_ats(jd["apply_url"]) or jd["apply_url"])
            continue
        if not jd["text"]:
            counts["jd_missing"] += 1
        res = actions.save_job(conn, run_id, lane, src, c["company"], c["title"], location=loc, url=c["url"],
                               posted=c.get("listed") or c.get("ago") or "", jd_text=jd["text"][:6000],
                               notes=("%s applicants" % jd["applicants"]) if jd["applicants"] is not None else None,
                               apply_url=_ats(jd["apply_url"]))
        if res.get("job_id") and not res.get("duplicate") and res["job_id"] not in {t["job_id"] for t in targets}:
            counts["saved"] += 1
            targets.append({"job_id": res["job_id"], "company": c["company"], "role": c["title"], "url": c["url"],
                            "location": loc, "jd": bool(jd["text"]), "applicants": jd["applicants"],
                            "age_h": _age_hours(c.get("ago"))})
    # fewest applicants first: being early matters most there
    # freshest first (under 3 h, under 12 h, older), then fewest applicants: being early matters most
    targets.sort(key=lambda t: (t.get("retry", False), _bucket(t.get("age_h")),
                                t.get("applicants") if t.get("applicants") is not None else 60))
    return {"targets": targets, "counts": counts, "reasons": reasons}


def _age_hours(ago):
    m = re.search(r"(\d+)\s+(minute|hour|day|week)s?\s+ago", ago or "", re.I)
    if not m:
        return 0.0 if re.search(r"just now|moments? ago|seconds? ago", ago or "", re.I) else None
    return int(m.group(1)) * {"minute": 1 / 60, "hour": 1, "day": 24, "week": 168}[m.group(2).lower()]


def _bucket(age_h):
    if age_h is None:
        return 1
    return 0 if age_h < 3 else (1 if age_h < 12 else 2)


def _ats(u):
    return u if u and re.search(r"greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com", u, re.I) else None
