"""Watch mode + run dispatcher.

While the app is open:
  * every `watch.interval_min` (default 10) it reads LinkedIn's PUBLIC job list (no login, no AI, one small
    request per region: Salesforce, Remote, Easy Apply, posted in the last hour), keeps the jobs it has never
    seen whose title is a Salesforce role, and queues them;
  * when no run is going it starts a run of the easy_apply lane for exactly those jobs (they get prefilled,
    questions go to the Questions tab, finished forms are marked ready);
  * every `watch.posts_interval_min` (default 30) it starts a short LinkedIn-posts pass;
  * applications Akshay approved with "Submit selected" are handed to the submit lane as soon as nothing
    else is running — approvals always go first.
Nothing here clicks anything: it only decides WHEN to start runs.
"""
import gzip
import html as htmllib
import logging
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from . import actions, config, db, dedupe, events, runner

log = logging.getLogger("jobhunter.watch")

GEO = {"us": "103644278", "india": "102713980", "world": "92000000"}
REGION_SOURCE = {"us": "linkedin_us", "india": "linkedin_india", "world": "linkedin_world"}
GUEST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/129.0.0.0 Safari/537.36")
TICK_S = 15
MAX_TARGETS = 10
BOARD_RETRY_S = 900        # an unchanged Job board queue is retried at most every 15 min

TITLE_OK = re.compile(r"salesforce|sfdc|\bapex\b|\blwc\b|\bcpq\b|omnistudio|omni studio|vlocity|agentforce|"
                      r"pardot|marketing cloud|service cloud|sales cloud|experience cloud|data cloud|health cloud|"
                      r"field service lightning|\bfsl\b|copado|mulesoft", re.I)
TITLE_BAD = re.compile(r"account executive|sales executive|sales (manager|director|representative|rep)\b|"
                       r"\b(sdr|bdr)\b|business development|account manager|customer success|recruiter|talent acq|"
                       r"sales development|inside sales|marketing manager|\bintern(ship)?\b", re.I)


# --------------------------------------------------------------------------- LinkedIn guest page

def search_url(region: str, window_s: int = 3600, keywords: str = "salesforce", start: int = 0) -> str:
    q = {"keywords": keywords, "geoId": GEO[region], "f_WT": "2", "f_AL": "true",
         "f_TPR": "r%d" % int(window_s), "sortBy": "DD", "start": str(int(start))}
    return GUEST_URL + "?" + urllib.parse.urlencode(q)


def gunzip_limited(raw: bytes, limit: int) -> bytes:
    """gzip body -> at most `limit` bytes. A plain gzip.decompress() expands a few MB of zeros into gigabytes
    (decompression bomb) and takes the whole app down; anything past the limit is cut (and fails to parse)."""
    import zlib
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    return d.decompress(raw, limit)


def fetch(url: str, timeout: float = 20.0):
    """(http_status, text). Network problems -> (0, error text)."""
    import os
    if os.environ.get("JOBHUNTER_OFFLINE") == "1":     # tests: never touch the network (same as himalayas.fetch_json)
        return 0, "offline mode"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000)
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gunzip_limited(raw, 6_000_000)
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # DNS, timeout, proxy, TLS
        return 0, "%s: %s" % (type(e).__name__, e)


_URN = re.compile(r"urn:li:jobPosting:(\d{6,15})\b")
_HREF_VIEW = re.compile(r"href=\"[^\"]*?/jobs/view/(?:[^\"/?]*?-)?(\d{6,15})(?=[/?\"#])", re.I)
MAX_CARD = 20000          # bytes of markup looked at per card (keeps the regexes linear on odd pages)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]{0,2000}>", " ", fragment or ""))).strip()


def _cls(chunk: str, cls: str) -> str:
    """Text of the first element whose class list contains exactly `cls` (not `cls-hidden` etc.)."""
    m = re.search(r"<(\w{1,10})[^>]{0,800}?class=\"(?:[^\"]{0,400}\s)?%s(?:\s[^\"]{0,400})?\"[^>]{0,800}>(.{0,6000}?)</\1>"
                  % re.escape(cls), chunk, re.S | re.I)
    return _text(m.group(2)) if m else ""


def _cards(text: str):
    """One chunk per job card. Split on <li>; a piece with no job id (a nested <li> inside a card) is glued
    back onto the card before it, so its company/location aren't lost."""
    pieces = re.split(r"<li[\s>]", text, flags=re.I)[1:]
    cards = []
    for pc in pieces:
        if _URN.search(pc[:MAX_CARD]) or _HREF_VIEW.search(pc[:MAX_CARD]) or not cards:
            cards.append(pc[:MAX_CARD])
        else:
            cards[-1] = (cards[-1] + "<li " + pc)[:MAX_CARD]
    return cards


def parse_guest_html(text: str) -> list:
    """Job cards from LinkedIn's public job list HTML: [{ext_id, title, company, location, url, listed, ago}]."""
    out, seen = [], set()
    for chunk in _cards((text or "")[:5_000_000]):
        m = _URN.search(chunk) or _HREF_VIEW.search(chunk)
        if not m:
            continue
        ext = m.group(1)
        if ext in seen:
            continue
        title = _cls(chunk, "base-search-card__title") or _cls(chunk, "sr-only")
        if not title:
            continue
        seen.add(ext)
        company = _cls(chunk, "base-search-card__subtitle")
        location = _cls(chunk, "job-search-card__location")
        t = re.search(r"<time[^>]{0,400}?datetime=\"([^\"]{0,40})\"[^>]{0,400}>(.{0,200}?)</time>", chunk, re.S | re.I)
        out.append({"ext_id": ext, "title": title[:200], "company": company[:150], "location": location[:150],
                    "url": "https://www.linkedin.com/jobs/view/%s/" % ext,
                    "listed": t.group(1) if t else "", "ago": _text(t.group(2)) if t else ""})
    return out


def title_ok(title: str) -> bool:
    return bool(TITLE_OK.search(title or "")) and not TITLE_BAD.search(title or "")


# --------------------------------------------------------------------------- seen store

def record_cards(conn, region: str, cards: list) -> list:
    """Store cards in watch_seen; return the newly queued ones (never seen, Salesforce title, not applied)."""
    now = db.now_iso()
    new = []
    for c in cards:
        if conn.execute("SELECT 1 FROM watch_seen WHERE ext_id=?", (c["ext_id"],)).fetchone():
            continue
        status, reason = "queued", None
        if not title_ok(c["title"]):
            status, reason = "ignored", "not a Salesforce technical title"
        elif known_job(conn, c["ext_id"]):
            status, reason = "known", "already in Job Hunter"
        elif c["company"] and dedupe.job_contacted(conn, c["company"], c["title"]):
            status, reason = "known", "already applied/contacted"
        conn.execute("INSERT OR IGNORE INTO watch_seen (ext_id, title, company, location, region, url, listed, status,"
                     " reason, first_seen, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (c["ext_id"], c["title"], c["company"], c["location"], region, c["url"], c["listed"], status,
                      reason, now, now))
        if status == "queued":
            new.append(dict(c, region=region))
    return new


def known_job(conn, ext_id: str) -> bool:
    """True if a saved job's URL points at this LinkedIn job id (/jobs/view/<id> or /jobs/view/<slug>-<id>)."""
    rx = re.compile(r"/jobs/view/(?:[^/?#\s]*-)?%s(?:[/?#]|$)" % re.escape(ext_id))
    for r in conn.execute("SELECT url, apply_url FROM jobs WHERE url LIKE ? OR apply_url LIKE ?",
                          ("%" + ext_id + "%",) * 2):
        if rx.search(r["url"] or "") or rx.search(r["apply_url"] or ""):
            return True
    return False


def queued(conn, limit=MAX_TARGETS) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM watch_seen WHERE status='queued' ORDER BY first_seen, ext_id LIMIT ?", (limit,))]


def recent(conn, limit=12) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT ext_id, title, company, location, region, url, status, reason, first_seen, run_id FROM watch_seen"
        " WHERE status<>'ignored' ORDER BY first_seen DESC LIMIT ?", (limit,))]


def found_today(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM watch_seen WHERE status NOT IN ('ignored') AND substr(first_seen,1,10)=?",
                        (db.today_ist().isoformat(),)).fetchone()[0]


# --------------------------------------------------------------------------- dispatcher

def li_fast_today(conn) -> int:
    """Easy Apply forms worked today (filled or submitted) — the LinkedIn Fast Apply daily cap counts these."""
    day = db.now_iso()[:10]
    return conn.execute("SELECT COUNT(*) FROM portal_apps WHERE ats='linkedin_easy_apply' AND substr(created_at,1,10)=?",
                        (day,)).fetchone()[0]


def over_budget(conn) -> bool:
    from . import runner as _r
    return _r.over_budget(conn)


class Watcher:
    """One per server. `tick()` is the whole brain; the thread just calls it every TICK_S seconds."""

    def __init__(self, db_path=None, fetcher=None, start_run=None, sleep=time.sleep, jobboard=None):
        self.db_path = db_path
        injected = fetcher is not None          # tests inject a fetcher; the real app passes none
        self.injected = injected
        if fetcher is None:
            from . import ligate
            fetcher = ligate.get        # shared LinkedIn spacing + rate-limit pause
        self.fetch = fetcher
        self.start_run = start_run or runner.start_run
        self.sleep = sleep
        self.stop = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.state = {"last_poll": None, "next_poll": 0.0, "next_posts": 0.0, "last_error": None,
                      "backoff": 0, "last_found": 0, "polls": 0,
                      # tests inject a fetcher and never want the background Job board refresh / autopilot
                      "no_jobboard": not (jobboard if jobboard is not None else not injected),
                      "next_followups": time.time() + 60, "next_li": time.time() + 300,
                      "next_li_fast": time.time() + 120}

    # ---- config
    def cfg(self) -> dict:
        return config.load().get("watch") or dict(config.DEFAULTS["watch"])

    def set(self, patch: dict) -> dict:
        cfg = config.load()
        if isinstance(patch, dict) and "autopilot" in patch:
            on = bool(patch.get("autopilot"))
            ap = dict(cfg.get("autopilot") or {}, on=on)
            upd = {"autopilot": ap}
            if not on:
                upd["watch"] = dict(cfg.get("watch") or {}, on=False)   # off means everything automatic stops
            cfg = config.apply_update(cfg, upd)
            config.save(cfg)
            with self.lock:
                if on:
                    self.state.update(next_poll=0.0, next_followups=time.time() + 30, backoff=0, last_error=None)
            conn = db.connect(self.db_path)
            try:
                events.emit(conn, None, "watch", "step", "Autopilot %s" % ("on: new jobs, replies and follow-ups are"
                            " handled by themselves while the app is open" if on else "off"), "info")
            finally:
                conn.close()
            patch = {k: v for k, v in patch.items() if k != "autopilot"}
            if not patch:
                return self.status()
        cur = dict(cfg.get("watch") or {})
        for k in ("on", "interval_min", "regions", "posts", "posts_interval_min"):
            if k in (patch or {}):
                cur[k] = patch[k]
        new = config.apply_update(cfg, {"watch": cur})
        was_on = bool((cfg.get("watch") or {}).get("on"))
        config.save(new)
        if new["watch"]["on"] and not was_on:
            with self.lock:
                self.state.update(next_poll=0.0, next_posts=time.time() + 60, backoff=0, last_error=None)
            conn = db.connect(self.db_path)
            try:
                events.emit(conn, None, "watch", "step", "Watch mode on: checking LinkedIn every %d min (%s)%s"
                            % (new["watch"]["interval_min"], ", ".join(r.upper() if r == "us" else r.title()
                                                                        for r in new["watch"]["regions"]),
                               "; posts every %d min" % new["watch"]["posts_interval_min"] if new["watch"]["posts"]
                               else ""), "info")
            finally:
                conn.close()
        elif was_on and not new["watch"]["on"]:
            conn = db.connect(self.db_path)
            try:
                events.emit(conn, None, "watch", "step", "Watch mode off", "info")
            finally:
                conn.close()
        return self.status()

    def status(self) -> dict:
        w = self.cfg()
        conn = db.connect(self.db_path)
        try:
            q = conn.execute("SELECT COUNT(*) FROM watch_seen WHERE status='queued'").fetchone()[0]
            out = dict(w, queued=q, found_today=found_today(conn), recent=recent(conn),
                       pending_submit=actions.pending_approvals(conn))
        finally:
            conn.close()
        with self.lock:
            st = dict(self.state)
        now = time.time()
        ap = self.autopilot() if not self.state.get("no_jobboard") else {}
        on = bool(w.get("on") or ap.get("on"))
        out.update(last_poll=st["last_poll"], last_error=st["last_error"], backoff_min=st["backoff"],
                   next_poll_in_s=max(0, int(st["next_poll"] - now)) if on else None,
                   next_posts_in_s=max(0, int(st["next_posts"] - now)) if on and w.get("posts") else None)
        out["linkedin_safe"] = self.linkedin_safe()
        lf = config.load().get("li_fast") or {}
        out["li_fast"] = bool(lf.get("on"))
        out["li_fast_every_min"] = int(lf.get("every_min") or 30)
        out["next_li_fast_in_s"] = max(0, int(st.get("next_li_fast", 0) - now)) if lf.get("on") else None
        if out["linkedin_safe"]:
            on = False                         # no LinkedIn polling in safe mode
            out.update(next_poll_in_s=None, next_posts_in_s=None)
        out["watch_on"] = bool(w.get("on"))
        out["on"] = on
        out["autopilot"] = bool(ap.get("on"))
        if ap.get("on"):
            conn = db.connect(self.db_path)
            try:
                spent = runner.spent_today(conn)
            finally:
                conn.close()
            budget = float((config.load().get("budget") or {}).get("daily_usd") or 0)
            out.update(next_followups_in_s=max(0, int(st["next_followups"] - now)),
                       next_li_in_s=max(0, int(st["next_li"] - now)), spent_today=spent, budget_usd=budget,
                       over_budget=bool(budget) and spent >= budget)
        return out

    # ---- thread
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="jh-watch", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()

    def _loop(self):
        while not self.stop.wait(TICK_S):
            try:
                self.tick()
            except Exception as e:   # never let the dispatcher die
                log.exception("watch tick failed: %s", e)

    # ---- the brain
    def tick(self, now=None):
        now = time.time() if now is None else now
        w = self.cfg()
        conn = db.connect(self.db_path)
        try:
            # 1. Akshay's approvals first (auto_submit: every ready form counts as approved)
            if not runner.is_running() and config.load().get("auto_submit"):
                actions.auto_approve_ready(conn)
            if not runner.is_running() and actions.pending_approvals(conn):
                return self._start(conn, "submit", lanes=["submit"])
            # 2. Job board: refresh in the background when due (plain HTTP); auto-fill picks after each refresh
            self.jobboard_tick(conn)
            ap = self.autopilot()
            if self.linkedin_safe():
                # account safety: nothing automatic on LinkedIn (no public-page polling, no LinkedIn runs); Autopilot
                # still checks replies, follows up and works the Job board (company portals, remote boards)
                if ap.get("on") and not runner.is_running():
                    if now >= self.state["next_followups"]:
                        self.state["next_followups"] = now + 60 * int(ap.get("followups_min") or 15)
                        return self._start(conn, "followups", lanes=["followups"])
                if not runner.is_running():
                    started = self.li_fast_tick(conn, now)      # his standing instruction: new LinkedIn jobs at once
                    if started:
                        return started
                if ap.get("on") and not runner.is_running():
                    if not over_budget(conn):
                        return self._start_board(conn)
                    return None
                return self._start_board(conn) if not ap.get("on") or not over_budget(conn) else None
            if ap.get("on") and not w.get("on"):
                w = dict(w, on=True, regions=ap.get("regions") or ["india", "world"])   # autopilot = watch mode on
            if not w.get("on"):
                return self._start_board(conn)
            # 3. poll LinkedIn's public list
            with self.lock:
                due = now >= self.state["next_poll"]
            if due:
                self.poll(conn, w, now)
            # 4. start work when idle: watch-mode Easy Apply jobs, then Job board applications, then posts
            if runner.is_running():
                return None
            q = queued(conn)
            if q:
                return self._start(conn, "easy_apply", lanes=["easy_apply"], targets=q)
            # replies are time-critical and cost nothing (IMAP): check them often
            if ap.get("on") and now >= self.state["next_followups"]:
                self.state["next_followups"] = now + 60 * int(ap.get("followups_min") or 15)
                return self._start(conn, "followups", lanes=["followups"])
            if ap.get("on") and over_budget(conn):
                return None                       # AI budget for today used up: only free checks until tomorrow
            started = self._start_board(conn)
            if started:
                return started
            with self.lock:
                posts_due = w.get("posts") and now >= self.state["next_posts"]
                if posts_due:
                    self.state["next_posts"] = now + 60 * int(w.get("posts_interval_min") or 30)
            if posts_due:
                return self._start(conn, "posts", lanes=["posts"], quick=True)
            if ap.get("on") and now >= self.state["next_li"]:
                self.state["next_li"] = now + 60 * int(ap.get("linkedin_jobs_min") or 60)
                return self._start(conn, "linkedin_jobs", lanes=["linkedin_jobs"], regions=ap.get("regions"))
            return None
        finally:
            conn.close()

    def poll(self, conn, w, now):
        regions = [r for r in (w.get("regions") or []) if r in GEO] or list(GEO)
        interval = 60 * int(w.get("interval_min") or 10)
        window = max(3600, 2 * interval)
        new, errors, cards_total, unparsed = [], [], 0, None
        for i, region in enumerate(regions):
            if i:
                self.sleep(random.uniform(2.0, 4.0))
            status, text = self.fetch(search_url(region, window))
            if status == 200:
                cards = parse_guest_html(text)
                cards_total += len(cards)
                if not cards and "<li" in text and "jobPosting" not in text and "/jobs/view/" not in text:
                    unparsed = text
                new += record_cards(conn, region, cards)
            elif status in (429, 999, 403):
                errors.append("LinkedIn is limiting requests (HTTP %d)" % status)
            elif status == 0:
                errors.append("no connection (%s)" % (text or "")[:120])
            else:
                errors.append("HTTP %d" % status)
        if unparsed is not None:
            try:
                (config.data_dir() / "logs").mkdir(exist_ok=True)
                (config.data_dir() / "logs" / "watch_last_page.html").write_text(unparsed[:500000], encoding="utf-8")
            except OSError:
                pass
            errors.append("page format not recognised (saved logs/watch_last_page.html)")
        with self.lock:
            st = self.state
            st["polls"] += 1
            st["last_poll"] = db.now_iso()
            st["last_found"] = len(new)
            if errors and len(errors) == len(regions):
                st["backoff"] = min(60, max(int(w.get("interval_min") or 10), (st["backoff"] or 0) * 2 or 20))
                st["last_error"] = errors[0]
                st["next_poll"] = now + 60 * st["backoff"]
            else:
                st["backoff"] = 0
                st["last_error"] = errors[0] if errors else None
                st["next_poll"] = now + interval
        if new:
            names = ", ".join("%s (%s)" % (c["company"] or "?", c["title"][:40]) for c in new[:3])
            events.emit(conn, None, "watch", "watch_found", "New Salesforce Easy Apply job%s: %s%s"
                        % ("" if len(new) == 1 else "s", names, " +%d more" % (len(new) - 3) if len(new) > 3 else ""),
                        "success", {"count": len(new), "jobs": [{k: c.get(k) for k in ("title", "company", "url", "region")}
                                                                 for c in new[:10]]})
        elif errors and len(errors) == len(regions):
            events.emit(conn, None, "watch", "warning", "Watch: %s — next check in %d min"
                        % (errors[0], self.state["backoff"]), "warn")
        return new

    def linkedin_safe(self) -> bool:
        """Real app with the account-safety setting on (tests that inject a fetcher keep the old behaviour)."""
        return not getattr(self, "injected", False) and bool(config.load().get("linkedin_safe", True))

    def autopilot(self) -> dict:
        """Autopilot settings, or {} when off (tests that inject a fetcher never get autopilot)."""
        if self.state.get("no_jobboard"):
            return {}
        ap = config.load().get("autopilot") or {}
        return ap if ap.get("on", True) else {}

    def jobboard_tick(self, conn):
        from . import jobboard
        if self.state.get("no_jobboard"):
            return
        cfg = config.load()
        if jobboard.is_refreshing() or not jobboard.due(conn, cfg):
            return

        def after(c):
            try:
                picked = jobboard.auto_pick(c)
                if picked:
                    events.emit(c, None, "jobboard", "step", "Auto-fill picked %d fresh job%s from the Job board"
                                % (len(picked), "" if len(picked) == 1 else "s"), "info", {"listings": picked})
            except Exception as e:
                log.warning("auto-fill pick: %s", e)
        jobboard.refresh_in_thread(self.db_path, on_done=after)

    def _start_board(self, conn):
        """Idle and Job board applications queued -> start a board_apply run."""
        from . import jobboard
        if self.state.get("no_jobboard") or runner.is_running() or not jobboard.pending(conn):
            return None
        # the same queue as the last board_apply start, again: that run could not work it (a hole in the status
        # machine) -> wait instead of restarting a run every TICK_S
        ids, now = jobboard.queued_ids(conn), time.time()
        last = self.state.get("board_last")
        if last and last[0] == ids and now - last[1] < BOARD_RETRY_S:
            return None
        started = self._start(conn, "board_apply", lanes=["board_apply"])
        if started:
            self.state["board_last"] = (ids, now)
        return started

    def li_fast_tick(self, conn, now, local_hour=None):
        """LinkedIn Fast Apply: every li_fast.every_min minutes (during his hours, under the daily cap and the AI budget)
        start an Easy Apply run on LinkedIn jobs from the past hour, in his own Chrome. Returns the start dict or None."""
        lf = config.load().get("li_fast") or {}
        if not lf.get("on") or now < self.state.get("next_li_fast", 0):
            return None
        hour = time.localtime(now).tm_hour if local_hour is None else local_hour
        if not (int(lf.get("start_hour", 8)) <= hour < int(lf.get("end_hour", 24))):
            return None
        self.state["next_li_fast"] = now + 60 * int(lf.get("every_min") or 30)
        cap = int(lf.get("daily_cap") or 0)
        if cap and li_fast_today(conn) >= cap:
            return None
        if over_budget(conn):
            return None
        regions = (config.load().get("autopilot") or {}).get("regions") or ["india", "world"]
        return self._start(conn, "li_fast", lanes=["easy_apply"], regions=regions)

    def _start(self, conn, kind, lanes, targets=None, quick=False, regions=None):
        w = self.cfg()
        try:
            if kind == "easy_apply":
                regions = sorted({t["region"] for t in targets if t.get("region") in GEO}) or w.get("regions")
                rid = self.start_run("watch", db_path=self.db_path, lanes=lanes, regions=regions,
                                     targets=[{k: t.get(k) for k in ("title", "company", "location", "url", "region")}
                                              for t in targets], quick=False)
                conn.executemany("UPDATE watch_seen SET status='sent', run_id=?, updated_at=? WHERE ext_id=?",
                                 [(rid, db.now_iso(), t["ext_id"]) for t in targets])
            elif kind == "posts":
                rid = self.start_run("watch", db_path=self.db_path, lanes=lanes, quick=True)
            elif kind == "board_apply":
                rid = self.start_run("jobboard", db_path=self.db_path, lanes=lanes)
            elif kind == "li_fast":
                rid = self.start_run("li_fast", db_path=self.db_path, lanes=lanes, regions=regions, fast=True)
            elif kind in ("followups", "linkedin_jobs"):
                rid = self.start_run("auto", db_path=self.db_path, lanes=lanes, regions=regions)
            else:
                rid = self.start_run("approved", db_path=self.db_path, lanes=lanes)
            return {"kind": kind, "run_id": rid}
        except runner.AlreadyRunning:
            return None

