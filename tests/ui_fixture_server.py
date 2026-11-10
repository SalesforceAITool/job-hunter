#!/usr/bin/env python3
"""UI fixture server for Job Hunter (stdlib only).

Serves web/ plus fake JSON for every endpoint in SPEC §6 and an SSE stream that
replays a realistic scripted run on a loop, so the dashboard can be developed and
screenshot-tested without the real backend, Claude, Chrome or Gmail.

    python tests/ui_fixture_server.py                 # http://127.0.0.1:8766, auto-loops runs
    python tests/ui_fixture_server.py --manual        # runs start only via Run now / POST /api/run
    python tests/ui_fixture_server.py --fresh         # first-run state: no Gmail, no Claude CLI, dry run on
    python tests/ui_fixture_server.py --missing /api/charts,/api/portal   # simulate backend 404s
    python tests/ui_fixture_server.py --speed 3       # run the script 3x faster
"""
import argparse
import copy
import json
import mimetypes
import os
import queue
import random
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def deep_merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def now():
    return datetime.now(IST)


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def iso(dt):
    return dt.replace(microsecond=0).isoformat()


def days_ago(d, hour=10, minute=0):
    t = now() - timedelta(days=d)
    return t.replace(hour=hour, minute=minute, second=0, microsecond=0)


# ─────────────────────────── Seed content ───────────────────────────

JD_TEMPLATE = """About {company}
{company} is growing its Salesforce team and is looking for a {role} to join a distributed engineering group working US hours.

What you'll do
- Design and build Apex classes, triggers and batch jobs that follow trigger-framework best practices
- Build Lightning Web Components for Sales Cloud and Service Cloud users
- Own integrations with REST/SOAP APIs, Platform Events and Named Credentials
- Work with admins on Flows, permission sets and release management (SFDX, GitHub Actions)
- Review code, write unit tests with 90%+ coverage, and mentor junior developers

What we're looking for
- 5+ years of hands-on Salesforce development (Apex, LWC, SOQL/SOSL)
- Experience with Experience Cloud or Agentforce is a plus
- Salesforce Platform Developer I required; Developer II preferred
- Comfortable working remotely with a US-based team, overlapping at least 4 hours with US Eastern

Engagement
Contract, 6–12 months with possibility of extension. Remote.
"""

INITIAL_BODY = """Hi {first},

I came across the {role} opening at {company} and wanted to reach out directly. I'm a Salesforce developer with 7+ years on Apex, LWC and integrations, most recently building Experience Cloud portals and Platform Event–driven integrations for a US healthcare client.

A couple of things that line up with the role:
- I've shipped LWC-heavy Service Cloud consoles used by 400+ agents
- I own CI/CD with SFDX and GitHub Actions on my current project

I'm based in India and work US hours as a contractor, so I can overlap fully with your team. My resume is attached — happy to share code samples or jump on a quick call.

Thanks,
Akshay Verma"""

NUDGE1_BODY = """Hi {first},

Just bumping this up in case it got buried. I'm still very interested in the {role} role and can start within two weeks.

Thanks,
Akshay Verma"""

NUDGE2_BODY = """Hi {first},

Following up one last time on the {role} position. If it's filled or not a fit, no worries at all — I'd appreciate a quick note either way.

Thanks,
Akshay Verma"""

#  (company, role, source, contact, title, email, email_source, sent_days_ago, status, fit, extra)
SEED_APPS = [
    ("Inspira Financial", "Senior Salesforce Developer", "jobright", "Patrick Soule", "Talent Acquisition Partner", "patrick.soule@inspirafinancial.com", "SalesQL", 4, "replied", 88,
     {"reply": (1, "Re: Senior Salesforce Developer — Akshay Verma", "Hi Akshay, thanks for reaching out — your Apex and LWC background lines up well with what the team needs. Are you free for a 30-minute call Tuesday or Wednesday morning, US Eastern?"), "nudges": [3]}),
    ("NeuraFlash", "Salesforce Developer — Agentforce", "posts", "Nicola Bryan", "Senior Technical Recruiter", "nicola.bryan@neuraflash.com", "post", 2, "replied", 91,
     {"reply": (0.2, "Re: Salesforce Developer — Agentforce", "Thanks Akshay. We do engage contractors in India for our Agentforce practice. I've shared your resume with the hiring manager and will get back to you by Friday."), "nudges": []}),
    ("RxCloud", "Salesforce Developer (1099)", "posts", "Arjun Vegesna", "Partner & CEO", "arjun.vegesna@therxcloud.com", "post", 3, "bounced", 72, {"bounce": 3}),
    ("CrossCountry Consulting", "Senior Salesforce Developer", "import", "Katie Martino", "Talent Acquisition Director", "kmartino@crosscountry-consulting.com", "import", 12, "nudge2", None, {"nudges": [10, 5], "done": True}),
    ("CrossCountry Consulting", "Senior Salesforce Developer", "import", "Emile Karam", "Managing Director, Salesforce Practice", "ekaram@crosscountry-consulting.com", "import", 11, "nudge1", None, {"nudges": [8]}),
    ("GBG Plc", "Salesforce Developer (4057)", "import", "GBG Talent Attraction", "Hiring inbox", "behired@gbgplc.com", "import", 12, "nudge2", None, {"nudges": [10, 5], "done": True}),
    ("Pashtek", "Salesforce Developer (part-time)", "import", "Arvind Kumar", "Senior IT Recruiter", "arvind@pashtek.com", "import", 12, "nudge2", None, {"nudges": [10, 5], "done": True}),
    ("Pashtek", "Salesforce Developer (part-time)", "import", "Sundhar Kasinathan", "CEO", "sundhar@pashtek.com", "import", 11, "nudge1", None, {"nudges": [8]}),
    ("Techouts", "Salesforce Developer", "import", "Poojitha K", "Talent Acquisition Specialist", "poojitha.k@techouts.com", "import", 11, "nudge1", None, {"nudges": [9]}),
    ("MillerKnoll", "Senior Software Engineer — Salesforce", "jobright", "Quinn Dorsey", "Talent Acquisition Partner", "quinn_dorsey@millerknoll.com", "SalesQL", 6, "nudge1", 79, {"nudges": [4]}),
    ("Celestica", "Senior Salesforce Developer / Admin", "linkedin_world", "Joyeeta Mitra", "Talent Acquisition Manager", "jo.mitra@celestica.com", "SalesQL", 6, "nudge1", 74, {"nudges": [5]}),
    ("Celestica", "Senior Salesforce Developer / Admin", "linkedin_world", "Ryan Salmon", "Talent Acquisition Leader", "ryan.salmon@celestica.com", "SalesQL", 6, "sent", 74, {}),
    ("Vibrant Emotional Health", "Senior Engineer, Salesforce", "jobright", "Melissa Cox", "Talent Acquisition Manager", "mcox@vibrant.org", "JobRight", 9, "nudge2", 81, {"nudges": [8, 4], "done": True}),
    ("Touchpoints", "Senior Salesforce Developer", "posts", "Naren Kini", "CEO", "naren.kini@touchpointsinc.com", "post", 5, "nudge1", 77, {"nudges": [4]}),
    ("Tekion", "Salesforce Technical Lead Engineer", "linkedin_world", "Niharika Sahu", "Talent Acquisition", "niharikas@tekion.com", "SalesQL", 2, "sent", 83, {}),
    ("CGI", "Senior Salesforce Developer (OmniStudio)", "linkedin_us", "Tatya Dave", "Talent Acquisition Specialist", "tatya.dave@cgi.com", "SalesQL", 1, "nudge1", 70, {"nudges": [0.1]}),
    ("Holman", "Salesforce Architect", "linkedin_us", "Josie Burns", "Talent Acquisition Specialist", "josie.burns@holman.com", "SalesQL", 0.13, "sent", 76, {}),
    ("Versapay", "Salesforce Developer", "jobright", "Elena Ruiz", "Senior Technical Recruiter", "elena.ruiz@versapay.com", "SalesQL", 0.14, "sent", 86, {}),
    ("Monogram Health", "Senior Salesforce Engineer", "posts", "Dana Whitfield", "Director, Talent Acquisition", "dana.whitfield@monogramhealth.com", "post", 0.15, "sent", 84, {}),
    ("Codiot Technologies", "Salesforce Developer (Contract)", "posts", "Rahul Mehta", "Founder & CEO", "rahul@codiot.com", "post", 0.16, "sent", 90, {}),
    ("Cloudsquare", "Salesforce LWC Developer", "jobright", "Hannah Lowe", "People Operations Lead", "hannah@cloudsquare.io", "Apollo", 0.9, "dry_run", 68, {}),
    ("Kizen", "Senior Salesforce Engineer", "linkedin_us", "Marcus Hale", "Head of Talent", "marcus@kizen.com", "SalesQL", 1.1, "sent", 73, {}),
]

SEED_SKIPPED = [
    ("Cherokee Federal", "Salesforce Developer", "jobright", "US citizenship + Public Trust required", 22, 7),
    ("DMI", "Salesforce Developer", "jobright", "US work eligibility + security clearance required", 25, 7),
    ("TechnoSphere", "Senior Salesforce Developer", "linkedin_us", "Onsite Tysons, VA + FSC mandatory", 41, 7),
    ("Peak & Peak", "Salesforce Developer (m/f/d)", "linkedin_world", "Germany role, German language required", 38, 6),
    ("Docker", "Senior Salesforce Developer", "linkedin_us", "No visa sponsorship, US/Canada employees only", 47, 6),
    ("CMG Financial", "Senior Salesforce Developer", "jobright", "Must be authorized to work in the US", 44, 5),
    ("Accenture Federal Services", "Salesforce Developer", "linkedin_us", "US citizenship required", 20, 5),
    ("Lovesac", "Sr. Engineer, Salesforce", "linkedin_us", "Must reside in the US, no sponsorship", 52, 0.14),
    ("Noblesoft Technologies", "CPQ Salesforce Developer", "posts", "W2 contract only", 58, 3),
    ("GranularTek LLC", "Senior Salesforce Developer", "jobright", "No verified recruiter email found", 66, 2),
    ("Monks", "Salesforce Architect", "linkedin_world", "Only the Co-CEO found at an 8k-person company; no recruiter email", 61, 2),
    ("Kforce", "Salesforce Developer", "jobright", "Agency posting, end client not named", 49, 0.15),
    ("Flourish & Thrive Labs", "Junior Salesforce Developer", "posts", "Junior role", 35, 1),
]

SEED_PORTAL = [
    ("Monogram Health", "Senior Salesforce Engineer", "Lever", "https://jobs.lever.co/monogramhealth/7d1c2c9e-salesforce", "needs_input", 84, 0.14,
     ["Are you legally authorized to work in the United States?", "Will you now or in the future require visa sponsorship?",
      "EEO: gender, race, veteran and disability status (voluntary)", "I have read and agree to the Privacy Notice (checkbox)"]),
    ("Codiot Technologies", "Salesforce Developer (Contract)", "Ashby", "https://jobs.ashbyhq.com/codiot/1f6a5b0e-salesforce-developer", "prefilled", 90, 0.13,
     ["Expected hourly rate (USD)"]),
    ("Versapay", "Salesforce Developer", "Greenhouse", "https://boards.greenhouse.io/versapay/jobs/4458120", "submitted", 86, 1.2, []),
    ("Gravity Infosolutions", "Salesforce Developer", "linkedin_easy_apply", "https://www.linkedin.com/jobs/view/4480011111/", "prefilled", 82, 0.05, []),
    ("Runmap Infotech", "Senior Salesforce (FSL) Developer", "linkedin_easy_apply", "https://www.linkedin.com/jobs/view/4480022222/", "prefilled", 78, 0.04, []),
]

DEFAULT_ARGS = ["--chrome", "--permission-mode", "dontAsk", "--allowedTools", "mcp__jobhunt__*", "mcp__claude-in-chrome__*",
                "--disallowedTools", "Agent", "--strict-mcp-config", "--max-turns", "150"]

# Jobs the scripted live run discovers (lane -> list of job specs)
LIVE_JOBS = {
    "posts": [
        {"company": "Ascendix Technologies", "role": "Salesforce Developer (Contract)", "source": "posts", "fit": 87,
         "contact": ("Priya Nair", "Talent Acquisition Lead", "priya.nair@ascendix.com", "post")},
        {"company": "Vertex Cloud", "role": "Salesforce Administrator", "source": "posts", "skip": "Admin-only role, no development", "fit": 31},
    ],
    "jobright": [
        {"company": "Capio Group", "role": "Senior Salesforce Developer", "source": "jobright", "fit": 89,
         "contact": ("Megan Torres", "Recruiting Manager", "megan.torres@capiogroup.com", "SalesQL"),
         "portal": ("Greenhouse", "https://boards.greenhouse.io/capiogroup/jobs/5120334",
                    ["Are you legally authorized to work in the US?", "Do you require sponsorship?", "EEO self-identification (voluntary)"])},
        {"company": "Leidos", "role": "Salesforce Developer", "source": "jobright", "skip": "US citizenship + Secret clearance required", "fit": 24},
        {"company": "Paylocity", "role": "Senior Salesforce Developer", "source": "jobright", "skip": "Must reside in the US", "fit": 45},
    ],
    "linkedin_jobs": [
        {"company": "Sagent Lending", "role": "Salesforce Developer", "source": "linkedin_us", "fit": 78,
         "contact": ("Brooke Hanley", "Technical Recruiter", "brooke.hanley@sagent.com", "SalesQL")},
        {"company": "Hexaware", "role": "Salesforce Lead (India remote)", "source": "linkedin_world", "fit": 82,
         "contact": ("Sneha Kulkarni", "Talent Acquisition Manager", "snehak@hexaware.com", "SalesQL")},
        {"company": "Fuze Health", "role": "Software Engineer — Salesforce", "source": "linkedin_us", "skip": "US work authorization required", "fit": 40},
    ],
}


LIVE_PLAN = {
    "jobs": LIVE_JOBS, "reply": "Touchpoints", "nudges": ("Techouts", "MillerKnoll", "Celestica"),
    "portal": [("Capio Group", "Greenhouse", "boards.greenhouse.io/capiogroup/jobs/5120334", 3)],
    "portal_skip": "Sagent Lending",
}

# Run #7 (earlier today) — matches the seeded rows sent ~3 hours ago
BACKLOG_PLAN = {
    "jobs": {
        "posts": [
            {"company": "Codiot Technologies", "role": "Salesforce Developer (Contract)", "source": "posts", "fit": 90,
             "contact": ("Rahul Mehta", "Founder & CEO", "rahul@codiot.com", "post")},
            {"company": "Monogram Health", "role": "Senior Salesforce Engineer", "source": "posts", "fit": 84,
             "contact": ("Dana Whitfield", "Director, Talent Acquisition", "dana.whitfield@monogramhealth.com", "post")},
        ],
        "jobright": [
            {"company": "Versapay", "role": "Salesforce Developer", "source": "jobright", "fit": 86,
             "contact": ("Elena Ruiz", "Senior Technical Recruiter", "elena.ruiz@versapay.com", "SalesQL")},
            {"company": "Kforce", "role": "Salesforce Developer", "source": "jobright", "skip": "Agency posting, end client not named", "fit": 49},
        ],
        "linkedin_jobs": [
            {"company": "Holman", "role": "Salesforce Architect", "source": "linkedin_us", "fit": 76,
             "contact": ("Josie Burns", "Talent Acquisition Specialist", "josie.burns@holman.com", "SalesQL")},
            {"company": "Lovesac", "role": "Sr. Engineer, Salesforce", "source": "linkedin_us", "skip": "Must reside in the US, no sponsorship", "fit": 52},
        ],
    },
    "reply": None, "nudges": ("CGI",),
    "portal": [("Monogram Health", "Lever", "jobs.lever.co/monogramhealth/7d1c2c9e-salesforce", 4),
               ("Codiot Technologies", "Ashby", "jobs.ashbyhq.com/codiot/1f6a5b0e-salesforce-developer", 1)],
    "portal_skip": "Holman",
}


# ─────────────────────────── World ───────────────────────────

class World:
    def __init__(self, fresh=False):
        self.lock = threading.RLock()
        self.fresh = fresh
        self.subs = []
        self.events = []
        self.runs = []
        self.next_event_id = 1
        self.running = False
        self.stop_flag = threading.Event()
        self.run_request = threading.Event()
        self.settings = {
            "port": 8765,
            "email": {"address": "" if fresh else "akshay.verma0031@gmail.com",
                      "app_password": "" if fresh else "abcdefghijklwxyz", "backend": "smtp", "from_name": "Akshay Verma"},
            "dry_run": True if fresh else False,
            "auto_run_on_open": True,
            "parallel_sources": False,
            "max_emails_per_run": 25,
            "engine": {"claude_path": "", "claude_args": list(DEFAULT_ARGS), "lane_timeout_min": 25, "mock": False, "model": ""},
        }
        self.seed()

    # ── seed data ──
    def seed(self):
        self.apps, self.details, self.skipped, self.portal = [], {}, [], []
        self.questions = [
            {"id": 1, "question": "What is your desired hourly rate (USD)?", "answer": None, "status": "open", "field_type": "number",
             "options": [], "times_seen": 4, "times_used": 0, "jobs": [1, 2], "last_company": "EnableComp", "last_lane": "easy_apply"},
            {"id": 2, "question": "Will you now or in the future require sponsorship for employment visa status?", "answer": None,
             "status": "open", "field_type": "yes_no", "options": ["Yes", "No"], "times_seen": 6, "times_used": 0, "jobs": [1],
             "last_company": "Monogram Health", "last_lane": "easy_apply"},
            {"id": 3, "question": "Which time zone do you prefer to work in?", "answer": None, "status": "open", "field_type": "choice",
             "options": ["EST", "CST", "PST", "Flexible"], "times_seen": 1, "times_used": 0, "jobs": [3], "last_company": "Capio Group (via Lever)", "last_lane": "portal"},
            {"id": 4, "question": "Describe a complex Salesforce integration you built.", "answer": None, "status": "open", "field_type": "textarea",
             "options": [], "times_seen": 2, "times_used": 0, "jobs": [4], "last_company": "Codiot Technologies", "last_lane": "portal"},
            {"id": 5, "question": "Notice period (in days)", "answer": "15", "status": "answered", "field_type": "number",
             "options": [], "times_seen": 9, "times_used": 7, "jobs": [], "last_company": "UST", "last_lane": "easy_apply"},
            {"id": 6, "question": "Are you willing to undergo a background check?", "answer": None, "status": "mine", "field_type": "yes_no",
             "options": ["Yes", "No"], "times_seen": 3, "times_used": 0, "jobs": [], "last_company": "EnableComp", "last_lane": "easy_apply"},
        ]
        self.cid = 100
        self.jid = 500
        self.watch = {"on": False, "interval_min": 10, "regions": ["us", "india", "world"], "posts": True,
                      "posts_interval_min": 30, "queued": 0, "found_today": 0, "recent": [], "pending_submit": 0,
                      "last_poll": None, "last_error": None, "backoff_min": 0, "next_poll_in_s": None, "next_posts_in_s": None}
        if self.fresh:
            return  # first run: nothing yet
        for row in SEED_APPS:
            self.add_app(*row)
        for (company, role, source, reason, fit, d) in SEED_SKIPPED:
            self.add_skipped(company, role, source, reason, fit, now() - timedelta(days=d))
        for (company, role, ats, url, status, fit, d, qs) in SEED_PORTAL:
            self.portal.append({"id": len(self.portal) + 1, "job_id": self.jid, "company": company, "role": role, "ats": ats,
                                "apply_url": url, "status": status, "fit_score": fit, "questions": qs,
                                "resume_path": "C:\\Users\\akshay\\jobhunter\\data\\resumes\\Akshay_Verma_Resume_%s.pdf" % re.sub(r"\W+", "_", company),
                                "created_at": iso(now() - timedelta(days=d)), "updated_at": iso(now() - timedelta(days=d * 0.9)),
                                "ready": 0 if qs else 1, "approved_at": None,
                                "answers": [{"question": "Total years of Salesforce experience", "answer": "7", "source": "standard"},
                                            {"question": "Notice period (days)", "answer": "15", "source": "bank"}]})
        self.backlog_run()

    def add_app(self, company, role, source, name, title, email, esrc, sent_d, status, fit, extra, sent_at=None):
        self.cid += 1
        self.jid += 1
        sent = sent_at or (now() - timedelta(days=sent_d))
        if sent_d >= 1 and not sent_at:
            sent = days_ago(int(sent_d), 10 + (self.cid % 8), (self.cid * 7) % 60)
        first = name.split()[0]
        fmt = dict(first=first, role=role, company=company)
        emails = [{"id": self.cid * 10, "kind": "initial", "subject": "%s — Akshay Verma (Salesforce, 7+ yrs)" % role,
                   "body": INITIAL_BODY.format(**fmt), "sent_at": iso(sent),
                   "status": "dry_run" if status == "dry_run" else ("bounced" if status == "bounced" else "sent"),
                   "resume_path": "data/resumes/Akshay_Verma_Resume.pdf"}]
        last = sent
        for i, nd in enumerate(extra.get("nudges", [])):
            t = now() - timedelta(days=nd)
            if nd >= 1:
                t = days_ago(int(nd), 11, 15)
            emails.append({"id": self.cid * 10 + i + 1, "kind": "nudge%d" % (i + 1), "subject": "Re: " + emails[0]["subject"],
                           "body": (NUDGE1_BODY if i == 0 else NUDGE2_BODY).format(**fmt), "sent_at": iso(t), "status": "sent"})
            last = max(last, t)
        replies = []
        reply_snip = None
        replied_at = None
        if "reply" in extra:
            rd, subj, snip = extra["reply"]
            replied_at = now() - timedelta(days=rd)
            replies.append({"from_addr": email, "subject": subj, "snippet": snip, "received_at": iso(replied_at), "kind": "reply"})
            reply_snip = snip
            last = max(last, replied_at)
        if "bounce" in extra:
            replies.append({"from_addr": "mailer-daemon@googlemail.com", "subject": "Delivery Status Notification (Failure)",
                            "snippet": "Address not found: %s — the email account that you tried to reach does not exist." % email,
                            "received_at": iso(sent + timedelta(minutes=2)), "kind": "bounce"})
        nxt = None
        if status in ("sent", "nudge1") and not extra.get("done"):
            base = last
            nxt = base + timedelta(days=1 if status == "sent" else 3)
            if nxt.weekday() >= 5:
                nxt += timedelta(days=7 - nxt.weekday())
        row = {"contact_id": self.cid, "job_id": self.jid, "company": company, "role": role, "source": source,
               "contact_name": name, "contact_title": title, "email": email, "email_source": esrc,
               "sent_at": iso(sent), "last_touch_at": iso(last), "next_followup_at": iso(nxt) if nxt else None,
               "app_status": status, "url": "https://www.linkedin.com/jobs/view/40%07d" % (self.jid * 37 % 9999999),
               "fit_score": fit, "reply_snippet": reply_snip, "replied_at": iso(replied_at) if replied_at else None}
        self.apps.append(row)
        tl = []
        for e in emails:
            lbl = {"initial": "Application sent", "nudge1": "Nudge 1 sent", "nudge2": "Nudge 2 sent"}[e["kind"]]
            if e["status"] == "dry_run":
                lbl += " (dry run, not sent)"
            tl.append({"ts": e["sent_at"], "kind": e["kind"], "status": e["status"], "label": lbl})
        for r in replies:
            tl.append({"ts": r["received_at"], "kind": r["kind"], "status": r["kind"], "label": {"reply": "Reply received", "auto": "Auto-reply", "bounce": "Bounced"}[r["kind"]]})
        tl.sort(key=lambda x: x["ts"])
        if nxt:
            k = "nudge1" if status == "sent" else "nudge2"
            tl.append({"ts": iso(nxt), "kind": k, "status": "due", "label": ("Nudge 1" if k == "nudge1" else "Nudge 2") + " due"})
        self.details[self.cid] = {
            "job": {"id": self.jid, "company": company, "role": role, "source": source,
                    "location": "United States (Remote)" if source not in ("linkedin_world",) else "Remote — Worldwide",
                    "remote": 1, "url": row["url"], "posted": "%d days ago" % max(1, int(sent_d) + 1), "fit_score": fit,
                    "jd_text": JD_TEMPLATE.format(company=company, role=role), "status": status,
                    "created_at": iso(sent - timedelta(minutes=6)), "notes": "Contract-friendly, overlaps US Eastern" if fit and fit > 80 else None,
                    "apply_url": None},
            "contact": {"id": self.cid, "name": name, "title": title, "email": email, "email_source": esrc,
                        "linkedin": "https://www.linkedin.com/in/%s" % re.sub(r"\W+", "-", name.lower())},
            "emails": emails, "replies": replies, "timeline": tl,
        }
        return row

    def add_skipped(self, company, role, source, reason, fit, when):
        self.jid += 1
        self.skipped.append({"id": self.jid, "company": company, "role": role, "source": source, "skip_reason": reason,
                             "fit_score": fit, "status": "skipped", "created_at": iso(when), "location": "United States",
                             "url": "https://jobright.ai/jobs/info/%d" % (self.jid * 911), "jd_text": JD_TEMPLATE.format(company=company, role=role)})

    # ── events ──
    def emit(self, run_id, lane, type_, message, level="info", data=None, ts=None, broadcast=True):
        with self.lock:
            ev = {"id": self.next_event_id, "ts": iso(ts or now()), "run_id": run_id, "lane": lane, "level": level,
                  "type": type_, "message": message, "data": data or {}}
            self.next_event_id += 1
            self.events.append(ev)
            subs = list(self.subs)
        if broadcast:
            for q in subs:
                q.put(ev)
        return ev

    def emit_stats(self, run_id):
        ev = {"id": None, "ts": iso(now()), "run_id": run_id, "lane": None, "level": "debug", "type": "stats",
              "message": "stats", "data": self.summary()}
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            q.put(ev)

    # ── computed ──
    def summary(self):
        with self.lock:
            t = now()
            real = [a for a in self.apps if a["app_status"] != "dry_run"]
            def sent_dt(a):
                return datetime.fromisoformat(a["sent_at"])
            today = [a for a in real if sent_dt(a).date() == t.date()]
            week = [a for a in real if (t - sent_dt(a)).days < 7]
            replied = [a for a in real if a["app_status"] == "replied"]
            awaiting = [a for a in real if a["app_status"] in ("sent", "nudge1", "nudge2")]
            due = [a for a in awaiting if a["next_followup_at"] and datetime.fromisoformat(a["next_followup_at"]) <= t]
            by = {}
            for a in real:
                by[a["source"]] = by.get(a["source"], 0) + 1
            lr = self.runs[-1] if self.runs else None
            per_hour = None
            if lr:
                st = datetime.fromisoformat(lr["started_at"])
                fin = datetime.fromisoformat(lr["finished_at"]) if lr.get("finished_at") else t
                hrs = max((fin - st).total_seconds() / 3600, 1 / 60)
                per_hour = round(lr.get("sent", 0) / hrs, 1)
            skipped_today = sum(1 for s in self.skipped if datetime.fromisoformat(s["created_at"]).date() == t.date())
            return {
                "sent_today": len(today), "sent_7d": len(week), "sent_total": len(real),
                "replies_total": len(replied), "reply_rate": round(len(replied) / len(real), 4) if real else 0,
                "awaiting": len(awaiting), "nudges_due": len(due),
                "bounced": sum(1 for a in real if a["app_status"] == "bounced"),
                "skipped_today": skipped_today, "per_hour_current_run": per_hour,
                "last_run": ({k: lr.get(k) for k in ("id", "started_at", "finished_at", "status")} if lr else None),
                "running": self.running, "by_source": by,
                "dry_run": bool(self.settings.get("dry_run")),
                "gmail_configured": bool(self.settings["email"].get("address") and self.settings["email"].get("app_password")),
                "current_run_id": self.runs[-1]["id"] if (self.running and self.runs) else None,
                "dry_run_today": sum(1 for a in self.apps if a["app_status"] == "dry_run" and datetime.fromisoformat(a["sent_at"]).date() == t.date()),
            }

    def charts(self):
        rnd = random.Random(42)
        daily = []
        t = now()
        for i in range(13, -1, -1):
            d = (t - timedelta(days=i)).date()
            weekend = d.weekday() >= 5
            base = 0 if weekend else 1
            row = {"date": d.isoformat(),
                   "posts": rnd.randint(0, 3) * base, "jobright": rnd.randint(1, 4) * base,
                   "linkedin_us": rnd.randint(0, 3) * base, "linkedin_world": rnd.randint(0, 2) * base,
                   "replies": (1 if rnd.random() > .75 else 0) * base}
            if i > 11 or self.fresh:
                row = {"date": d.isoformat(), "posts": 0, "jobright": 0, "linkedin_us": 0, "linkedin_world": 0, "replies": 0}
            daily.append(row)
        # today reflects real sends
        with self.lock:
            today = [a for a in self.apps if a["app_status"] != "dry_run" and datetime.fromisoformat(a["sent_at"]).date() == t.date()]
            for k in ("posts", "jobright", "linkedin_us", "linkedin_world"):
                daily[-1][k] = sum(1 for a in today if a["source"] == k)
            daily[-1]["replies"] = sum(1 for a in self.apps if a.get("replied_at") and datetime.fromisoformat(a["replied_at"]).date() == t.date())
            contacted = len([a for a in self.apps if a["app_status"] != "dry_run"])
            replied = len([a for a in self.apps if a["app_status"] == "replied"])
            qualified = contacted + 9
            for row in daily:
                row["import"] = row["manual"] = 0
                row["total"] = sum(row[k] for k in ("posts", "jobright", "linkedin_us", "linkedin_world"))
            latest = []
            for a in self.apps:
                for r in self.details.get(a["contact_id"], {}).get("replies", []):
                    if r["kind"] == "reply":
                        latest.append({"contact_id": a["contact_id"], "job_id": a["job_id"], "from_addr": r["from_addr"], "subject": r["subject"],
                                       "snippet": r["snippet"], "received_at": r["received_at"], "contact_name": a["contact_name"],
                                       "company": a["company"], "role": a["role"]})
            latest.sort(key=lambda r: r["received_at"], reverse=True)
            return {"daily": daily, "funnel": {"found": qualified + len(self.skipped) + 58, "qualified": qualified,
                                                "contacted": contacted, "replied": replied}, "latest_replies": latest[:8]}

    # ── scripted run ──
    def build_script(self, run_id, live=True, plan=None):
        """Return a list of (delay_s, callable->None). Each callable emits events / mutates world."""
        plan = plan or LIVE_PLAN
        steps = []
        W = self

        def ev(delay, lane, type_, msg, level="info", data=None, effect=None):
            def f(ts=None):
                if effect and live:
                    effect()
                W.emit(run_id, lane, type_, msg, level, data, ts=ts, broadcast=live)
                if live and type_ in ("email_sent", "reply_found", "nudge_sent", "bounce_found", "job_skipped", "portal_ready", "job_qualified"):
                    W.emit_stats(run_id)
            steps.append((delay, f))

        counts = {}

        def c(lane, k):
            counts.setdefault(lane, {}).setdefault(k, 0)
            counts[lane][k] += 1

        ev(0.2, None, "run_started", "Run #%d started" % run_id, data={"trigger": "button"})
        # follow-ups (runner emits these; in the real app they run in a thread alongside)
        ev(0.6, "followups", "lane_started", "Follow-ups & replies started")
        ev(0.9, "followups", "step", "Checking INBOX for replies from 17 contacts since their first email")
        touch = next((a for a in self.apps if a["company"] == plan["reply"]), None) if plan["reply"] else None
        if touch and live:
            def mark_reply(a=touch):
                a["app_status"] = "replied"
                a["reply_snippet"] = "Thanks Akshay — can you share your hourly rate and when you could start? We work US Central hours."
                a["replied_at"] = a["last_touch_at"] = iso(now())
                a["next_followup_at"] = None
                d = self.details.get(a["contact_id"])
                if d:
                    d["replies"].append({"from_addr": a["email"], "subject": "Re: Senior Salesforce Developer", "snippet": a["reply_snippet"], "received_at": iso(now()), "kind": "reply"})
                    d["timeline"] = [x for x in d["timeline"] if x.get("status") != "due"]
                    d["timeline"].append({"ts": iso(now()), "kind": "reply", "status": "reply", "label": "Reply received"})
            ev(1.4, "followups", "reply_found", "Reply from Naren Kini (Touchpoints) — nudges stopped", "success", {"contact_id": touch["contact_id"]}, effect=mark_reply)
            c("followups", "replies")
        for co in plan["nudges"]:
            a = next((x for x in self.apps if x["company"] == co and x["app_status"] in ("sent", "nudge1")), None)
            if not a:
                continue
            def nudge(a=a):
                a["app_status"] = "nudge2" if a["app_status"] == "nudge1" else "nudge1"
                a["last_touch_at"] = iso(now())
                a["next_followup_at"] = iso(now() + timedelta(days=3)) if a["app_status"] == "nudge1" else None
            if live:
                kind = "Nudge 2" if a["app_status"] == "nudge1" else "Nudge 1"
            else:  # backlog: the seed already reflects this nudge
                kind = "Nudge 1" if a["app_status"] == "nudge1" else "Nudge 2"
            ev(1.1, "followups", "nudge_sent", "%s sent to %s (%s)" % (kind, a["contact_name"], co), "info", {"contact_id": a["contact_id"]}, effect=nudge)
            c("followups", "nudges")
        ev(0.8, "followups", "step", "No new bounces in the last 7 days")
        fc = counts.get("followups", {})
        ev(0.6, "followups", "lane_finished", "Follow-ups done: %s, %s" % (plural(fc.get("replies", 0), "reply", "replies"), plural(fc.get("nudges", 0), "nudge")),
           data={"status": "done", "counts": fc})

        sent_total = [0]
        ev(0.3, "outreach", "lane_started", "Outreach runs inside each source lane as jobs qualify")
        for lane in ("posts", "jobright", "linkedin_jobs"):
            ev(0.8, lane, "lane_started", {"posts": "LinkedIn posts started", "jobright": "JobRight started", "linkedin_jobs": "LinkedIn Jobs started"}[lane])
            if lane == "posts":
                ev(1.0, lane, "tool", "Chrome: navigate linkedin.com/search/results/content?keywords=hiring salesforce developer")
                ev(1.4, lane, "step", "Searching posts: “hiring Salesforce developer” · past 24 hours · 31 results")
                ev(1.2, lane, "step", "Expanding “…more” on 12 posts to read the full text")
                ev(0.6, lane, "tool", "Chrome: get_page_text")
            elif lane == "jobright":
                ev(1.0, lane, "tool", "Chrome: navigate jobright.ai/jobs/recommend?query=salesforce developer remote")
                ev(1.3, lane, "step", "Loaded 24 US remote Salesforce jobs posted in the last 2 days")
            else:
                ev(1.0, lane, "tool", "Chrome: navigate linkedin.com/jobs/search/?keywords=salesforce developer&f_WT=2&f_TPR=r86400")
                ev(1.3, lane, "step", "LinkedIn Jobs US: 19 remote results · then Worldwide")
            for job in plan["jobs"][lane]:
                co, role = job["company"], job["role"]
                ev(1.2, lane, "step", "Opened %s — %s" % (co, role))
                ev(0.5, lane, "job_found", "Found %s — %s" % (co, role), data={"company": co, "role": role})
                c(lane, "found")
                ev(1.0, lane, "step", "Reading the job description: remote, contract, Apex + LWC")
                if job.get("skip"):
                    def add_skip(job=job):
                        self.add_skipped(job["company"], job["role"], job["source"], job["skip"], job["fit"], now())
                    ev(0.9, lane, "job_skipped", "Skipped %s — %s" % (co, job["skip"]), "warn", {"fit_score": job["fit"]}, effect=add_skip)
                    c(lane, "skipped")
                    continue
                ev(0.6, lane, "tool", "jobhunt: already_contacted")
                ev(0.7, lane, "job_qualified", "Qualified %s — %s (fit %d)" % (co, role, job["fit"]), "success", {"fit_score": job["fit"]})
                c(lane, "qualified")
                name, title, email, esrc = job["contact"]
                if esrc == "SalesQL":
                    ev(1.0, lane, "step", "Hiring team: %s, %s — opening LinkedIn profile" % (name, title))
                    ev(1.2, lane, "tool", "SalesQL lookup")
                elif esrc == "post":
                    ev(0.9, lane, "step", "Email in post by %s: %s" % (name, email))
                ev(0.6, lane, "contact_found", "Contact: %s, %s <%s>" % (name, title, email), data={"email_source": esrc})
                ev(1.6, lane, "step", "Writing a tailored resume and email for %s" % co)
                ev(0.5, lane, "tool", "jobhunt: send_application")

                dry = bool(self.settings.get("dry_run"))

                def add_sent(job=job, dry=dry):
                    n, t, e, s = job["contact"]
                    self.add_app(job["company"], job["role"], job["source"], n, t, e, s, 0, "dry_run" if dry else "sent", job["fit"], {}, sent_at=now())
                    with self.lock:
                        if self.runs and not dry:
                            self.runs[-1]["sent"] = self.runs[-1].get("sent", 0) + 1
                if dry:
                    ev(1.4, lane, "email_sent", "[dry run] would email %s (%s) - %s" % (name, email, co), "info",
                       {"company": co, "email": email, "dry_run": True}, effect=add_sent)
                else:
                    ev(1.4, lane, "email_sent", "Sent to %s at %s with Akshay_Verma_Resume.pdf" % (name, co), "success",
                       {"company": co, "email": email, "dry_run": False}, effect=add_sent)
                c(lane, "sent")
                sent_total[0] += 1
            ev(0.8, lane, "lane_finished", "%s done" % {"posts": "LinkedIn posts", "jobright": "JobRight", "linkedin_jobs": "LinkedIn Jobs"}[lane],
               data={"status": "done", "counts": counts.get(lane, {}), "cost_usd": 0.0, "turns": 38})

        dry_all = bool(self.settings.get("dry_run"))
        ev(0.4, "outreach", "lane_finished", "outreach: %d sent, %d dry run, 0 failed" % (0 if dry_all else sent_total[0], sent_total[0] if dry_all else 0),
           "success", {"status": "done", "counts": {"sent": 0 if dry_all else sent_total[0], "dry_run": sent_total[0] if dry_all else 0, "failed": 0}})
        # portal prefill
        ev(0.8, "portal", "lane_started", "Portal prefill started")
        for (co, ats, url, nq) in plan["portal"]:
            ev(1.0, "portal", "step", "%s apply link is %s (no account needed)" % (co, ats))
            ev(1.0, "portal", "tool", "Chrome: navigate " + url)
            ev(1.4, "portal", "step", "Filled name, email, phone, LinkedIn and location “India (remote)”")
            ev(1.0, "portal", "tool", "Chrome: file_upload Akshay_Verma_Resume.pdf")
            ev(1.2, "portal", "step", "Left %s for you — stopped before Submit" % plural(nq, "question"))
            job = next((j for lane_jobs in plan["jobs"].values() for j in lane_jobs if j["company"] == co), None)

            def add_portal(job=job):
                if not job or "portal" not in job:
                    return
                ats_, url_, qs = job["portal"]
                self.portal.append({"id": len(self.portal) + 1, "job_id": self.jid, "company": job["company"], "role": job["role"], "ats": ats_,
                                    "apply_url": url_, "status": "needs_input" if qs else "prefilled", "fit_score": job["fit"], "questions": qs,
                                    "resume_path": "C:\\Users\\akshay\\jobhunter\\data\\resumes\\Akshay_Verma_Resume_%s.pdf" % re.sub(r"\W+", "_", job["company"]),
                                    "created_at": iso(now()), "updated_at": iso(now())})
            role = job["role"] if job else "role"
            ev(0.6, "portal", "portal_ready", "%s — %s is ready to submit (%s)" % (co, role, ats), "success", {"ats": ats}, effect=add_portal)
            c("portal", "ready")
        if plan.get("portal_skip"):
            ev(1.0, "portal", "step", "%s applies through Workday — needs an account, skipped" % plan["portal_skip"])
            ev(0.4, "portal", "job_skipped", "Portal skipped: %s (Workday)" % plan["portal_skip"], "warn")
            c("portal", "skipped")
        ev(0.8, "portal", "lane_finished", "Portal prefill done", data={"status": "done", "counts": counts.get("portal", {})})
        n_sent = 0 if dry_all else sent_total[0]
        n_dry = sent_total[0] if dry_all else 0
        ev(0.6, None, "run_finished", "Run %d done: %d sent, %d dry run, %d nudges, %d replies" % (run_id, n_sent, n_dry, counts.get("followups", {}).get("nudges", 0), counts.get("followups", {}).get("replies", 0)),
           "success", {"status": "done", "stats": {"sent": n_sent, "dry_run": n_dry, "nudges": counts.get("followups", {}).get("nudges", 0), "replies": counts.get("followups", {}).get("replies", 0)}})
        return steps

    def backlog_run(self):
        """Create a finished run #7 earlier today, with its events in the DB (not broadcast)."""
        run_id = 7
        start = now() - timedelta(hours=3, minutes=14)
        steps = self.build_script(run_id, live=False, plan=BACKLOG_PLAN)
        total = sum(d for d, _ in steps)
        scale = (14 * 60) / total
        t = start
        for d, f in steps:
            t = t + timedelta(seconds=d * scale)
            f(ts=t)
        self.runs.append({"id": run_id, "started_at": iso(start), "finished_at": iso(t), "status": "done", "trigger": "open", "sent": 4,
                          "stats": {"sent": 4, "dry_run": 0, "nudges": 1, "replies": 0}})

    def do_run(self, speed):
        with self.lock:
            if self.running:
                return
            self.seed_for_loop()
            run_id = (self.runs[-1]["id"] + 1) if self.runs else 1
            self.runs.append({"id": run_id, "started_at": iso(now()), "finished_at": None, "status": "running", "trigger": "button", "sent": 0, "stats": {}})
            self.running = True
            self.stop_flag.clear()
        steps = self.build_script(run_id, live=True)
        status = "done"
        for d, f in steps:
            if self.stop_flag.wait(d / speed):
                status = "stopped"
                break
            f()
        if status == "stopped":
            for lane in ("followups", "posts", "jobright", "linkedin_jobs", "portal"):
                started = any(e for e in self.events if e["run_id"] == run_id and e["lane"] == lane and e["type"] == "lane_started")
                finished = any(e for e in self.events if e["run_id"] == run_id and e["lane"] == lane and e["type"] == "lane_finished")
                if started and not finished:
                    self.emit(run_id, lane, "lane_finished", "Stopped by you", "warn", {"status": "stopped", "counts": {}})
            self.emit(run_id, None, "run_finished", "Run #%d stopped" % run_id, "warn", {"status": "stopped"})
        with self.lock:
            self.running = False
            self.runs[-1]["finished_at"] = iso(now())
            self.runs[-1]["status"] = status
            self.runs[-1]["stats"] = {"sent": self.runs[-1].get("sent", 0), "dry_run": 0, "nudges": 0, "replies": 0}
        self.emit_stats(run_id)

    def seed_for_loop(self):
        """Each loop restarts from the seed data so the same story replays (new rows animate in again)."""
        events, runs, nid = self.events, self.runs, self.next_event_id
        portal_status = {p["company"]: p["status"] for p in self.portal}
        self.events, self.runs = [], []
        self.seed()
        self.events, self.runs, self.next_event_id = events, runs, nid
        for p in self.portal:
            if p["company"] in portal_status:
                p["status"] = portal_status[p["company"]]


# ─────────────────────────── HTTP ───────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "JobHunterFixture/1.0"
    world = None
    missing = set()
    speed = 1.0
    quiet = True

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def masked(self):
        s = copy.deepcopy(self.world.settings)
        pw = s["email"].get("app_password") or ""
        s["email"]["app_password"] = ("••••••••" + pw[-4:]) if pw else ""
        s["email"]["app_password_set"] = bool(pw)
        return s

    def send_json(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def read_json(self):
        try:
            return json.loads(self.read_body() or b"{}")
        except ValueError:
            return {}

    def is_missing(self, path):
        return any(path == m or path.startswith(m.rstrip("/") + "/") for m in self.missing)

    def do_GET(self):  # noqa: C901
        u = urlparse(self.path)
        path, qs = u.path, {k: v[-1] for k, v in parse_qs(u.query).items()}
        W = self.world
        if path.startswith("/api/") and self.is_missing(path):
            return self.send_json({"error": "not found"}, 404)
        if path == "/api/summary":
            return self.send_json(W.summary())
        if path == "/api/applications":
            rows = list(W.apps)
            st, src, q = qs.get("status"), qs.get("source"), (qs.get("q") or "").lower()
            if st == "awaiting":
                rows = [r for r in rows if r["app_status"] in ("sent", "nudge1", "nudge2")]
            elif st:
                rows = [r for r in rows if r["app_status"] == st]
            if src:
                rows = [r for r in rows if r["source"] == src]
            if q:
                rows = [r for r in rows if q in (" ".join(str(r.get(k) or "") for k in ("company", "role", "contact_name", "email"))).lower()]
            rows.sort(key=lambda r: r["last_touch_at"], reverse=True)
            off, lim = int(qs.get("offset", 0)), int(qs.get("limit", 200))
            return self.send_json(rows[off:off + lim], headers={"X-Total-Count": str(len(rows))})
        m = re.match(r"^/api/applications/(\d+)$", path)
        if m:
            d = W.details.get(int(m.group(1)))
            return self.send_json(d) if d else self.send_json({"error": "no such application"}, 404)
        if path == "/api/jobs":
            rows = W.skipped if qs.get("status", "skipped") == "skipped" else []
            return self.send_json(sorted(rows, key=lambda r: r["created_at"], reverse=True))
        if path == "/api/runs":
            return self.send_json(list(reversed(W.runs)))
        m = re.match(r"^/api/runs/(\d+)$", path)
        if m:
            r = next((x for x in W.runs if x["id"] == int(m.group(1))), None)
            return self.send_json(r) if r else self.send_json({"error": "no such run"}, 404)
        if path == "/api/events":
            with W.lock:
                evs = list(W.events)
            if qs.get("run_id"):
                evs = [e for e in evs if str(e["run_id"]) == qs["run_id"]]
            if qs.get("after_id"):
                evs = [e for e in evs if e["id"] > int(qs["after_id"])]
                evs = evs[:int(qs.get("limit", 300))]
            else:
                evs = evs[-int(qs.get("limit", 300)):]
            return self.send_json(evs)
        if path == "/api/stream":
            return self.stream()
        if path == "/api/charts":
            return self.send_json(W.charts())
        if path == "/api/settings":
            return self.send_json(self.masked())
        if path == "/api/env":
            return self.send_json(self.env_checks())
        if path == "/api/portal":
            return self.send_json(W.portal)
        if path == "/api/profiles":
            return self.send_json({"profiles": [{"id": "akshay", "name": "Akshay Kumar Verma", "email": "", "default": True}]})
        if path == "/api/version":
            if ROOT not in sys.path:  # run as `python tests/ui_fixture_server.py`: sys.path[0] is tests/
                sys.path.insert(0, ROOT)
            from jh import BUILD
            return self.send_json({"build": BUILD})
        if path == "/api/agencies":
            return self.send_json({"agencies": [], "counts": {}, "tracks": {}, "sent_today": 0,
                                   "settings": {"per_run": 5, "daily_cap": 15, "focus": "outside"}})
        if path == "/api/jobboard":
            return self.send_json(jobboard_sample())
        if path == "/api/watch":
            return self.send_json(W.watch)
        if path == "/api/questions":
            qs_ = W.questions
            cnt = {k: sum(1 for x in qs_ if x["status"] == k) for k in ("open", "answered", "mine")}
            return self.send_json({"questions": qs_, "counts": cnt})
        return self.static(path)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        W = self.world
        if self.is_missing(path):
            self.read_body()
            return self.send_json({"error": "not found"}, 404)
        if path == "/api/run":
            self.read_body()
            if W.running:
                return self.send_json({"error": "a run is already in progress"}, 409)
            next_id = (W.runs[-1]["id"] + 1) if W.runs else 1
            W.run_request.set()
            return self.send_json({"run_id": next_id})
        if path == "/api/stop":
            self.read_body()
            W.stop_flag.set()
            return self.send_json({"ok": True})
        if path == "/api/settings":
            body = self.read_json()
            with W.lock:
                new = body if isinstance(body, dict) else {}
                em = new.get("email") or {}
                em.pop("app_password_set", None)
                pw = em.get("app_password")
                if pw is None or (isinstance(pw, str) and pw.startswith("••••")):
                    em.pop("app_password", None)
                if isinstance((new.get("engine") or {}).get("claude_args"), str):
                    new["engine"]["claude_args"] = new["engine"]["claude_args"].split()
                deep_merge(W.settings, new)
            out = self.masked()
            out["_restart_needed"] = False
            return self.send_json(out)
        if path == "/api/settings/test-email":
            body = self.read_json()
            time.sleep(1.2)
            em = body.get("email") or {}
            addr = em.get("address") or W.settings["email"].get("address")
            pw = em.get("app_password") or W.settings["email"].get("app_password")
            if not addr:
                return self.send_json({"ok": False, "error": "Gmail address is not set"})
            if not pw:
                return self.send_json({"ok": False, "error": "App password is not set"})
            return self.send_json({"ok": True, "error": None})
        if path == "/api/engine-test":
            self.read_body()
            time.sleep(2.5)
            if W.fresh:
                return self.send_json({"ok": False, "checks": [
                    {"id": "claude_found", "ok": False, "detail": "Claude Code not found. Install: irm https://claude.ai/install.ps1 | iex, then run 'claude' once to log in."}]})
            return self.send_json({"ok": True, "checks": [
                {"id": "claude_found", "ok": True, "detail": "C:\\Users\\akshay\\.local\\bin\\claude.exe"},
                {"id": "version", "ok": True, "detail": "2.1.302 (Claude Code)"},
                {"id": "auth", "ok": True, "detail": "authMethod: claude.ai"},
                {"id": "mcp_jobhunt", "ok": True, "detail": "jobhunt: connected"},
                {"id": "log_step", "ok": True, "detail": "mcp__jobhunt__log_step called"},
                {"id": "chrome", "ok": True, "detail": "mcp__claude-in-chrome__tabs_context_mcp returned 3 tabs"}]})
        if path == "/api/watch":
            body = self.read_json()
            for k in ("on", "regions", "posts", "interval_min", "posts_interval_min"):
                if k in body:
                    W.watch[k] = body[k]
            if W.watch["on"]:
                W.watch.update(last_poll=iso(now()), next_poll_in_s=540, found_today=3, queued=1)
            return self.send_json(W.watch)
        if path.startswith("/api/jobboard"):
            body = self.read_json()
            if path == "/api/jobboard/apply":
                return self.send_json({"queued": (body or {}).get("ids") or [], "skipped": [], "run_id": None, "waiting": True})
            if path == "/api/jobboard/refresh":
                return self.send_json({"started": True, "refreshing": True})
            if path == "/api/jobboard/settings":
                return self.send_json(dict(jobboard_sample()["status"]["settings"], **(body or {})))
            return self.send_json({"id": 1, "status": (body or {}).get("status")})
        if path == "/api/portal/submit":
            body = self.read_json()
            ok, bad = [], []
            for i in body.get("ids") or []:
                p = next((x for x in W.portal if x["id"] == i), None)
                if p and p.get("ready") and p["status"] in ("prefilled", "needs_input"):
                    p["approved_at"] = iso(now()); ok.append(i)
                else:
                    bad.append({"id": i, "reason": "has required questions left"})
            return self.send_json({"approved": ok, "rejected": bad, "run_id": 99 if ok else None})
        if path == "/api/questions":
            body = self.read_json()
            nid = max([x["id"] for x in W.questions] + [0]) + 1
            row = {"id": nid, "question": body.get("question"), "answer": body.get("answer"), "status": "answered",
                   "field_type": "text", "options": [], "times_seen": 0, "times_used": 0, "jobs": [], "last_company": None,
                   "last_lane": None, "source": "user"}
            W.questions.append(row)
            return self.send_json(row)
        mq = re.match(r"^/api/questions/(\d+)$", path)
        if mq:
            body = self.read_json()
            row = next((x for x in W.questions if x["id"] == int(mq.group(1))), None)
            if not row:
                return self.send_json({"error": "question not found"}, 404)
            if body.get("delete"):
                W.questions.remove(row)
                return self.send_json({"ok": True, "deleted": row["id"]})
            if body.get("answer") is not None:
                row["answer"] = body["answer"]
            row["status"] = body.get("status") or ("answered" if row.get("answer") else "open")
            return self.send_json(row)
        m = re.match(r"^/api/portal/(\d+)$", path)
        if m:
            body = self.read_json()
            p = next((x for x in W.portal if x["id"] == int(m.group(1))), None)
            if not p:
                return self.send_json({"error": "no such portal item"}, 404)
            if body.get("status") not in ("submitted", "abandoned"):
                return self.send_json({"error": "status must be submitted or abandoned"}, 400)
            p["status"] = body["status"]
            p["updated_at"] = iso(now())
            return self.send_json({"ok": True, "item": p})
        if path == "/api/import":
            raw = self.read_body()
            rows = max(0, raw.count(b"\n") - 1)
            return self.send_json({"ok": True, "counts": {"contacts": rows or 38, "skipped_jobs": 21, "duplicates": 4}})
        return self.send_json({"error": "not found"}, 404)

    def env_checks(self):
        W = self.world
        gm = W.settings.get("email", {}).get("address") if W.settings.get("email", {}).get("app_password") else ""
        fresh = W.fresh
        return [
            {"id": "python", "label": "Python", "ok": True, "detail": "Python 3.12.4 (pythonw available)", "fix": ""},
            {"id": "reportlab", "label": "reportlab (resume PDF)", "ok": True, "detail": "reportlab 4.2.2", "fix": ""},
            {"id": "claude_cli", "label": "Claude Code CLI", "ok": not fresh,
             "detail": "'claude' was not found on PATH" if fresh else "claude 2.1.302 at C:\\Users\\akshay\\.local\\bin\\claude.exe",
             "fix": "Open PowerShell and run:  irm https://claude.ai/install.ps1 | iex\nThen run  claude  once and log in." if fresh else ""},
            {"id": "claude_auth", "label": "Claude login (subscription)", "ok": not fresh, "detail": "Not checked — CLI missing" if fresh else "authMethod: claude.ai (subscription)",
             "fix": "Run  claude  in PowerShell and sign in with your Claude account." if fresh else ""},
            {"id": "api_key_env", "label": "No ANTHROPIC_API_KEY in environment", "ok": True, "detail": "Not set (good — runs use your subscription)", "fix": ""},
            {"id": "chrome", "label": "Claude in Chrome extension", "ok": not fresh, "detail": "Extension not detected" if fresh else "Connected to Chrome 141",
             "fix": "Install Claude in Chrome from the Chrome Web Store and sign in." if fresh else ""},
            {"id": "dry_run", "label": "Live sending", "ok": not W.settings.get("dry_run"), "detail": "Dry run is on — nothing is sent" if W.settings.get("dry_run") else "On",
             "fix": "Turn off Dry run in Settings once the email test passes." if W.settings.get("dry_run") else ""},
            {"id": "gmail_configured", "label": "Gmail configured", "ok": bool(gm), "detail": ("%s" % gm) if gm else "Gmail address or app password not set",
             "fix": "" if gm else "Open Settings → Gmail and add your address and an app password."},
        ]

    def stream(self):
        W = self.world
        q = queue.Queue()
        with W.lock:
            W.subs.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            after = self.headers.get("Last-Event-ID") or parse_qs(urlparse(self.path).query).get("after_id", [None])[-1]
            if after and str(after).isdigit():
                with W.lock:
                    backlog = [e for e in W.events if e["id"] and e["id"] > int(after)]
                for e in backlog:
                    self.wfile.write(self.sse_chunk(e))
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(self.sse_chunk(ev))
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with W.lock:
                if q in W.subs:
                    W.subs.remove(q)

    @staticmethod
    def sse_chunk(ev):
        head = ("id: %s\n" % ev["id"]) if ev.get("id") else ""
        return (head + "event: ev\ndata: %s\n\n" % json.dumps(ev)).encode("utf-8")

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "application/javascript"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/javascript")) else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_loop(world, speed, idle, manual):
    """Replays the scripted run forever: idle → run → idle …, or only on request with --manual."""
    first = True
    while True:
        if manual:
            world.run_request.wait()
        else:
            world.run_request.wait(timeout=(3 if first else idle) / max(speed, 0.1))
        first = False
        world.run_request.clear()
        world.do_run(speed)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--speed", type=float, default=1.0, help="script speed multiplier (2 = twice as fast)")
    ap.add_argument("--idle", type=float, default=15.0, help="seconds idle between looped runs")
    ap.add_argument("--manual", action="store_true", help="don't auto-loop; runs start only via POST /api/run")
    ap.add_argument("--fresh", action="store_true", help="first-run state: Gmail not set, Claude CLI missing, dry run on")
    ap.add_argument("--missing", default="", help="comma-separated API paths that return 404")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    world = World(fresh=a.fresh)
    Handler.world = world
    Handler.missing = {m.strip() for m in a.missing.split(",") if m.strip()}
    Handler.speed = a.speed
    Handler.quiet = not a.verbose
    threading.Thread(target=run_loop, args=(world, a.speed, a.idle, a.manual), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print("Job Hunter UI fixture on http://127.0.0.1:%d  (%s)" % (a.port, "manual runs" if a.manual else "auto-looping runs"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass




def jobboard_sample():
    """Job board fixture: a realistic mix of states, sources and eligibility (times relative to now)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    def t(h):
        return (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        (1, "Senior Salesforce Developer (Agentforce)", "NeuraFlash", "Remote - India", "India", 1.2, "portal", "greenhouse", "open", "", ["careers", "linkedin"], "new", 9),
        (2, "Salesforce CPQ Consultant", "Spaulding Ridge", "Remote (APAC)", "APAC", 5, "portal", "greenhouse", "open", "", ["careers"], "ready", None),
        (3, "Salesforce Technical Architect", "Penrod", "Remote, United States", "US", 7, "login_portal", "workday", "check", "", ["linkedin"], "needs_you", 41),
        (4, "Salesforce Developer - LWC", "Devsinc", "Remote", "Worldwide", 2, "easy_apply", "linkedin", "open", "", ["linkedin"], "filling", 18),
        (5, "Salesforce Admin", "Acme Health", "Remote - US", "US", 3, "portal", "lever", "blocked", "requires US work authorization (must be authorized to work in the U.S.)", ["careers"], "new", None),
        (6, "Freelance Salesforce Engineer", "Kite Labs", "Remote", "Worldwide", 20, "email", "email", "open", "", ["hn"], "emailed", None),
        (7, "Salesforce Marketing Cloud Developer", "Two95 International", "Remote — Anywhere in the World", "Worldwide", 30, "board", "weworkremotely", "open", "", ["wwr"], "new", None),
        (8, "Salesforce Data Cloud Engineer", "Conga", "Remote - Germany", "Europe", 10, "portal", "greenhouse", "locked", "remote only for Germany", ["careers"], "new", None),
    ]
    out = []
    for (i, title, co, loc, reg, h, kind, plat, elig, reason, srcs, state, appl) in rows:
        out.append({"id": i, "title": title, "company": co, "location": loc, "region": reg, "posted_at": t(h), "first_seen": t(h),
                    "apply_kind": kind, "platform": plat, "elig": elig, "reason": reason, "sources": srcs, "source": srcs[0],
                    "state": state, "status": "new", "applicants": appl, "emails": ["jobs@kitelabs.dev"] if kind == "email" else [],
                    "url": "https://example.com/jobs/%d" % i, "apply_url": "https://example.com/apply/%d" % i,
                    "portal_id": 50 + i if state in ("ready", "needs_you") else None, "portal_seen": t(1),
                    "questions": ["Expected salary in USD — left for you", "consent: I agree to the privacy notice"] if state == "needs_you" else [],
                    "jd": "Remote role. Apex, LWC, Agentforce. Open to contractors.", "salary": "USD 90k-120k" if i == 2 else "",
                    "job_id": i, "skip_reason": None, "note": None, "jd_checked": 1})
    return {"listings": out,
            "status": {"refreshing": False, "next_in_s": 1200, "auto_today": 3, "spent_today": 1.4, "queued": 0, "linkedin_paused_s": 0,
                       "settings": {"on": True, "auto_fill": True, "per_run": 5, "daily_cap": 20, "daily_usd": 6, "fast_minutes": 30,
                                    "li_every_hours": 2, "li_regions": ["world", "in", "us"]},
                       "last": {"at": t(0.2), "new": 12, "health": {"careers": {"ok": True, "cards": 214, "boards": 23},
                                                                     "himalayas": {"ok": True, "cards": 18}, "jobicy": {"ok": True, "cards": 7},
                                                                     "linkedin": {"ok": True, "cards": 96}, "wwr": {"ok": False, "cards": 0, "error": "HTTP 403"}}}},
            "sources": {"careers": {"listed": 31, "applied": 6, "replies": 1}, "linkedin": {"listed": 88, "applied": 9, "replies": 0},
                        "hn": {"listed": 4, "applied": 1, "replies": 1}}}


if __name__ == "__main__":
    main()
