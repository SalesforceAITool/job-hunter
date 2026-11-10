"""Run orchestration (SPEC §1/§16).

A run = `followups` lane in a background thread, in parallel with the AI lanes:
posts -> jobright -> linkedin_jobs (sequential unless parallel_sources) and then `portal`.
`outreach` is a virtual lane: sends happen inside the source lanes (engine calls send_application);
the runner opens it when the AI phase starts and closes it with the run's email counts.
The runner is the only emitter of lane_started / lane_finished.
"""
import importlib
import json
import threading
import time
import traceback

from . import config, db, events, followups

SOURCE_LANES = ["posts", "jobright", "linkedin_jobs", "himalayas"]
ALL_LANES = ["followups"] + SOURCE_LANES + ["outreach", "agencies", "portal", "easy_apply", "board_apply", "submit"]
ATS_LIKE = ("%greenhouse.io%", "%lever.co%", "%ashbyhq.com%")
SELECTABLE_LANES = ("followups", "posts", "jobright", "linkedin_jobs", "himalayas", "agencies", "easy_apply", "portal",
                    "board_apply", "submit")
REGIONS = ("us", "india", "world")
BOARD_FORMS_CAP = 6        # engine/prompts/board_apply.md: "at most 6 forms" per run
FULL_RUN_OFF = ("jobright", "himalayas")
LINKEDIN_LANES = ("posts", "linkedin_jobs", "easy_apply", "agencies")


def normalize_selection(lanes=None, regions=None):
    """(lanes, regions) cleaned. None/empty lanes -> None (= the full default run).
    Regions apply to linkedin_jobs and easy_apply; empty -> all three."""
    sel = None
    if lanes:
        if isinstance(lanes, str):
            lanes = [x.strip() for x in lanes.split(",")]
        sel = [ln for ln in SELECTABLE_LANES if ln in set(lanes)] or None
    reg = []
    if regions:
        if isinstance(regions, str):
            regions = [x.strip() for x in regions.split(",")]
        reg = [r for r in REGIONS if r in set(regions)]
    return sel, (reg or list(REGIONS))


class AlreadyRunning(Exception):
    pass


_lock = threading.Lock()
_state = {"run_id": None, "thread": None, "stop": None, "db_path": None}


def is_running() -> bool:
    with _lock:
        t = _state["thread"]
        return bool(t and t.is_alive())


def current_run_id():
    with _lock:
        t = _state["thread"]
        return _state["run_id"] if t and t.is_alive() else None


def stop_run() -> bool:
    """Ask the current run to stop (engine processes are killed by the engine on stop)."""
    with _lock:
        t, ev = _state["thread"], _state["stop"]
        if not (t and t.is_alive() and ev):
            return False
        ev.set()
    return True


def wait(timeout=None) -> bool:
    """Block until the current run finishes (tests / CLI). Returns True if finished."""
    with _lock:
        t = _state["thread"]
    if t:
        t.join(timeout)
        return not t.is_alive()
    return True


def _load_engine():
    """Import jh.engine lazily; raises ImportError (or anything) when unavailable."""
    return importlib.import_module("jh.engine")


class _Emitter:
    """emit(lane, type, message, level, data) with one DB connection per thread."""

    def __init__(self, db_path, run_id):
        self.db_path, self.run_id = db_path, run_id
        self.local = threading.local()

    def conn(self):
        c = getattr(self.local, "conn", None)
        if c is None:
            c = self.local.conn = db.connect(self.db_path)
        return c

    def __call__(self, lane, type, message, level="info", data=None):
        return events.emit(self.conn(), self.run_id, lane, type, message, level, data)

    def for_engine(self, lane_default):
        """Emit wrapper handed to the engine: lane lifecycle events stay with the runner."""
        def emit(lane, type, message, level="info", data=None):
            lane = lane or lane_default
            if type == "lane_started":
                return None
            if type == "lane_finished":
                type, level = "step", level or "info"
                message = message or "Engine finished"
            return self(lane, type, message, level, data)
        return emit


def start_run(trigger="button", cfg=None, db_path=None, lanes=None, regions=None, targets=None, quick=False,
              fast=False) -> int:
    """Create a run row and start its thread. Raises AlreadyRunning.
    lanes: subset of SELECTABLE_LANES to run (None = full default run).
    regions: subset of REGIONS for linkedin_jobs / easy_apply (None = all)."""
    with _lock:
        t = _state["thread"]
        if t and t.is_alive():
            raise AlreadyRunning("a run is already in progress (run %s)" % _state["run_id"])
        cfg = dict(cfg or config.load())
        sel, reg = normalize_selection(lanes, regions or cfg.get("default_regions") or None)
        cfg["_run_lanes"] = sel
        cfg["_regions"] = reg
        cfg["_targets"] = list(targets or [])[:15]   # watch mode: the exact Easy Apply jobs to work on
        cfg["_quick"] = bool(quick)                  # watch mode: short posts pass
        cfg["_li_fast"] = bool(fast)                 # LinkedIn Fast Apply: jobs from the past hour, apply at once
        conn = db.connect(db_path)
        cur = conn.execute("INSERT INTO runs (started_at, status, trigger, stats_json) VALUES (?, 'running', ?, '{}')",
                           (db.now_iso(), trigger))
        run_id = cur.lastrowid
        conn.close()
        stop = threading.Event()
        th = threading.Thread(target=_run_main, args=(run_id, trigger, cfg, db_path, stop),
                              name="jh-run-%s" % run_id, daemon=True)
        _state.update(run_id=run_id, thread=th, stop=stop, db_path=db_path)
        th.start()
        return run_id


def _lane_counts(conn, run_id, lane) -> dict:
    c = {"found": 0, "qualified": 0, "skipped": 0, "contacts": 0, "sent": 0, "dry_run": 0, "failed": 0}
    for r in conn.execute("SELECT type, data_json FROM events WHERE run_id=? AND lane=?", (run_id, lane)):
        t = r["type"]
        if t in ("job_qualified", "job_skipped"):
            c["found"] += 1
            c["qualified" if t == "job_qualified" else "skipped"] += 1
        elif t == "contact_found":
            c["contacts"] += 1
        elif t == "email_sent":
            dry = '"dry_run": true' in (r["data_json"] or "")
            c["dry_run" if dry else "sent"] += 1
        elif t == "email_failed":
            c["failed"] += 1
    return c


def spent_today(conn) -> float:
    """AI dollars every lane used today (IST), from the lane_finished events."""
    usd = 0.0
    for (d,) in conn.execute("SELECT data_json FROM events WHERE type='lane_finished' AND substr(ts,1,10)=?",
                             (db.now_iso()[:10],)):
        try:
            usd += float(json.loads(d or "{}").get("cost_usd") or 0)
        except (TypeError, ValueError):
            pass
    return round(usd, 2)


def over_budget(conn, cfg=None) -> bool:
    cap = float(((cfg or config.load()).get("budget") or {}).get("daily_usd") or 0)
    return bool(cap) and spent_today(conn) >= cap


def _run_ai_lane(engine, lane, run_id, cfg, emit, stop, db_path, results, started=False, t0=None):
    if not started:
        emit(lane, "lane_started", "Starting %s" % lane)
    t0 = t0 or time.time()
    if lane != "submit" and over_budget(emit.conn(), cfg):
        # never blocks the submit lane: that is Akshay's own click on forms already paid for
        data = {"status": "skipped", "counts": _lane_counts(emit.conn(), run_id, lane), "elapsed_s": 0,
                "reason": "daily AI budget reached"}
        emit(lane, "warning", "%s skipped: today's AI budget ($%s, Settings) is used up — free checks continue,"
             " AI lanes resume tomorrow" % (lane, (cfg.get("budget") or {}).get("daily_usd")), "warn")
        emit(lane, "lane_finished", "%s skipped" % lane, "warn", data)
        results[lane] = data
        return
    status, extra = "done", {}
    try:
        res = engine.run_lane(run_id, lane, cfg, emit.for_engine(lane), stop) or {}
        status = res.get("status") or "done"
        extra = {k: res.get(k) for k in ("cost_usd", "error") if res.get(k) is not None}
        eng_counts = res.get("counts") or {}
    except Exception as e:
        status, eng_counts = "error", {}
        extra["error"] = "%s: %s" % (type(e).__name__, e)
        emit(lane, "error", "Lane crashed: %s" % extra["error"], "error", {"traceback": traceback.format_exc()[-2000:]})
    if stop.is_set() and status == "done":
        status = "stopped"
    counts = dict(eng_counts)
    counts.update(_lane_counts(emit.conn(), run_id, lane))
    data = {"status": status, "counts": counts, "elapsed_s": round(time.time() - t0, 1), **extra}
    level = {"done": "success", "error": "error"}.get(status, "warn")
    emit(lane, "lane_finished", "%s %s" % (lane, status), level, data)
    results[lane] = data


def _release_unsubmitted(emit, run_id, before=None, reason="the submit run ended before this one was sent"):
    """Approved rows the submit lane did not finish go back to Akshay's list (still approvable), so the
    dispatcher never loops on them. Rows approved after `before` (while the lane was running) are kept:
    the dispatcher starts another submit run for them."""
    from . import actions
    conn = emit.conn()
    for item in actions.submit_queue(conn):
        if before and (item.get("approved_at") or "") > before:
            continue
        actions.mark_submit_failed(conn, run_id, "submit", item["id"], reason, still_ready=True)


def _submit_pending(emit) -> int:
    from . import actions
    return actions.pending_approvals(emit.conn())


def _run_himalayas(engine, run_id, cfg, emit, stop, db_path, results):
    """Remote boards lane (lane id kept as "himalayas"): Himalayas + Jobicy + Hacker News hiring threads +
    company career pages (Greenhouse/Lever/Ashby). Plain HTTP, no AI; only new jobs that pass the rules go to
    the AI for contacts."""
    from . import boards
    lane = "himalayas"
    t0 = time.time()
    emit(lane, "lane_started", "Fetching remote Salesforce jobs: Himalayas, Jobicy, Hacker News, company career pages")
    try:
        res = boards.collect(emit.conn(), run_id, lane, stop=stop)
    except Exception as e:
        res = {"targets": [], "counts": {}, "errors": ["%s: %s" % (type(e).__name__, e)]}
    c = res.get("counts") or {}
    for err in res.get("errors") or []:
        emit(lane, "warning", err, "warn")
    by = ", ".join("%s %d" % (boards.LABEL.get(k, k).split(" (")[0], n) for k, n in (c.get("by_source") or {}).items())
    emit(lane, "step", "Remote boards: %d jobs read, %d Salesforce + remote + open to India, %d new%s, %d already handled,"
         " %d skipped by rules, %d career pages checked"
         % (c.get("fetched", 0), c.get("saved", 0) + c.get("seen", 0) + c.get("screened_out", 0), c.get("saved", 0),
            (" (%s)" % by) if by else "", c.get("seen", 0), c.get("screened_out", 0), c.get("boards", 0)),
         "success" if c.get("saved") else "info", {"counts": c})
    targets = res.get("targets") or []
    if not targets or stop.is_set():
        errs = res.get("errors") or []
        status = "stopped" if stop.is_set() else ("error" if errs and not c.get("fetched") else "done")
        data = {"status": status, "counts": _lane_counts(emit.conn(), run_id, lane), "elapsed_s": round(time.time() - t0, 1)}
        if errs:
            data["error"] = "; ".join(errs)[:300]
        emit(lane, "lane_finished", "%s %s" % (lane, status), {"done": "success", "error": "error"}.get(status, "warn"), data)
        results[lane] = data
        return
    _run_ai_lane(engine, lane, run_id, dict(cfg, _targets=targets), emit, stop, db_path, results, started=True, t0=t0)


def _ext_id(url):
    import re
    m = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d{6,15})", url or "")
    return m.group(1) if m else None


def _run_li_lane(engine, lane, run_id, cfg, emit, stop, db_path, results):
    """LinkedIn Jobs / Easy Apply: pre-screen in Python first (public list + JD + skip rules), so the AI only
    gets jobs worth working. Public list unreadable -> the lane runs the old way."""
    from . import liprefetch
    if _mock_mode(cfg) or cfg.get("prescreen") is False:
        return _run_ai_lane(engine, lane, run_id, cfg, emit, stop, db_path, results)
    t0 = time.time()
    emit(lane, "lane_started", "Starting %s" % lane)
    watch_t = (cfg.get("_targets") or []) if lane == "easy_apply" else []
    extra = None
    if watch_t:
        extra = [{"ext_id": _ext_id(t.get("url")), "title": t.get("title") or "", "company": t.get("company") or "",
                  "location": t.get("location") or "", "url": t.get("url"), "region": t.get("region")}
                 for t in watch_t if isinstance(t, dict) and _ext_id(t.get("url"))]
    emit(lane, "step", "Checking LinkedIn's public job list and JDs first (no AI) — only good new jobs go to the AI")
    try:
        res = liprefetch.collect(emit.conn(), run_id, lane, regions=cfg.get("_regions") or list(REGIONS),
                                 lookback=cfg.get("lookback", "24h"), extra_cards=extra,
                                 emit=lambda m: emit(lane, "warning", m, "warn"))
    except Exception as e:
        emit(lane, "warning", "Pre-screen failed (%s: %s) — the AI will search itself" % (type(e).__name__, e), "warn")
        res = None
    if res is None:
        emit(lane, "step", "LinkedIn's public list is not reachable right now — the AI searches LinkedIn itself this time")
        return _run_ai_lane(engine, lane, run_id, cfg, emit, stop, db_path, results, started=True, t0=t0)
    c, why = res["counts"], res.get("reasons") or {}
    top = ", ".join("%d %s" % (n, k) for k, n in sorted(why.items(), key=lambda x: -x[1])[:4])
    emit(lane, "step", "Pre-screen: %d Salesforce jobs, %d already handled earlier, %d skipped by rules%s, %d new for the AI%s"
         % (c["cards"] - c["not_salesforce"], c["seen"], c["screened_out"] + c["closed"], (" (%s)" % top) if top else "",
            c["saved"], (" + %d retries" % c["retry"]) if c.get("retry") else ""),
         "success" if res["targets"] else "info", {"counts": c, "reasons": why})
    targets = res["targets"]
    if not targets or stop.is_set():
        status = "stopped" if stop.is_set() else "done"
        data = {"status": status, "counts": _lane_counts(emit.conn(), run_id, lane), "elapsed_s": round(time.time() - t0, 1),
                "reason": "nothing new worth the AI's time"}
        emit(lane, "lane_finished", "%s %s (no AI needed)" % (lane, status), "success" if status == "done" else "warn", data)
        results[lane] = data
        return
    _run_ai_lane(engine, lane, run_id, dict(cfg, _targets=targets), emit, stop, db_path, results, started=True, t0=t0)


def _run_agencies(engine, run_id, cfg, emit, stop, db_path, results):
    """Agency pitches: pick agencies in Python (seed sheet + agency-like companies from your jobs), then the AI finds
    1-2 recruiters per agency and sends the C2C pitch. Only runs when "Agencies" is ticked."""
    from . import agencies
    lane = "agencies"
    t0 = time.time()
    acfg = cfg.get("agencies") if isinstance(cfg.get("agencies"), dict) else {}
    try:
        per_run = max(1, min(20, int(acfg.get("per_run") or 5)))
    except (TypeError, ValueError):
        per_run = 5
    track = {"india": "in India", "all": "in India and abroad"}.get(acfg.get("focus"), "outside India")
    emit(lane, "lane_started", "Picking %d agencies %s to pitch" % (per_run, track))
    try:
        tg = agencies.targets(emit.conn(), run_id, limit=per_run, focus=acfg.get("focus") or "outside", lane=lane)
    except Exception as e:
        tg = []
        emit(lane, "warning", "Could not pick agencies: %s: %s" % (type(e).__name__, e), "warn")
    if tg:
        emit(lane, "step", "Agencies to pitch: " + ", ".join("%s (%s)" % (t["name"], t["region"]) for t in tg), "info",
             {"agencies": [t["agency_id"] for t in tg]})
    if not tg or stop.is_set():
        status = "stopped" if stop.is_set() else "done"
        data = {"status": status, "counts": _lane_counts(emit.conn(), run_id, lane), "elapsed_s": round(time.time() - t0, 1),
                "reason": "no agency due for a pitch"}
        emit(lane, "lane_finished", "%s %s" % (lane, status), "success" if status == "done" else "warn", data)
        results[lane] = data
        return
    _run_ai_lane(engine, lane, run_id, dict(cfg, _targets=tg), emit, stop, db_path, results, started=True, t0=t0)


def _run_board_apply(engine, run_id, cfg, emit, stop, db_path, results):
    """Job board applications: the listings Akshay (or auto-fill) queued. Python turns each into a saved job;
    LinkedIn Easy Apply ones go to the Easy Apply lane, every other one to the board_apply AI lane. Only runs when
    ticked or started from the Job board."""
    from . import jobboard
    lane = "board_apply"
    t0 = time.time()
    jb = cfg.get("jobboard") if isinstance(cfg.get("jobboard"), dict) else {}
    try:
        per_run = max(1, min(15, int(jb.get("per_run") or 5)))
    except (TypeError, ValueError):
        per_run = 5
    emit(lane, "lane_started", "Filling job board applications")
    try:
        # the board_apply prompt caps a run at BOARD_FORMS_CAP forms: claiming more would burn tries on jobs the AI
        # never opens (and mark them "could not be filled")
        ea, forms, notes = jobboard.claim(emit.conn(), run_id, limit=min(per_run, BOARD_FORMS_CAP), cfg=cfg)
    except Exception as e:
        ea, forms, notes = [], [], []
        emit(lane, "warning", "Could not pick job board jobs: %s: %s" % (type(e).__name__, e), "warn")
        try:   # never leave the queue as it was: the watcher would restart this run on every tick
            jobboard.release_queue(emit.conn(), "failed", "Could not start filling (%s) — click Fill to retry"
                                   % type(e).__name__)
        except Exception:
            pass
    since = db.now_iso()
    for n in notes:
        emit(lane, "step", "Skipped %s" % n, "info")
    ids = [t["listing_id"] for t in ea + forms]
    if ids:
        emit(lane, "step", "Job board: %d to fill (%d LinkedIn Easy Apply, %d portal / email)" % (len(ids), len(ea), len(forms)),
             "info", {"listings": ids})
    try:
        if not ids or stop.is_set():
            if stop.is_set():
                _release_board_queue(emit, "new", "Stopped before it was filled")
            status = "stopped" if stop.is_set() else "done"
            data = {"status": status, "counts": _lane_counts(emit.conn(), run_id, lane), "elapsed_s": round(time.time() - t0, 1),
                    "reason": "nothing queued on the Job board"}
            emit(lane, "lane_finished", "%s %s" % (lane, status), "success" if status == "done" else "warn", data)
            results[lane] = data
            return
        if forms:
            _run_ai_lane(engine, lane, run_id, dict(cfg, _targets=forms), emit, stop, db_path, results, started=True, t0=t0)
        else:
            data = {"status": "done", "counts": _lane_counts(emit.conn(), run_id, lane), "elapsed_s": round(time.time() - t0, 1)}
            emit(lane, "lane_finished", "%s done (Easy Apply only)" % lane, "success", data)
            results[lane] = data
        if ea and cfg.get("linkedin_safe", True):
            # account safety: no automatic LinkedIn runs; he applies to these himself (one click on LinkedIn)
            c = emit.conn()
            for t in ea:
                c.execute("UPDATE listings SET status='new', note=? WHERE id=?",
                          ("LinkedIn Easy Apply: open the job page and apply yourself (automatic LinkedIn is off)", t["listing_id"]))
            emit(lane, "step", "%d LinkedIn Easy Apply job(s) left for you to apply on LinkedIn (automatic LinkedIn is off)"
                 % len(ea), "info")
            ea = []
        if ea and not stop.is_set():
            _run_ai_lane(engine, "easy_apply", run_id, dict(cfg, _targets=ea), emit, stop, db_path, results)
    finally:
        try:
            jobboard.finish(emit.conn(), run_id, ids, since=since, stopped=stop.is_set())
            if stop.is_set():
                _release_board_queue(emit, "new", "Stopped before it was filled")
        except Exception as e:
            emit(lane, "warning", "Job board bookkeeping failed: %s" % e, "warn")


def _release_board_queue(emit, status, note):
    """Queued Job board rows a run could not work go back to the list (like _release_unsubmitted), so the
    watcher's "queued -> start board_apply" rule cannot restart runs forever (Stop pressed, AI engine missing)."""
    from . import jobboard
    try:
        n = jobboard.release_queue(emit.conn(), status, note)
        if n:
            emit("board_apply", "step", "%d Job board application%s back on your list: %s"
                 % (n, "" if n == 1 else "s", note), "info")
    except Exception as e:
        emit("board_apply", "warning", "Job board queue release failed: %s" % e, "warn")


def _run_source_lane(engine, lane, run_id, cfg, emit, stop, db_path, results):
    if lane == "himalayas":
        return _run_himalayas(engine, run_id, cfg, emit, stop, db_path, results)
    if lane == "linkedin_jobs":
        return _run_li_lane(engine, lane, run_id, cfg, emit, stop, db_path, results)
    return _run_ai_lane(engine, lane, run_id, cfg, emit, stop, db_path, results)


def _skip_lane(emit, lane, reason, results, level="warn"):
    emit(lane, "warning", "%s skipped: %s" % (lane, reason), level)
    data = {"status": "skipped", "counts": {}, "reason": reason}
    emit(lane, "lane_finished", "%s skipped" % lane, level, data)
    results[lane] = data


def _portal_candidates(conn, run_id) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM jobs j WHERE j.run_id=? AND j.status IN ('qualified','contacted') AND"
        " (j.apply_url LIKE ? OR j.apply_url LIKE ? OR j.apply_url LIKE ?) AND NOT EXISTS"
        " (SELECT 1 FROM portal_apps p WHERE p.job_id=j.id)", (run_id,) + ATS_LIKE).fetchone()[0]


def _mock_mode(cfg) -> bool:
    import os
    return bool((cfg.get("engine") or {}).get("mock")) or os.environ.get("JOBHUNTER_MOCK_ENGINE") == "1"


def _run_main(run_id, trigger, cfg, db_path, stop):
    emit = _Emitter(db_path, run_id)
    results = {}
    sel = cfg.get("_run_lanes")          # None = full default run
    # a full default run leaves out the lanes the data says don't bring calls (architect review 2026-10-22):
    # JobRight (0 human replies, jobs up to 6 days old) and the Remote-boards email lane (jobs 3-4 days old; the
    # same boards now feed the Job board for free). Both still run when picked.
    chosen0 = (lambda ln: ln in sel if sel is not None else ln not in FULL_RUN_OFF)
    # LinkedIn account safety: a run Akshay did not click himself (app open, Autopilot, Job board) never touches
    # LinkedIn; the LinkedIn lanes run only from his Run button
    li_block = bool(cfg.get("linkedin_safe", True)) and trigger not in ("button",)
    li_fast_ok = trigger == "li_fast" and bool((cfg.get("li_fast") or {}).get("on"))   # his standing instruction
    chosen = (lambda ln: chosen0(ln) and not (li_block and ln in LINKEDIN_LANES
                                               and not (li_fast_ok and ln == "easy_apply")))
    NOT_SEL = "not selected for this run"
    t0 = time.time()
    final = "done"
    side = bool(db_path) and cfg.get("profile_id") not in (None, "", "akshay")
    if side:                               # another person's run, in his own database: show it live too
        events.start_side_tailer(db_path)
    try:
        emit(None, "run_started", "Run %s started (%s)" % (run_id, trigger), "info",
             {"trigger": trigger, "dry_run": bool(cfg.get("dry_run")), "parallel_sources": bool(cfg.get("parallel_sources")),
              "lanes": sel or "all", "regions": cfg.get("_regions") or list(REGIONS),
              "targets": len(cfg.get("_targets") or []), "quick": bool(cfg.get("_quick"))})

        def fu():
            emit("followups", "lane_started", "Checking replies, bounces and due nudges")
            t1 = time.time()
            try:
                counts = followups.run_followups(emit.conn(), run_id, cfg, emit, stop)
                if counts.get("skipped_reason"):
                    status = "skipped"
                elif counts.get("error"):
                    status = "error"
                else:
                    status = "stopped" if stop.is_set() else "done"
            except Exception as e:
                counts, status = {"error": str(e)}, "error"
                emit("followups", "error", "Follow-ups crashed: %s" % e, "error",
                     {"traceback": traceback.format_exc()[-2000:]})
            data = {"status": status, "counts": counts, "elapsed_s": round(time.time() - t1, 1)}
            emit("followups", "lane_finished", "followups %s" % status,
                 {"done": "success", "error": "error"}.get(status, "warn"), data)
            results["followups"] = data

        fu_thread = threading.Thread(target=fu, name="jh-followups-%s" % run_id, daemon=True)
        if chosen("followups"):
            fu_thread.start()
        else:
            _skip_lane(emit, "followups", NOT_SEL, results, "info")
        src_lanes = [ln for ln in SOURCE_LANES if chosen(ln)]

        try:
            engine = _load_engine()
            if not hasattr(engine, "run_lane"):
                raise ImportError("jh.engine has no run_lane()")
        except Exception as e:
            engine = None
            reason = "AI engine not available (%s: %s)" % (type(e).__name__, e)
            for lane in SOURCE_LANES + ["outreach", "agencies", "portal", "easy_apply", "board_apply", "submit"]:
                if lane in SOURCE_LANES + ["agencies", "portal", "easy_apply", "board_apply", "submit"] and not chosen(lane):
                    _skip_lane(emit, lane, NOT_SEL, results, "info")
                elif lane == "outreach" and not src_lanes:
                    _skip_lane(emit, lane, "no source lane selected", results, "info")
                else:
                    _skip_lane(emit, lane, reason, results)
            if sel is not None and "submit" in sel:
                _release_unsubmitted(emit, run_id, reason="the AI engine is not available")
            if sel is not None and "board_apply" in sel:
                _release_board_queue(emit, "failed", "Not filled: %s" % reason)

        if engine is not None:
            # Easy Apply first: being early on a fresh posting matters most (it used to run last, after up to an
            # hour of the other lanes)
            if not chosen("easy_apply"):
                _skip_lane(emit, "easy_apply", NOT_SEL, results, "info")
            elif stop.is_set():
                _skip_lane(emit, "easy_apply", "run stopped", results, "info")
            elif sel is None and not cfg.get("easy_apply", True):
                _skip_lane(emit, "easy_apply", "Easy Apply prefill is turned off in Settings", results, "info")
            else:
                _run_li_lane(engine, "easy_apply", run_id, cfg, emit, stop, db_path, results)
            for ln in SOURCE_LANES:
                if not chosen(ln):
                    _skip_lane(emit, ln, NOT_SEL, results, "info")
            if src_lanes:
                emit("outreach", "lane_started", "Outreach runs inside each source lane as jobs qualify")
                if cfg.get("parallel_sources"):
                    ths = [threading.Thread(target=_run_source_lane, args=(engine, ln, run_id, cfg, emit, stop, db_path, results),
                                            name="jh-lane-%s" % ln, daemon=True) for ln in src_lanes]
                    for th in ths:
                        th.start()
                    for th in ths:
                        th.join()
                else:
                    for ln in src_lanes:
                        if stop.is_set():
                            _skip_lane(emit, ln, "run stopped", results, "info")
                            continue
                        _run_source_lane(engine, ln, run_id, cfg, emit, stop, db_path, results)
                oc = {"sent": 0, "dry_run": 0, "failed": 0}
                for ln in SOURCE_LANES + ["outreach"]:
                    lc = _lane_counts(emit.conn(), run_id, ln)
                    for k in oc:
                        oc[k] += lc[k]
                odata = {"status": "stopped" if stop.is_set() else "done", "counts": oc}
                emit("outreach", "lane_finished", "outreach: %d sent, %d dry run, %d failed"
                     % (oc["sent"], oc["dry_run"], oc["failed"]), "success", odata)
                results["outreach"] = odata
            else:
                _skip_lane(emit, "outreach", "no source lane selected", results, "info")
            # agencies only when ticked (never part of the default full run)
            if sel is None or "agencies" not in sel:
                _skip_lane(emit, "agencies", "only runs when you tick Agencies", results, "info")
            elif stop.is_set():
                _skip_lane(emit, "agencies", "run stopped", results, "info")
            else:
                _run_agencies(engine, run_id, cfg, emit, stop, db_path, results)
            if not chosen("portal"):
                _skip_lane(emit, "portal", NOT_SEL, results, "info")
            elif stop.is_set():
                _skip_lane(emit, "portal", "run stopped", results, "info")
            elif sel is not None or _portal_candidates(emit.conn(), run_id) or _mock_mode(cfg):
                # picked explicitly -> run it (it also works through older jobs via list_portal_candidates)
                _run_ai_lane(engine, "portal", run_id, cfg, emit, stop, db_path, results)
            else:
                _skip_lane(emit, "portal", "no Greenhouse/Lever/Ashby jobs this run", results, "info")
            # Job board applications: only when picked (the Job board / auto-fill starts runs with this lane)
            if sel is None or "board_apply" not in sel:
                _skip_lane(emit, "board_apply", "only runs from the Job board", results, "info")
            elif stop.is_set():
                _skip_lane(emit, "board_apply", "run stopped", results, "info")
                _release_board_queue(emit, "new", "Stopped before it was filled")
            else:
                _run_board_apply(engine, run_id, cfg, emit, stop, db_path, results)
            # submit only runs when picked explicitly (Akshay's "Submit selected" click), never in a default run
            if sel is None or "submit" not in sel:
                _skip_lane(emit, "submit", "only runs when you click Submit selected", results, "info")
            elif stop.is_set():
                _skip_lane(emit, "submit", "run stopped", results, "info")
                _release_unsubmitted(emit, run_id, reason="the run was stopped before submitting")
            elif not _submit_pending(emit):
                _skip_lane(emit, "submit", "nothing approved to submit", results, "info")
            else:
                lane_start = db.now_iso()
                _run_ai_lane(engine, "submit", run_id, cfg, emit, stop, db_path, results)
                _release_unsubmitted(emit, run_id, before=lane_start)

        while fu_thread.is_alive():
            fu_thread.join(0.5)
        # lane errors are shown per lane; the run itself still completed
        final = "stopped" if stop.is_set() else "done"
    except Exception as e:
        final = "error"
        try:
            emit(None, "error", "Run crashed: %s" % e, "error", {"traceback": traceback.format_exc()[-2000:]})
        except Exception:
            pass
    finally:
        try:
            conn = emit.conn()
            stats = _run_stats(conn, run_id, results, time.time() - t0)
            conn.execute("UPDATE runs SET finished_at=?, status=?, stats_json=? WHERE id=?",
                         (db.now_iso(), final, json.dumps(stats), run_id))
            emit(None, "run_finished", "Run %s %s: %d sent, %d dry run, %d nudges, %d replies"
                 % (run_id, final, stats["sent"], stats["dry_run"], stats["nudges"], stats["replies"]),
                 "success" if final == "done" else "warn", {"status": final, "stats": stats})
        except Exception:
            pass
        if side:
            events.stop_side_tailer()


def _run_stats(conn, run_id, results, elapsed) -> dict:
    def n(sql, *a):
        return conn.execute(sql, a).fetchone()[0]
    stats = {
        "lanes": results,
        "sent": n("SELECT COUNT(*) FROM emails WHERE run_id=? AND kind='initial' AND status IN ('sent','bounced')", run_id),
        "dry_run": n("SELECT COUNT(*) FROM emails WHERE run_id=? AND kind='initial' AND status='dry_run'", run_id),
        "failed": n("SELECT COUNT(*) FROM emails WHERE run_id=? AND status='failed'", run_id),
        "nudges": n("SELECT COUNT(*) FROM emails WHERE run_id=? AND kind IN ('nudge1','nudge2') AND status='sent'", run_id),
        "replies": n("SELECT COUNT(*) FROM events WHERE run_id=? AND type='reply_found'", run_id),
        "bounces": n("SELECT COUNT(*) FROM events WHERE run_id=? AND type='bounce_found'", run_id),
        "jobs_found": n("SELECT COUNT(*) FROM events WHERE run_id=? AND type IN ('job_qualified','job_skipped')", run_id),
        "qualified": n("SELECT COUNT(*) FROM events WHERE run_id=? AND type='job_qualified'", run_id),
        "skipped": n("SELECT COUNT(*) FROM events WHERE run_id=? AND type='job_skipped'", run_id),
        "portal_ready": n("SELECT COUNT(*) FROM events WHERE run_id=? AND type='portal_ready'", run_id),
        "duration_s": round(elapsed, 1),
    }
    stats["per_hour"] = db.per_hour(conn, run_id)
    return stats
