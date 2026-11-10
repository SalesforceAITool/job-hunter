"""Remote job boards lane: Himalayas + Jobicy + company career pages (Greenhouse / Lever / Ashby).

Everything here is plain HTTP against free public JSON APIs — no login, no browser, no AI. Only the jobs that
survive the filters AND are new to Job Hunter go on to the AI lane (contacts + email); career-page jobs also
get their form prefilled by the Portal lane (apply_url is a Greenhouse/Lever/Ashby link).

Filters (Akshay: remote only, India or worldwide):
  * Salesforce technical title (watch.title_ok)
  * remote (Himalayas/Jobicy are remote-only boards; career pages must say remote)
  * open to India: no location lock, or the lock includes India / worldwide / anywhere / APAC / Asia
  * posted in the last MAX_AGE_DAYS
Endpoint shapes for Greenhouse/Lever/Ashby/Jobicy were verified live on 2026-08-21/23 (project doc
sfjobs/evidence-live-ats-endpoints.md, sfjobs/repo/phase0-sources.py).
"""
import html as htmllib
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import actions, config, db, himalayas, screen, seen
from .watch import title_ok

log = logging.getLogger("jobhunter.boards")

MAX_AGE_DAYS = 14
MAX_TARGETS = 12
# Verified 2026-08-23 (sfjobs/FINDINGS): neuraflash has the most Salesforce roles (several India), litmos had one.
# Others were live-verified boards with occasional Salesforce roles; jobgether (Lever) is a remote-jobs aggregator.
SEED_BOARDS = [("greenhouse", "neuraflash"), ("greenhouse", "litmos"), ("greenhouse", "twilio"), ("greenhouse", "databricks"),
               ("greenhouse", "cloudflare"), ("greenhouse", "gitlab"), ("lever", "matillion"), ("lever", "jobgether"),
               ("ashby", "ramp"),
               # found 2026-10-22 from indexed Salesforce postings (not API-verified from the cloud: a board that
               # answers 404/410 three times is marked dead automatically)
               ("greenhouse", "spauldingridge"), ("greenhouse", "penrodsoftware"), ("greenhouse", "conga"),
               ("lever", "smart-working-solutions"), ("lever", "brillio-2"),
               ("workable", "two95-international-inc-3"), ("workable", "devsinc-17")]

_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b|anywhere|distributed|home[- ]based|virtual", re.I)
_OPEN = re.compile(r"\bindia\b|anywhere|worldwide|world-wide|global|\bapac\b|asia|international|any location|"
                   r"all locations|emea\s*&\s*apac", re.I)
_LOCKED_ELSEWHERE = re.compile(r"\b(united states|usa|u\.s\.|us only|us-based|canada|united kingdom|uk|europe|eu|"
                               r"latam|latin america|germany|france|spain|poland|brazil|mexico|australia|"
                               r"philippines|singapore|japan|netherlands|ireland|portugal|romania)\b", re.I)
_ATS_URL = re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)|"
                      r"https?://boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)|"
                      r"https?://jobs\.lever\.co/([A-Za-z0-9_.-]+)|"
                      r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)|"
                      r"https?://apply\.workable\.com/(?:api/v\d/(?:widget/)?accounts/)?([A-Za-z0-9_-]+)|"
                      r"https?://([A-Za-z0-9-]+)\.recruitee\.com", re.I)
_SKIP_TOKENS = {"embed", "jobs", "job_board", "v1", "api", "j", "www", "app", "careers"}
PLATFORMS = ("greenhouse", "lever", "ashby", "workable", "recruitee")


_BLOCK_OPEN = re.compile(r"<(script|style)\b", re.I)


def _drop_blocks(h):
    """Remove <script>/<style> blocks in one forward pass (the old lazy-regex form re-scanned to the end of the
    text for every unclosed <script: quadratic on hostile job descriptions)."""
    low, out, i = h.lower(), [], 0
    while True:
        m = _BLOCK_OPEN.search(low, i)
        if not m:
            out.append(h[i:])
            break
        end = low.find("</" + m.group(1).lower(), m.end())
        if end < 0:                       # unclosed: keep the rest as text (tags are stripped below)
            out.append(h[i:])
            break
        out.append(h[i:m.start()] + " ")
        gt = low.find(">", end)
        i = gt + 1 if gt >= 0 else len(h)
    return "".join(out)


def _strip(h):
    h = h if isinstance(h, str) else ("" if h is None else str(h))
    t = _drop_blocks(h[:1_000_000])
    t = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", t)
    t = htmllib.unescape(re.sub(r"<[^<>]+>", " ", t))       # [^<>]: linear even on a flood of '<'
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", t)).strip()


def _ts(v):
    return himalayas._ts(v)


# --------------------------------------------------------------------------- board registry (career pages)

def board_of(url):
    """(platform, token) of a Greenhouse/Lever/Ashby URL, else None."""
    m = _ATS_URL.search(url or "")
    if not m:
        return None
    gh, gh_api, lv, ab, wk, rc = m.groups()
    if gh or gh_api:
        tok, plat = (gh or gh_api), "greenhouse"
    elif lv:
        tok, plat = lv, "lever"
    elif ab:
        tok, plat = urllib.parse.unquote(ab), "ashby"
    elif wk:
        tok, plat = wk, "workable"
    else:
        tok, plat = rc, "recruitee"
    tok = tok.strip().lower()
    if not tok or tok in _SKIP_TOKENS:
        return None
    return plat, tok


def ensure_seed(conn):
    for plat, tok in SEED_BOARDS:
        conn.execute("INSERT OR IGNORE INTO career_boards (platform, token, added_at, origin, status) VALUES (?,?,?,?,'active')",
                     (plat, tok, db.now_iso(), "seed"))


def discover(conn, url, origin="apply link") -> bool:
    """Remember a company's career board from any apply link we see (the flywheel)."""
    b = board_of(url)
    if not b:
        return False
    cur = conn.execute("INSERT OR IGNORE INTO career_boards (platform, token, added_at, origin, status) VALUES (?,?,?,?,'active')",
                       (b[0], b[1], db.now_iso(), origin))
    return bool(cur.rowcount)


def discover_from_db(conn):
    """Every Greenhouse/Lever/Ashby apply link any lane ever saved adds that company's board (the flywheel)."""
    n = 0
    for (u,) in conn.execute("SELECT DISTINCT apply_url FROM jobs WHERE apply_url LIKE '%greenhouse.io%' OR"
                             " apply_url LIKE '%lever.co%' OR apply_url LIKE '%ashbyhq.com%' OR"
                             " apply_url LIKE '%apply.workable.com%' OR apply_url LIKE '%recruitee.com%'"):
        n += discover(conn, u, origin="saved job")
    return n


def active_boards(conn, limit=60):
    ensure_seed(conn)
    discover_from_db(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM career_boards WHERE status='active' ORDER BY COALESCE(sf_jobs,0) DESC, id LIMIT ?", (limit,))]


def board_result(conn, plat, tok, ok, jobs=0, sf=0):
    now = db.now_iso()
    if ok:
        conn.execute("UPDATE career_boards SET last_ok=?, misses=0, jobs=?, sf_jobs=? WHERE platform=? AND token=?",
                     (now, jobs, sf, plat, tok))
    else:
        conn.execute("UPDATE career_boards SET misses=COALESCE(misses,0)+1, status=CASE WHEN COALESCE(misses,0)+1>=3"
                     " THEN 'dead' ELSE status END WHERE platform=? AND token=?", (plat, tok))


# --------------------------------------------------------------------------- adapters -> cards
# card = {source, ext_id, title, company, url, apply_url, location, remote(bool|None), open(bool|None), text, pub, salary}

def gh_url(tok):
    return "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % urllib.parse.quote(tok)


def lv_url(tok):
    return "https://api.lever.co/v0/postings/%s?mode=json" % urllib.parse.quote(tok)


def ab_url(tok):
    return "https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % urllib.parse.quote(tok)


def wk_url(tok):
    """Workable's public careers-widget feed (GET, no key): {name, jobs:[...]}; details=true adds the description."""
    return "https://apply.workable.com/api/v1/widget/accounts/%s?details=true" % urllib.parse.quote(tok)


def rc_url(tok):
    """Recruitee's public offers feed (GET, no key): {offers:[...]}."""
    return "https://%s.recruitee.com/api/offers/" % urllib.parse.quote(tok)




def board_url(plat, tok):
    """API URL for a career board. Tokens only ever come from the board regexes, but they end up in a URL (for
    Recruitee in the HOST part): refuse anything that could change the host or path."""
    tok = str(tok or "")
    if not tok or len(tok) > 100 or ".." in tok or re.search(r"[/\\?#@\x00-\x1f]", tok) or \
            (plat in ("recruitee", "workable") and not re.fullmatch(r"[A-Za-z0-9_-]+", tok)):
        raise ValueError("bad board token %r" % tok[:60])
    return {"greenhouse": gh_url, "lever": lv_url, "ashby": ab_url, "workable": wk_url, "recruitee": rc_url}[plat](tok)


def jobicy_url(count=50):
    return "https://jobicy.com/api/v2/remote-jobs?count=%d&tag=salesforce" % count


def _geo_verdict(location, text=""):
    """(remote, open_to_india) from a location string (+ JD text as a weak hint)."""
    loc = location or ""
    remote = bool(_REMOTE.search(loc)) or None
    if _OPEN.search(loc):
        return remote, True
    if _LOCKED_ELSEWHERE.search(loc):
        return remote, False
    if remote and _OPEN.search(text or ""):
        return remote, True
    return remote, None          # unknown: the AI checks the JD


def gh_cards(doc, tok):
    out = []
    for j in (doc or {}).get("jobs", []) if isinstance(doc, dict) else []:
        loc = ((j.get("location") or {}).get("name") or "") if isinstance(j.get("location"), dict) else ""
        offices = " ".join(str(o.get("name") or "") for o in (j.get("offices") or []) if isinstance(o, dict))
        text = _strip(htmllib.unescape(j.get("content") or ""))
        remote, open_ = _geo_verdict(loc + " " + offices, text[:3000])
        out.append({"source": "careers", "ext_id": "gh:%s:%s" % (tok, j.get("id")), "title": j.get("title") or "",
                    "company": j.get("company_name") or tok.title(), "url": j.get("absolute_url") or "",
                    "apply_url": j.get("absolute_url") or "", "location": loc or offices, "remote": remote,
                    "open": open_, "text": text, "pub": _ts(j.get("first_published") or j.get("updated_at")), "salary": "",
                    "pub_exact": bool(j.get("first_published"))})
    return out


def lv_cards(doc, tok):
    out = []
    for j in doc if isinstance(doc, list) else []:
        cats = j.get("categories") or {}
        loc = " ".join(str(x) for x in [cats.get("location") or "", " ".join(cats.get("allLocations") or []) if isinstance(cats.get("allLocations"), list) else "", j.get("country") or ""] if x)
        text = "\n\n".join(x for x in (j.get("descriptionPlain") or _strip(j.get("description")),
                                       "\n".join("%s\n%s" % (l.get("text") or "", _strip(l.get("content"))) for l in (j.get("lists") or []) if isinstance(l, dict)),
                                       j.get("additionalPlain") or _strip(j.get("additional"))) if x)
        remote, open_ = _geo_verdict(loc, text[:3000])
        if str(j.get("workplaceType") or "").lower() == "remote":
            remote = True
        elif str(j.get("workplaceType") or "").lower() in ("onsite", "on-site", "hybrid"):
            remote = False
        if (j.get("country") or "").upper() == "IN":
            open_ = True
        out.append({"source": "careers", "ext_id": "lv:%s:%s" % (tok, j.get("id")), "title": j.get("text") or "",
                    "company": tok.replace("-", " ").title(), "url": j.get("hostedUrl") or j.get("applyUrl") or "",
                    "apply_url": j.get("applyUrl") or j.get("hostedUrl") or "", "location": loc, "remote": remote,
                    "open": open_, "text": text, "pub": _ts(j.get("createdAt")), "salary": ""})
    return out


def ab_cards(doc, tok):
    out = []
    for j in (doc or {}).get("jobs", []) if isinstance(doc, dict) else []:
        if j.get("isListed") is False:
            continue
        sec = " ".join(str(s.get("location") or "") for s in (j.get("secondaryLocations") or []) if isinstance(s, dict))
        loc = (j.get("location") or "") + (" " + sec if sec else "")
        text = j.get("descriptionPlain") or _strip(j.get("descriptionHtml"))
        remote, open_ = _geo_verdict(loc, text[:3000])
        if j.get("isRemote") is True or str(j.get("workplaceType") or "").lower() == "remote":
            remote = True
        elif str(j.get("workplaceType") or "").lower() in ("onsite", "hybrid"):
            remote = False
        comp = (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
        out.append({"source": "careers", "ext_id": "ab:%s:%s" % (tok, j.get("id")), "title": j.get("title") or "",
                    "company": tok.replace("-", " ").title(), "url": j.get("jobUrl") or j.get("applyUrl") or "",
                    "apply_url": j.get("applyUrl") or j.get("jobUrl") or "", "location": loc, "remote": remote,
                    "open": open_, "text": text, "pub": _ts(j.get("publishedAt")), "salary": comp})
    return out


def wk_cards(doc, tok):
    out = []
    company = (doc or {}).get("name") if isinstance(doc, dict) else None
    for j in (doc or {}).get("jobs", []) if isinstance(doc, dict) else []:
        if not isinstance(j, dict):
            continue
        locs = [x for x in (j.get("locations") or []) if isinstance(x, dict) and not x.get("hidden")]
        loc = "; ".join(", ".join(str(x.get(k) or "") for k in ("city", "country") if x.get(k)) for x in locs) or \
            ", ".join(str(j.get(k)) for k in ("city", "state", "country") if j.get(k))
        text = _strip(j.get("description") or "")
        remote, open_ = _geo_verdict(loc, text[:3000])
        if j.get("telecommuting") is True or str(j.get("workplace") or "").lower() == "remote":
            remote = True
        if any(str(x.get("countryCode") or "").upper() == "IN" for x in locs) or str(j.get("country") or "").lower() == "india":
            open_ = True
        code = j.get("shortcode") or j.get("code") or ""
        url = j.get("url") or j.get("shortlink") or ("https://apply.workable.com/%s/j/%s/" % (tok, code) if code else "")
        out.append({"source": "careers", "ext_id": "wk:%s:%s" % (tok, code or j.get("title")), "title": j.get("title") or "",
                    "company": company or tok.replace("-", " ").title(), "url": url,
                    "apply_url": j.get("application_url") or url, "location": loc + (" (Remote)" if remote and "remote" not in loc.lower() else ""),
                    "remote": remote, "open": open_, "text": text,
                    "pub": _ts(j.get("published_on") or j.get("created_at")), "salary": ""})
    return out


def _rc_ts(v):
    return _ts(re.sub(r"\s*UTC$", "+00:00", str(v)).replace(" ", "T", 1) if v else v)


def rc_cards(doc, tok):
    out = []
    for j in (doc or {}).get("offers", []) if isinstance(doc, dict) else []:
        if not isinstance(j, dict) or (j.get("status") and j.get("status") != "published"):
            continue
        loc = j.get("location") or ", ".join(str(j.get(k)) for k in ("city", "country") if j.get(k))
        text = "\n\n".join(x for x in (_strip(j.get("description") or ""), _strip(j.get("requirements") or "")) if x)
        remote, open_ = _geo_verdict(loc, text[:3000])
        if j.get("remote") is True:
            remote = True
        if str(j.get("country_code") or "").upper() == "IN":
            open_ = True
        url = j.get("careers_url") or "https://%s.recruitee.com/o/%s" % (tok, j.get("slug") or "")
        out.append({"source": "careers", "ext_id": "rc:%s:%s" % (tok, j.get("id")), "title": j.get("title") or "",
                    "company": j.get("company_name") or tok.replace("-", " ").title(), "url": url,
                    "apply_url": j.get("careers_apply_url") or url, "location": loc + (" (Remote)" if remote and "remote" not in (loc or "").lower() else ""),
                    "remote": remote, "open": open_, "text": text, "pub": _rc_ts(j.get("published_at") or j.get("created_at")),
                    "salary": ""})
    return out


CARD_PARSERS = {"greenhouse": gh_cards, "lever": lv_cards, "ashby": ab_cards, "workable": wk_cards, "recruitee": rc_cards}


def jobicy_cards(doc):
    out = []
    for j in (doc or {}).get("jobs", []) if isinstance(doc, dict) else []:
        if not isinstance(j, dict):
            continue
        geo = str(j.get("jobGeo") or "").strip()
        open_ = True if (not geo or _OPEN.search(geo)) else (False if _LOCKED_ELSEWHERE.search(geo) else None)
        sal = ""
        if j.get("annualSalaryMin") or j.get("annualSalaryMax"):
            sal = "%s %s-%s (annual)" % (j.get("salaryCurrency") or "USD", j.get("annualSalaryMin") or "?", j.get("annualSalaryMax") or "?")
        out.append({"source": "jobicy", "ext_id": "jb:%s" % j.get("id"), "title": j.get("jobTitle") or "",
                    "company": j.get("companyName") or "", "url": j.get("url") or "", "apply_url": None,
                    "location": "Remote — %s" % geo if geo else "Remote", "remote": True, "open": open_,
                    "text": _strip(j.get("jobDescription") or j.get("jobExcerpt")), "pub": _ts(j.get("pubDate")),
                    "salary": sal})
    return out


# --------------------------------------------------------------------------- Hacker News (Y Combinator)
# Official API: github.com/HackerNews/API (Firebase, no key). The "whoishiring" account posts
# "Ask HN: Who is hiring? (Month YYYY)" and "Ask HN: Freelancer? Seeking freelancer? (Month YYYY)" on the
# first weekday of each month. Algolia's HN search (hn.algolia.com, also free) finds the Salesforce comments
# in one request; the Firebase API is the fallback.
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/%s.json"
HN_USER = "https://hacker-news.firebaseio.com/v0/user/whoishiring.json"
HN_ALGOLIA = "https://hn.algolia.com/api/v1/search?tags=comment,story_%s&query=salesforce&hitsPerPage=200"
HN_MAX_AGE_DAYS = 40
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,120}\.[A-Za-z]{2,24}")
_TLD = r"(com|io|co|ai|dev|org|net|app|tech)\b"
_EMAIL_OBF = re.compile(r"\b([A-Za-z0-9._%+-]{1,64})\s*(?:[\[\(\{]\s*at\s*[\]\)\}]\s*([A-Za-z0-9-]{1,60})\s*"
                        r"(?:[\[\(\{]\s*dot\s*[\]\)\}]|\.|\s+dot\s+)\s*" + _TLD + r"|\s+at\s+([A-Za-z0-9-]{1,60})\s*"
                        r"(?:[\[\(\{]\s*dot\s*[\]\)\}]|\s+dot\s+)\s*" + _TLD + ")", re.I)
_NOT_LOCAL = {"out", "us", "me", "him", "her", "them", "look", "great", "work", "based", "located", "apply"}
_SF_ROLE = re.compile(r"((?:senior|sr\.?|lead|staff|principal)\s+)?salesforce\s+(?:[a-z]+\s+)?"
                      r"(developer|engineer|architect|administrator|admin|consultant|lead)s?\b", re.I)


def hn_threads(fetcher, limit=6):
    """[(story_id, kind)] of the latest who-is-hiring / freelancer threads (kind: 'hiring' | 'freelance')."""
    st, user, _ = fetcher(HN_USER)
    out = []
    if st != 200 or not isinstance(user, dict):
        return out
    for sid in (user.get("submitted") or [])[:limit]:
        st, it, _ = fetcher(HN_ITEM % sid)
        if st != 200 or not isinstance(it, dict):
            continue
        t = (it.get("title") or "").lower()
        if "who is hiring" in t:
            out.append((sid, "hiring", it))
        elif "freelancer" in t:
            out.append((sid, "freelance", it))
        if {k for _, k, _ in out} == {"hiring", "freelance"}:
            break
    return out


def hn_comments(fetcher, story, max_kids=900):
    """[(id, html, epoch)] of Salesforce comments in a thread: Algolia first, Firebase fallback."""
    sid = story.get("id")
    st, doc, _ = fetcher(HN_ALGOLIA % sid)
    if st == 200 and isinstance(doc, dict) and isinstance(doc.get("hits"), list):
        return [(h.get("objectID"), h["comment_text"], h.get("created_at_i"))
                for h in doc["hits"] if isinstance(h, dict) and str(h.get("parent_id")) == str(sid)
                and isinstance(h.get("comment_text"), str) and h["comment_text"]]
    out = []
    for kid in (story.get("kids") or [])[:max_kids]:
        st, it, _ = fetcher(HN_ITEM % kid)
        if st == 200 and isinstance(it, dict) and not it.get("deleted") and isinstance(it.get("text"), str) \
                and re.search(r"salesforce", it["text"], re.I):
            out.append((it.get("id"), it.get("text") or "", it.get("time")))
    return out


def hn_cards(fetcher):
    cards = []
    for sid, kind, story in hn_threads(fetcher):
        for cid, html, ts in hn_comments(fetcher, story):
            text = _strip(re.sub(r"(?i)<p\b[^>]*>", "\n", html or ""))
            first = text.split("\n", 1)[0]
            if kind == "freelance" and not re.match(r"\s*seeking\s+freelancer", text, re.I):
                continue                      # freelancers advertising themselves
            segs = [x.strip() for x in first.split("|") if x.strip()]
            company = re.sub(r"(?i)^seeking\s+freelancer[:\s-]*", "", segs[0] if segs else "")[:80].strip(" :-")
            role = next((x for x in segs[1:] if title_ok(x)), "")
            if not role:
                m = _SF_ROLE.search(text)
                role = m.group(0).strip().title() if m else ""
            if kind == "freelance" and not role and re.search(r"salesforce", text, re.I):
                role = "Salesforce Contractor"
            emails = sorted({e.strip(".").lower() for e in _EMAIL.findall(text)} |
                            {("%s@%s.%s" % (m[0], m[1] or m[3], m[2] or m[4])).lower() for m in _EMAIL_OBF.findall(text)
                             if m[0].lower() not in _NOT_LOCAL})
            ats = next((u for u in re.findall(r"https?://[^\s<>\"')]+", htmllib.unescape(html or "")) if board_of(u)), None)
            remote = bool(re.search(r"\bremote\b", text, re.I)) or None
            low = first.lower()
            open_ = True if re.search(r"remote\s*\(?\s*(?:worldwide|global|anywhere)|\bworldwide\b|\banywhere\b|\bglobal\b|\bindia\b", text, re.I) \
                else (False if re.search(r"remote\s*\(?\s*(?:us|usa|u\.s\.|eu|uk|canada|europe)(?:[\s/-]only)?\b|\bus[\s-]only\b|us[\s-]based|\bus citizens?\b", low) else None)
            cards.append({"source": "hn", "ext_id": "hn:%s" % cid, "title": role[:120], "company": company,
                          "url": "https://news.ycombinator.com/item?id=%s" % cid, "apply_url": ats,
                          "location": "Remote" if remote else first[:120], "remote": remote, "open": open_,
                          "text": text[:6000], "pub": _ts(ts), "salary": "", "emails": emails[:3],
                          "kind": kind})
    return cards


def himalayas_cards(conn, fetcher):
    """Reuse the Himalayas adapter (search q=salesforce, country=India)."""
    out, err = [], None
    for page in range(1, himalayas.MAX_PAGES + 1):
        if page > 1:
            time.sleep(1.0)
        st, payload, raw = fetcher(himalayas.search_url(page))
        if st != 200 or payload is None:
            err = "Himalayas: HTTP %s" % st if st else "Himalayas: no connection"
            _save_raw("himalayas_last.txt", raw)
            break
        items = himalayas.jobs_of(payload)
        if not items:
            if page == 1 and isinstance(payload, dict) and not any(k in payload for k in ("jobs", "data", "results")):
                err = "Himalayas answer format not recognised (saved logs/himalayas_last.txt)"
                _save_raw("himalayas_last.txt", raw)
            break
        for j in items:
            n = himalayas.normalize(j)
            out.append({"source": "himalayas", "ext_id": "hm:" + n["ext_id"], "title": n["title"], "company": n["company"],
                        "url": n["url"], "apply_url": n["apply_url"],
                        "location": "Remote (%s)" % (", ".join(n["restrictions"][:6]) if n["restrictions"] else "worldwide"),
                        "remote": True, "open": himalayas.india_ok(n["restrictions"]), "text": n["text"],
                        "pub": n["pub"], "expiry": n["expiry"], "salary": n["salary"]})
    return out, err


def _save_raw(name, raw):
    if not raw:
        return
    try:
        (config.data_dir() / "logs").mkdir(exist_ok=True)
        (config.data_dir() / "logs" / name).write_text(raw[:300000], encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- the pipeline

SOURCES = ("himalayas", "jobicy", "hn", "careers")
LABEL = {"himalayas": "Himalayas", "jobicy": "Jobicy", "hn": "Hacker News (Y Combinator) hiring thread",
         "careers": "company career page"}


def collect(conn, run_id, lane="himalayas", fetcher=None, now=None, sources=SOURCES, pause=1.0, stop=None) -> dict:
    fetcher = fetcher or himalayas.fetch_json
    now = now or datetime.now(timezone.utc)
    if os.environ.get("JOBHUNTER_OFFLINE") == "1" and fetcher is None:
        return {"targets": [], "counts": {"fetched": 0}, "errors": ["offline"]}
    oldest = now - timedelta(days=MAX_AGE_DAYS)
    counts = {"fetched": 0, "not_salesforce": 0, "not_remote": 0, "not_india": 0, "old": 0, "seen": 0, "saved": 0,
              "boards": 0, "by_source": {}}
    errors, cards = [], []

    if "himalayas" in sources:
        c, err = himalayas_cards(conn, fetcher)
        cards += c
        if err:
            errors.append(err)
    if "jobicy" in sources:
        st, payload, raw = fetcher(jobicy_url())
        if st == 200 and isinstance(payload, dict):
            cards += jobicy_cards(payload)
        else:
            errors.append("Jobicy: HTTP %s" % st if st else "Jobicy: no connection")
            _save_raw("jobicy_last.txt", raw)
    if "hn" in sources:
        try:
            cards += hn_cards(fetcher)
        except Exception as e:
            errors.append("Hacker News: %s" % e)
    if "careers" in sources:
        for b in active_boards(conn):
            if stop is not None and stop.is_set():
                break
            time.sleep(pause)
            plat, tok = b["platform"], b["token"]
            if plat not in CARD_PARSERS:
                continue
            try:
                url = board_url(plat, tok)
            except ValueError:
                continue
            st, payload, raw = fetcher(url)
            if st != 200 or payload is None:
                if st in (404, 410) or (st == 200 and payload is None):   # board gone; 0/429/5xx are transient
                    board_result(conn, plat, tok, False)
                continue
            cs = CARD_PARSERS[plat](payload, tok)
            board_result(conn, plat, tok, True, len(cs), sum(1 for x in cs if title_ok(x["title"])))
            counts["boards"] += 1
            cards += cs

    keep, seen_ids = [], set()
    for c in cards:
        c["title"], c["company"] = (c.get("title") or "").strip(), (c.get("company") or "").strip()
        if not c["title"] or not c["company"] or c["ext_id"] in seen_ids:
            continue
        seen_ids.add(c["ext_id"])
        counts["fetched"] += 1
        age_limit = now - timedelta(days=HN_MAX_AGE_DAYS) if c["source"] == "hn" else oldest
        if (c.get("pub") and c["pub"] < age_limit) or (c.get("expiry") and c["expiry"] < now):
            counts["old"] += 1
            continue
        if not title_ok(c["title"]):
            counts["not_salesforce"] += 1
            continue
        if c["remote"] is False or (c["source"] in ("careers", "hn") and c["remote"] is None):
            counts["not_remote"] += 1
            continue
        if c["open"] is False:
            counts["not_india"] += 1
            continue
        keep.append(c)
    for c in keep:      # apply links feed the career-board flywheel
        if c.get("apply_url"):
            discover(conn, c["apply_url"], origin=c["source"])

    if not keep:
        return {"targets": [], "counts": counts, "errors": errors}
    flt = seen.filter_jobs(conn, [{"company": c["company"], "role": c["title"], "url": c["url"]} for c in keep])
    new_keys = {(x["company"], x["role"]) for x in flt["new"]}
    retry = {(x["company"], x["role"]): x["job_id"] for x in flt["retry"]}
    counts["seen"] = len(flt["seen"])
    targets = []
    for c in keep:
        key = (c["company"], c["title"])
        if key in retry:
            targets.append({"job_id": retry[key], "company": c["company"], "role": c["title"], "url": c["url"],
                            "apply_url": c["apply_url"], "source": c["source"], "retry": True,
                            "emails": c.get("emails") or []})
            continue
        if key not in new_keys:
            continue
        new_keys.discard(key)
        ok, why = screen.screen(c["title"], c.get("text") or "", "" if c["source"] == "hn" else c["location"],
                                india_role=True if re.search(r"\bindia\b", "%s %s" % (c["location"], (c.get("text") or "")[:3000]), re.I) else None)
        if not ok and not why.startswith("not a fit"):
            counts["screened_out"] = counts.get("screened_out", 0) + 1
            actions.save_job(conn, run_id, lane, c["source"], c["company"], c["title"], location=c["location"][:200],
                             url=c["url"], decision="skipped", skip_reason=why, jd_text=(c["text"] or "")[:4000],
                             notes="screened by Job Hunter (no AI)")
            continue
        notes = "via %s" % LABEL[c["source"]] + (" · hiring email in post: %s" % ", ".join(c["emails"]) if c.get("emails") else "") + (" · salary %s" % c["salary"] if c.get("salary") else "") + \
                ("" if c["open"] else " · India eligibility unclear — check the JD")
        res = actions.save_job(conn, run_id, lane, c["source"], c["company"], c["title"], location=c["location"][:200],
                               remote=True, url=c["url"], posted=c["pub"].date().isoformat() if c.get("pub") else "",
                               jd_text=(c["text"] or "")[:6000], notes=notes, apply_url=c.get("apply_url"))
        if res.get("job_id") and not res.get("duplicate") and res["job_id"] not in {t["job_id"] for t in targets}:
            counts["saved"] += 1
            counts["by_source"][c["source"]] = counts["by_source"].get(c["source"], 0) + 1
            targets.append({"job_id": res["job_id"], "company": c["company"], "role": c["title"], "url": c["url"],
                            "apply_url": c.get("apply_url"), "source": c["source"], "emails": c.get("emails") or []})
    return {"targets": targets[:MAX_TARGETS], "all_targets": len(targets), "counts": counts, "errors": errors}
