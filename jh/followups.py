"""Follow-ups: business-day nudge schedule, reply/auto-reply/bounce detection, nudge sending (SPEC §9).

Schedule: nudge1 when >=1 business day (Mon-Fri) since the initial email with no reply,
nudge2 when >=3 business days since nudge1, then the sequence is done.
"""
import re
from datetime import date, timedelta

from . import config, db, mailer
from . import replies as replies_mod

LANE = "followups"

NUDGE1_TEMPLATES = [
    "Hi {first},\n\nJust following up on my note about the {role} role. Happy to share anything else that helps, "
    "and I can make time on US hours for a quick call.\n\nThanks,\nAkshay Verma",
    "Hi {first},\n\nBringing this back to the top of your inbox. I'm still very interested in the {role} position "
    "at {company}, and my resume is attached to the email below.\n\nThanks,\nAkshay Verma",
    "Hi {first},\n\nQuick follow-up on the {role} role. If it's still open, I'd be glad to walk you through my "
    "Salesforce work on a short call whenever it suits you.\n\nThanks,\nAkshay Verma",
    "Hi {first},\n\nChecking in on my note about the {role} opening at {company}. Let me know if you need anything "
    "else from my side.\n\nThanks,\nAkshay Verma",
]
NUDGE2_TEMPLATES = [
    "Hi {first},\n\nOne last check-in on the {role} role. If the timing isn't right, no worries at all, and thanks "
    "for reading.\n\nThanks,\nAkshay Verma",
    "Hi {first},\n\nI don't want to crowd your inbox, so this is my last note about the {role} position at "
    "{company}. If it moves forward, I'd be glad to talk.\n\nThanks,\nAkshay Verma",
    "Hi {first},\n\nA final follow-up on the {role} role. If someone else on the team handles hiring for it, I'd be "
    "grateful if you could pass my resume along.\n\nThanks,\nAkshay Verma",
]

# Agency pitches (jobs.source='agency'): not about one job, so the nudges talk about their open reqs.
AGENCY_NUDGE1 = [
    "Hi {first},\n\nJust following up on my note. If {company} has any Salesforce reqs open with US clients "
    "(Apex, LWC, Agentforce, CPQ), I'm available within 15 days on C2C and happy to jump on a quick call.\n\n"
    "Thanks,\nAkshay Verma",
    "Hi {first},\n\nBringing this back up in case it got buried. Happy to be on your bench for remote Salesforce "
    "contract roles; my resume is on the email below.\n\nThanks,\nAkshay Verma",
]
AGENCY_NUDGE2 = [
    "Hi {first},\n\nLast note from me for now. If a Salesforce contract comes up that suits a remote, US-hours "
    "developer on C2C, I'd be glad to hear from you. Thanks for reading.\n\nThanks,\nAkshay Verma",
]

AUTO_SUBJECT = re.compile(r"out of (the )?office|automatic reply|auto[- ]?reply|autoreply|auto[- ]?response|"
                          r"away from (the )?office|on vacation|on leave|thank you for (your email|contacting)", re.I)
_GENERIC_NAME = re.compile(r"\b(inbox|team|hr|talent|hiring|careers?|jobs|recruit\w*|attraction|people)\b", re.I)


# ------------------------------------------------------------------ pure schedule logic

def add_business_days(d: date, n: int) -> date:
    """d plus n business days (Mon-Fri); weekends are skipped."""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def business_days_between(a: date, b: date) -> int:
    """Number of business days d with a < d <= b."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


AGENCY_ROLE_PREFIX = "Salesforce contract roles for your clients"   # jh.agencies.AGENCY_ROLE (pseudo jobs)


def _next_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def nudge_due(initial_date: date, nudge1_date=None, nudge2_date=None, agency=False):
    """(kind, due_date) of the next nudge, or (None, None) when the sequence is done.
    Agency pitches (Agencies tab: "follow-ups on day 3 and 7"): nudge1 on day 3, nudge2 on day 7 after the pitch
    (moved to the next weekday when that falls on a weekend)."""
    if nudge2_date:
        return None, None
    if agency:
        if nudge1_date:
            return "nudge2", max(_next_business_day(initial_date + timedelta(days=7)), add_business_days(nudge1_date, 1))
        return "nudge1", _next_business_day(initial_date + timedelta(days=3))
    if nudge1_date:
        return "nudge2", add_business_days(nudge1_date, 3)
    # A weekend send is read on Monday, so a full business day has passed only on Tuesday.
    base = initial_date
    while base.weekday() >= 5:
        base += timedelta(days=1)
    if base != initial_date:
        return "nudge1", add_business_days(base, 1)
    return "nudge1", add_business_days(initial_date, 1)


def thread_state(t: dict) -> dict:
    """Derived fields for one contact thread (emails + replies lists already loaded)."""
    emails, replies = t.get("emails", []), t.get("replies", [])
    inits = [e for e in emails if e["kind"] == "initial"]
    # the initial that actually went out wins over earlier dry-run / failed attempts for the same contact
    initial = next((e for e in inits if e["status"] in ("sent", "bounced")), None) or (inits[-1] if inits else None)
    sent = {e["kind"]: e for e in emails if e["status"] in ("sent", "bounced")}
    replied = any(r["kind"] == "reply" for r in replies)
    bounced = any(e["status"] == "bounced" for e in emails) or any(r["kind"] == "bounce" for r in replies)
    initial_sent = bool(initial and initial["status"] in ("sent", "bounced"))
    if replied:
        status = "replied"
    elif bounced:
        status = "bounced"
    elif "nudge2" in sent:
        status = "nudge2"
    elif "nudge1" in sent:
        status = "nudge1"
    elif initial_sent:
        status = "sent"
    elif initial:
        status = initial["status"] if initial["status"] in ("dry_run", "failed") else "pending"
    else:
        status = "pending"
    next_kind, next_due = None, None
    if status in ("sent", "nudge1") and not t.get("stop_nudges"):
        next_kind, next_due = nudge_due(db.ts_date(initial["sent_at"]),
                                        db.ts_date(sent["nudge1"]["sent_at"]) if "nudge1" in sent else None,
                                        None, agency=(t.get("role") or "").startswith(AGENCY_ROLE_PREFIX))
    touches = [e["sent_at"] for e in emails if e["sent_at"]] + [r["received_at"] for r in replies if r["received_at"]]
    return {
        "initial": initial, "initial_sent": initial_sent,
        "sent_at": initial["sent_at"] if initial else None,
        "last_touch_at": max(touches) if touches else None,
        "app_status": status, "replied": replied, "bounced": bounced,
        "next_kind": next_kind, "next_followup": next_due,
        "sequence_done": status == "nudge2" or bool(t.get("stop_nudges")),
    }


def is_auto_reply(msg: dict) -> bool:
    """Out-of-office / automatic replies (SPEC §9): subject keywords or Auto-Submitted != no."""
    auto = (msg.get("auto_submitted") or "").lower()
    if auto and auto != "no":
        return True
    if msg.get("x_autoreply") in ("yes", "true", "1") or msg.get("precedence") == "auto_reply":
        return True
    return bool(AUTO_SUBJECT.search(msg.get("subject") or ""))


def first_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name in ("-",) or _GENERIC_NAME.search(name):
        return "there"
    first = re.split(r"[\s,]+", name)[0].strip(".")
    return first if len(first) > 1 else "there"


def display_company(company: str) -> str:
    return re.sub(r"\s*\((jobright|linkedin[^)]*)\)\s*$", "", company or "", flags=re.I).strip() or "your team"


def display_role(role: str) -> str:
    """'Salesforce Developer (US remote; public-sector consulting)' -> 'Salesforce Developer' (tracker notes off)."""
    r = re.sub(r"\s*(\([^()]*\)|\[[^\[\]]*\])", "", role or "").strip(" -,;")
    return re.sub(r"\s+", " ", r) or (role or "").strip() or "open"


def nudge_text(kind: str, contact_id: int, name: str, role: str, company: str) -> str:
    if (role or "").startswith("Salesforce contract roles for your clients"):
        templates = AGENCY_NUDGE1 if kind == "nudge1" else AGENCY_NUDGE2
    else:
        templates = NUDGE1_TEMPLATES if kind == "nudge1" else NUDGE2_TEMPLATES
    tpl = templates[(contact_id or 0) % len(templates)]
    return tpl.format(first=first_name(name), role=display_role(role), company=display_company(company))


def reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if re.match(r"^re\s*:", s, re.I) else "Re: " + s


# ------------------------------------------------------------------ the lane

def _mark_bounce(conn, b, emit, counts):
    addr = (b.get("failed_addr") or "").lower()
    if not addr:
        return
    rows = conn.execute("SELECT id, job_id FROM contacts WHERE lower(trim(email))=?", (addr,)).fetchall()
    for c in rows:
        if conn.execute("SELECT 1 FROM replies WHERE contact_id=? AND kind='bounce'", (c["id"],)).fetchone():
            continue
        if not conn.execute("SELECT 1 FROM emails WHERE contact_id=? AND status='sent'", (c["id"],)).fetchone():
            continue
        conn.execute("UPDATE emails SET status='bounced' WHERE contact_id=? AND status='sent'", (c["id"],))
        conn.execute("UPDATE contacts SET stop_nudges=1 WHERE id=?", (c["id"],))
        conn.execute("INSERT INTO replies (contact_id, job_id, from_addr, subject, snippet, received_at, kind)"
                     " VALUES (?,?,?,?,?,?, 'bounce')",
                     (c["id"], c["job_id"], "mailer-daemon", b.get("subject"), "Delivery failed to " + addr,
                      b.get("date") or db.now_iso()))
        # job is bounced only when no other contact on it is still reachable
        live = conn.execute("SELECT COUNT(*) FROM emails WHERE job_id=? AND status='sent'", (c["job_id"],)).fetchone()[0]
        if not live:
            conn.execute("UPDATE jobs SET status='bounced', updated_at=? WHERE id=? AND status!='replied'",
                         (db.now_iso(), c["job_id"]))
        counts["bounces"] += 1
        emit(LANE, "bounce_found", "Bounced: %s" % addr, "warn",
             {"contact_id": c["id"], "job_id": c["job_id"], "email": addr, "subject": b.get("subject")})


def _record_replies(conn, t, msgs, emit, counts, mb=None, cfg=None) -> bool:
    """Store new replies for a thread; returns True if a real (non-auto) reply exists."""
    real = False
    for m in msgs:
        if mailer.is_bounce(m):
            continue
        kind = "auto" if is_auto_reply(m) else "reply"
        exists = conn.execute("SELECT 1 FROM replies WHERE contact_id=? AND received_at=? AND COALESCE(subject,'')=?",
                              (t["contact_id"], m["date"], m["subject"] or "")).fetchone()
        if kind == "reply":
            real = True
        if exists:
            continue
        cat = replies_mod.classify(m.get("subject"), m.get("text") or m.get("snippet")) if kind == "reply" else None
        cur = conn.execute("INSERT INTO replies (contact_id, job_id, from_addr, subject, snippet, received_at, kind, category,"
                           " message_id) VALUES (?,?,?,?,?,?,?,?,?)",
                           (t["contact_id"], t["job_id"], m["from_addr"], m["subject"], m["snippet"], m["date"], kind, cat,
                            m.get("message_id")))
        if kind == "reply":
            counts["replies"] += 1
            conn.execute("UPDATE jobs SET status='replied', updated_at=? WHERE id=?", (db.now_iso(), t["job_id"]))
            drafted = _draft(conn, mb, cfg, cur.lastrowid, cat, m, t.get("name"), t.get("company"), t.get("role"),
                             t.get("job_id"), counts, emit)
            emit(LANE, "reply_found", "Reply from %s (%s) — %s: %s" % (
                t["name"] or m["from_addr"], display_company(t["company"]), replies_mod.CATEGORY_LABEL.get(cat, "Reply"),
                m["subject"]), "success",
                 {"contact_id": t["contact_id"], "job_id": t["job_id"], "from": m["from_addr"], "category": cat,
                  "draft": drafted, "subject": m["subject"], "snippet": m["snippet"]})
        else:
            counts["auto_replies"] += 1
            emit(LANE, "step", "Auto-reply from %s (nudges continue): %s" % (m["from_addr"], m["subject"]), "info",
                 {"contact_id": t["contact_id"]})
    return real


def _india_job(conn, job_id):
    r = conn.execute("SELECT location, source, jd_text FROM jobs WHERE id=?", (job_id,)).fetchone() if job_id else None
    if not r:
        return False
    return r["source"] == "linkedin_india" or bool(re.search(r"\bindia\b", r["location"] or "", re.I))


def _draft(conn, mb, cfg, reply_id, cat, m, name, company, role, job_id, counts, emit) -> bool:
    """Save a ready-to-send reply in Gmail Drafts for replies that need his answer. Never sends."""
    if mb is None or cfg is None or cat not in replies_mod.ACTION or not m.get("from_addr") or not hasattr(mb, "save_draft"):
        return False
    try:
        body = replies_mod.draft_text(cat, name or m.get("from_addr"), company, role, india=_india_job(conn, job_id))
        mid = m.get("message_id") or None
        msg = mailer.build_message(cfg, m["from_addr"], reply_subject(m.get("subject") or (role or "Salesforce role")), body,
                                   None, in_reply_to=mid, references=(m.get("references") + " " + mid).strip()
                                   if mid and m.get("references") else mid)
        ok = bool(mb.save_draft(msg))
    except Exception as e:
        emit(LANE, "warning", "Could not save a reply draft for %s: %s" % (m.get("from_addr"), e), "warn")
        return False
    if ok:
        conn.execute("UPDATE replies SET draft=1 WHERE id=?", (reply_id,))
        counts["drafts"] = counts.get("drafts", 0) + 1
    return ok


def _applied_companies(conn, days=30):
    """(job_id, company) of jobs he applied to or emailed in the last days (for matching inbound mail)."""
    since = (db.today_ist() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT j.id, j.company FROM jobs j WHERE substr(COALESCE(j.updated_at, j.created_at),1,10)>=? AND ("
        " EXISTS (SELECT 1 FROM portal_apps p WHERE p.job_id=j.id AND p.status IN ('submitted','prefilled','needs_input'))"
        " OR EXISTS (SELECT 1 FROM emails e WHERE e.job_id=j.id AND e.status IN ('sent','bounced')))"
        " ORDER BY j.id DESC LIMIT 600", (since,)).fetchall()
    return [(r["id"], r["company"]) for r in rows]


def scan_inbound(conn, mb, cfg, emit, counts, days=4):
    """Replies we can't see by thread: ATS / company mail about Easy Apply and portal applications, recruiters
    writing from another address. Matched to his applied jobs by company name / email domain."""
    if not hasattr(mb, "recent_inbox"):
        return
    own = ((cfg.get("email") or {}).get("address") or "").lower()
    known = {(r["from_addr"] or "", r["received_at"] or "", r["subject"] or "")
             for r in conn.execute("SELECT from_addr, received_at, subject FROM replies WHERE received_at>=?",
                                   ((db.today_ist() - timedelta(days=days + 2)).isoformat(),))}
    comps = _applied_companies(conn)
    if not comps:
        return
    for m in mb.recent_inbox(days):
        frm = (m.get("from_addr") or "").lower()
        if not frm or frm == own or (frm, m.get("date") or "", m.get("subject") or "") in known:
            continue
        if any(frm.endswith("@" + d) or frm.endswith("." + d) for d in replies_mod.NOISE_DOMAINS) or is_auto_reply(m):
            continue
        cat = replies_mod.classify(m.get("subject"), m.get("text") or m.get("snippet"))
        if cat in ("ack", "other") and replies_mod.NOREPLY.match(frm):
            continue                       # "thanks for applying" robots
        if cat == "ack":
            continue
        job_id = replies_mod.match_company(m, comps)
        if not job_id:
            continue
        if conn.execute("SELECT 1 FROM replies WHERE from_addr=? AND received_at=? AND COALESCE(subject,'')=?",
                        (frm, m.get("date"), m.get("subject") or "")).fetchone():
            continue
        job = conn.execute("SELECT company, role FROM jobs WHERE id=?", (job_id,)).fetchone()
        cur = conn.execute("INSERT INTO replies (contact_id, job_id, from_addr, subject, snippet, received_at, kind, category,"
                           " message_id) VALUES (NULL,?,?,?,?,?,'inbound',?,?)",
                           (job_id, frm, m.get("subject"), m.get("snippet"), m.get("date"), cat, m.get("message_id")))
        known.add((frm, m.get("date") or "", m.get("subject") or ""))
        counts["inbound"] = counts.get("inbound", 0) + 1
        if cat != "rejection":
            conn.execute("UPDATE jobs SET status='replied', updated_at=? WHERE id=?", (db.now_iso(), job_id))
        sender = m.get("from_name") or frm
        drafted = False if replies_mod.NOREPLY.match(frm) else _draft(
            conn, mb, cfg, cur.lastrowid, cat, m, sender, job["company"], job["role"], job_id, counts, emit)
        emit(LANE, "reply_found", "%s from %s about %s — %s" % (
            "Message", frm, display_company(job["company"]), replies_mod.CATEGORY_LABEL.get(cat, "Reply")),
             "success" if cat != "rejection" else "info",
             {"job_id": job_id, "from": frm, "category": cat, "draft": drafted, "subject": m.get("subject"),
              "snippet": m.get("snippet"), "inbound": True})


def _original(conn, mb, t):
    """(message_id, subject) of the initial email; looks it up in Gmail Sent Mail for imported rows."""
    ini = t["initial"]
    if ini.get("message_id"):
        return ini["message_id"], ini.get("subject") or ""
    hint = ini.get("subject") or None
    found = mb.find_sent_message(t["email"], hint)
    if not found or not found.get("message_id"):
        return None, None
    conn.execute("UPDATE emails SET message_id=?, subject=COALESCE(subject, ?) WHERE id=?",
                 (found["message_id"], found.get("subject"), ini["id"]))
    return found["message_id"], ini.get("subject") or found.get("subject") or ""


def run_followups(conn, run_id, cfg, emit_fn=None, stop_event=None, today=None) -> dict:
    """Check bounces and replies, then send due nudges. emit_fn(lane, type, message, level, data)."""
    from . import events
    if emit_fn is None:
        def emit_fn(lane, type, message, level="info", data=None):
            return events.emit(conn, run_id, lane, type, message, level, data)
    emit = emit_fn
    today = today or db.today_ist()
    counts = {"checked": 0, "replies": 0, "auto_replies": 0, "bounces": 0, "nudges_sent": 0,
              "nudges_due": 0, "nudges_skipped": 0, "errors": 0}
    stopped = lambda: stop_event is not None and stop_event.is_set()  # noqa: E731
    dry = bool(cfg.get("dry_run"))

    threads = [t for t in db.load_threads(conn).values()
               if t["initial_sent"] and t["app_status"] in ("sent", "nudge1", "nudge2")]
    if not config.email_configured(cfg):
        due = sum(1 for t in threads if t["next_followup"] and t["next_followup"] <= today)
        counts["nudges_due"] = due
        emit(LANE, "warning", "Gmail is not set up (Settings) - skipped reply/bounce checks and %d due nudges" % due,
             "warn")
        counts["skipped_reason"] = "gmail not configured"
        return counts

    emit(LANE, "step", "Checking %d open threads for replies and bounces" % len(threads))
    try:
        with mailer.mailbox(cfg) as mb:
            try:
                for b in mb.find_bounces(7):
                    _mark_bounce(conn, b, emit, counts)
            except Exception as e:  # bounce check failure shouldn't block replies/nudges
                counts["errors"] += 1
                emit(LANE, "warning", "Bounce check failed: %s" % e, "warn")
            if counts["bounces"]:
                ids = [t["contact_id"] for t in threads]
                threads = [t for t in db.load_threads(conn, ids).values()
                           if t["app_status"] in ("sent", "nudge1", "nudge2")]
            for t in threads:
                if stopped():
                    emit(LANE, "warning", "Stopped by user", "warn")
                    break
                counts["checked"] += 1
                try:
                    msgs = mb.find_replies(t["email"], t["sent_at"])
                    if _record_replies(conn, t, msgs, emit, counts, mb, cfg):
                        continue
                    if not t["next_followup"] or t["next_followup"] > today:
                        continue
                    # someone else on this job already answered (HR replied -> don't chase the manager)
                    if conn.execute("SELECT 1 FROM jobs WHERE id=? AND status='replied'", (t["job_id"],)).fetchone():
                        continue
                    counts["nudges_due"] += 1
                    kind = t["next_kind"]
                    if dry:
                        emit(LANE, "step", "[dry run] would send %s to %s (%s)" % (kind, t["email"], t["company"]),
                             "info", {"contact_id": t["contact_id"], "kind": kind, "dry_run": True})
                        continue
                    orig_id, orig_subject = _original(conn, mb, t)
                    if not orig_id:
                        counts["nudges_skipped"] += 1
                        emit(LANE, "warning", "Skipped %s to %s: original email not found in Sent Mail"
                             % (kind, t["email"]), "warn", {"contact_id": t["contact_id"]})
                        continue
                    subject = reply_subject(orig_subject or ("%s role" % (t["role"] or "Salesforce")))
                    body = nudge_text(kind, t["contact_id"], t["name"], t["role"], t["company"])
                    try:
                        mid = mailer.send(cfg, t["email"], subject, body, None, in_reply_to=orig_id, references=orig_id)
                    except Exception as e:
                        if not getattr(e, "maybe_sent", False):
                            raise
                        # Gmail may already have it: record the nudge as done so the next run never sends it twice
                        conn.execute(
                            "INSERT INTO emails (job_id, contact_id, kind, subject, body, in_reply_to, status, error, sent_at,"
                            " run_id) VALUES (?,?,?,?,?,?, 'sent', ?, ?, ?)",
                            (t["job_id"], t["contact_id"], kind, subject, body, orig_id,
                             (str(e) + " - may have been delivered; not retried")[:1000], db.now_iso(), run_id))
                        counts["errors"] += 1
                        emit(LANE, "warning", "%s to %s may not have gone out (not retried): %s"
                             % ("Nudge 1" if kind == "nudge1" else "Nudge 2", t["email"], str(e)[:200]), "warn",
                             {"contact_id": t["contact_id"], "kind": kind})
                        continue
                    cur = conn.execute(
                        "INSERT INTO emails (job_id, contact_id, kind, subject, body, message_id, in_reply_to,"
                        " status, sent_at, run_id) VALUES (?,?,?,?,?,?,?, 'sent', ?, ?)",
                        (t["job_id"], t["contact_id"], kind, subject, body, mid, orig_id, db.now_iso(), run_id))
                    counts["nudges_sent"] += 1
                    emit(LANE, "nudge_sent", "%s sent to %s (%s)" % ("Nudge 1" if kind == "nudge1" else "Nudge 2",
                                                                     t["name"] or t["email"],
                                                                     display_company(t["company"])),
                         "success", {"contact_id": t["contact_id"], "job_id": t["job_id"], "kind": kind,
                                     "email_id": cur.lastrowid})
                except Exception as e:
                    counts["errors"] += 1
                    emit(LANE, "error", "Follow-up for %s failed: %s" % (t["email"], e), "error",
                         {"contact_id": t["contact_id"]})
            if not stopped():
                try:
                    scan_inbound(conn, mb, cfg, emit, counts)
                except Exception as e:      # the inbound scan never blocks nudges
                    counts["errors"] += 1
                    emit(LANE, "warning", "Inbox scan for application replies failed: %s" % e, "warn")
    except mailer.MailError as e:
        counts["errors"] += 1
        emit(LANE, "error", "Mailbox unavailable: %s" % e, "error")
        counts["error"] = str(e)
    return counts
