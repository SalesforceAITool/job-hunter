"""Outbox (27 Nov): every recruiter email and follow-up leaves from here — one at a time, spaced out, spread over
Akshay's Gmail accounts, and never into a Gmail "sending limit" wall.

The AI lanes (posts, agencies, Easy Apply recruiter mails ...) and the follow-up lane only QUEUE a message: the
email row is written with status 'sending' and queued=1 (so every "already contacted" check counts it at once) and the
tailored PDF is already built. The watcher calls tick() every 15 s in the app process; tick() sends at most one
message when sendguard.pick() finds an account that is not paused, under its caps and not used a moment ago.

  * initial emails go out from any free account (least used today first)
  * a follow-up must come from the account that sent the first email of that thread (replies land there)
  * Gmail "limit" answer -> that account pauses 24 h, the message waits (it is NOT marked failed)
  * wrong app password  -> that account pauses 6 h, the message waits
  * recipient refused (550 5.1.1 user unknown) -> 'failed', the address is remembered as dead, no nudges
  * anything else       -> retried up to 5 times, 15 min apart, then 'failed'
"""
import logging
import os
import time
from datetime import timedelta

from . import db, events, mailer, sendguard

log = logging.getLogger("jobhunter.outbox")
MAX_ATTEMPTS = 5
RETRY_MIN = 15
WAIT_MIN = 30          # paused / capped: look again after this long


def queue_mode(cfg) -> bool:
    em = (cfg or {}).get("email") or {}
    return em.get("backend", "smtp") == "smtp" and em.get("queue", True) is not False


def _later(minutes):
    return (db.now() + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def pending(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM emails WHERE status='sending' AND COALESCE(queued,0)=1").fetchone()[0]


def pending_rows(conn, limit=30):
    now = db.now_iso()
    return conn.execute(
        "SELECT e.*, c.email AS to_addr, c.name AS contact_name, j.company, j.role FROM emails e"
        " JOIN contacts c ON c.id=e.contact_id LEFT JOIN jobs j ON j.id=e.job_id"
        " WHERE e.status='sending' AND COALESCE(e.queued,0)=1 AND (e.next_try IS NULL OR e.next_try<=?)"
        " ORDER BY CASE WHEN e.kind='initial' THEN 0 ELSE 1 END, e.id LIMIT ?", (now, limit)).fetchall()


def _label(row):
    return {"initial": "Email", "nudge1": "Nudge 1", "nudge2": "Nudge 2"}.get(row["kind"], row["kind"])


def requeue_limit_failures(conn, days=4) -> int:
    """Once (27 Nov): applications Gmail refused with "5.4.5 sending limit" were marked failed (or stuck at 'sending')
    and never retried - put the recent ones back in the outbox."""
    if sendguard._get(conn, "requeue_limit_v1"):
        return 0
    since = (db.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    n = conn.execute("UPDATE emails SET status='sending', queued=1, next_try=NULL, attempts=0 WHERE kind='initial' AND"
                     " status IN ('failed','sending') AND COALESCE(queued,0)=0 AND sent_at>=? AND (error LIKE '%5.4.5%'"
                     " OR error LIKE '%sending limit%')", (since,)).rowcount
    sendguard._set(conn, "requeue_limit_v1", db.now_iso())
    return n


def tick(conn, cfg, now=None, send_fn=None) -> dict:
    """Send at most one queued message. -> {"sent": email_id} | {"waiting": reason} | {} (nothing queued)."""
    now = time.time() if now is None else now
    send_fn = send_fn or mailer.send
    requeue_limit_failures(conn)
    rows = pending_rows(conn)
    if not rows:
        return {}
    last_why = ""
    for row in rows:
        prefer = None if row["kind"] == "initial" else sendguard.sender_of(row, cfg)
        if row["kind"] != "initial" and not prefer:
            conn.execute("UPDATE emails SET status='failed', queued=0, error=? WHERE id=?",
                         ("the account that sent the first email is unknown - nudge not sent", row["id"]))
            continue
        acct, why = sendguard.pick(conn, cfg, prefer=prefer, now=now)
        if not acct:
            last_why = why
            if row["kind"] == "initial":
                break                    # no account for new mail now; nudges may still have theirs free
            continue
        return _send_one(conn, cfg, row, acct, now, send_fn)
    return {"waiting": last_why or "no account free right now"}


def _send_one(conn, cfg, row, acct, now, send_fn):
    addr = acct["address"]
    attach = row["resume_path"] if row["kind"] == "initial" and row["resume_path"] and os.path.isfile(row["resume_path"]) \
        else None
    irt = row["in_reply_to"] or None
    try:
        mid = send_fn(sendguard.acct_cfg(cfg, acct), row["to_addr"], row["subject"], row["body"], attach,
                      in_reply_to=irt, references=irt)
    except Exception as e:
        err = str(e)
        if getattr(e, "maybe_sent", False):     # Gmail may have it: never send twice
            conn.execute("UPDATE emails SET status='sent', queued=0, sender=?, sent_at=?, error=? WHERE id=?",
                         (addr, db.now_iso(), (err + " - may have been delivered; not retried")[:1000], row["id"]))
            sendguard.mark_sent(conn, addr, now)
            return {"sent": row["id"], "maybe": True}
        kind = sendguard.on_send_error(conn, cfg, addr, err)
        if kind in ("limit", "auth"):
            conn.execute("UPDATE emails SET next_try=?, error=? WHERE id=?", (_later(WAIT_MIN), err[:1000], row["id"]))
            events.emit(conn, None, "outbox", "warning", "%s paused (%s); %s to %s waits in the outbox" % (
                addr, "Gmail sending limit" if kind == "limit" else "login refused", _label(row), row["to_addr"]), "warn",
                {"email_id": row["id"], "account": addr})
            return {"waiting": "%s paused" % addr}
        if kind == "dead":
            conn.execute("UPDATE emails SET status='failed', queued=0, sender=?, error=? WHERE id=?",
                         (addr, err[:1000], row["id"]))
            conn.execute("UPDATE contacts SET stop_nudges=1 WHERE id=?", (row["contact_id"],))
            sendguard._set(conn, "dead:" + (row["to_addr"] or "").lower(), "smtp refused")
            events.emit(conn, None, "outbox", "email_failed", "%s to %s refused by the mail server (address does not"
                        " exist)" % (_label(row), row["to_addr"]), "warn", {"email_id": row["id"]})
            return {"failed": row["id"]}
        attempts = int(row["attempts"] or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            conn.execute("UPDATE emails SET status='failed', queued=0, attempts=?, error=? WHERE id=?",
                         (attempts, err[:1000], row["id"]))
            events.emit(conn, None, "outbox", "email_failed", "%s to %s failed after %d tries: %s"
                        % (_label(row), row["to_addr"], attempts, err[:200]), "error", {"email_id": row["id"]})
            return {"failed": row["id"]}
        conn.execute("UPDATE emails SET attempts=?, next_try=?, error=? WHERE id=?",
                     (attempts, _later(RETRY_MIN), err[:1000], row["id"]))
        return {"waiting": "retry later: %s" % err[:120]}
    ts = db.now_iso()
    conn.execute("UPDATE emails SET status='sent', queued=0, sender=?, message_id=?, sent_at=?, error=NULL WHERE id=?",
                 (addr, mid, ts, row["id"]))
    sendguard.mark_sent(conn, addr, now)
    if row["kind"] == "initial":
        conn.execute("UPDATE jobs SET status='contacted', updated_at=? WHERE id=? AND status IN ('found','qualified','skipped')",
                     (ts, row["job_id"]))
    events.emit(conn, row["run_id"], "outbox", "email_sent" if row["kind"] == "initial" else "nudge_sent",
                "%s sent to %s (%s) from %s" % (_label(row), row["contact_name"] or row["to_addr"], row["company"] or "?",
                                               addr), "success",
                {"email_id": row["id"], "job_id": row["job_id"], "contact_id": row["contact_id"], "kind": row["kind"],
                 "account": addr, "message_id": mid})
    return {"sent": row["id"]}
