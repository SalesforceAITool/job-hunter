"""Daily report email (Akshay, 11 Nov): what Job Hunter actually did, so he can track applications per day.

* Applications submitted end to end: LinkedIn Easy Apply, company career sites / job portals (each with a link).
* Recruiter emails sent with the resume: from LinkedIn hiring posts, LinkedIn job listings, Jobright, agencies, ...
  (each with the recruiter's address and the post / job link). Only mail Gmail accepted counts as sent.
* Follow-ups sent, recruiter replies, forms waiting for an answer, emails that did not go out, AI cycles and spend.
* Totals since Job Hunter started.

Sent once a day at daily_report.hour (IST) to daily_report.to (empty = the application Gmail). Each report covers
everything since the previous one, so nothing is counted twice or missed; the laptop off at report time -> it goes
out when Job Hunter is back and covers the whole gap. "Send report now" (Settings) mails today-so-far without
touching the schedule.
"""
import html
import json
import logging
import re
from datetime import datetime, timedelta

from . import config, db, mailer

log = logging.getLogger("jobhunter.report")
META_KEY = "daily_report_last_end"
MAX_DAYS = 7                      # a report never looks back further than a week
RETRY_S = 30 * 60                 # a failed send (no internet, Gmail limit) is tried again after 30 minutes
LIST_MAX = 60                     # rows per section in the email (counts are always complete)
_next_try = {"at": 0.0}

SOURCE_LABEL = [("posts", "LinkedIn hiring posts"), ("linkedin", "LinkedIn job listings"), ("jobright", "Jobright"),
                ("himalayas", "Himalayas"), ("agency", "Recruitment agencies"), ("careers", "Company career sites"),
                ("import", "Imported tracker"), ("", "Other")]
ATS_LABEL = {"linkedin_easy_apply": "LinkedIn Easy Apply", "greenhouse": "Greenhouse", "lever": "Lever",
             "ashby": "Ashby", "workday": "Workday", "smartrecruiters": "SmartRecruiters"}


def source_label(src):
    s = str(src or "").lower()
    for key, label in SOURCE_LABEL:
        if key and s.startswith(key):
            return label
    return "Other"


def settings(cfg=None) -> dict:
    cfg = cfg or config.load()
    dr = cfg.get("daily_report") if isinstance(cfg.get("daily_report"), dict) else {}
    em = cfg.get("email") or {}
    to = str(dr.get("to") or "").strip() or str(em.get("address") or "").strip()
    try:
        hour = min(23, max(0, int(dr.get("hour", 23))))
    except (TypeError, ValueError):
        hour = 23
    return {"on": dr.get("on", True) not in (False, "false", "0", 0, "off", "no"), "hour": hour, "to": to}


def _iso(dt):
    return dt.astimezone(db.IST).replace(microsecond=0).isoformat()


def _when(ts):
    dt = db.parse_ts(ts)
    return dt.strftime("%d %b %I:%M %p").replace(" 0", " ") if dt else "?"


def _company(x):
    return re.sub(r"\s*\((jobright|linkedin[^)]*)\)\s*$", "", str(x or ""), flags=re.I).strip() or "?"


def _role(x):
    r = re.sub(r"\s*(\([^()]*\)|\[[^\[\]]*\])", "", str(x or "")).strip(" -,;")
    return re.sub(r"\s+", " ", r) or str(x or "").strip() or "?"


def _first_question(qj):
    try:
        qs = json.loads(qj or "[]")
    except ValueError:
        return ""
    qs = [q for q in (qs if isinstance(qs, list) else []) if isinstance(q, str) and q.strip()]
    return (qs[0][:140] + (" (+%d more)" % (len(qs) - 1) if len(qs) > 1 else "")) if qs else ""


def collect(conn, start, end) -> dict:
    """Everything Job Hunter did in [start, end) (IST datetimes)."""
    a, b = _iso(start), _iso(end)
    out = {"start": a, "end": b}
    rows = conn.execute(
        "SELECT p.id, p.ats, p.apply_url, p.submitted_at, j.company, j.role, j.url, j.source FROM portal_apps p"
        " JOIN jobs j ON j.id=p.job_id WHERE p.status='submitted' AND p.submitted_at>=? AND p.submitted_at<?"
        " ORDER BY p.submitted_at", (a, b)).fetchall()
    out["easy_apply"] = [dict(r) for r in rows if r["ats"] == "linkedin_easy_apply"]
    out["portals"] = [dict(r) for r in rows if r["ats"] != "linkedin_easy_apply"]
    mails = [dict(r) for r in conn.execute(
        "SELECT e.id, e.kind, e.status, e.error, e.sent_at, e.subject, c.email, c.name, j.company, j.role, j.url, j.source"
        " FROM emails e LEFT JOIN contacts c ON c.id=e.contact_id LEFT JOIN jobs j ON j.id=e.job_id"
        " WHERE e.sent_at>=? AND e.sent_at<? AND e.status IN ('sent','sending','failed','bounced') ORDER BY e.sent_at",
        (a, b)).fetchall()]
    ok = [m for m in mails if m["status"] == "sent" and not (m["error"] or "").strip()]
    out["emails"] = [m for m in ok if m["kind"] == "initial"]
    out["nudges"] = [m for m in ok if m["kind"] != "initial"]
    out["not_sent"] = [m for m in mails if m not in ok]
    for m in out["not_sent"]:
        err = (m["error"] or "").lower()
        m["why"] = ("bounced (address not valid)" if m["status"] == "bounced"
                    else "Gmail daily sending limit reached" if "5.4.5" in err or "sending limit" in err
                    else "was still being sent" if m["status"] == "sending" and not err
                    else (m["error"] or "send failed")[:120])
    out["by_source"] = {}
    for m in out["emails"]:
        out["by_source"].setdefault(source_label(m["source"]), []).append(m)
    out["waiting"] = [dict(r) for r in conn.execute(
        "SELECT p.id, p.ats, p.apply_url, p.questions_json, p.updated_at, j.company, j.role, j.url FROM portal_apps p"
        " JOIN jobs j ON j.id=p.job_id WHERE p.status='needs_input' AND p.updated_at>=? AND p.updated_at<?"
        " ORDER BY p.updated_at", (a, b)).fetchall()]
    try:
        out["replies"] = [dict(r) for r in conn.execute(
            "SELECT r.from_addr, r.subject, r.received_at, r.category, j.company FROM replies r"
            " LEFT JOIN jobs j ON j.id=r.job_id WHERE r.received_at>=? AND r.received_at<? ORDER BY r.received_at",
            (a, b)).fetchall()]
    except Exception:                            # older DB without replies.category
        out["replies"] = [dict(r) for r in conn.execute(
            "SELECT r.from_addr, r.subject, r.received_at, j.company FROM replies r LEFT JOIN jobs j ON j.id=r.job_id"
            " WHERE r.received_at>=? AND r.received_at<? ORDER BY r.received_at", (a, b)).fetchall()]
    # bounce notices are not recruiter replies (the bounced emails are listed under "did not go out")
    out["replies"] = [r for r in out["replies"]
                      if not re.search(r"mailer-daemon|postmaster|no-?reply", str(r.get("from_addr") or ""), re.I)]
    runs = conn.execute("SELECT trigger, COUNT(*) n FROM runs WHERE started_at>=? AND started_at<? GROUP BY trigger",
                        (a, b)).fetchall()
    out["runs"] = {r["trigger"]: r["n"] for r in runs}
    usd = 0.0
    for (d,) in conn.execute("SELECT data_json FROM events WHERE type='lane_finished' AND ts>=? AND ts<?", (a, b)):
        try:
            usd += float(json.loads(d or "{}").get("cost_usd") or 0)
        except (TypeError, ValueError):
            pass
    out["ai_usd"] = round(usd, 2)
    one = lambda q, *p: conn.execute(q, p).fetchone()[0]
    out["totals"] = {
        "easy_apply": one("SELECT COUNT(*) FROM portal_apps WHERE status='submitted' AND ats='linkedin_easy_apply'"),
        "portals": one("SELECT COUNT(*) FROM portal_apps WHERE status='submitted' AND ats!='linkedin_easy_apply'"),
        "emails": one("SELECT COUNT(*) FROM emails WHERE kind='initial' AND status='sent' AND COALESCE(error,'')=''"),
        "replies": one("SELECT COUNT(*) FROM replies"),
    }
    out["applied"] = len(out["easy_apply"]) + len(out["portals"])
    return out


def _label_window(s):
    a, b = db.parse_ts(s["start"]), db.parse_ts(s["end"])
    fmt = lambda d: d.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
    return "%s  →  %s (IST)" % (fmt(a), fmt(b))


def render(s, title_day=None) -> tuple:
    """-> (subject, plain text, html)."""
    end = db.parse_ts(s["end"])
    day = title_day or end.strftime("%a %d %b").replace(" 0", " ")
    n_ea, n_po, n_em = len(s["easy_apply"]), len(s["portals"]), len(s["emails"])
    subject = "Job Hunter %s: %d applied (%d Easy Apply%s), %d recruiter email%s" % (
        day, n_ea + n_po, n_ea, (", %d portal" % n_po) if n_po else "", n_em, "" if n_em == 1 else "s")
    cycles = sum(s["runs"].values())
    summary = [
        ("Applications submitted end to end", n_ea + n_po, True),
        ("   LinkedIn Easy Apply", n_ea, False),
        ("   Company career sites / job portals", n_po, False),
        ("Recruiter emails sent (resume attached)", n_em, True),
    ] + [("   From " + k, len(v), False) for k, v in sorted(s["by_source"].items(), key=lambda x: -len(x[1]))] + [
        ("Follow-up emails sent", len(s["nudges"]), True),
        ("Replies from recruiters", len(s["replies"]), True),
        ("Forms waiting for your answer", len(s["waiting"]), True),
        ("Emails that did NOT go out", len(s["not_sent"]), True),
        ("Job Hunter runs / AI spend", "%d runs · $%.2f" % (cycles, s["ai_usd"]), True),
    ]
    sections = []

    def link(x):
        return x if isinstance(x, str) and x.startswith("http") else ""

    sections.append(("LinkedIn Easy Apply — submitted", [
        (_when(r["submitted_at"]), "%s — %s" % (_company(r["company"]), _role(r["role"])), link(r["apply_url"]) or link(r["url"]))
        for r in s["easy_apply"]]))
    sections.append(("Company career sites / job portals — submitted", [
        (_when(r["submitted_at"]), "%s — %s (%s)" % (_company(r["company"]), _role(r["role"]),
                                                     ATS_LABEL.get(r["ats"], r["ats"] or "portal")),
         link(r["apply_url"]) or link(r["url"])) for r in s["portals"]]))
    for src, ms in sorted(s["by_source"].items(), key=lambda x: -len(x[1])):
        sections.append(("Recruiter emails — from %s" % src, [
            (_when(m["sent_at"]), "%s — %s → %s" % (_company(m["company"]), _role(m["role"]), m["email"] or "?"),
             link(m["url"])) for m in ms]))
    sections.append(("Follow-ups sent", [
        (_when(m["sent_at"]), "%s → %s (%s)" % (_company(m["company"]), m["email"] or "?",
                                               "follow-up 1" if m["kind"] == "nudge1" else "follow-up 2"), "")
        for m in s["nudges"]]))
    sections.append(("Replies", [
        (_when(r["received_at"]), "%s — %s%s" % (r.get("from_addr") or "?", (r.get("subject") or "")[:90],
                                                  (" [%s]" % r["category"]) if r.get("category") else ""), "")
        for r in s["replies"]]))
    sections.append(("Waiting for your answer (open Job Hunter → Applications)", [
        (_when(r["updated_at"]), "%s — %s: %s" % (_company(r["company"]), _role(r["role"]),
                                                  _first_question(r["questions_json"]) or "needs a check"),
         link(r["apply_url"]) or link(r["url"])) for r in s["waiting"]]))
    sections.append(("Emails that did not go out", [
        (_when(m["sent_at"]), "%s → %s: %s" % (_company(m["company"]), m["email"] or "?", m["why"]), "")
        for m in s["not_sent"]]))
    t = s["totals"]
    totals = ("Since Job Hunter started: %d Easy Apply submitted, %d portal applications, %d recruiter emails, %d replies."
              % (t["easy_apply"], t["portals"], t["emails"], t["replies"]))

    # ---- plain text
    lines = ["Job Hunter — daily report", _label_window(s), ""]
    for label, n, top in summary:
        lines.append("%-46s %s" % (label, n) if top else "%-46s %s" % (label, n))
    for title, rows in sections:
        if not rows:
            continue
        lines += ["", "%s (%d)" % (title.upper(), len(rows))]
        for when, what, url in rows[:LIST_MAX]:
            lines.append("  %s  %s%s" % (when, what, ("\n      " + url) if url else ""))
        if len(rows) > LIST_MAX:
            lines.append("  … and %d more (open Job Hunter for the full list)" % (len(rows) - LIST_MAX))
    lines += ["", totals, "", "Sent by Job Hunter on the Dell laptop. Change the time or address in Settings → Daily report."]
    text = "\n".join(lines)

    # ---- html
    e = html.escape
    rows_html = "".join(
        '<tr><td style="padding:4px 12px 4px %dpx;%s">%s</td><td style="padding:4px 12px;text-align:right;%s">%s</td></tr>'
        % (12 if top else 28, "font-weight:600;" if top else "color:#555;", e(label.strip()),
           "font-weight:600;" if top else "color:#555;", e(str(n)))
        for label, n, top in summary)
    body = ['<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;color:#1d1d1f;max-width:760px">',
            '<h2 style="margin:0 0 4px;font-size:18px">Job Hunter — daily report</h2>',
            '<div style="color:#666;margin-bottom:14px">%s</div>' % e(_label_window(s)),
            '<table style="border-collapse:collapse;background:#f6f7f9;border-radius:8px;min-width:360px">%s</table>' % rows_html]
    for title, rows in sections:
        if not rows:
            continue
        body.append('<h3 style="font-size:15px;margin:22px 0 6px">%s <span style="color:#888;font-weight:400">(%d)</span></h3>'
                    % (e(title), len(rows)))
        body.append('<table style="border-collapse:collapse;width:100%">')
        for when, what, url in rows[:LIST_MAX]:
            cell = ('<a href="%s" style="color:#0a66c2;text-decoration:none">%s</a>' % (e(url, quote=True), e(what))
                    if url else e(what))
            body.append('<tr><td style="padding:3px 10px 3px 0;color:#888;white-space:nowrap;vertical-align:top">%s</td>'
                        '<td style="padding:3px 0">%s</td></tr>' % (e(when), cell))
        body.append("</table>")
        if len(rows) > LIST_MAX:
            body.append('<div style="color:#888">… and %d more (open Job Hunter for the full list)</div>' % (len(rows) - LIST_MAX))
    body.append('<p style="margin-top:22px;color:#444">%s</p>' % e(totals))
    body.append('<p style="color:#999;font-size:12px">Sent by Job Hunter on the Dell laptop. '
                'Change the time or address in Settings → Daily report.</p></div>')
    return subject, text, "".join(body)


def send(conn, cfg, start, end, title_day=None) -> dict:
    st = settings(cfg)
    if not st["to"] or "@" not in st["to"]:
        return {"ok": False, "error": "no address to send the report to (Settings → Daily report)"}
    s = collect(conn, start, end)
    subject, text, html_body = render(s, title_day)
    try:
        mailer.send(cfg, st["to"], subject, text, html=html_body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "subject": subject}
    return {"ok": True, "to": st["to"], "subject": subject, "applied": s["applied"], "emails": len(s["emails"])}


def _meta(conn, k):
    r = conn.execute("SELECT v FROM app_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def due(conn, cfg=None, now_dt=None):
    """-> (start, end) when a scheduled report is due now, else None."""
    st = settings(cfg)
    if not st["on"]:
        return None
    now_dt = now_dt or db.now()
    slot = now_dt.replace(hour=st["hour"], minute=0, second=0, microsecond=0)
    if slot > now_dt:
        slot -= timedelta(days=1)              # the most recent report time that has passed
    last = db.parse_ts(_meta(conn, META_KEY))
    if last and last >= slot:
        return None
    start = last or now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    start = max(start, now_dt - timedelta(days=MAX_DAYS))
    return start, now_dt


def tick(conn, cfg=None, now_dt=None, now_s=None):
    """Called by the watcher about every 15 s: send the daily report when it is due. -> result dict or None."""
    import time
    now_s = time.time() if now_s is None else now_s
    if now_s < _next_try["at"]:
        return None
    cfg = cfg or config.load()
    if cfg.get("dry_run"):
        return None                            # dry run: Job Hunter sends nothing
    win = due(conn, cfg, now_dt)
    if not win:
        return None
    res = send(conn, cfg, *win)
    from . import events
    if res.get("ok"):
        conn.execute("INSERT OR REPLACE INTO app_meta (k, v) VALUES (?, ?)", (META_KEY, _iso(win[1])))
        _next_try["at"] = 0.0
        events.emit(conn, None, "report", "step", "Daily report emailed to %s: %s" % (res["to"], res["subject"]),
                    "success", {"report": res})
    else:
        _next_try["at"] = now_s + RETRY_S
        events.emit(conn, None, "report", "warning", "Daily report not sent (will try again in 30 min): %s"
                    % res.get("error"), "warn", {"report": res})
    return res


def send_now(conn, cfg=None) -> dict:
    """Settings → 'Send report now': today so far, without moving the daily schedule."""
    cfg = cfg or config.load()
    now_dt = db.now()
    return send(conn, cfg, now_dt.replace(hour=0, minute=0, second=0, microsecond=0), now_dt,
                title_day="today so far (%s)" % now_dt.strftime("%I:%M %p").lstrip("0"))
