"""Job board: every remote Salesforce job posted in the last 48 hours, worldwide — fetched by plain Python.

No AI and no login here. Two refresh speeds:
  * fast (every `jobboard.fast_minutes`, default 30): company career pages (Greenhouse, Lever, Ashby, Workable,
    Recruitee — the career_boards flywheel), Himalayas, Jobicy, Hacker News hiring threads, Arbeitnow,
    We Work Remotely (RSS). All free public JSON/RSS feeds.
  * LinkedIn (every `jobboard.li_every_hours`, default 2): LinkedIn's PUBLIC job list (the guest endpoint, no
    account) for the picked regions with the remote filter, posted in the last 48 h; then the public JD page of the
    newest unchecked jobs (apply method + JD for the skip rules), at most MAX_LI_JD per refresh.

Every Salesforce-titled job posted in the window is stored in `listings` (one row per job: the same job seen on
LinkedIn and on Greenhouse is merged by company+title) with an eligibility verdict from the same Python rules the
lanes use (jh/screen.py):
  open    — India / worldwide / anywhere / APAC named, or an India-friendly board says so
  check   — remote, but where they hire is not stated (or the JD was not read yet)
  locked  — remote only for one country/region that is not India
  blocked — needs US work authorization, onsite/hybrid, a language he doesn't speak, not remote
Nothing is deleted on a rule: the page can show filtered jobs with the reason.

Applying: Akshay picks jobs ("Fill applications") or auto-fill picks the best few after each refresh (daily cap).
`claim()` turns each queued listing into a normal `jobs` row and the runner hands it to the right AI lane:
LinkedIn Easy Apply -> the existing easy_apply lane; everything else -> the board_apply lane. Forms are only ever
prefilled; submitting still needs his "Submit selected" click.
"""
import email.utils
import html as htmllib
import json
import logging
import re
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import actions, boards, config, db, dedupe, events, himalayas, ligate, screen, seen
from .watch import GUEST_URL, parse_guest_html, title_ok

log = logging.getLogger("jobhunter.jobboard")

WINDOW_H = 48
KEEP_DAYS = 7                     # listings older than this (posted) are purged unless he acted on them
MAX_BOARDS = 150
MAX_LI_JD = 40
UTC = timezone.utc

# LinkedIn guest search: geoId per region (LinkedIn's own ids)
LI_REGIONS = {
    "world": ("Worldwide", "92000000", 3), "in": ("India", "102713980", 2), "us": ("United States", "103644278", 3),
    "uk": ("United Kingdom", "101165590", 1), "ca": ("Canada", "101174742", 1), "au": ("Australia", "101452733", 1),
    "eu": ("European Union", "91000000", 1), "ae": ("UAE", "104305776", 1), "sg": ("Singapore", "102454443", 1),
}
LI_JD_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/%s"
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api?page=%d"
WWR_URLS = ("https://weworkremotely.com/remote-jobs/search.rss?term=salesforce",
            "https://weworkremotely.com/remote-jobs.rss")

SOURCE_LABEL = {"linkedin": "LinkedIn", "careers": "Company site", "himalayas": "Himalayas", "jobicy": "Jobicy",
                "hn": "Hacker News", "arbeitnow": "Arbeitnow", "wwr": "We Work Remotely"}
FAST_SOURCES = ("careers", "himalayas", "jobicy", "hn", "arbeitnow", "wwr")

# application pages by platform. "portal" = a form that needs no account; "login_portal" = the company's
# candidate account (Workday & co): the AI continues only if his Chrome is already signed in.
NO_ACCOUNT = {"greenhouse": ("greenhouse.io",), "lever": ("lever.co",), "ashby": ("ashbyhq.com",),
              "workable": ("workable.com",), "recruitee": ("recruitee.com",), "smartrecruiters": ("smartrecruiters.com",),
              "bamboohr": ("bamboohr.com",), "breezy": ("breezy.hr",), "jazzhr": ("applytojob.com", "jazzhr.com"),
              "teamtailor": ("teamtailor.com",), "personio": ("personio.de", "personio.com"), "pinpoint": ("pinpointhq.com",),
              "rippling": ("rippling-ats.com", "ats.rippling.com"), "dover": ("dover.com", "dover.io"),
              "jobvite": ("jobvite.com",), "zoho": ("zohorecruit.com", "zohorecruit.in", "zohorecruit.eu"),
              "freshteam": ("freshteam.com",), "keka": ("keka.com",), "jobscore": ("jobscore.com",),
              "comeet": ("comeet.com", "comeet.co"), "homerun": ("homerun.co",), "wellfound": ("wellfound.com",)}
LOGIN = {"workday": ("myworkdayjobs.com", "myworkdaysite.com", "workday.com"), "icims": ("icims.com",),
         "taleo": ("taleo.net",), "successfactors": ("successfactors.com", "successfactors.eu", "sapsf.com"),
         "oracle": ("oraclecloud.com",), "darwinbox": ("darwinbox.in", "darwinbox.com"), "ultipro": ("ultipro.com",),
         "adp": ("adp.com",), "avature": ("avature.net",), "phenom": ("phenompeople.com",)}
AGGREGATORS = ("himalayas.app", "jobicy.com", "arbeitnow.com", "weworkremotely.com", "news.ycombinator.com",
               "indeed.com", "glassdoor.com", "naukri.com", "ziprecruiter.com", "remotive.com", "remoteok.com",
               "jobgether.com", "dice.com", "monster.com", "simplyhired.com", "talent.com", "builtin.com")
AUTO_KINDS = ("portal", "easy_apply", "email")
FILL_KINDS = ("portal", "login_portal", "company_site", "easy_apply", "email", "board", "unknown")

_lock = threading.Lock()
_state = {"running": False, "started": None, "last_fast": 0.0, "last_li": 0.0, "last": None, "loaded": False}
# aggregator boards list other companies' jobs under their own name: never merge their jobs by company+title
AGGREGATOR_COMPANIES = {"jobgether", "remote", "himalayas", "jobicy", "turing", "toptal", "crossover", "braintrust"}
JD_KEEP_FILTERED = 600            # filtered rows keep only the reason + the start of the JD


# --------------------------------------------------------------------------- small helpers

def _now():
    return datetime.now(UTC)


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _host(url):
    try:
        return (urllib.parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _on(host, suffixes):
    return any(host == s or host.endswith("." + s) for s in suffixes)


_LOCAL_SUFFIXES = ("localhost", "local", "internal", "lan", "home", "intranet", "corp", "localdomain")


def _public_host(h):
    """A real internet job site: a dotted DNS name, never an IP literal, localhost or a local-network name.
    Keeps board data from pointing the AI's Chrome (signed in, with Job Hunter's own dashboard on 127.0.0.1)
    at local pages."""
    h = (h or "").strip(".").lower()
    if not h or "." not in h or ":" in h or re.fullmatch(r"[\d.]+", h) or h.startswith("["):
        return False
    return not _on(h, _LOCAL_SUFFIXES)


def web_url(u):
    """u if it is an http(s) URL on a public host, else None (javascript:, file:, data:, 127.0.0.1, ...)."""
    if not isinstance(u, str) or len(u) > 2000 or not re.match(r"(?i)https?://", u.strip()):
        return None
    u = u.strip()
    return u if _public_host(_host(u)) else None


def platform_of(url):
    """(platform, kind) of an application URL. kind: portal | login_portal | easy_apply | board | company_site."""
    if not web_url(url):
        return None, None
    h = _host(url)
    if not h:
        return None, None
    if _on(h, ("linkedin.com",)):
        return "linkedin", ("easy_apply" if "/jobs/view/" in (url or "") else "board")
    for name, sfx in NO_ACCOUNT.items():
        if _on(h, sfx):
            return name, "portal"
    for name, sfx in LOGIN.items():
        if _on(h, sfx):
            if name == "oracle" and ".fa." not in "." + h:
                continue          # *.oraclecloud.com also hosts anyone's files (object storage); recruiting is *.fa.*
            return name, "login_portal"
    if _on(h, AGGREGATORS):
        return h.split(".")[-2], "board"
    return h[4:] if h.startswith("www.") else h, "company_site"


def _ago(text, listed, now):
    """LinkedIn card time: "5 hours ago" -> exact; else the listed date (YYYY-MM-DD, IST) -> that day 09:00 IST."""
    m = re.search(r"(\d+)\s+(minute|hour|day|week)s?\s+ago", text or "", re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now - {"minute": timedelta(minutes=n), "hour": timedelta(hours=n), "day": timedelta(days=n),
                      "week": timedelta(weeks=n)}[unit]
    if re.search(r"just now|moments? ago|seconds? ago", text or "", re.I):
        return now
    try:
        d = datetime.strptime((listed or "")[:10], "%Y-%m-%d")
        return d.replace(hour=3, minute=30, tzinfo=UTC)          # 09:00 IST
    except ValueError:
        return None


REGION_RX = [("India", r"\bindia\b|bengaluru|bangalore|hyderabad|pune|chennai|noida|gurugram|gurgaon|mumbai|delhi|kolkata"),
             ("Worldwide", r"worldwide|world-wide|anywhere|\bglobal\b|international"),
             ("APAC", r"\bapac\b|\basia\b|singapore|philippines|malaysia|japan|australia|new zealand"),
             ("US", r"united states|\busa?\b|\bu\.s\.|, (?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|IL|IN|MA|MD|MI|MN|MO|NC|NJ|NY|OH|"
                    r"OR|PA|TN|TX|UT|VA|WA|WI)\b"),
             ("UK", r"united kingdom|\buk\b|england|london|scotland"),
             ("Canada", r"canada|toronto|vancouver|montreal"),
             ("Europe", r"europe|\beu\b|\bemea\b|germany|france|spain|portugal|netherlands|poland|ireland|italy|romania|"
                        r"sweden|denmark|belgium|austria|switzerland|czech"),
             ("Middle East", r"\buae\b|dubai|abu dhabi|saudi|qatar|bahrain|oman|kuwait|israel"),
             ("LATAM", r"latam|latin america|mexico|brazil|argentina|colombia|chile")]


def region_of(location, li_region=None):
    loc = location or ""
    for name, rx in REGION_RX:
        if re.search(rx, loc, re.I if name != "US" else 0) or (name == "US" and re.search(r"united states", loc, re.I)):
            return name
    return {"world": "Worldwide", "in": "India", "us": "US", "uk": "UK", "ca": "Canada", "au": "APAC", "eu": "Europe",
            "ae": "Middle East", "sg": "APAC"}.get(li_region or "", "")


# --------------------------------------------------------------------------- extra adapters -> cards
# card = {source, ext_id, title, company, url, apply_url, location, remote, open, text, pub, salary, emails?, region?}

def arbeitnow_cards(doc):
    out = []
    for j in (doc or {}).get("data", []) if isinstance(doc, dict) else []:
        if not isinstance(j, dict):
            continue
        loc = str(j.get("location") or "")
        text = boards._strip(j.get("description") or "")
        remote, open_ = boards._geo_verdict(loc, text[:3000])
        if j.get("remote") is True:
            remote = True
        elif j.get("remote") is False:
            remote = False
        out.append({"source": "arbeitnow", "ext_id": "an:%s" % (j.get("slug") or j.get("url")), "title": j.get("title") or "",
                    "company": j.get("company_name") or "", "url": j.get("url") or "", "apply_url": None,
                    "location": loc + (" (Remote)" if remote else ""), "remote": remote, "open": open_, "text": text,
                    "pub": himalayas._ts(j.get("created_at")), "salary": ""})
    return out


def _elements(text, name, limit=None):
    """Inner text of <name ...>...</name> elements, in order. Linear scan with str.find (a lazy `(.*?)` regex
    re-scans to the end of the input for every unclosed opening tag: quadratic on a hostile 3 MB feed)."""
    low, out, i, open_, close = text.lower(), [], 0, "<" + name, "</" + name + ">"
    while limit is None or len(out) < limit:
        s = low.find(open_, i)
        if s < 0:
            break
        nxt = low[s + len(open_):s + len(open_) + 1]
        if nxt and nxt not in "> \t\r\n/":
            i = s + len(open_)                # <items>, <titles>, ...: not this element
            continue
        gt = low.find(">", s)
        if gt < 0:
            break
        e = low.find(close, gt + 1)
        if e < 0:
            break                             # unclosed: nothing after it can close either
        out.append(text[gt + 1:e])
        i = e + len(close)
    return out


def _tag(item, name):
    got = _elements(item, name, limit=1)
    if not got:
        return ""
    v = got[0].strip()
    c = re.match(r"<!\[CDATA\[(.*)\]\]>$", v, re.S)
    return htmllib.unescape(c.group(1) if c else v).strip()


def wwr_cards(xml):
    """We Work Remotely RSS (string scan, no XML parser on untrusted input). Title is "Company: Role"."""
    out = []
    for item in _elements((xml or "")[:3_000_000], "item", limit=400):
        title = _tag(item, "title")
        company, _, role = title.partition(":")
        if not role:
            company, role = "", title
        region = _tag(item, "region")
        link = _tag(item, "link") or _tag(item, "guid")
        try:
            pub = email.utils.parsedate_to_datetime(_tag(item, "pubDate"))
            pub = pub if pub.tzinfo else pub.replace(tzinfo=UTC)
        except (TypeError, ValueError, IndexError):
            pub = None
        open_ = True if re.search(r"anywhere|worldwide|\bindia\b|\basia\b|apac", region, re.I) else \
            (False if region and re.search(r"only|usa|united states|europe|uk|canada|americas|emea", region, re.I) else None)
        out.append({"source": "wwr", "ext_id": "wwr:%s" % link, "title": role.strip(), "company": company.strip(),
                    "url": link, "apply_url": None, "location": "Remote — %s" % region if region else "Remote",
                    "remote": True, "open": open_, "text": boards._strip(_tag(item, "description"))[:8000],
                    "pub": pub, "salary": ""})
    return out


def li_search_url(region, start=0):
    q = {"keywords": "salesforce", "geoId": LI_REGIONS[region][1], "f_WT": "2", "f_TPR": "r172800", "sortBy": "DD",
         "start": str(int(start))}
    return GUEST_URL + "?" + urllib.parse.urlencode(q)


def li_cards(get, regions, now, say=lambda m: None, pause=None, stop=None):
    """LinkedIn public list per region. Returns (cards, ok_regions, rate_limited)."""
    cards, ok, limited = [], 0, False
    for region in [r for r in LI_REGIONS if r in (regions or [])]:
        if stop is not None and stop.is_set():
            break
        got = 0
        for page in range(LI_REGIONS[region][2]):
            st, body = get(li_search_url(region, 25 * page))
            if st in ligate.LIMIT_CODES:
                limited = True
                say("LinkedIn public list: LinkedIn asked us to slow down — LinkedIn is paused for a while")
                break
            if st != 200:
                if page == 0:
                    say("LinkedIn public list %s: HTTP %s" % (LI_REGIONS[region][0], st or str(body)[:80]))
                break
            cs = parse_guest_html(body)
            for c in cs:
                loc = c.get("location") or ""
                remote, open_ = boards._geo_verdict(loc)
                if region == "in" or re.search(r"\bindia\b", loc, re.I):
                    open_ = True
                cards.append({"source": "linkedin", "ext_id": "li:%s" % c["ext_id"], "li_id": c["ext_id"],
                              "title": c["title"], "company": c.get("company") or "", "url": c["url"], "apply_url": None,
                              "location": loc, "remote": True,      # the guest list's remote filter is not reliable
                              "open": True if open_ else None, "text": "", "pub": _ago(c.get("ago"), c.get("listed"), now),
                              "salary": "", "region": region})
            got += len(cs)
            if len(cs) < 10:
                break
            _pause(pause)
        if limited:
            break
        if got:
            ok += 1
        _pause(pause)
    return cards, ok, limited


def _pause(p):
    if p is None:
        import os
        import random
        if os.environ.get("JOBHUNTER_OFFLINE") == "1":
            return
        p = random.uniform(1.2, 2.6)
    if p:
        time.sleep(p)


# --------------------------------------------------------------------------- eligibility

def classify(c):
    """(elig, reason) for a card. Assumes a Salesforce technical title."""
    text = c.get("text") or ""
    loc = c.get("location") or ""
    india = bool(re.search(r"\bindia\b", "%s %s" % (loc, text[:3000]), re.I))
    ok, why = screen.screen(c.get("title"), text, "" if c.get("source") == "hn" else loc, india_role=True if india else None)
    if ok and c.get("source") == "linkedin":
        ok, why = screen.guest_remote_check(loc, text)
    if not ok:
        return ("locked" if why.startswith("remote only for") else "blocked"), why
    if c.get("remote") is False:
        return "blocked", "onsite/hybrid"
    if c.get("remote") is None and c.get("source") in ("careers", "hn"):
        return "blocked", "not marked remote"
    if c.get("open") is True or india:
        return "open", ""
    if c.get("open") is False:
        if c.get("source") == "linkedin" and not text:
            return "check", "JD not read yet"
        place = region_of(loc) or "one country"
        return "locked", "remote only for %s" % place
    return "check", "" if text else "JD not read yet"


def apply_kind_of(c):
    if c.get("source") == "linkedin":
        if c.get("closed"):
            return None, "closed"
        if not c.get("jd_checked"):
            return "linkedin", "unknown"
        if c.get("apply_url"):
            return platform_of(c["apply_url"])
        return "linkedin", "easy_apply"
    if c.get("apply_url"):
        plat, kind = platform_of(c["apply_url"])
        if kind == "board" and c.get("emails"):
            return plat, "email"
        return plat, kind
    if c.get("emails"):
        return "email", "email"
    return platform_of(c.get("url"))[0] or c.get("source"), "board"


# --------------------------------------------------------------------------- storage

def _merge_sources(a, b):
    xs = [x for x in (a or "").split(",") if x]
    if b and b not in xs:
        xs.append(b)
    return ",".join(xs)


def _keys(c):
    """Dedupe keys, strongest first: the application URL, then the source's own id."""
    ks = []
    if c.get("apply_url") and platform_of(c["apply_url"])[1] in ("portal", "login_portal", "company_site"):
        ks.append("apply:" + seen.url_key(c["apply_url"]))
    ks.append(c["ext_id"])
    return ks


def upsert(conn, c, now):
    """Store/refresh one card. Returns (listing_id, is_new)."""
    c = dict(c, url=web_url(c.get("url")) or "", apply_url=web_url(c.get("apply_url")))   # board data: http(s), public
    ts = iso(now)
    key = dedupe.norm_key(c["company"], c["title"])
    plat, kind = apply_kind_of(c)
    elig, reason = classify(c)
    region = region_of(c.get("location"), c.get("region"))
    keys = _keys(c)
    if c.get("apply_url") and kind == "portal":
        try:
            boards.discover(conn, c["apply_url"], origin=c.get("source") or "job board")   # company board flywheel
        except Exception:
            pass
    row = None
    for k in keys:
        row = conn.execute("SELECT l.* FROM listing_ids i JOIN listings l ON l.id=i.listing_id WHERE i.ext_id=?",
                           (k,)).fetchone()
        if row and k.startswith("apply:") and platform_of(c["apply_url"])[1] == "company_site" and row["norm_key"] != key:
            row = None                     # a company's generic careers URL is shared by all its roles: not the same job
            continue
        if row:
            break
    if not row and dedupe.normalize_company(c["company"]) not in AGGREGATOR_COMPANIES:
        row = conn.execute("SELECT * FROM listings WHERE norm_key=? AND posted_at>=? ORDER BY id DESC LIMIT 1",
                           (key, iso(now - timedelta(days=KEEP_DAYS)))).fetchone()
    text = (c.get("text") or "")[:20000]
    if elig in ("blocked", "locked"):
        text = text[:JD_KEEP_FILTERED]
    emails = ",".join(c.get("emails") or [])
    if row:
        upd = {"last_seen": ts, "sources": _merge_sources(row["sources"], c["source"]), "updated_at": ts}
        better_apply = kind in ("portal", "login_portal", "company_site") and row["apply_kind"] != "portal"
        if better_apply or (not row["apply_url"] and c.get("apply_url")):
            upd.update(apply_url=c.get("apply_url"), apply_kind=kind, platform=plat)
        if c.get("text") and len(c["text"]) > len(row["jd_text"] or "") and not (row["source"] == "linkedin" and row["jd_checked"]):
            upd.update(jd_text=text, elig=elig, reason=reason)
            if c.get("location"):
                upd["location"] = c["location"][:200]
        elif row["elig"] == "check" and elig in ("open", "locked", "blocked") and (c.get("text") or elig == "open"):
            upd.update(elig=elig, reason=reason)
        if c.get("pub") and (not row["posted_at"] or iso(c["pub"]) < row["posted_at"]):
            upd["posted_at"] = iso(c["pub"])
        if emails and not row["emails"]:
            upd["emails"] = emails
        if c.get("salary") and not row["salary"]:
            upd["salary"] = c["salary"][:120]
        conn.execute("UPDATE listings SET %s WHERE id=?" % ", ".join("%s=?" % k for k in upd),
                     list(upd.values()) + [row["id"]])
        for k in keys:
            conn.execute("INSERT OR IGNORE INTO listing_ids (ext_id, listing_id) VALUES (?,?)", (k, row["id"]))
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO listings (norm_key, source, sources, platform, company, title, location, region, url, apply_url,"
        " apply_kind, posted_at, first_seen, last_seen, salary, jd_text, jd_checked, elig, reason, emails, status,"
        " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?)",
        (key, c["source"], c["source"], plat, c["company"][:150], c["title"][:200], (c.get("location") or "")[:200],
         region, c.get("url") or "", c.get("apply_url"), kind, iso(c.get("pub")) or ts, ts, ts,
         (c.get("salary") or "")[:120], text, 0 if c["source"] == "linkedin" else 1, elig, reason, emails, ts))
    lid = cur.lastrowid
    for k in keys:
        conn.execute("INSERT OR IGNORE INTO listing_ids (ext_id, listing_id) VALUES (?,?)", (k, lid))
    return lid, True


def check_linkedin_jd(conn, get, limit=MAX_LI_JD, ids=None, pause=None):
    """Read the public JD page of unchecked LinkedIn listings: JD text, apply method, applicants, closed.
    Returns (checked, rate_limited)."""
    if ids:
        rows = conn.execute("SELECT * FROM listings WHERE id IN (%s) AND jd_checked=0" % ",".join("?" * len(ids)),
                            list(ids)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM listings WHERE jd_checked=0 AND source='linkedin' AND status IN ('new','queued')"
            " AND elig IN ('open','check') ORDER BY CASE region WHEN 'India' THEN 0 WHEN 'Worldwide' THEN 1 ELSE 2 END,"
            " posted_at DESC LIMIT ?", (int(limit),)).fetchall()
    from .liprefetch import parse_jd
    n = 0
    for r in rows:
        li_id = conn.execute("SELECT ext_id FROM listing_ids WHERE listing_id=? AND ext_id LIKE 'li:%' LIMIT 1",
                             (r["id"],)).fetchone()
        if not li_id:
            continue
        _pause(pause)
        st, body = get(LI_JD_URL % li_id[0][3:])
        if st in ligate.LIMIT_CODES:
            return n, True
        if st != 200:
            continue
        jd = parse_jd(body)
        jd["apply_url"] = web_url(jd.get("apply_url"))
        n += 1
        c = {"source": "linkedin", "title": r["title"], "company": r["company"], "location": jd["location"] or r["location"],
             "remote": True, "open": True if r["region"] == "India" else None, "text": jd["text"],
             "apply_url": jd["apply_url"], "jd_checked": True, "closed": jd["closed"]}
        ts = iso(_now())
        if jd["closed"]:
            conn.execute("UPDATE listings SET jd_checked=1, elig='blocked', reason='posting closed', updated_at=? WHERE id=?",
                         (ts, r["id"]))
            continue
        elig, reason = classify(c)
        plat, kind = apply_kind_of(c)
        if jd["apply_url"]:
            try:
                boards.discover(conn, jd["apply_url"], origin="linkedin apply link")   # company board flywheel
            except Exception:
                pass
            for k in _keys({"apply_url": jd["apply_url"], "ext_id": li_id[0]}):
                conn.execute("INSERT OR IGNORE INTO listing_ids (ext_id, listing_id) VALUES (?,?)", (k, r["id"]))
        conn.execute("UPDATE listings SET jd_checked=1, jd_text=?, apply_url=COALESCE(?, apply_url), platform=?, apply_kind=?,"
                     " applicants=?, elig=?, reason=?, updated_at=? WHERE id=?",
                     (jd["text"][:20000 if elig in ("open", "check") else JD_KEEP_FILTERED], jd["apply_url"], plat, kind, jd["applicants"], elig, reason, ts, r["id"]))
    return n, False


def link_known(conn):
    """Point listings at the job Job Hunter already has for the same company+role (emailed, skipped, applied...)."""
    conn.execute("UPDATE listings SET job_id=(SELECT j.id FROM jobs j WHERE j.norm_key=listings.norm_key ORDER BY j.id DESC"
                 " LIMIT 1) WHERE job_id IS NULL AND EXISTS (SELECT 1 FROM jobs j WHERE j.norm_key=listings.norm_key)")


def purge(conn, now):
    old = iso(now - timedelta(days=KEEP_DAYS))
    older = iso(now - timedelta(days=30))     # worked rows stay a month (the source scoreboard uses 14 days)
    ids = [r[0] for r in conn.execute("SELECT id FROM listings WHERE (posted_at<? AND status IN ('new','hidden'))"
                                      " OR (posted_at<? AND status IN ('done','failed','applied_self'))", (old, older))]
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ",".join("?" * len(chunk))
        conn.execute("DELETE FROM listing_ids WHERE listing_id IN (%s)" % q, chunk)
        conn.execute("DELETE FROM listings WHERE id IN (%s)" % q, chunk)
    return len(ids)


# --------------------------------------------------------------------------- refresh

def _save_raw(name, raw):
    try:
        d = config.data_dir() / "logs" / "jobboard"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(str(raw or "")[:200000], encoding="utf-8")
    except OSError:
        pass


def fetch_fast_cards(conn, fetcher, sources=FAST_SOURCES, stop=None, pause=0.4):
    """Cards from every cheap source + per-source health {name: {ok, cards, error}}."""
    cards, health = [], {}

    def note(name, ok, n=0, err="", raw=None):
        health[name] = {"ok": ok, "cards": n, "error": err}
        if (not ok or not n) and raw:
            _save_raw("%s_last.txt" % name, raw)

    if "careers" in sources:
        nb = ok_b = 0
        for b in boards.active_boards(conn, limit=MAX_BOARDS):
            if stop is not None and stop.is_set():
                break
            plat, tok = b["platform"], b["token"]
            known = bool(b.get("last_ok"))
            if plat not in boards.CARD_PARSERS:
                continue
            if pause:
                time.sleep(pause)
            try:
                url = boards.board_url(plat, tok)
            except ValueError:
                continue
            st, payload, raw = fetcher(url)
            nb += 1
            if st != 200 or payload is None:
                if st in (404, 410) or (st == 200 and payload is None):
                    boards.board_result(conn, plat, tok, False)
                continue
            try:
                cs = boards.CARD_PARSERS[plat](payload, tok)
                sf = 0
                for x in cs:
                    x["board_known"] = known
                    if isinstance(x.get("title"), str) and title_ok(x["title"]):
                        sf += 1
                    else:
                        x["text"] = ""             # never stored: don't hold big boards' JDs in memory
            except Exception as e:                     # one odd board never stops the refresh
                log.warning("board %s/%s: %s", plat, tok, e)
                continue
            boards.board_result(conn, plat, tok, True, len(cs), sf)
            ok_b += 1
            cards += cs
        health["careers"] = {"ok": bool(ok_b) or not nb, "cards": sum(1 for c in cards if c["source"] == "careers"),
                             "error": "" if ok_b or not nb else "no career page answered", "boards": ok_b, "tried": nb}
    if "himalayas" in sources and not (stop is not None and stop.is_set()):
        try:
            cs, err = boards.himalayas_cards(conn, fetcher)
        except Exception as e:
            cs, err = [], "%s: %s" % (type(e).__name__, e)
        cards += cs
        note("himalayas", not err, len(cs), err or "")
    if "jobicy" in sources and not (stop is not None and stop.is_set()):
        try:
            st, payload, raw = fetcher(boards.jobicy_url())
            cs = boards.jobicy_cards(payload) if st == 200 and isinstance(payload, dict) else []
            cards += cs
            note("jobicy", st == 200, len(cs), "" if st == 200 else "HTTP %s" % st, raw)
        except Exception as e:                         # one odd feed never stops the refresh
            note("jobicy", False, 0, "%s: %s" % (type(e).__name__, e))
    if "hn" in sources and not (stop is not None and stop.is_set()):
        try:
            cs = boards.hn_cards(fetcher)
            note("hn", True, len(cs))
            cards += cs
        except Exception as e:
            note("hn", False, 0, str(e))
    if "arbeitnow" in sources and not (stop is not None and stop.is_set()):
        n, err = 0, ""
        try:
            for page in (1, 2, 3):
                st, payload, raw = fetcher(ARBEITNOW_URL % page)
                if st != 200 or not isinstance(payload, dict):
                    err = "" if page > 1 else "HTTP %s" % st
                    if page == 1:
                        _save_raw("arbeitnow_last.txt", raw)
                    break
                cs = arbeitnow_cards(payload)
                cards += cs
                n += len(cs)
                if len(cs) < 50:
                    break
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        health["arbeitnow"] = {"ok": not err, "cards": n, "error": err}
    if "wwr" in sources and not (stop is not None and stop.is_set()):
        cs, err, raw = [], "", ""
        for u in WWR_URLS:
            try:
                st, _, raw = fetcher(u)
                if st == 200 and "<item" in (raw or ""):
                    cs = wwr_cards(raw)
                    break
                err = "HTTP %s" % st
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, e)
        cards += cs
        note("wwr", bool(cs) or not err, len(cs), "" if cs else err, raw)
    return cards, health


def _exists(conn, c):
    for k in _keys(c):
        if conn.execute("SELECT 1 FROM listing_ids WHERE ext_id=?", (k,)).fetchone():
            return True
    return False


def _ingest_one(conn, c, now, oldest, seen_ext, counts):
    """Window + title filter + upsert for one card. None = not stored, else is_new."""
    if not isinstance(c, dict):
        return None
    title, company = c.get("title"), c.get("company")
    c["title"] = title.strip() if isinstance(title, str) else ""
    c["company"] = company.strip() if isinstance(company, str) else ""
    c["ext_id"] = str(c.get("ext_id") or "")[:500]
    for k in ("url", "apply_url", "location", "text", "salary"):
        if c.get(k) is not None and not isinstance(c[k], str):
            c[k] = None if k == "apply_url" else ""      # a number where text belongs (odd feed records)
    if not c["title"] or not c["company"] or not c.get("ext_id") or c["ext_id"] in seen_ext:
        return None
    seen_ext.add(c["ext_id"])
    counts["read"] += 1
    if not title_ok(c["title"]):
        return None
    counts["salesforce"] += 1
    if c.get("pub_exact") is False and not _exists(conn, c):
        # Greenhouse without first_published: its date is the last EDIT, not the posting. Trust it only as
        # "new since our last look at this board" (never on the first crawl of a board).
        if not c.get("board_known"):
            counts["old"] += 1
            return None
        c["pub"] = now
    pub = c.get("pub")
    if isinstance(pub, datetime) and pub.tzinfo is None:
        pub = c["pub"] = pub.replace(tzinfo=UTC)
    exp = c.get("expiry")
    if isinstance(exp, datetime) and exp.tzinfo is None:
        exp = c["expiry"] = exp.replace(tzinfo=UTC)
    if not isinstance(pub, datetime) or pub < oldest or (isinstance(exp, datetime) and exp < now):
        counts["old"] += 1
        return None
    if pub > now + timedelta(hours=2):
        c["pub"] = now
    return upsert(conn, c, now)[1]


def ingest(conn, cards, now, counts):
    """Window + title filter, then upsert (in batched transactions). Updates counts in place."""
    oldest = now - timedelta(hours=WINDOW_H)
    seen_ext, n = set(), 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for c in cards:
            # one malformed card (missing id, naive/odd date, bad type) is skipped on its own: it must never roll
            # back the batch or kill the refresh
            conn.execute("SAVEPOINT card")
            try:
                new = _ingest_one(conn, c, now, oldest, seen_ext, counts)
                conn.execute("RELEASE card")
            except Exception as e:
                conn.execute("ROLLBACK TO card")
                conn.execute("RELEASE card")
                log.warning("listing %s: %s: %s", c.get("ext_id") if isinstance(c, dict) else "?", type(e).__name__, e)
                continue
            if new is None:
                continue
            counts["new" if new else "updated"] += 1
            counts["by_source"][c["source"]] = counts["by_source"].get(c["source"], 0) + 1
            n += 1
            if n % 200 == 0:                 # let the MCP server / runs write between batches
                conn.execute("COMMIT")
                conn.execute("BEGIN IMMEDIATE")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def refresh(conn, cfg=None, li=None, fetcher=None, html_get=None, stop=None, now=None, pause=None, emit=None) -> dict:
    """One refresh. li=None -> LinkedIn only when due. Returns counts (also emitted as a jobboard_refresh event)."""
    cfg = cfg or config.load()
    jb = cfg.get("jobboard") or config.DEFAULTS["jobboard"]
    fetcher = fetcher or himalayas.fetch_json
    get = html_get or ligate.get
    now = now or _now()
    t0 = time.time()
    counts = {"read": 0, "salesforce": 0, "old": 0, "new": 0, "updated": 0, "by_source": {}, "jd_checked": 0,
              "li": False, "errors": []}
    cards, health = fetch_fast_cards(conn, fetcher, stop=stop, pause=0.4 if pause is None else pause)
    li_due = li if li is not None else time.time() - _state["last_li"] >= 3600 * int(jb.get("li_every_hours") or 2)
    if li_due and html_get is None and ligate.safe_mode():
        li_due = False                    # account safety: the Job board never fetches LinkedIn
        counts["li_off"] = True
    if li_due and html_get is None and ligate.cooling():
        li_due = False
        counts["errors"].append("LinkedIn: paused after a rate limit — tried again later")
        counts["li_paused_until"] = iso(datetime.fromtimestamp(ligate.cooling_until(), UTC))
    if li_due and html_get is None and li is None:
        from . import runner
        if runner.is_running():
            # a run's own LinkedIn pre-screen is using LinkedIn right now: two bursts together get us rate limited
            # (22 Oct 09:40: app start + run + board refresh -> 429). The next refresh picks it up.
            li_due = False
            counts["li_deferred"] = True
    if li_due and jb.get("li_regions"):
        cs, ok, limited = li_cards(get, jb["li_regions"], now, say=lambda m: counts["errors"].append(m), pause=pause, stop=stop)
        cards += cs
        health["linkedin"] = {"ok": bool(ok) and not limited, "cards": len(cs), "error": "rate limited" if limited else ""}
        counts["li"] = True
        if not limited:
            _state["last_li"] = time.time()
        else:
            li_due = False
    ingest(conn, cards, now, counts)
    if li_due and not (stop is not None and stop.is_set()):
        n, limited = check_linkedin_jd(conn, get, pause=pause)
        counts["jd_checked"] = n
        if limited:
            counts["errors"].append("LinkedIn JD pages: LinkedIn asked us to slow down — the rest are read next time")
    link_known(conn)
    counts["purged"] = purge(conn, now)
    for k, h in health.items():
        if not h.get("ok"):
            counts["errors"].append("%s: %s" % (SOURCE_LABEL.get(k, k), h.get("error") or "failed"))
    counts["health"] = health
    counts["elapsed_s"] = round(time.time() - t0, 1)
    counts["at"] = iso(now)
    _state["last_fast"] = time.time()
    _state["last"] = counts
    try:
        events.emit(conn, None, "jobboard", "jobboard_refresh",
                    "Job board refreshed: %d new Salesforce jobs (%d checked)%s" % (
                        counts["new"], counts["salesforce"], " incl. LinkedIn" if counts["li"] else ""),
                    "warn" if counts["errors"] and not counts["new"] else "info",
                    {k: counts.get(k) for k in ("read", "new", "updated", "salesforce", "old", "by_source", "jd_checked", "li",
                                                "errors", "elapsed_s", "at", "health", "li_paused_until", "li_deferred", "li_off")})
    except Exception as e:
        log.warning("jobboard event: %s", e)
    return counts


def _load_state(conn):
    """Remember the last refresh times across app restarts (from the last jobboard_refresh events)."""
    if _state["loaded"]:
        return
    _state["loaded"] = True
    for r in conn.execute("SELECT data_json FROM events WHERE type='jobboard_refresh' ORDER BY id DESC LIMIT 12"):
        try:
            d = json.loads(r[0] or "{}")
            t = datetime.strptime(d.get("at"), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        except (TypeError, ValueError):
            continue
        _state["last_fast"] = max(_state["last_fast"], t)
        if d.get("li"):
            _state["last_li"] = max(_state["last_li"], t)
            break


def due(conn, cfg=None, now_ts=None) -> bool:
    jb = (cfg or config.load()).get("jobboard") or {}
    if not jb.get("on", True):
        return False
    import os
    if os.environ.get("JOBHUNTER_OFFLINE") == "1":
        return False
    _load_state(conn)
    now_ts = time.time() if now_ts is None else now_ts
    if now_ts - _state["last_fast"] >= 60 * int(jb.get("fast_minutes") or 30):
        return True
    # LinkedIn makes a refresh due early only when a LinkedIn pass can actually run: with no regions picked, or
    # while LinkedIn is paused after a rate limit, last_li never moves and this would start a full refresh
    # (150 company pages + every board) on every watcher tick
    return bool(jb.get("li_regions")) and not ligate.cooling() and \
        now_ts - _state["last_li"] >= 3600 * int(jb.get("li_every_hours") or 2)


def is_refreshing():
    with _lock:
        return _state["running"]


def refresh_in_thread(db_path=None, li=None, on_done=None) -> bool:
    """Start a refresh in the background. False if one is already running."""
    with _lock:
        if _state["running"]:
            return False
        _state["running"], _state["started"] = True, time.time()

    def work():
        conn = db.connect(db_path)
        try:
            refresh(conn, li=li)
        except Exception as e:
            log.exception("job board refresh failed: %s", e)
            # a failed refresh counts as an attempt: retried after fast_minutes, not on every watcher tick
            _state["last_fast"] = max(_state["last_fast"], time.time())
            try:
                events.emit(conn, None, "jobboard", "warning", "Job board refresh failed: %s: %s" % (type(e).__name__, e), "warn")
            except Exception:
                pass
        finally:
            with _lock:
                _state["running"] = False
            try:
                if on_done:
                    on_done(conn)
            except Exception as e:
                log.warning("job board after-refresh: %s", e)
            conn.close()
    threading.Thread(target=work, name="jh-jobboard", daemon=True).start()
    return True


# --------------------------------------------------------------------------- reading for the page

def _state_of(r):
    st = r["status"]
    if st == "hidden":
        return "hidden"
    if st == "applied_self":
        return "applied"
    ps = r["p_status"]
    if ps:
        if ps == "submitted":
            return "submitted"
        if r["p_approved"]:
            return "submitting"
        if ps in ("prefilled", "needs_input"):
            return "ready" if r["p_ready"] == 1 else "needs_you"
        if ps == "failed":
            return "needs_you"
    if st == "queued":
        return "queued"
    if st == "working":
        return "filling"
    if st == "failed":
        return "needs_you"
    js = r["j_status"]
    if js in ("contacted", "replied", "bounced", "closed") or r["sent"]:
        return "emailed"
    if js == "skipped":
        return "needs_you" if (r["j_skip"] or "").lower().startswith("needs you") else "skipped"
    return "new"


def list_listings(conn, hours=48, now=None) -> dict:
    now = now or _now()
    since = iso(now - timedelta(hours=max(1, min(int(hours or 48), WINDOW_H))))
    rows = conn.execute(
        "SELECT l.*, j.status AS j_status, j.skip_reason AS j_skip, p.id AS p_id, p.status AS p_status, p.ready AS p_ready,"
        " p.approved_at AS p_approved, p.questions_json AS p_questions, p.updated_at AS p_updated, p.apply_url AS p_url,"
        " (SELECT COUNT(*) FROM emails e WHERE e.job_id=l.job_id AND e.kind='initial' AND e.status IN ('sent','bounced')) AS sent"
        " FROM listings l LEFT JOIN jobs j ON j.id=l.job_id"
        " LEFT JOIN portal_apps p ON p.id=(SELECT MAX(id) FROM portal_apps WHERE job_id=l.job_id)"
        " WHERE l.posted_at>=? OR l.status IN ('queued','working') ORDER BY l.posted_at DESC LIMIT 1500", (since,)).fetchall()
    out = []
    for r in rows:
        try:
            qs = json.loads(r["p_questions"] or "[]")
        except ValueError:
            qs = []
        out.append({"id": r["id"], "company": r["company"], "title": r["title"], "location": r["location"],
                    "region": r["region"], "url": r["url"], "apply_url": r["p_url"] or r["apply_url"],
                    "platform": r["platform"], "apply_kind": r["apply_kind"], "posted_at": r["posted_at"],
                    "first_seen": r["first_seen"], "salary": r["salary"], "elig": r["elig"], "reason": r["reason"],
                    "sources": [s for s in (r["sources"] or r["source"] or "").split(",") if s], "source": r["source"],
                    "applicants": r["applicants"], "emails": [e for e in (r["emails"] or "").split(",") if e],
                    "status": r["status"], "state": _state_of(r), "job_id": r["job_id"], "job_status": r["j_status"],
                    "skip_reason": r["j_skip"], "portal_id": r["p_id"], "portal_seen": r["p_updated"],
                    "questions": [q if isinstance(q, str) else (q.get("question") or q.get("label") or str(q))
                                  for q in qs][:20] if isinstance(qs, list) else [],
                    "jd": (r["jd_text"] or "")[:1500], "jd_checked": r["jd_checked"], "note": r["note"],
                    "queued_by": r["queued_by"]})
    return {"listings": out, "status": status(conn, now), "sources": source_stats(conn, now)}


def status(conn, now=None) -> dict:
    _load_state(conn)
    jb = config.load().get("jobboard") or {}
    last = _state.get("last")
    if last is None:
        r = conn.execute("SELECT data_json FROM events WHERE type='jobboard_refresh' ORDER BY id DESC LIMIT 1").fetchone()
        try:
            last = json.loads(r[0]) if r else None
        except ValueError:
            last = None
    now_ts = time.time()
    fast_in = max(0, int(60 * int(jb.get("fast_minutes") or 30) - (now_ts - _state["last_fast"])))
    return {"refreshing": is_refreshing(), "last": last, "settings": jb,
            "next_in_s": fast_in if jb.get("on", True) else None,
            "linkedin_paused_s": max(0, int(ligate.cooling_until() - now_ts)) if ligate.cooling() else 0,
            "auto_today": auto_today(conn), "spent_today": spent_today(conn), "queued": pending(conn)}


def source_stats(conn, now=None):
    """Per source, last 14 days: jobs listed, applications (submitted forms + sent emails), replies."""
    since = iso((now or _now()) - timedelta(days=14))
    out = {}
    for r in conn.execute(
            "SELECT l.source, COUNT(*) AS listed,"
            " SUM(CASE WHEN EXISTS (SELECT 1 FROM portal_apps p WHERE p.job_id=l.job_id AND p.status='submitted')"
            "  OR EXISTS (SELECT 1 FROM emails e WHERE e.job_id=l.job_id AND e.kind='initial' AND e.status IN ('sent','bounced'))"
            "  OR l.status='applied_self' THEN 1 ELSE 0 END) AS applied,"
            " SUM(CASE WHEN EXISTS (SELECT 1 FROM replies x WHERE x.job_id=l.job_id) THEN 1 ELSE 0 END) AS replies"
            " FROM listings l WHERE l.first_seen>=? AND l.elig IN ('open','check') GROUP BY l.source", (since,)):
        out[r["source"]] = {"listed": r["listed"], "applied": r["applied"] or 0, "replies": r["replies"] or 0}
    return out


# --------------------------------------------------------------------------- Akshay's actions

def set_status(conn, lid, status):
    if status not in ("hidden", "applied_self", "new"):
        return {"error": "status must be hidden, applied_self or new"}
    r = conn.execute("SELECT id, status FROM listings WHERE id=?", (int(lid),)).fetchone()
    if not r:
        return {"error": "listing %s not found" % lid}
    cur = conn.execute("UPDATE listings SET status=?, updated_at=? WHERE id=? AND status<>'working'",
                       (status, iso(_now()), r["id"]))
    if not cur.rowcount:                   # atomic: a run may have claimed it since the read
        return {"error": "the AI is filling this application right now"}
    return {"id": r["id"], "status": status}


def queue(conn, ids, by="you") -> dict:
    """Mark listings to be filled by the next board_apply run."""
    ok, skipped = [], []
    ts = iso(_now())
    for raw in (ids or [])[:100]:
        try:
            lid = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if isinstance(raw, bool) or not 0 < lid < 2 ** 63:
            continue
        r = conn.execute("SELECT l.id, l.status, l.apply_kind, l.job_id FROM listings l WHERE l.id=?", (lid,)).fetchone()
        if not r:
            continue
        if r["status"] in ("queued", "working"):
            skipped.append({"id": lid, "reason": "already queued"})
            continue
        if r["apply_kind"] == "closed":
            skipped.append({"id": lid, "reason": "posting closed"})
            continue
        if r["job_id"] and conn.execute("SELECT 1 FROM portal_apps WHERE job_id=? AND (status='submitted' OR"
                                        " (status IN ('prefilled','needs_input') AND (ready=1 OR approved_at IS NOT NULL"
                                        " OR ?='auto')))", (r["job_id"], by)).fetchone():
            skipped.append({"id": lid, "reason": "already filled — see Ready / Needs you"})
            continue
        conn.execute("UPDATE listings SET status='queued', queued_at=?, queued_by=?, note=NULL, updated_at=? WHERE id=?",
                     (ts, by, ts, lid))
        ok.append(lid)
    return {"queued": ok, "skipped": skipped}


def auto_today(conn):
    today = db.today_ist().isoformat() if hasattr(db, "today_ist") else db.now_iso()[:10]
    n = 0
    for (q,) in conn.execute("SELECT queued_at FROM listings WHERE queued_by='auto' AND queued_at IS NOT NULL"):
        try:
            d = datetime.strptime(q, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone(db.IST).date().isoformat()
        except (TypeError, ValueError):
            continue
        n += d == today
    return n


def spent_today(conn) -> float:
    """AI dollars Job board applications used today (lane_finished events): the board_apply lane, plus the Easy Apply
    lane when a Job board run hands it the board's LinkedIn Easy Apply jobs (auto-fill picks those too)."""
    today = db.now_iso()[:10]
    usd = 0.0
    for (d,) in conn.execute("SELECT e.data_json FROM events e LEFT JOIN runs r ON r.id=e.run_id WHERE e.type='lane_finished'"
                             " AND (e.lane='board_apply' OR (e.lane='easy_apply' AND r.trigger='jobboard'))"
                             " AND substr(e.ts,1,10)=?", (today,)):
        try:
            usd += float(json.loads(d or "{}").get("cost_usd") or 0)
        except (TypeError, ValueError):
            pass
    return round(usd, 2)


def over_budget(conn, jb) -> bool:
    """The daily AI dollar budget for Job board auto-fill is used up ("AI budget per day" 0 = no auto-fill spending,
    like the other caps)."""
    try:
        cap = float(6 if (jb or {}).get("daily_usd") is None else jb.get("daily_usd"))
    except (TypeError, ValueError):
        cap = 6.0
    return spent_today(conn) >= cap


def auto_pick(conn, cfg=None, now=None) -> list:
    """After a refresh: queue the best fresh jobs for filling, within the daily form cap and dollar budget.
    Only forms that need no account, Easy Apply and email applications. Order: open to India first, fewest
    applicants (being early matters most), cheapest apply type, newest."""
    cfg = cfg or config.load()
    jb = cfg.get("jobboard") or {}
    if not jb.get("on", True) or not jb.get("auto_fill", True):
        return []
    if pending(conn):
        return []                          # still working through earlier picks
    if over_budget(conn, jb):
        return []
    # Easy Apply switched off in Settings: auto-fill never drives his LinkedIn account (a click on a job still does)
    kinds = AUTO_KINDS if cfg.get("easy_apply", True) else tuple(k for k in AUTO_KINDS if k != "easy_apply")
    left = int(jb.get("daily_cap", 20)) - auto_today(conn)
    n = min(int(jb.get("per_run", 5)), left)
    if n <= 0:
        return []
    now = now or _now()
    rows = conn.execute(
        "SELECT l.id FROM listings l LEFT JOIN jobs j ON j.id=l.job_id WHERE l.status='new' AND l.posted_at>=?"
        " AND COALESCE(l.tries, 0)=0 AND l.note IS NULL"
        " AND (l.elig='open' OR (l.elig='check' AND l.jd_checked=1 AND l.jd_text<>''))"
        " AND COALESCE(l.applicants, 0)<=100"
        " AND l.apply_kind IN (%s) AND (l.job_id IS NULL OR (j.status='qualified' AND NOT EXISTS"
        " (SELECT 1 FROM portal_apps p WHERE p.job_id=l.job_id)))"
        " ORDER BY CASE l.elig WHEN 'open' THEN 0 ELSE 1 END, CASE WHEN l.posted_at>=? THEN 0 ELSE 1 END,"
        " CASE WHEN COALESCE(l.applicants, 30)<25 THEN 0 ELSE 1 END,"
        " CASE l.apply_kind WHEN 'email' THEN 0 WHEN 'portal' THEN 1 ELSE 2 END, l.posted_at DESC LIMIT ?"
        % ",".join("?" * len(kinds)), (iso(now - timedelta(hours=24)),) + kinds + (iso(now - timedelta(hours=3)), n)).fetchall()
    return queue(conn, [r[0] for r in rows], by="auto")["queued"]


# --------------------------------------------------------------------------- the apply run

def _job_source(r):
    if r["source"] == "linkedin":
        return {"US": "linkedin_us", "India": "linkedin_india"}.get(r["region"], "linkedin_world")
    return r["source"] if r["source"] in ("careers", "himalayas", "jobicy", "hn") else "board"


def ensure_job(conn, run_id, r, lane="board_apply"):
    """The jobs row for a listing (existing one for the same company+role, else a new qualified job)."""
    ex = conn.execute("SELECT id, status, apply_url FROM jobs WHERE norm_key=? ORDER BY id DESC LIMIT 1",
                      (r["norm_key"],)).fetchone()
    if ex:
        if r["apply_url"] and not ex["apply_url"]:
            conn.execute("UPDATE jobs SET apply_url=?, updated_at=? WHERE id=?", (r["apply_url"], db.now_iso(), ex["id"]))
        return ex["id"], ex["status"]
    notes = "via Job board (%s)" % ", ".join(SOURCE_LABEL.get(s, s) for s in (r["sources"] or r["source"]).split(","))
    if r["emails"]:
        notes += " · hiring email in post: %s" % r["emails"]
    if r["salary"]:
        notes += " · salary %s" % r["salary"]
    res = actions.save_job(conn, run_id, lane, _job_source(r), r["company"], r["title"], location=r["location"] or "",
                           remote=True, url=r["url"] or "", posted=(r["posted_at"] or "")[:10], jd_text=r["jd_text"] or "",
                           notes=notes, apply_url=r["apply_url"])
    return res.get("job_id"), res.get("status") or "qualified"


def _unqueue_auto(conn, note, kind=None):
    """Auto-fill picks that may not run now go back to the list (Akshay's own picks are never touched)."""
    q = "UPDATE listings SET status='new', note=?, updated_at=? WHERE status='queued' AND queued_by='auto'"
    args = [note, iso(_now())]
    if kind:
        q += " AND apply_kind=?"
        args.append(kind)
    return conn.execute(q, args).rowcount


def claim(conn, run_id, limit=8, html_get=None, cfg=None):
    """Queued listings -> jobs rows, marked 'working'. Returns (easy_apply_targets, form_targets, notes)."""
    cfg = cfg or config.load()
    notes = []
    if over_budget(conn, cfg.get("jobboard") or {}) and _unqueue_auto(conn, "Daily AI budget reached — not filled automatically"):
        notes.append("auto-fill picks: today's AI budget for the Job board is used up")
    if not cfg.get("easy_apply", True) and _unqueue_auto(conn, "Easy Apply is off in Settings", kind="easy_apply"):
        notes.append("auto-fill Easy Apply picks: Easy Apply is turned off in Settings")
    rows = conn.execute("SELECT * FROM listings WHERE status='queued' ORDER BY CASE queued_by WHEN 'you' THEN 0 ELSE 1 END,"
                        " CASE elig WHEN 'open' THEN 0 WHEN 'check' THEN 1 ELSE 2 END, posted_at DESC LIMIT ?",
                        (int(limit),)).fetchall()
    unchecked = [r["id"] for r in rows if r["source"] == "linkedin" and not r["jd_checked"]]
    if unchecked and (html_get is not None or not ligate.cooling()):
        try:
            check_linkedin_jd(conn, html_get or ligate.get, ids=unchecked)
        except Exception as e:
            log.warning("JD check before apply: %s", e)
        rows = [conn.execute("SELECT * FROM listings WHERE id=?", (r["id"],)).fetchone() for r in rows]
    ea, forms = [], []
    ts = iso(_now())
    for r in rows:
        if r is None:
            continue
        if r["elig"] == "blocked" and r["reason"] == "posting closed":
            conn.execute("UPDATE listings SET status='failed', note='posting closed', updated_at=? WHERE id=?", (ts, r["id"]))
            notes.append("%s — %s: posting closed" % (r["company"], r["title"]))
            continue
        if r["apply_kind"] == "easy_apply" and r["queued_by"] == "auto" and not cfg.get("easy_apply", True):
            conn.execute("UPDATE listings SET status='new', note='Easy Apply is off in Settings', updated_at=? WHERE id=?",
                         (ts, r["id"]))
            continue
        job_id, jstatus = ensure_job(conn, run_id, r)
        if not job_id:
            conn.execute("UPDATE listings SET status='failed', note='could not save the job', updated_at=? WHERE id=?",
                         (ts, r["id"]))
            continue
        if r["queued_by"] == "auto" and (jstatus not in ("found", "qualified") or conn.execute(
                "SELECT 1 FROM portal_apps WHERE job_id=?", (job_id,)).fetchone()):
            conn.execute("UPDATE listings SET status='new', job_id=?, updated_at=? WHERE id=? AND status='queued'",
                         (job_id, ts, r["id"]))
            continue                       # already handled (emailed / skipped / filled): the auto pass never reopens it
        if jstatus == "skipped":
            # "Try filling again" after a sign-in wall ("needs you: sign in to <portal> ..."): reopen the job, else the
            # new prefill would be voided at once as "job was skipped after prefill" (a new wall just skips it again)
            conn.execute("UPDATE jobs SET status='qualified', skip_reason=NULL, updated_at=? WHERE id=? AND status='skipped'"
                         " AND lower(COALESCE(skip_reason,'')) LIKE 'needs you%'", (db.now_iso(), job_id))
        cur = conn.execute("UPDATE listings SET status='working', job_id=?, tries=COALESCE(tries,0)+1, updated_at=?"
                           " WHERE id=? AND status='queued'", (job_id, ts, r["id"]))
        if not cur.rowcount:
            continue                       # hidden / unqueued by him since the read: leave it alone
        t = {"job_id": job_id, "listing_id": r["id"], "company": r["company"], "role": r["title"], "url": r["url"],
             "apply_url": r["apply_url"], "platform": r["platform"], "kind": r["apply_kind"], "location": r["location"],
             "elig": r["elig"], "emails": [e for e in (r["emails"] or "").split(",") if e], "jd": bool(r["jd_text"])}
        if r["apply_kind"] == "easy_apply" and re.fullmatch(r"https://www\.linkedin\.com/jobs/view/\d{1,15}/?", r["url"] or ""):
            ea.append(t)
        else:
            forms.append(t)
    return ea, forms, notes


def finish(conn, run_id, ids, since=None, stopped=False):
    """After the lanes: 'working' listings the AI handled -> 'done'; untouched ones go back to the queue once,
    then to "needs you". since = db.now_iso() taken right after claim(): any verdict the AI recorded on the job after
    it (e.g. qualified + "Easy Apply on LinkedIn") counts as handled, so the job is never reopened.
    stopped = he pressed Stop: untouched rows go back to the list (not the queue, so the watcher does not restart)."""
    ts = iso(_now())
    for lid in ids or []:
        r = conn.execute("SELECT l.*, j.status AS j_status, j.updated_at AS j_updated FROM listings l"
                         " LEFT JOIN jobs j ON j.id=l.job_id WHERE l.id=?", (lid,)).fetchone()
        if not r or r["status"] != "working":
            continue
        handled = conn.execute("SELECT 1 FROM portal_apps WHERE job_id=?", (r["job_id"],)).fetchone() or \
            r["j_status"] in ("skipped", "contacted", "replied", "bounced", "closed") or \
            bool(since and r["j_updated"] and r["j_updated"] > since)
        if handled:
            conn.execute("UPDATE listings SET status='done', updated_at=? WHERE id=?", (ts, lid))
        elif stopped:
            conn.execute("UPDATE listings SET status='new', tries=MAX(COALESCE(tries,1)-1,0), note='stopped before it was"
                         " filled', updated_at=? WHERE id=?", (ts, lid))
        elif (r["tries"] or 0) >= 2:
            conn.execute("UPDATE listings SET status='failed', note='Could not be filled automatically — open it and apply"
                         " yourself', updated_at=? WHERE id=?", (ts, lid))
        else:
            conn.execute("UPDATE listings SET status='queued', updated_at=? WHERE id=?", (ts, lid))


def release_queue(conn, status, note):
    """Every queued (and leftover working) listing leaves the queue: used when a board_apply run cannot work it
    (stopped, AI engine missing, claim failed), so the watcher never restarts runs on the same queue forever."""
    if status not in ("new", "failed"):
        raise ValueError(status)
    return conn.execute("UPDATE listings SET status=?, note=?, updated_at=? WHERE status IN ('queued','working')",
                        (status, str(note)[:300], iso(_now()))).rowcount


def queued_ids(conn, limit=200):
    """Signature of the queue: (id, queued_at) pairs — a fresh click (new queued_at) changes it, a re-queue by
    finish() does not."""
    return tuple((r[0], r[1]) for r in conn.execute(
        "SELECT id, queued_at FROM listings WHERE status='queued' ORDER BY id LIMIT ?", (int(limit),)))


def release_working(conn):
    """App start: rows left 'working' by a crashed/stopped run go back to the queue."""
    conn.execute("UPDATE listings SET status='queued' WHERE status='working'")


def pending(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM listings WHERE status='queued'").fetchone()[0]
