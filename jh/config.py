"""Load/save data/config.json and resolve app paths.

Data dir resolution: env JOBHUNTER_DATA > directory of env JOBHUNTER_DB > <app root>/data.
Config file: env JOBHUNTER_CONFIG > <data dir>/config.json.
"""
import copy
import json
import os
import re
import shlex
import threading
from pathlib import Path

MASK_PREFIX = "••••"  # "••••"

DEFAULT_CLAUDE_ARGS = [
    "--chrome", "--permission-mode", "dontAsk",
    "--allowedTools", "mcp__jobhunt__*", "mcp__claude-in-chrome__*",
    "--disallowedTools", "Agent", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch",
    "--strict-mcp-config",
    "--max-turns", "150",
]

DEFAULTS = {
    "port": 8765,
    "auto_run_on_open": False,
    "parallel_sources": False,
    "dry_run": True,
    "max_emails_per_run": 25,
    "lookback": "24h",        # job age: "24h" or "week"
    "easy_apply": True,       # prefill LinkedIn Easy Apply forms
    # Akshay, 29 Oct: "submit bhi tumko hi krna hai" - every fully filled ("ready") application is approved and
    # submitted by itself, no "Submit selected" click. Off = the old per-batch approval.
    "auto_submit": True,
    "watch": {                # watch mode: poll LinkedIn's public job list, prefill new Easy Apply jobs at once
        "on": False,
        "interval_min": 10,
        "regions": ["us", "india", "world"],
        "posts": True,
        "posts_interval_min": 30,
    },
    "email": {
        "backend": "smtp",
        "address": "akshay.verma0031@gmail.com",
        "app_password": "",
        "from_name": "Akshay Verma",
    },
    "agencies": {"per_run": 5, "daily_cap": 15, "focus": "all"},   # focus: outside | india | all; own caps
    # Autopilot (architect 2026-10-22): runs by themselves while the app is open, so new jobs are reached in
    # minutes, not after the next button click. Every AI lane stops for the day at budget.daily_usd.
    "autopilot": {"on": True, "followups_min": 15, "linkedin_jobs_min": 60, "regions": ["india", "world"]},
    "budget": {"daily_usd": 40},
    # Akshay, 11 Nov: one email a day with what Job Hunter did (Easy Apply / portal applications submitted, recruiter
    # emails sent, follow-ups, replies, forms waiting) at `hour` IST, to `to` (empty = the application Gmail)
    "daily_report": {"on": True, "hour": 23, "to": ""},
    # LinkedIn account safety (Akshay, 22 Oct): no automatic LinkedIn traffic at all — no public-page fetching (watch
    # mode, pre-screen, Job board) and no LinkedIn runs started by Autopilot. LinkedIn lanes run only on his Run click.
    "linkedin_safe": True,
    # LinkedIn Fast Apply (Akshay, 30 Oct: "whenever a new job is posted on LinkedIn, find it and apply immediately"):
    # while the app is open, every every_min minutes his own Chrome opens LinkedIn's "Easy Apply + Remote + past hour"
    # search and applies to the new jobs (fills + submits), capped per run and per day, only between the hours below.
    "li_fast": {"on": True, "every_min": 30, "per_run": 5, "daily_cap": 30, "start_hour": 8, "end_hour": 24,
                # Akshay, 10 Nov: one LinkedIn cycle (Easy Apply jobs + hiring posts) every 2 hours, and the next AI
                # cycle only gap_after_min minutes after the previous one FINISHED, to save tokens (0 = no gap)
                "lanes": ["easy_apply"], "gap_after_min": 0,
                # Akshay, 11 Nov (token saving): before each cycle, read LinkedIn's PUBLIC Easy Apply list (logged out,
                # no AI, one request per region) and send the AI only jobs never seen before; nothing new -> no Easy
                # Apply AI run. The public list unreadable -> the AI searches LinkedIn itself, as before.
                "probe": True},
    # extra ways to a VERIFIED work email when SalesQL finds none (jh/emailfind.py); keys optional, free tiers work
    "email_finder": {"hunter_api_key": "", "apollo_api_key": "", "daily_cap": 25},
    "roles": "developer",     # Akshay, 30 Oct: apply only to Salesforce Developer/Engineer roles ("all" = any SF role)
    # Akshay, 10 Nov: never architect-level roles, and only jobs asking for at most 6-7 years of experience
    # Akshay, 10 Nov (evening): by default the app works ONLY on LinkedIn hiring posts + Easy Apply, every 10 minutes;
    # everything else (follow-ups, job board, portals, remote boards) runs only when he ticks it and clicks Run
    "focus_linkedin": False,   # set true in data/config.json on the Job Hunter laptop
    "skip_architect": True,
    "max_years_required": 7,
    # Akshay, 10 Nov: runs quietly in the background - no dashboard tab pops up when Windows starts
    "open_browser_at_boot": False,
    "default_regions": ["india", "world"],   # LinkedIn regions when a run doesn't pick any (US: 0 replies so far)
    # Job board: remote Salesforce jobs worldwide from the last 48 h, fetched by plain Python (no AI)
    # fast_minutes: company portals + remote boards (cheap JSON); li_every_hours: LinkedIn's public list per region
    "jobboard": {"on": True, "auto_fill": True, "per_run": 5, "daily_cap": 20, "daily_usd": 6,
                 "li_regions": ["world", "in", "us"], "fast_minutes": 30, "li_every_hours": 2},
    "engine": {
        "claude_path": "",
        "claude_args": DEFAULT_CLAUDE_ARGS,
        "lane_timeout_min": 25,
        "mock": False,
        "model": "sonnet",      # sonnet = ~5x cheaper than opus for browsing lanes; "opus"; "default" = Claude's own
        "lane_models": {},      # optional per lane, e.g. {"easy_apply": "opus"}
        "chrome_browser": "",   # connected Chrome signed in to LinkedIn (remembered by the engine) - display name only
        # the extension's deviceId of Akshay's own Chrome on THIS laptop: the ONLY browser ever used once set
        # (names like "Browser 2" are renumbered every time browsers connect, so they can't be trusted)
        "chrome_device_id": "",
        "chrome_profile_dir": "",   # Job Hunter Chrome profile folder (chrome://version "Profile Path", e.g. "Profile 2"): opened at boot
    },
    "profile": {
        "name": "Akshay Kumar Verma",
        "email": "akshay.verma0031@gmail.com",
        "phone": "+91 75664 42162",
        "linkedin": "linkedin.com/in/akshay-kumar-18565910a",
        "location": "India (remote, US hours)",
    },
}

_lock = threading.Lock()


def app_root() -> Path:
    """Repo root (folder containing app.py and jh/)."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Data directory, created if missing."""
    env = os.environ.get("JOBHUNTER_DATA")
    if env:
        p = Path(env)
    elif os.environ.get("JOBHUNTER_DB"):
        p = Path(os.environ["JOBHUNTER_DB"]).resolve().parent
    else:
        p = app_root() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub_dir(name: str) -> Path:
    """A subfolder of the data dir (resumes, outbox, inbox, logs), created if missing."""
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    env = os.environ.get("JOBHUNTER_CONFIG")
    return Path(env) if env else data_dir() / "config.json"


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(path=None) -> dict:
    """Return config merged over defaults; writes defaults on first run."""
    p = Path(path) if path else config_path()
    with _lock:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            _write(p, DEFAULTS)
            return copy.deepcopy(DEFAULTS)
        try:
            raw = json.loads(p.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            raw = {}
    return _deep_merge(DEFAULTS, raw if isinstance(raw, dict) else {})


def _write(p: Path, cfg: dict):
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(p))


def save(cfg: dict, path=None) -> dict:
    p = Path(path) if path else config_path()
    clean = {k: v for k, v in cfg.items() if not str(k).startswith("_")}
    with _lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        _write(p, clean)
    return clean


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return MASK_PREFIX


def masked(cfg: dict) -> dict:
    """Copy of cfg with the app password masked (for GET /api/settings)."""
    out = copy.deepcopy(cfg)
    em = out.get("email") or {}
    em["app_password"] = mask_secret(em.get("app_password", ""))
    em["app_password_set"] = bool((cfg.get("email") or {}).get("app_password"))
    out["email"] = em
    ef = dict(out.get("email_finder") or {})
    for k in ("hunter_api_key", "apollo_api_key"):
        ef[k + "_set"] = bool(ef.get(k))
        ef[k] = mask_secret(ef.get(k, ""))
    out["email_finder"] = ef
    return out


def clean_model(v):
    v = str(v or "").strip()
    return v if re.fullmatch(r"[A-Za-z0-9.\-\[\]]{1,48}", v) else ""


def _as_bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _unquote(tok: str) -> str:
    """shlex(posix=False) - used on Windows to keep backslashes - leaves the quotes on: strip one pair."""
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


# Never writable over HTTP: anything that decides WHAT gets executed or WHERE data lives. A page running on
# 127.0.0.1 (e.g. a tab the AI engine was tricked into opening) must not be able to repoint the engine.
API_BLOCKED = {"data_dir", "db_path", "config_path"}
API_BLOCKED_ENGINE = {"claude_path", "claude_args", "python_path", "system_prompt_mode"}


def apply_update(cfg: dict, incoming: dict, from_api: bool = False) -> dict:
    """Merge a (partial) settings payload into cfg, validating types.

    A masked app password (starting with "••••") is ignored so the real one is kept.
    from_api=True drops the keys in API_BLOCKED / API_BLOCKED_ENGINE (edit those in config.json by hand).
    """
    incoming = copy.deepcopy(incoming or {})
    if isinstance(incoming, dict):
        # a section that is an object in the config (email, engine, watch, ...) never gets replaced by a scalar/list
        incoming = {k: v for k, v in incoming.items() if not (isinstance(DEFAULTS.get(k), dict) and not isinstance(v, dict))}
    if from_api:
        incoming = {k: v for k, v in incoming.items() if k not in API_BLOCKED} if isinstance(incoming, dict) else {}
        if isinstance(incoming.get("engine"), dict):
            incoming["engine"] = {k: v for k, v in incoming["engine"].items() if k not in API_BLOCKED_ENGINE}
    em = incoming.get("email")
    if isinstance(em, dict):
        em.pop("app_password_set", None)
        pw = em.get("app_password")
        if pw is None or (isinstance(pw, str) and pw.startswith(MASK_PREFIX)):
            em.pop("app_password", None)
        elif isinstance(pw, str):
            em["app_password"] = pw.replace(" ", "").strip()
    ef = incoming.get("email_finder")
    if isinstance(ef, dict):
        for k in ("hunter_api_key", "apollo_api_key"):
            ef.pop(k + "_set", None)
            v = ef.get(k)
            if v is None or (isinstance(v, str) and v.startswith(MASK_PREFIX)):
                ef.pop(k, None)                   # masked value sent back unchanged: keep the saved key
            elif isinstance(v, str):
                ef[k] = re.sub(r"\s+", "", v)[:120]
    eng = incoming.get("engine")
    if isinstance(eng, dict) and isinstance(eng.get("claude_args"), str):
        s = eng["claude_args"]
        eng["claude_args"] = [_unquote(a) for a in shlex.split(s, posix=(os.name != "nt"))] if s.strip() else []
    out = _deep_merge(cfg, incoming)
    for k in ("auto_run_on_open", "parallel_sources", "dry_run", "easy_apply", "auto_submit"):
        out[k] = _as_bool(out.get(k))
    out["engine"]["mock"] = _as_bool(out["engine"].get("mock"))
    for k, lo, hi in (("port", 1, 65535), ("max_emails_per_run", 0, 10 ** 6)):
        try:
            out[k] = min(hi, max(lo, int(out.get(k))))
        except (TypeError, ValueError, OverflowError):
            out[k] = DEFAULTS[k]
    try:
        out["engine"]["lane_timeout_min"] = max(1, int(out["engine"].get("lane_timeout_min")))
    except (TypeError, ValueError, OverflowError):
        out["engine"]["lane_timeout_min"] = DEFAULTS["engine"]["lane_timeout_min"]
    if not isinstance(out["engine"].get("claude_args"), list):
        out["engine"]["claude_args"] = list(DEFAULT_CLAUDE_ARGS)
    out["engine"]["claude_args"] = [str(a) for a in out["engine"]["claude_args"]]
    out["engine"]["model"] = clean_model(out["engine"].get("model")) or "sonnet"
    ag = out.get("agencies") if isinstance(out.get("agencies"), dict) else {}
    ag = _deep_merge(DEFAULTS["agencies"], ag)
    for k, lo, hi in (("per_run", 1, 20), ("daily_cap", 0, 50)):
        try:
            ag[k] = min(hi, max(lo, int(ag.get(k))))
        except (TypeError, ValueError, OverflowError):
            ag[k] = DEFAULTS["agencies"][k]
    ag["focus"] = {"us_first": "outside", "us_only": "outside"}.get(ag.get("focus"), ag.get("focus"))
    if ag.get("focus") not in ("outside", "india", "all"):
        ag["focus"] = "outside"
    out["agencies"] = {k: ag[k] for k in ("per_run", "daily_cap", "focus")}
    jb = out.get("jobboard") if isinstance(out.get("jobboard"), dict) else {}
    jb = _deep_merge(DEFAULTS["jobboard"], jb)
    jb["on"], jb["auto_fill"] = _as_bool(jb.get("on")), _as_bool(jb.get("auto_fill"))
    for k, lo, hi in (("per_run", 1, 15), ("daily_cap", 0, 60), ("fast_minutes", 15, 240),
                      ("li_every_hours", 1, 24), ("daily_usd", 0, 100)):
        try:
            jb[k] = min(hi, max(lo, int(jb.get(k))))
        except (TypeError, ValueError, OverflowError):
            jb[k] = DEFAULTS["jobboard"][k]
    allowed = ["world", "in", "us", "uk", "ca", "au", "eu", "ae", "sg"]
    regs = jb.get("li_regions")
    if isinstance(regs, str):                     # "us" / "us,in" - never "every region"
        regs = [x.strip().lower() for x in regs.split(",")]
    elif not isinstance(regs, list):
        regs = DEFAULTS["jobboard"]["li_regions"]
    jb["li_regions"] = [r for r in allowed if r in regs]
    out["jobboard"] = {k: jb[k] for k in DEFAULTS["jobboard"]}
    ap = _deep_merge(DEFAULTS["autopilot"], out.get("autopilot") if isinstance(out.get("autopilot"), dict) else {})
    ap["on"] = _as_bool(ap.get("on"))
    for k, lo, hi in (("followups_min", 5, 240), ("linkedin_jobs_min", 15, 720)):
        try:
            ap[k] = min(hi, max(lo, int(ap.get(k))))
        except (TypeError, ValueError, OverflowError):
            ap[k] = DEFAULTS["autopilot"][k]
    ap["regions"] = [r for r in ("us", "india", "world") if r in (ap.get("regions") or [])] or ["india", "world"]
    out["autopilot"] = {k: ap[k] for k in DEFAULTS["autopilot"]}
    bd = _deep_merge(DEFAULTS["budget"], out.get("budget") if isinstance(out.get("budget"), dict) else {})
    try:
        bd["daily_usd"] = min(500.0, max(0.0, float(bd.get("daily_usd"))))
    except (TypeError, ValueError, OverflowError):
        bd["daily_usd"] = DEFAULTS["budget"]["daily_usd"]
    out["budget"] = {"daily_usd": bd["daily_usd"]}
    dr = _deep_merge(DEFAULTS["daily_report"], out.get("daily_report") if isinstance(out.get("daily_report"), dict) else {})
    dr["on"] = _as_bool(dr.get("on", True))
    try:
        dr["hour"] = min(23, max(0, int(dr.get("hour"))))
    except (TypeError, ValueError, OverflowError):
        dr["hour"] = DEFAULTS["daily_report"]["hour"]
    to = re.sub(r"\s+", "", str(dr.get("to") or ""))[:120]
    dr["to"] = to if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to) else ""
    out["daily_report"] = {k: dr[k] for k in DEFAULTS["daily_report"]}
    out["linkedin_safe"] = _as_bool(out.get("linkedin_safe", True))
    lf = _deep_merge(DEFAULTS["li_fast"], out.get("li_fast") if isinstance(out.get("li_fast"), dict) else {})
    lf["on"] = _as_bool(lf.get("on"))
    for k, lo, hi in (("every_min", 10, 240), ("per_run", 1, 10), ("daily_cap", 0, 60), ("start_hour", 0, 23),
                      ("end_hour", 1, 24)):
        try:
            lf[k] = min(hi, max(lo, int(lf.get(k))))
        except (TypeError, ValueError, OverflowError):
            lf[k] = DEFAULTS["li_fast"][k]
    lanes = lf.get("lanes") if isinstance(lf.get("lanes"), list) else DEFAULTS["li_fast"]["lanes"]
    lf["lanes"] = [x for x in ("easy_apply", "posts", "linkedin_jobs") if x in [str(v) for v in lanes]] or ["easy_apply"]
    try:
        lf["gap_after_min"] = min(600, max(0, int(lf.get("gap_after_min"))))
    except (TypeError, ValueError, OverflowError):
        lf["gap_after_min"] = 0
    lf["probe"] = _as_bool(lf.get("probe", True))
    out["li_fast"] = {k: lf[k] for k in DEFAULTS["li_fast"]}
    out["skip_architect"] = _as_bool(out.get("skip_architect", True))
    out["focus_linkedin"] = _as_bool(out.get("focus_linkedin", False))
    try:
        out["max_years_required"] = min(30, max(1, int(out.get("max_years_required"))))
    except (TypeError, ValueError, OverflowError):
        out["max_years_required"] = DEFAULTS["max_years_required"]
    out["open_browser_at_boot"] = _as_bool(out.get("open_browser_at_boot", False))
    ef = _deep_merge(DEFAULTS["email_finder"], out.get("email_finder") if isinstance(out.get("email_finder"), dict) else {})
    try:
        ef["daily_cap"] = min(500, max(0, int(ef.get("daily_cap"))))
    except (TypeError, ValueError, OverflowError):
        ef["daily_cap"] = 25
    out["email_finder"] = {k: (str(ef.get(k) or "") if k != "daily_cap" else ef[k]) for k in DEFAULTS["email_finder"]}
    dr = out.get("default_regions") if isinstance(out.get("default_regions"), list) else DEFAULTS["default_regions"]
    out["default_regions"] = [r for r in ("us", "india", "world") if r in dr] or ["india", "world"]
    lm = out["engine"].get("lane_models")
    out["engine"]["lane_models"] = {str(k): clean_model(v) for k, v in lm.items() if clean_model(v)} if isinstance(lm, dict) else {}
    w = out.get("watch") if isinstance(out.get("watch"), dict) else {}
    w = _deep_merge(DEFAULTS["watch"], w)
    w["on"] = _as_bool(w.get("on"))
    w["posts"] = _as_bool(w.get("posts"))
    for k, lo, hi in (("interval_min", 5, 120), ("posts_interval_min", 15, 240)):
        try:
            w[k] = min(hi, max(lo, int(w.get(k))))
        except (TypeError, ValueError, OverflowError):
            w[k] = DEFAULTS["watch"][k]
    regs = [r for r in ("us", "india", "world") if r in (w.get("regions") or [])]
    w["regions"] = regs or list(DEFAULTS["watch"]["regions"])
    out["watch"] = w
    if out.get("lookback") not in ("24h", "week"):
        out["lookback"] = "24h"
    if out["email"].get("backend") not in ("smtp", "file"):
        out["email"]["backend"] = "smtp"
    return out


def email_configured(cfg: dict) -> bool:
    em = cfg.get("email") or {}
    if em.get("backend") == "file":
        return True
    return bool(em.get("address") and em.get("app_password"))
