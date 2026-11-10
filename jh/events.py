"""Event bus: events table + in-memory fan-out to SSE subscribers.

* emit() inserts the row and publishes it to every subscriber queue.
* A DB tailer thread (start_db_tailer) picks up rows written by other processes
  (the MCP server) and publishes them too. Both paths share one lock and a set of
  already-published ids, so nothing is delivered twice.
* `stats` events are published in memory only (not stored, id = None) by the server's
  stats pump whenever a KPI-relevant event went by (see stats_dirty).
"""
import json
import os
import queue
import threading
import time

from . import db

_lock = threading.RLock()
_subs = []                 # list[queue.Queue]
_published = set()         # ids published in-process and not yet passed by the tailer
_tail_last_id = 0
_tailer = None
_tailer_stop = threading.Event()
_main_path = None          # the tailed (Akshay's) database; another profile's run writes to its own database
_side = {"thread": None, "stop": threading.Event()}
_paths = {}                # id(conn) -> database file of that connection

stats_dirty = threading.Event()
KPI_TYPES = {"run_started", "run_finished", "lane_finished", "job_found", "job_qualified", "job_skipped",
             "contact_found", "email_sent", "email_failed", "nudge_sent", "reply_found", "bounce_found",
             "portal_ready"}
QUEUE_MAX = 2000


def subscribe() -> queue.Queue:
    q = queue.Queue(maxsize=QUEUE_MAX)
    with _lock:
        _subs.append(q)
    return q


def unsubscribe(q):
    with _lock:
        try:
            _subs.remove(q)
        except ValueError:
            pass


def subscriber_count() -> int:
    with _lock:
        return len(_subs)


def publish(ev: dict):
    """Deliver an event dict to all subscribers (drops the oldest item if a queue is full)."""
    with _lock:
        subs = list(_subs)
    for q in subs:
        try:
            q.put_nowait(ev)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(ev)
            except (queue.Empty, queue.Full):
                pass
    if ev.get("type") in KPI_TYPES:
        stats_dirty.set()


def _db_file(conn):
    try:
        return os.path.realpath(conn.execute("PRAGMA database_list").fetchone()[2] or "")
    except Exception:
        return ""


def _other_db(conn) -> bool:
    """True for a connection to another profile's database while the main tailer runs (ids/run ids differ there)."""
    if _tailer is None or not _main_path:
        return False
    f = _db_file(conn)
    return bool(f) and f != _main_path


def _side_copy(ev):
    """Live copy of another profile's event: no id / run id (they belong to his database, not the dashboard's)."""
    return dict(ev, id=None, run_id=None, data=dict(ev.get("data") or {}, other_profile=True))


def emit(conn, run_id, lane, type, message, level="info", data=None) -> dict:
    """Insert an event row and fan it out. Returns the full event dict (SPEC §5)."""
    ts = db.now_iso()
    data = data or {}
    message = "" if message is None else str(message)
    with _lock:
        cur = conn.execute(
            "INSERT INTO events (ts, run_id, lane, level, type, message, data_json) VALUES (?,?,?,?,?,?,?)",
            (ts, run_id, lane, level, type, message, json.dumps(data, default=str)))
        ev = {"id": cur.lastrowid, "ts": ts, "run_id": run_id, "lane": lane, "level": level,
              "type": type, "message": message, "data": data}
        other = _other_db(conn)
        if not other and _tailer is not None and ev["id"] > _tail_last_id:
            _published.add(ev["id"])
        if other and _side.get("path") and _db_file(conn) == _side.get("path"):
            _side_seen.add(ev["id"])          # the side tailer must not publish it a second time
    publish(_side_copy(ev) if other else ev)
    return ev


def publish_stats(summary: dict):
    publish({"id": None, "ts": db.now_iso(), "run_id": summary.get("last_run", {}).get("id")
             if isinstance(summary.get("last_run"), dict) else None,
             "lane": None, "level": "debug", "type": "stats", "message": "", "data": summary})


def _tail_once(conn):
    global _tail_last_id
    with _lock:
        rows = conn.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT 1000", (_tail_last_id,)).fetchall()
        fresh = []
        for r in rows:
            if r["id"] in _published:
                _published.discard(r["id"])
            else:
                fresh.append(db.event_row_to_dict(r))
            _tail_last_id = max(_tail_last_id, r["id"])
        # ids at/below the tail mark can never be seen again
        for i in [i for i in _published if i <= _tail_last_id]:
            _published.discard(i)
    for ev in fresh:
        publish(ev)
    return len(fresh)


def start_db_tailer(db_path=None, interval=0.5):
    """Start (once) the thread that publishes events written by other processes."""
    global _tailer, _tail_last_id
    with _lock:
        if _tailer is not None and _tailer.is_alive():
            return _tailer
        _tailer_stop.clear()
        conn = db.connect(db_path)
        global _main_path
        _main_path = _db_file(conn)
        _tail_last_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        _published.clear()

        def loop():
            while not _tailer_stop.is_set():
                try:
                    _tail_once(conn)
                except Exception:  # keep tailing through transient DB errors
                    time.sleep(1)
                _tailer_stop.wait(interval)
            conn.close()

        _tailer = threading.Thread(target=loop, name="jh-event-tailer", daemon=True)
        _tailer.start()
        return _tailer


def stop_db_tailer():
    global _tailer
    _tailer_stop.set()
    t = _tailer
    if t is not None:
        t.join(timeout=3)
    with _lock:
        _tailer = None
        _published.clear()


def start_side_tailer(db_path, interval=0.7):
    """While another profile's run works in its own database: publish the rows its AI tool server writes there
    (jobs saved, applications submitted) live, as side copies. stop_side_tailer() when the run ends."""
    stop_side_tailer()
    stop = threading.Event()
    conn = db.connect(db_path)
    last = [conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]]

    def loop():
        while not stop.is_set():
            try:
                rows = conn.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT 500", (last[0],)).fetchall()
                for r in rows:
                    last[0] = max(last[0], r["id"])
                    ev = db.event_row_to_dict(r)
                    with _lock:
                        dup = ev["id"] in _side_seen
                        _side_seen.discard(ev["id"])
                    if not dup:
                        publish(_side_copy(ev))
            except Exception:
                time.sleep(1)
            stop.wait(interval)
        conn.close()

    th = threading.Thread(target=loop, name="jh-side-tailer", daemon=True)
    _side.update(thread=th, stop=stop, path=os.path.realpath(str(db_path)))
    th.start()
    return th


def stop_side_tailer():
    t, stop = _side.get("thread"), _side.get("stop")
    if stop is not None:
        stop.set()
    if t is not None and t.is_alive():
        t.join(timeout=3)
    _side.update(thread=None, path=None)
    with _lock:
        _side_seen.clear()


_side_seen = set()          # ids of the side database already published in-process (runner thread)
