"""One gate for every request Job Hunter makes to LinkedIn's PUBLIC pages (guest job list + public JD pages).

Watch mode, the LinkedIn pre-screen and the Job board all go through `get()`, so together they never hit LinkedIn
faster than one request every MIN_GAP seconds, and one "slow down" answer (HTTP 429 / 999 / 403) pauses all of
them: 15 min, then 30, then 60 on repeated strikes. The pause is saved in app_meta, so restarting the app does not
reset it. Tests inject their own fetchers and never come here.
"""
import os
import threading
import time

from . import db

MIN_GAP = 4.0          # 2.5 s still drew a 429 when a run and the board hit LinkedIn together
COOLDOWN_MIN = (15, 30, 60)
LIMIT_CODES = (429, 999, 403)

_lock = threading.Lock()
_st = {"last": 0.0, "until": None, "strikes": 0}


def _meta(key, value=None):
    try:
        conn = db.connect()
        try:
            if value is None:
                r = conn.execute("SELECT v FROM app_meta WHERE k=?", (key,)).fetchone()
                return r[0] if r else None
            conn.execute("INSERT OR REPLACE INTO app_meta (k, v) VALUES (?,?)", (key, str(value)))
        finally:
            conn.close()
    except Exception:
        return None


def safe_mode() -> bool:
    """LinkedIn account safety: no automatic requests to LinkedIn at all (config linkedin_safe, default on)."""
    try:
        from . import config
        return bool(config.load().get("linkedin_safe", True))
    except Exception:
        return True


def cooling_until() -> float:
    """Epoch seconds until which LinkedIn is paused (0 = not paused)."""
    with _lock:
        if _st["until"] is None:
            try:
                _st["until"] = float(_meta("li_cooldown_until") or 0)
                _st["strikes"] = int(float(_meta("li_strikes") or 0))
            except (TypeError, ValueError):
                _st["until"] = 0.0
        return _st["until"] if _st["until"] > time.time() else 0.0


def cooling() -> bool:
    return cooling_until() > 0


def strike(code=429):
    """LinkedIn said slow down: pause every LinkedIn request for 15 / 30 / 60 minutes."""
    with _lock:
        _st["strikes"] = min(len(COOLDOWN_MIN), (_st["strikes"] or 0) + 1)
        _st["until"] = time.time() + 60 * COOLDOWN_MIN[_st["strikes"] - 1]
        until, strikes = _st["until"], _st["strikes"]
    _meta("li_cooldown_until", until)
    _meta("li_strikes", strikes)


def _ok():
    with _lock:
        had = _st["strikes"]
        _st["strikes"] = 0
    if had:
        _meta("li_strikes", 0)


def probe_on() -> bool:
    """LinkedIn Fast Apply's no-AI check of the PUBLIC Easy Apply list (config li_fast.probe, default on). It reads
    logged-out pages only, never his account, so it is allowed even with the account-safety setting on."""
    try:
        from . import config
        lf = config.load().get("li_fast") or {}
        return bool(lf.get("on")) and lf.get("probe", True) not in (False, "false", "0", 0, "off", "no")
    except Exception:
        return False


def get(url, timeout=20.0, fetch=None, public_ok=False):
    """(status, text) like watch.fetch, through the gate. While paused: (429, "paused") without a request.
    public_ok: the Fast Apply check (probe_on) may read public pages even with the account-safety setting on."""
    if fetch is None and os.environ.get("JOBHUNTER_OFFLINE") == "1":
        return 0, "offline mode"           # tests / QA: never touch the network
    if fetch is None and safe_mode() and not (public_ok and probe_on()):
        return 0, "LinkedIn public pages are off (account safety setting)"
    if cooling():
        return 429, "LinkedIn paused after a rate limit (until %s)" % time.strftime("%H:%M", time.localtime(cooling_until()))
    if fetch is None:
        from .watch import fetch as fetch
    with _lock:
        wait = _st["last"] + MIN_GAP - time.time()
        _st["last"] = max(time.time(), _st["last"] + MIN_GAP)
    if wait > 0 and os.environ.get("JOBHUNTER_OFFLINE") != "1":
        time.sleep(wait)
    st, body = fetch(url, timeout)
    if st in LIMIT_CODES:
        strike(st)
    elif st == 200:
        _ok()
    return st, body
