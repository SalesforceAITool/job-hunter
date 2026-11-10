#!/usr/bin/env python3
"""jobhunt — stdio MCP server that gives the AI engine access to the Job Hunter DB.

Protocol: JSON-RPC 2.0, one JSON message per line on stdin/stdout (MCP stdio
transport). Logs go to stderr only. Standard library only.

Env:
  JOBHUNTER_DB        SQLite path (passed to jh.db.connect)
  JOBHUNTER_RUN_ID    current run id ("" = none)
  JOBHUNTER_LANE      lane id for events
  JOBHUNTER_CONFIG    path to config.json (send_application needs cfg)
  JOBHUNTER_APP_ROOT  where the `jh` package lives (default: parent of engine/)

Every tool is a thin wrapper over jh.actions (SPEC §16).
"""
import json
import re
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = Path(os.environ.get("JOBHUNTER_APP_ROOT") or HERE.parent)
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

SERVER_NAME = "jobhunt"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"

PROJECT_KEYS = ["nissan", "asda", "scv", "amkor", "siemens", "bcbsm", "dubai", "emaar", "belden", "druce", "lifesci", "wm"]
ATS_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com")


def log(*a):
    try:
        print("[jobhunt_mcp]", *a, file=sys.stderr, flush=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- schemas

RESUME_SCHEMA = {
    "type": "object",
    "description": "Tailored resume content. Only real skills and projects.",
    "properties": {
        "headline": {"type": "string", "description": "One line, max ~95 characters, e.g. 'Salesforce Developer (7+ years) | OmniStudio, Apex, LWC | Integrations'"},
        "summary": {"type": "string", "description": "3-4 sentences matched to the JD, honest, first person implied"},
        "skills": {"type": "array", "minItems": 6, "maxItems": 6,
                   "description": "Exactly 6 rows of [label, text] matched to the JD",
                   "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"}}},
        "project_order": {"type": "array", "description": "Project keys, most relevant first (bcbsm first for OmniStudio JDs)",
                          "items": {"type": "string", "enum": PROJECT_KEYS}},
    },
    "required": ["headline", "summary", "skills", "project_order"],
}

TOOLS = [
    {
        "name": "log_step",
        "description": "Tell Akshay what you are about to do, in one short plain sentence (he watches this live). Call before every page load or action.",
        "inputSchema": {"type": "object", "properties": {
            "message": {"type": "string", "description": "e.g. 'Opening JobRight search for Salesforce Developer'"},
            "level": {"type": "string", "enum": ["info", "success", "warn", "error"], "default": "info"},
        }, "required": ["message"]},
    },
    {
        "name": "already_contacted",
        "description": "Check the history before working on a job: true if this company+role, any of these emails, or this LinkedIn profile was already contacted.",
        "inputSchema": {"type": "object", "properties": {
            "company": {"type": "string"},
            "role": {"type": "string"},
            "emails": {"type": "array", "items": {"type": "string"}, "default": []},
            "linkedin": {"type": "string", "description": "poster/contact LinkedIn profile URL, optional"},
        }, "required": ["company", "role"]},
    },
    {
        "name": "get_job",
        "description": "Read a job already saved in the app (company, role, location, url, apply_url, status, JD text).",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "integer"}}, "required": ["job_id"]},
    },
    {
        "name": "set_job_status",
        "description": "Set the verdict for a job that is already saved (e.g. a Himalayas job): skipped with a short reason, or qualified.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"}, "status": {"type": "string", "enum": ["skipped", "qualified"]},
            "reason": {"type": "string"},
            "note": {"type": "string", "description": "optional note kept on the job, e.g. 'external apply (no Easy Apply)'"}},
            "required": ["job_id", "status"]},
    },
    {
        "name": "filter_new_jobs",
        "description": "Call ONCE per list page, BEFORE opening any job/post, with every card on it (company, role, url). Returns new (open these), seen (already handled in an earlier run — never open or save them again) and retry (saved before with no contact found; do only the contact lookup for its job_id, don't re-read or re-save the job).",
        "inputSchema": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "company": {"type": "string"}, "role": {"type": "string"}, "url": {"type": "string"}}}},
        }, "required": ["items"]},
    },
    {
        "name": "screen_jobs",
        "description": "Use INSTEAD of filter_new_jobs when the list page already shows the job/post text (LinkedIn posts, JobRight cards): pass every card with its text. Job Hunter drops jobs handled before, applies the hard skip rules in code (onsite/hybrid, US work authorization, country-locked remote, other language, sales roles) and saves those skips itself. Returns work (open only these), retry (contact lookup only, use job_id) and a count of what was dropped. Don't save_job the dropped ones.",
        "inputSchema": {"type": "object", "properties": {
            "source": {"type": "string", "enum": ["posts", "jobright", "linkedin_us", "linkedin_world", "linkedin_india"]},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "company": {"type": "string"}, "role": {"type": "string"}, "url": {"type": "string"},
                "location": {"type": "string"}, "text": {"type": "string", "description": "post / card text, up to ~1500 chars"}}}},
        }, "required": ["source", "items"]},
    },
    {
        "name": "filter_new_profiles",
        "description": "Before opening LinkedIn profiles for SalesQL: pass their URLs; returns the ones never looked up before (new). Skip the seen ones (already revealed or already tried).",
        "inputSchema": {"type": "object", "properties": {
            "urls": {"type": "array", "items": {"type": "string"}}}, "required": ["urls"]},
    },
    {
        "name": "record_profile_lookup",
        "description": "After a SalesQL lookup that found NO usable work email, record it so the profile is not opened again (profiles with an email are recorded automatically by save_contact).",
        "inputSchema": {"type": "object", "properties": {
            "url": {"type": "string"}, "found": {"type": "boolean", "default": False}}, "required": ["url"]},
    },
    {
        "name": "save_job",
        "description": "Save every job/post you looked at, qualified or skipped. Returns {job_id}.",
        "inputSchema": {"type": "object", "properties": {
            "source": {"type": "string", "enum": ["posts", "jobright", "linkedin_us", "linkedin_world", "linkedin_india", "himalayas", "jobicy", "hn", "careers", "manual"]},
            "company": {"type": "string"},
            "role": {"type": "string"},
            "location": {"type": "string", "default": ""},
            "remote": {"type": "boolean", "default": True},
            "url": {"type": "string", "description": "Job page or post URL", "default": ""},
            "posted": {"type": "string", "description": "e.g. '3 hours ago' or a date", "default": ""},
            "jd_text": {"type": "string", "description": "Full JD or post text (trim to ~4000 chars)", "default": ""},
            "decision": {"type": "string", "enum": ["qualified", "skipped"]},
            "skip_reason": {"type": "string", "description": "Required when decision=skipped, short and specific"},
            "notes": {"type": "string"},
            "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "apply_url": {"type": "string", "description": "Direct application URL if seen (Greenhouse/Lever/Ashby etc.)"},
        }, "required": ["source", "company", "role", "decision", "fit_score"]},
    },
    {
        "name": "find_email",
        "description": "When SalesQL (or the post) gives no work email: look for a VERIFIED address at the company, cheapest first — people at that domain who already wrote to Akshay (his replies / Gmail inbox), then Hunter.io / Apollo.io if he added a key in Settings (only addresses those services verify). Pass the company's website domain (from its LinkedIn company page, the JD or its careers site) and, if you know them, the hiring person's first and last name. Returns {candidates:[{email,name,title,source,already_emailed}], tried, hint}. Save a candidate with save_contact(email_source = its source). Never guess an address.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"}, "company": {"type": "string"}, "domain": {"type": "string"},
            "first_name": {"type": "string"}, "last_name": {"type": "string"}},
            "required": ["job_id", "company", "domain"]},
    },
    {
        "name": "save_contact",
        "description": "Save a person to email for a job (HR/recruiter, hiring manager, architect/lead). Refuses duplicates and developer/QA titles. Returns {contact_id} or {error}.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"},
            "name": {"type": "string"},
            "title": {"type": "string"},
            "email": {"type": "string"},
            "email_source": {"type": "string", "enum": ["post", "SalesQL", "JobRight", "pattern", "website", "Vibe", "Apollo", "sheet",
                                                         "replies", "gmail", "hunter", "apollo"]},
            "linkedin": {"type": "string"},
        }, "required": ["job_id", "name", "title", "email", "email_source"]},
    },
    {
        "name": "send_application",
        "description": "Build the tailored resume PDF and email it to the contact (plain text ~100 words). Returns {ok, email_id, error, dry_run}.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"},
            "contact_id": {"type": "integer"},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Plain text, ~100 words, ends with the standard sign-off"},
            "resume": RESUME_SCHEMA,
        }, "required": ["job_id", "contact_id", "subject", "body", "resume"]},
    },
    {
        "name": "build_resume",
        "description": "Portal lane: build the tailored resume PDF for a job and return its absolute path for the Chrome file upload tool.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"},
            "resume": RESUME_SCHEMA,
        }, "required": ["job_id", "resume"]},
    },
    {
        "name": "save_portal_prefill",
        "description": "Portal / Easy Apply / Job board lane: record a prefilled (NOT submitted) form. ready=true ONLY when you reached the final Review/Submit page and every required field is answered (Akshay can then approve it with one click). Returns {portal_id, resume_path, ready}.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"},
            "ats": {"type": "string", "description": "greenhouse | lever | ashby | linkedin_easy_apply | workable | recruitee | workday | ... | company_site (the app detects it from apply_url)"},
            "ready": {"type": "boolean", "default": False,
                      "description": "true = final Review/Submit page reached, no required field blank"},
            "apply_url": {"type": "string"},
            "resume": RESUME_SCHEMA,
            "questions_left": {"type": "array", "items": {"type": "string"},
                               "description": "Every field left empty for Akshay, e.g. 'Work authorization (US) — left for you'"},
        }, "required": ["job_id", "ats", "apply_url", "resume", "questions_left"]},
    },
    {
        "name": "get_submit_queue",
        "description": "Submit lane: the applications Akshay approved in his dashboard (clicked 'Submit selected'), with job URL, apply URL, resume path and every answer filled earlier. Submit ONLY these.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mark_submitted",
        "description": "Submit lane: call right after LinkedIn/the ATS confirms the application was sent (e.g. 'Your application was sent'). Refuses rows Akshay did not approve.",
        "inputSchema": {"type": "object", "properties": {
            "portal_id": {"type": "integer"},
            "confirmation": {"type": "string", "description": "the confirmation text you saw"},
        }, "required": ["portal_id", "confirmation"]},
    },
    {
        "name": "mark_submit_failed",
        "description": "Submit lane: you could not submit this approved application (a new required question, job closed, login wall, error). It goes back to Akshay's queue.",
        "inputSchema": {"type": "object", "properties": {
            "portal_id": {"type": "integer"},
            "reason": {"type": "string"},
            "still_ready": {"type": "boolean", "default": False},
        }, "required": ["portal_id", "reason"]},
    },
    {
        "name": "list_portal_candidates",
        "description": "Portal lane: recent qualified jobs (and jobs skipped only for 'no email found') that have no portal prefill yet, best first.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "default": 15, "minimum": 1, "maximum": 50},
            "days": {"type": "integer", "default": 3, "minimum": 1, "maximum": 30},
        }},
    },
    {
        "name": "lookup_answers",
        "description": "Akshay's question bank. Call once per form step with every question on that step (label text, field type, options). Returns, per question, found=true + answer (+ the best matching option) when he already answered the same question; status 'mine' means leave it blank for him; `candidates` are near matches — use one only if it asks exactly the same thing (same skill, same country, same currency).",
        "inputSchema": {"type": "object", "properties": {
            "questions": {"type": "array", "items": {"type": "object", "properties": {
                "question": {"type": "string", "description": "the field label exactly as shown"},
                "field_type": {"type": "string", "enum": ["text", "number", "yes_no", "choice", "multi", "date", "textarea", "checkbox", "other"]},
                "options": {"type": "array", "items": {"type": "string"}},
            }, "required": ["question"]}},
        }, "required": ["questions"]},
    },
    {
        "name": "report_questions",
        "description": "Send questions you could NOT answer (not in the question bank, not in the standard answers) to Akshay's Questions tab. He answers them once; next time lookup_answers returns them. Then leave those fields blank and carry on — never wait for him.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"},
            "questions": {"type": "array", "items": {"type": "object", "properties": {
                "question": {"type": "string"},
                "field_type": {"type": "string", "enum": ["text", "number", "yes_no", "choice", "multi", "date", "textarea", "checkbox", "other"]},
                "options": {"type": "array", "items": {"type": "string"}},
                "required": {"type": "boolean"},
            }, "required": ["question"]}},
        }, "required": ["questions"]},
    },
    {
        "name": "record_answers",
        "description": "After filling a form step, log EVERY answer you put in (Akshay keeps an Excel log and the stable answers are reused next time). source: bank (from lookup_answers), standard (standard answers rules), profile, resume, linkedin (already prefilled by LinkedIn and kept), ai (your own judgement). per_job=true for answers that depend on this job (salary/CTC picked from the JD, dates, free text).",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "integer"},
            "answers": {"type": "array", "items": {"type": "object", "properties": {
                "question": {"type": "string"},
                "answer": {"type": "string"},
                "field_type": {"type": "string", "enum": ["text", "number", "yes_no", "choice", "multi", "date", "textarea", "checkbox", "other"]},
                "options": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string", "enum": ["bank", "standard", "profile", "resume", "linkedin", "ai"]},
                "per_job": {"type": "boolean"},
            }, "required": ["question", "answer"]}},
        }, "required": ["answers"]},
    },
    {
        "name": "set_preferred_browser",
        "description": "Lock Job Hunter to Akshay's own Chrome on this laptop: pass its deviceId (and name) from list_connected_browsers. Once set, only that browser is ever used.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "device_id": {"type": "string"}},
                        "required": ["device_id"]},
    },
    {
        "name": "get_rules",
        "description": "Return the honesty / skip / targeting rules text.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
AUTO_SUBMIT_LANES = ("easy_apply", "portal", "board_apply")


def _auto_submit_on(ctx):
    """Akshay, 29-30 Oct: forms are submitted right after they are filled (config auto_submit, default on)."""
    try:
        return bool((ctx.cfg() or {}).get("auto_submit", True))
    except Exception:
        return False


TOOL_NAMES = {t["name"] for t in TOOLS}


# --------------------------------------------------------------------------- context

class Ctx:
    def __init__(self):
        self.conn = None
        self.actions = None
        self.db = None
        self.import_error = None
        rid = (os.environ.get("JOBHUNTER_RUN_ID") or "").strip()
        try:
            self.run_id = int(rid) if rid else None
        except ValueError:
            self.run_id = None
        self.lane = os.environ.get("JOBHUNTER_LANE") or "engine"
        self.db_path = os.environ.get("JOBHUNTER_DB") or None

    def ensure(self):
        if self.actions is None and self.import_error is None:
            try:
                import jh.actions as actions  # noqa
                import jh.db as db  # noqa
                self.actions, self.db = actions, db
            except Exception as e:
                self.import_error = "cannot import jh.actions/jh.db from %s: %s" % (APP_ROOT, e)
                log(self.import_error)
        if self.import_error:
            raise RuntimeError(self.import_error)
        if self.conn is None:
            self.conn = self.db.connect(self.db_path) if self.db_path else self.db.connect()
            try:
                self.conn.execute("PRAGMA busy_timeout=10000")
            except Exception:
                pass
        return self.conn

    def cfg(self):
        """Fresh config each call (dry_run / caps can change during a run)."""
        try:
            import jh.config as jc  # optional
            for fn in ("load_config", "load"):
                f = getattr(jc, fn, None)
                if callable(f):
                    try:
                        c = f()
                        if isinstance(c, dict):
                            return c
                    except TypeError:
                        pass
        except Exception:
            pass
        p = os.environ.get("JOBHUNTER_CONFIG")
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as e:
                log("config read failed:", e)
        return {}


def _norm_resume(r):
    if not isinstance(r, dict):
        return {}
    r = dict(r)
    sk = r.get("skills")
    if isinstance(sk, list):
        rows = []
        for row in sk:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                rows.append([str(row[0]), str(row[1])])
            elif isinstance(row, dict):
                rows.append([str(row.get("label", "")), str(row.get("text", ""))])
        r["skills"] = rows
    po = r.get("project_order")
    if isinstance(po, list):
        r["project_order"] = [k for k in (str(x).strip().lower() for x in po) if k in PROJECT_KEYS]
    return r


def _int(v, name):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer" % name)


def _portal_candidates(conn, limit=15, days=3):
    """Read-only query over the documented schema (fallback when jh.actions has no helper)."""
    import datetime as dt
    since = (dt.datetime.now() - dt.timedelta(days=int(days))).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT j.id, j.company, j.role, j.url, j.apply_url, j.fit_score, j.source, j.status, j.skip_reason
             FROM jobs j
            WHERE (j.status IN ('qualified','contacted')
                   OR (j.status='skipped' AND lower(coalesce(j.skip_reason,'')) LIKE 'no email%'))
              AND coalesce(j.created_at,'') >= ?
              AND NOT EXISTS (SELECT 1 FROM portal_apps p WHERE p.job_id=j.id)
            ORDER BY coalesce(j.fit_score,0) DESC, j.id DESC
            LIMIT 200""", (since,)).fetchall()
    out = []
    for r in rows:
        d = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else dict(zip(
            ["id", "company", "role", "url", "apply_url", "fit_score", "source", "status", "skip_reason"], r))
        au = (d.get("apply_url") or "").lower()
        d["ats_link"] = any(h in au for h in ATS_HOSTS)
        out.append(d)
    out.sort(key=lambda d: (not d["ats_link"], -(d.get("fit_score") or 0)))
    return out[: int(limit)]


def _rules_text():
    p = HERE / "prompts" / "common.md"
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return "Rules file missing: %s" % p


def _valid_job_id(conn, v):
    """int id of an existing job, else None (booleans and unknown ids are dropped)."""
    if isinstance(v, bool) or v in (None, ""):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    try:
        return v if conn.execute("SELECT 1 FROM jobs WHERE id=?", (v,)).fetchone() else None
    except Exception:
        return None


def call_tool(ctx, name, args):
    """Execute a tool. Returns (result_obj, is_error)."""
    args = args if isinstance(args, dict) else {}
    if name not in TOOL_NAMES:
        return {"error": "unknown tool: %s" % name}, True
    if name == "get_rules":
        text = None
        try:
            import jh.actions as _a
            f = getattr(_a, "get_rules", None)
            text = f() if callable(f) else None
        except Exception:
            text = None
        return {"rules": (text + "\n\n" + _rules_text()) if text else _rules_text()}, False
    conn = ctx.ensure()
    a = ctx.actions
    rid, lane = ctx.run_id, ctx.lane

    if name == "set_preferred_browser":
        nm = str(args.get("name") or "").strip()[:120]
        dev = str(args.get("device_id") or "").strip()[:64]
        if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", dev):
            return {"error": "device_id (from list_connected_browsers) is required"}, True
        try:
            import jh.config as jc
            cfg = jc.load()
            eng = cfg.setdefault("engine", {})
            cur = str(eng.get("chrome_device_id") or "").strip()
            if dev in [str(x) for x in (eng.get("exclude_device_ids") or [])]:
                return {"error": "that Chrome belongs to another profile — find this profile's own Chrome"}, True
            if cur and cur != dev:
                return {"error": "the Chrome browser is locked to deviceId %s (Akshay changes it in Settings) — use only"
                                 " that one" % cur}, True
            eng["chrome_device_id"] = dev
            if nm:
                eng["chrome_browser"] = nm
            jc.save(cfg)
        except Exception as e:
            return {"error": "could not save: %s" % e}, True
        a.log_step(conn, rid, lane, "Locked to Chrome %s (deviceId %s): the only browser used from now on" % (nm or "-", dev),
                   level="info")
        return {"ok": True, "device_id": dev}, False

    if name in ("get_job", "set_job_status"):
        try:
            jid = _int(args.get("job_id"), "job_id")
        except ValueError as e:
            return {"error": str(e)}, True
        if name == "get_job":
            res = a.get_job(conn, jid)
        else:
            res = a.set_job_status(conn, rid, lane, jid, str(args.get("status") or ""), args.get("reason"),
                                   note=args.get("note") if isinstance(args.get("note"), str) else None)
        return res, bool(res.get("error"))

    if name == "screen_jobs":
        import jh.screen as screen
        import jh.seen as seen
        items = [x for x in (args.get("items") or []) if isinstance(x, dict)][:60]
        src = str(args.get("source") or lane)
        res = seen.filter_jobs(conn, items)
        by_url = {}
        for x in items:
            by_url.setdefault((str(x.get("company") or "").strip(), str(x.get("role") or "").strip(), str(x.get("url") or "").strip()), x)
        work, dropped = [], {}
        for e in res["new"]:
            x = by_url.get((e["company"], e["role"], e["url"])) or {}
            ok, why = screen.screen(e["role"], str(x.get("text") or "")[:4000], str(x.get("location") or ""))
            if ok or not e["company"] or not e["role"]:
                work.append(e)
                continue
            a.save_job(conn, rid, lane, src, e["company"], e["role"], location=str(x.get("location") or "")[:200],
                       url=e["url"], decision="skipped", skip_reason=why, jd_text=str(x.get("text") or "")[:4000],
                       notes="screened by Job Hunter (no AI)")
            k = why.split(" (")[0]
            dropped[k] = dropped.get(k, 0) + 1
        a.log_step(conn, rid, lane, "Screened %d cards: %d to work, %d already handled earlier, %d skipped by rules%s"
                   % (len(items), len(work), len(res["seen"]), sum(dropped.values()),
                      (" (%s)" % ", ".join("%d %s" % (n, k) for k, n in dropped.items())) if dropped else ""))
        return {"work": work, "retry": res["retry"], "already_handled": len(res["seen"]), "skipped_by_rules": dropped}, False

    if name in ("filter_new_jobs", "filter_new_profiles", "record_profile_lookup"):
        import jh.seen as seen
        if name == "filter_new_jobs":
            items = args.get("items") or []
            if not isinstance(items, list):
                return {"error": "items must be a list"}, True
            res = seen.filter_jobs(conn, items)
            c = res["counts"]
            if c["seen"] or c["retry"]:
                a.log_step(conn, rid, lane, "%d new, %d already handled earlier (not reopened)%s"
                           % (c["new"], c["seen"], ", %d retried for a contact" % c["retry"] if c["retry"] else ""))
            return res, False
        if name == "filter_new_profiles":
            urls = args.get("urls") or []
            if not isinstance(urls, list):
                return {"error": "urls must be a list"}, True
            return seen.filter_profiles(conn, urls), False
        res = seen.record_profile(conn, str(args.get("url") or ""), found=args.get("found") is True, lane=lane)
        return res, bool(res.get("error"))

    if name == "record_answers":
        import jh.qa as qa
        ans = args.get("answers") or []
        if not isinstance(ans, list):
            return {"error": "answers must be a list"}, True
        jid = _valid_job_id(conn, args.get("job_id"))
        return qa.record(conn, rid, lane, jid, ans), False

    if name in ("lookup_answers", "report_questions"):
        import jh.qa as qa
        qs_ = args.get("questions") or []
        if not isinstance(qs_, list):
            return {"error": "questions must be a list"}, True
        if name == "lookup_answers":
            return qa.lookup(conn, qs_), False
        jid = _valid_job_id(conn, args.get("job_id"))
        res = qa.report(conn, rid, lane, jid, qs_)
        if res.get("new"):
            a.log_step(conn, rid, lane, "%d new question%s for you in the Questions tab"
                       % (res["new"], "" if res["new"] == 1 else "s"), level="warn")
        return res, False

    if name == "log_step":
        msg = str(args.get("message") or "").strip()
        if not msg:
            return {"error": "message is required"}, True
        lvl = args.get("level") or "info"
        if lvl not in ("info", "success", "warn", "error"):
            lvl = "info"
        a.log_step(conn, rid, lane, msg[:500], level=lvl)
        return {"ok": True}, False

    if name == "already_contacted":
        emails = args.get("emails") or []
        if isinstance(emails, str):
            emails = [emails]
        li = str(args.get("linkedin") or "").strip()
        extra = {"linkedin": li} if li else {}
        res = a.already_contacted(conn, str(args.get("company") or ""), str(args.get("role") or ""), list(emails),
                                  **extra)
        return res, False

    if name == "save_job":
        if str(args.get("source") or "").strip().lower() == "agency":
            return {"error": "source 'agency' is reserved for the app's agency pitch jobs"}, True
        decision = args.get("decision") or "qualified"
        if decision not in ("qualified", "skipped"):
            return {"error": "decision must be qualified or skipped"}, True
        if decision == "skipped" and not args.get("skip_reason"):
            return {"error": "skip_reason is required when decision=skipped"}, True
        fs = args.get("fit_score")
        try:
            fs = None if fs is None else max(0, min(100, int(fs)))
        except (TypeError, ValueError):
            fs = None
        jd = str(args.get("jd_text") or "")[:20000]
        res = a.save_job(conn, rid, lane, str(args.get("source") or lane), str(args.get("company") or ""),
                         str(args.get("role") or ""), location=str(args.get("location") or ""),
                         remote=bool(args.get("remote", True)), url=str(args.get("url") or ""),
                         posted=str(args.get("posted") or ""), jd_text=jd, decision=decision,
                         skip_reason=args.get("skip_reason"), notes=args.get("notes"), fit_score=fs,
                         apply_url=args.get("apply_url"))
        return res, bool(isinstance(res, dict) and res.get("error"))

    if name == "find_email":
        import jh.config as jc
        from jh import emailfind
        jid = _int(args.get("job_id"), "job_id")
        if not conn.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
            return {"error": "job %s not found" % jid}, True
        res = emailfind.find(conn, jc.load(), jid, str(args.get("company") or "")[:150], str(args.get("domain") or ""),
                             str(args.get("first_name") or ""), str(args.get("last_name") or ""))
        a.log_step(conn, rid, lane, "Email lookup for %s (%s): %d verified (%s)" % (
            str(args.get("company") or "")[:60], res.get("domain") or "no domain", len(res["candidates"]),
            ", ".join(res["tried"]) or "nothing to try"), level="info")
        return res, False

    if name == "save_contact":
        res = a.save_contact(conn, rid, lane, _int(args.get("job_id"), "job_id"), str(args.get("name") or ""),
                             str(args.get("title") or ""), str(args.get("email") or "").strip(),
                             str(args.get("email_source") or ""), linkedin=args.get("linkedin"))
        return res, bool(isinstance(res, dict) and res.get("error"))

    if name == "send_application":
        res = a.send_application(conn, rid, lane, _int(args.get("job_id"), "job_id"),
                                 _int(args.get("contact_id"), "contact_id"), str(args.get("subject") or ""),
                                 str(args.get("body") or ""), _norm_resume(args.get("resume")), ctx.cfg())
        return res, bool(isinstance(res, dict) and not res.get("ok"))

    if name == "build_resume":
        path = a.build_resume_only(conn, _int(args.get("job_id"), "job_id"), _norm_resume(args.get("resume")))
        return {"resume_path": str(path)}, False

    if name == "save_portal_prefill":
        ql = args.get("questions_left") or []
        if isinstance(ql, str):
            ql = [ql]
        res = a.save_portal_prefill(conn, rid, lane, _int(args.get("job_id"), "job_id"), str(args.get("ats") or ""),
                                    str(args.get("apply_url") or ""), _norm_resume(args.get("resume")),
                                    [str(q) for q in ql], ctx.cfg(), ready=args.get("ready") is True)
        if isinstance(res, dict) and not res.get("error") and res.get("portal_id") and lane in AUTO_SUBMIT_LANES \
                and _auto_submit_on(ctx):
            pid = res["portal_id"]
            row = conn.execute("SELECT ready FROM portal_apps WHERE id=?", (pid,)).fetchone()
            if row and row["ready"]:
                ap = a.approve_submit(conn, [pid])
                if ap.get("approved"):
                    res = dict(res, submit_now=True,
                               next="Auto-submit is ON: on this same form untick Follow, tick the required consent boxes,"
                                    " click the final Submit once, then mark_submitted(portal_id, confirmation text)"
                                    " or mark_submit_failed(portal_id, reason).")
                else:
                    res = dict(res, submit_now=False,
                               submit_note=(ap.get("rejected") or [{}])[0].get("reason") or "not allowed")
            else:
                res = dict(res, submit_now=False)
        return res, bool(isinstance(res, dict) and res.get("error"))

    if name == "get_submit_queue" and lane != "submit":
        return {"error": "get_submit_queue is only available in the submit lane"}, True
    if name in ("mark_submitted", "mark_submit_failed") and lane != "submit" and not (
            lane in AUTO_SUBMIT_LANES and _auto_submit_on(ctx)):
        return {"error": "%s is only available in the submit lane (or a form lane with auto-submit on)" % name}, True

    if name == "get_submit_queue":
        return {"queue": a.submit_queue(conn, claim=True)}, False

    if name in ("mark_submitted", "mark_submit_failed"):
        pid = args.get("portal_id")
        if isinstance(pid, bool):
            return {"error": "portal_id must be an integer"}, True
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return {"error": "portal_id must be an integer"}, True
        if name == "mark_submitted":
            res = a.mark_submitted(conn, rid, lane, pid, str(args.get("confirmation") or ""))
        else:
            res = a.mark_submit_failed(conn, rid, lane, pid, str(args.get("reason") or "unknown"),
                                       still_ready=args.get("still_ready") is True)
        return res, bool(res.get("error"))

    if name == "list_portal_candidates":
        limit = int(args.get("limit") or 15)
        days = int(args.get("days") or 3)
        f = getattr(a, "list_portal_candidates", None)
        rows = f(conn, limit=limit, days=days) if callable(f) else _portal_candidates(conn, limit, days)
        return {"jobs": rows}, False

    return {"error": "not implemented: %s" % name}, True


# --------------------------------------------------------------------------- JSON-RPC

def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(ctx, msg):
    """Handle one decoded JSON-RPC message; return a response dict or None."""
    if not isinstance(msg, dict):
        return _err(None, -32600, "Invalid Request")
    mid = msg.get("id")
    method = msg.get("method")
    is_notification = "id" not in msg
    params = msg.get("params") or {}
    if method is None:
        return None  # a response from the client; ignore
    try:
        if method == "initialize":
            pv = params.get("protocolVersion") or DEFAULT_PROTOCOL
            res = {"protocolVersion": pv,
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                   "instructions": "Job Hunter app tools: log every step with log_step, check already_contacted, save every job with save_job."}
            return None if is_notification else _ok(mid, res)
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return None if is_notification else _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": TOOLS})
        if method in ("resources/list",):
            return _ok(mid, {"resources": []})
        if method in ("resources/templates/list",):
            return _ok(mid, {"resourceTemplates": []})
        if method in ("prompts/list",):
            return _ok(mid, {"prompts": []})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                res, is_err = call_tool(ctx, name, args)
            except Exception as e:
                log("tool %s failed: %s\n%s" % (name, e, traceback.format_exc()))
                res, is_err = {"error": "%s: %s" % (type(e).__name__, e)}, True
            return _ok(mid, {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, default=str)}],
                             "isError": bool(is_err)})
        if is_notification:
            return None
        return _err(mid, -32601, "Method not found: %s" % method)
    except Exception as e:
        log("handler error:", e, traceback.format_exc())
        return None if is_notification else _err(mid, -32603, "Internal error: %s" % e)


def _binary_stdio():
    if os.name == "nt":
        try:
            import msvcrt
            msvcrt.setmode(0, os.O_BINARY)
            msvcrt.setmode(1, os.O_BINARY)
        except Exception:
            pass
    inp = sys.stdin.buffer if (sys.stdin is not None and hasattr(sys.stdin, "buffer")) else os.fdopen(0, "rb")
    out = sys.stdout.buffer if (sys.stdout is not None and hasattr(sys.stdout, "buffer")) else os.fdopen(1, "wb")
    return inp, out


def serve(inp=None, out=None, ctx=None):
    if inp is None or out is None:
        inp, out = _binary_stdio()
    ctx = ctx or Ctx()
    log("started (lane=%s run=%s db=%s root=%s)" % (ctx.lane, ctx.run_id, ctx.db_path, APP_ROOT))

    def write(obj):
        out.write(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
        out.flush()

    while True:
        raw = inp.readline()
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            write(_err(None, -32700, "Parse error"))
            continue
        if isinstance(msg, list):  # batch (older protocol revisions)
            resps = [r for r in (handle(ctx, m) for m in msg) if r is not None]
            if resps:
                write(resps)
            continue
        resp = handle(ctx, msg)
        if resp is not None:
            write(resp)
    if ctx.conn is not None:
        try:
            ctx.conn.close()
        except Exception:
            pass
    log("stdin closed, exiting")


if __name__ == "__main__":
    try:
        serve()
    except KeyboardInterrupt:
        pass
