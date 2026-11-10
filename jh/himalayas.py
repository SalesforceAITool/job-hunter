"""Himalayas (himalayas.app) source — free public jobs API, no login, no AI, no browser.

Himalayas' API terms allow "feeding AI agents and automation workflows"; jobs keep a link back to Himalayas
(the job URL) and are never re-published. Data refreshes daily, so one pass per run is plenty.

collect(): search "salesforce" for jobs open to India (country filter = India-allowed + worldwide jobs),
newest first, keep Salesforce technical titles whose location restrictions allow India, skip anything
already in Job Hunter (seen.filter_jobs), save the rest as qualified jobs (source "himalayas") and return
them as targets for the AI contact lane. Jobs with a Greenhouse/Lever/Ashby apply link are picked up by
the Portal lane automatically (apply_url is saved).
"""
import gzip
import os
import html as htmllib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import actions, config, db, events, seen
from .watch import gunzip_limited, title_ok

log = logging.getLogger("jobhunter.himalayas")

SEARCH = "https://himalayas.app/jobs/api/search"
UA = "JobHunter/1.0 (personal job search; contact akshay.verma0031@gmail.com)"
MAX_PAGES = 4
MAX_AGE_DAYS = 10
MAX_TARGETS = 10


def search_url(page=1, q="salesforce", country="India") -> str:
    return SEARCH + "?" + urllib.parse.urlencode({"q": q, "country": country, "sort": "recent", "page": page})


def fetch_json(url, timeout=25.0):
    """(status, parsed_json_or_None, raw_text)."""
    if os.environ.get("JOBHUNTER_OFFLINE") == "1":     # tests: never touch the network
        return 0, None, "offline mode"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(8_000_000)
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gunzip_limited(raw, 16_000_000)
            text = raw.decode("utf-8", "replace")
            try:
                return r.status, json.loads(text), text
            except ValueError:
                return r.status, None, text
    except urllib.error.HTTPError as e:
        return e.code, None, ""
    except Exception as e:
        return 0, None, "%s: %s" % (type(e).__name__, e)


def jobs_of(payload) -> list:
    """The job list, whatever the envelope ({jobs:[]}, {data:[]}, {results:[]} or a bare list)."""
    if isinstance(payload, list):
        return [j for j in payload if isinstance(j, dict)]
    if isinstance(payload, dict):
        for k in ("jobs", "data", "results", "items"):
            v = payload.get(k)
            if isinstance(v, list):
                return [j for j in v if isinstance(j, dict)]
            if isinstance(v, dict) and isinstance(v.get("jobs"), list):
                return [j for j in v["jobs"] if isinstance(j, dict)]
    return []


def _ts(v):
    """pubDate / expiryDate: epoch seconds or ms, or ISO text -> aware datetime (None if unknown)."""
    if v in (None, ""):
        return None
    try:
        x = float(v)
        if x > 1e12:
            x /= 1000.0
        return datetime.fromtimestamp(x, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _text(h):
    t = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", h or "", flags=re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def _salary(j):
    lo, hi, cur = j.get("minSalary"), j.get("maxSalary"), j.get("currency") or ""
    if lo is None and hi is None:
        return ""
    per = j.get("salaryPeriod") or "annual"
    rng = "%s-%s" % (lo, hi) if lo is not None and hi is not None else str(lo if lo is not None else hi)
    return "%s %s (%s)" % (cur, rng, per)


def normalize(j) -> dict:
    restr = j.get("locationRestrictions") or []
    if isinstance(restr, str):
        restr = [restr]
    names = []
    for r in restr:
        names.append(str(r.get("name") or r.get("country") or r) if isinstance(r, dict) else str(r))
    guid = str(j.get("guid") or "").strip()
    url = guid if guid.startswith("http") else ""
    if not url and j.get("companySlug") and j.get("slug"):
        url = "https://himalayas.app/companies/%s/jobs/%s" % (j["companySlug"], j["slug"])
    return {
        "ext_id": guid or str(j.get("id") or ""),
        "title": str(j.get("title") or "").strip()[:200],
        "company": str(j.get("companyName") or "").strip()[:150],
        "url": url or str(j.get("applicationLink") or ""),
        "apply_url": str(j.get("applicationLink") or "").strip() or None,
        "restrictions": names,
        "employment": j.get("employmentType") or "",
        "salary": _salary(j),
        "pub": _ts(j.get("pubDate")),
        "expiry": _ts(j.get("expiryDate")),
        "text": _text(j.get("description") or j.get("excerpt") or ""),
    }


def india_ok(restrictions) -> bool:
    if not restrictions:
        return True                              # no restriction = worldwide
    for r in restrictions:
        x = str(r).strip().lower()
        if x in ("india", "in", "ind", "worldwide", "anywhere", "global") or "india" in x:
            return True
    return False


def collect(conn, run_id, emit_lane="himalayas", fetcher=None, pages=MAX_PAGES, now=None) -> dict:
    """Fetch + filter + save. Returns {targets:[...], counts:{...}, error}."""
    fetcher = fetcher or fetch_json
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(days=MAX_AGE_DAYS)
    counts = {"fetched": 0, "kept": 0, "seen": 0, "not_salesforce": 0, "not_india": 0, "old": 0, "saved": 0}
    seen_guid, cards, err = set(), [], None
    for page in range(1, pages + 1):
        if page > 1:
            time.sleep(1.0)
        st, payload, raw = fetcher(search_url(page))
        if st != 200 or payload is None:
            err = ("Himalayas returned HTTP %s" % st) if st else ("no connection to Himalayas (%s)" % raw[:120])
            if st == 200 and payload is None:
                err = "Himalayas sent something that is not JSON"
            if raw:
                try:
                    (config.data_dir() / "logs").mkdir(exist_ok=True)
                    (config.data_dir() / "logs" / "himalayas_last.txt").write_text(raw[:300000], encoding="utf-8")
                except OSError:
                    pass
            break
        items = jobs_of(payload)
        if not items:
            if page == 1 and isinstance(payload, dict) and not any(k in payload for k in ("jobs", "data", "results")):
                err = "Himalayas answer format not recognised (saved logs/himalayas_last.txt)"
                try:
                    (config.data_dir() / "logs").mkdir(exist_ok=True)
                    (config.data_dir() / "logs" / "himalayas_last.txt").write_text(raw[:300000], encoding="utf-8")
                except OSError:
                    pass
            break
        stop = False
        for j in items:
            n = normalize(j)
            if not n["title"] or not n["company"] or n["ext_id"] in seen_guid:
                continue
            seen_guid.add(n["ext_id"])
            counts["fetched"] += 1
            if n["pub"] and n["pub"] < oldest:
                counts["old"] += 1
                stop = True
                continue
            if n["expiry"] and n["expiry"] < now:
                counts["old"] += 1
                continue
            if not title_ok(n["title"]):
                counts["not_salesforce"] += 1
                continue
            if not india_ok(n["restrictions"]):
                counts["not_india"] += 1
                continue
            cards.append(n)
        if stop:
            break
    if not cards:
        return {"targets": [], "counts": counts, "error": err}
    flt = seen.filter_jobs(conn, [{"company": c["company"], "role": c["title"], "url": c["url"]} for c in cards])
    new_keys = {(x["company"], x["role"]) for x in flt["new"]}
    retry = {(x["company"], x["role"]): x["job_id"] for x in flt["retry"]}
    counts["seen"] = len(flt["seen"])
    targets = []
    for c in cards:
        key = (c["company"], c["title"])
        if key in retry:
            targets.append({"job_id": retry[key], "company": c["company"], "role": c["title"], "url": c["url"],
                            "apply_url": c["apply_url"], "retry": True})
            continue
        if key not in new_keys:
            continue
        loc = "Remote (%s)" % (", ".join(c["restrictions"][:6]) if c["restrictions"] else "worldwide")
        notes = "via Himalayas" + (" · salary %s" % c["salary"] if c["salary"] else "") + \
                (" · %s" % c["employment"] if c["employment"] else "")
        res = actions.save_job(conn, run_id, emit_lane, "himalayas", c["company"], c["title"], location=loc,
                               remote=True, url=c["url"], posted=c["pub"].date().isoformat() if c["pub"] else "",
                               jd_text=c["text"][:6000], notes=notes, apply_url=c["apply_url"])
        if res.get("job_id") and not res.get("duplicate") and res["job_id"] not in {t["job_id"] for t in targets}:
            counts["saved"] += 1
            targets.append({"job_id": res["job_id"], "company": c["company"], "role": c["title"], "url": c["url"],
                            "apply_url": c["apply_url"]})
    counts["kept"] = len(targets)
    return {"targets": targets[:MAX_TARGETS], "all_targets": len(targets), "counts": counts, "error": err}
