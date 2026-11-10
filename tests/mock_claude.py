#!/usr/bin/env python3
"""Mock of `claude -p ... --output-format stream-json` for Job Hunter (SPEC §8, §16).

Same CLI shape as claude (all flags ignored). Prints realistic stream-json for the
lane in env JOBHUNTER_LANE over ~10-20 s and performs REAL writes through jh.actions
(save_job, save_contact, send_application, save_portal_prefill), always forcing the
`file` email backend so nothing is ever really emailed. Contacts use reserved
`.example` domains.

Env knobs (tests):
  JOBHUNTER_MOCK_SPEED=0.05   multiply every pause (1 = realistic)
  JOBHUNTER_MOCK_NO_WRITES=1  don't import jh; fabricate tool results
  JOBHUNTER_MOCK_FAIL=1       emit a few lines then exit 3 with stderr noise
  JOBHUNTER_MOCK_HANG=1       emit init then sleep (stop/timeout tests)
  JOBHUNTER_MOCK_SEED=<int>   deterministic company picks
  JOBHUNTER_MOCK_DUPES=1      posts lane also tries to re-contact a tracker email (Capio / Sneha) and a
                              tracker company+role (Celestica) - both must be refused by the app
"""
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = Path(os.environ.get("JOBHUNTER_APP_ROOT") or HERE.parent)
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

LANE = os.environ.get("JOBHUNTER_LANE") or "posts"
SPEED = float(os.environ.get("JOBHUNTER_MOCK_SPEED") or "1")
NO_WRITES = os.environ.get("JOBHUNTER_MOCK_NO_WRITES") == "1"
_rid = (os.environ.get("JOBHUNTER_RUN_ID") or "").strip()
RUN_ID = int(_rid) if _rid.isdigit() else None
SESSION = str(uuid.uuid4())
_seed = os.environ.get("JOBHUNTER_MOCK_SEED")
RNG = random.Random(int(_seed) if _seed else (time.time_ns() ^ (RUN_ID or 0)))

CHROME = "mcp__claude-in-chrome__"
JH = "mcp__jobhunt__"
TOOLS = [JH + t for t in ("log_step", "already_contacted", "save_job", "save_contact", "send_application",
                          "build_resume", "save_portal_prefill", "list_portal_candidates", "get_rules")] + \
        [CHROME + t for t in ("tabs_context_mcp", "tabs_create_mcp", "tabs_close_mcp", "navigate", "javascript_tool",
                              "get_page_text", "find", "computer", "read_page", "form_input", "file_upload")]

COMPANIES = [
    ("Northwind Health", "northwindhealth"), ("Capio Group", "capiogroup"), ("Bluefin Cloud Partners", "bluefincloud"),
    ("Cedar & Pine Consulting", "cedarpine"), ("Lumen Ridge Software", "lumenridge"), ("Orbitive Labs", "orbitive"),
    ("Harborline Insurance", "harborline"), ("Quantica Systems", "quantica"), ("Tidewater Analytics", "tidewater"),
    ("Silverleaf CRM", "silverleafcrm"), ("Brightpath Solutions", "brightpath"), ("Ironclad Logistics", "ironcladlog"),
    ("Meridian Cloudworks", "meridiancw"), ("Pinecrest Digital", "pinecrest"), ("Vantage Point Apps", "vantagepoint"),
    ("Kestrel Health Tech", "kestrelht"), ("Arcadia Revenue Ops", "arcadiarev"), ("Summit Forge", "summitforge"),
]
ROLES = [
    ("Salesforce Developer", "amkor"), ("Senior Salesforce Developer", "amkor"), ("Salesforce OmniStudio Developer", "bcbsm"),
    ("Salesforce Technical Lead", "siemens"), ("Salesforce CPQ Developer", "amkor"), ("Salesforce Architect", "siemens"),
    ("Salesforce Experience Cloud Developer", "lifesci"), ("Salesforce LWC Developer", "dubai"),
]
PEOPLE = [
    ("Sarah Mitchell", "Senior Technical Recruiter"), ("Daniel Ortiz", "Talent Acquisition Partner"),
    ("Priya Raman", "Salesforce Practice Lead"), ("Michael Chen", "Director of CRM Engineering"),
    ("Emily Carter", "HR Manager"), ("James Whitaker", "Salesforce Solution Architect"),
    ("Hannah Brooks", "Recruiting Lead"), ("Rahul Mehta", "Engineering Manager, Salesforce"),
]
SKIPS = [
    ("requires US work authorization", "Must be authorized to work in the US without sponsorship. W2 only."),
    ("onsite/hybrid", "Hybrid, 3 days a week in our Austin office."),
    ("remote only for Brazil", "Remote - Brazil. Candidates must reside in Brazil."),
    ("core skill he lacks: nCino", "5+ years of nCino configuration required."),
    ("not a Salesforce technical role", "Salesforce Business Analyst gathering requirements."),
]
ORDER = ["amkor", "siemens", "bcbsm", "dubai", "emaar", "belden", "druce", "lifesci", "wm"]

state = {"turns": 0, "sent": 0, "qualified": 0, "skipped": 0, "used": set(), "portal": 0}
_ctx = {"conn": None, "actions": None, "cfg": None}


# --------------------------------------------------------------------------- output helpers

def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def pause(a=0.15, b=0.45):
    time.sleep(RNG.uniform(a, b) * SPEED)


def say(text):
    state["turns"] += 1
    out({"type": "assistant", "session_id": SESSION, "parent_tool_use_id": None,
         "message": {"id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant", "model": "mock",
                     "content": [{"type": "text", "text": text}]}})
    pause(0.1, 0.3)


def tool(name, inp, result, is_error=False):
    state["turns"] += 1
    tid = "toolu_" + uuid.uuid4().hex[:16]
    out({"type": "assistant", "session_id": SESSION, "parent_tool_use_id": None,
         "message": {"id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant", "model": "mock",
                     "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
    pause()
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    out({"type": "user", "session_id": SESSION, "parent_tool_use_id": None,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                  "content": [{"type": "text", "text": text}], "is_error": bool(is_error)}]}})
    pause(0.05, 0.2)
    return result


# --------------------------------------------------------------------------- jh.actions bridge

def _load_cfg():
    cfg = {}
    try:
        import jh.config as jc
        for fn in ("load_config", "load"):
            f = getattr(jc, fn, None)
            if callable(f):
                try:
                    c = f()
                    if isinstance(c, dict):
                        cfg = c
                        break
                except TypeError:
                    pass
    except Exception:
        pass
    if not cfg:
        p = os.environ.get("JOBHUNTER_CONFIG")
        if p and os.path.isfile(p):
            try:
                cfg = json.load(open(p, encoding="utf-8"))
            except Exception:
                cfg = {}
    cfg = json.loads(json.dumps(cfg))  # deep copy
    em = cfg.get("email") if isinstance(cfg.get("email"), dict) else {}
    em["backend"] = "file"  # SAFETY: the mock never sends real email
    cfg["email"] = em
    cfg["email_backend"] = "file"
    return cfg


def _ensure():
    if NO_WRITES:
        return None
    if _ctx["conn"] is None:
        import jh.actions as actions
        import jh.db as db
        p = os.environ.get("JOBHUNTER_DB")
        _ctx["conn"] = db.connect(p) if p else db.connect()
        try:
            _ctx["conn"].execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass
        _ctx["actions"] = actions
        _ctx["cfg"] = _load_cfg()
    return _ctx["actions"]


_fake_ids = {"job": 1000, "contact": 2000, "email": 3000, "portal": 4000}


def act(tool_name, **kw):
    """Call jh.actions.<tool_name> (or fake it) and emit tool_use/tool_result lines."""
    a = _ensure()
    conn = _ctx["conn"]
    err = False
    try:
        if a is None:
            res = _fake(tool_name, kw)
        elif tool_name == "log_step":
            a.log_step(conn, RUN_ID, LANE, kw["message"], level=kw.get("level", "info"))
            res = {"ok": True}
        elif tool_name == "already_contacted":
            res = a.already_contacted(conn, kw["company"], kw["role"], kw.get("emails") or [])
        elif tool_name == "save_job":
            res = a.save_job(conn, RUN_ID, LANE, **kw)
        elif tool_name == "save_contact":
            res = a.save_contact(conn, RUN_ID, LANE, **kw)
            err = bool(res.get("error"))
        elif tool_name == "send_application":
            res = a.send_application(conn, RUN_ID, LANE, kw["job_id"], kw["contact_id"], kw["subject"],
                                     kw["body"], kw["resume"], _ctx["cfg"])
            err = not res.get("ok")
        elif tool_name == "build_resume":
            res = {"resume_path": str(a.build_resume_only(conn, kw["job_id"], kw["resume"]))}
        elif tool_name == "save_portal_prefill":
            res = a.save_portal_prefill(conn, RUN_ID, LANE, kw["job_id"], kw["ats"], kw["apply_url"], kw["resume"],
                                        kw["questions_left"], _ctx["cfg"], ready=kw.get("ready") is True)
        elif tool_name == "set_job_status":
            res = a.set_job_status(conn, RUN_ID, LANE, kw["job_id"], kw["status"], kw.get("reason"), note=kw.get("note"))
            err = bool(res.get("error"))
        elif tool_name == "get_submit_queue":
            res = {"queue": a.submit_queue(conn, claim=True)}
        elif tool_name == "mark_submitted":
            res = a.mark_submitted(conn, RUN_ID, LANE, kw["portal_id"], kw.get("confirmation") or "")
            err = bool(res.get("error"))
        elif tool_name == "list_portal_candidates":
            f = getattr(a, "list_portal_candidates", None)
            if callable(f):
                rows = f(conn, limit=kw.get("limit", 15), days=kw.get("days", 3))
            else:
                sys.path.insert(0, str(APP_ROOT / "engine"))
                import jobhunt_mcp
                rows = jobhunt_mcp._portal_candidates(conn, kw.get("limit", 15), kw.get("days", 3))
            res = {"jobs": rows}
        else:
            res = {"error": "unknown tool"}
            err = True
    except Exception as e:
        res, err = {"error": "%s: %s" % (type(e).__name__, e)}, True
        print("mock_claude: %s failed: %s" % (tool_name, e), file=sys.stderr, flush=True)
    tool(JH + tool_name, kw, res, is_error=err)
    return res if isinstance(res, dict) else {}


def _fake(name, kw):
    if name == "already_contacted":
        return {"contacted": False, "reason": ""}
    if name == "save_job":
        _fake_ids["job"] += 1
        return {"job_id": _fake_ids["job"]}
    if name == "save_contact":
        _fake_ids["contact"] += 1
        return {"contact_id": _fake_ids["contact"]}
    if name == "send_application":
        _fake_ids["email"] += 1
        return {"ok": True, "email_id": _fake_ids["email"], "error": None, "dry_run": True}
    if name == "build_resume":
        return {"resume_path": str(APP_ROOT / "data" / "resumes" / "mock.pdf")}
    if name == "save_portal_prefill":
        _fake_ids["portal"] += 1
        return {"portal_id": _fake_ids["portal"], "resume_path": str(APP_ROOT / "data" / "resumes" / "mock.pdf")}
    if name == "list_portal_candidates":
        return {"jobs": []}
    return {"ok": True}


# --------------------------------------------------------------------------- content helpers

def log(msg, level="info"):
    act("log_step", message=msg, level=level)


def pick_company():
    for _ in range(40):
        c = RNG.choice(COMPANIES)
        if c[0] not in state["used"]:
            state["used"].add(c[0])
            return c
    return RNG.choice(COMPANIES)


def email_for(person, slug):
    first, last = person[0].lower().split()[0], person[0].lower().split()[-1]
    return "%s.%s@%s.example" % (first, last, slug)


def resume_for(role, key):
    order = [key] + [k for k in ORDER if k != key]
    focus = {"bcbsm": "OmniStudio", "amkor": "CPQ, Integrations", "siemens": "Technical Lead",
             "lifesci": "Experience Cloud", "dubai": "LWC, Apex"}.get(key, "Apex, LWC")
    return {
        "headline": "Salesforce Developer (7+ years) | %s | Apex, LWC" % focus,
        "summary": ("Salesforce developer with 7+ years across consulting projects. Currently own a CPQ org for Amkor "
                    "including its SAP integration; earlier led 17 developers on a Siemens Sales Cloud rollout. "
                    "Based in India and used to working US hours."),
        "skills": [["Development", "Apex, LWC, Aura, SOQL/SOSL, JavaScript"],
                   ["Clouds", "Sales Cloud, Service Cloud, Experience Cloud, OmniStudio"],
                   ["CPQ", "Salesforce CPQ (SBQQ), Advanced Approvals"],
                   ["Integration", "REST and SOAP, named credentials, platform events, SAP"],
                   ["Platform", "Flows, Custom Metadata, permission sets, reports"],
                   ["DevOps", "Git, Bitbucket, CI/CD, JIRA"]],
        "project_order": order,
    }


def body_for(person, role, where, key):
    first = person[0].split()[0]
    spec = {
        "bcbsm": "I built the BCBSMA member plan-shopping site on OmniStudio (OmniScripts, FlexCards, Integration Procedures)",
        "amkor": "I currently own a Salesforce CPQ org for Amkor, including the SAP price integration",
        "siemens": "I built integrations on a global Sales Cloud rollout for Siemens",
        "lifesci": "I set up Experience Cloud portals for CROs, logistics partners and hospitals",
        "dubai": "I built a single LWC that drives nine finance reports for Dubai Holding",
    }.get(key, "I work mostly in Apex and LWC with integrations")
    return ("Hi %s,\n\nI %s about the %s role and wanted to reach out. %s, which lines up well with what you described. "
            "I have 7+ years on Salesforce and hold the Platform Developer II and Data Cloud certifications.\n\n"
            "I'm based in India, work US hours, and am open to a contract setup. I've attached my resume. "
            "Happy to talk whenever suits you.\n\nThanks,\nAkshay Verma\nlinkedin.com/in/akshay-kumar-18565910a"
            % (first, where, role, spec))


def qualify_and_send(source, where, company, role_t, people, url, jd, fit, apply_url=None, posted="3h"):
    role, key = role_t
    emails = [email_for(p, company[1]) for p in people]
    ac = act("already_contacted", company=company[0], role=role, emails=emails)
    if ac.get("contacted"):
        act("save_job", source=source, company=company[0], role=role, location="Remote", remote=True, url=url,
            posted=posted, jd_text=jd, decision="skipped", skip_reason="already contacted", fit_score=fit)
        state["skipped"] += 1
        return
    job = act("save_job", source=source, company=company[0], role=role, location="Remote (US)", remote=True,
              url=url, posted=posted, jd_text=jd, decision="qualified", fit_score=fit, apply_url=apply_url,
              notes="mock engine")
    state["qualified"] += 1
    jid = job.get("job_id")
    for p, em in zip(people, emails):
        c = act("save_contact", job_id=jid, name=p[0], title=p[1], email=em,
                email_source="post" if source == "posts" else "SalesQL",
                linkedin="https://www.linkedin.com/in/%s-%s/" % (p[0].lower().replace(" ", "-"), company[1]))
        if c.get("error") or c.get("contact_id") is None:
            continue
        log("Writing a tailored email to %s (%s)" % (p[0], p[1]))
        r = act("send_application", job_id=jid, contact_id=c["contact_id"],
                subject="%s role - Akshay Verma (7+ yrs Salesforce)" % role,
                body=body_for(p, role, where, key), resume=resume_for(role, key))
        if r.get("ok"):
            state["sent"] += 1
            log("Application sent to %s at %s" % (p[0], company[0]), "success")


def skip_job(source, company, role, url, reason_t, fit):
    act("already_contacted", company=company[0], role=role, emails=[])
    act("save_job", source=source, company=company[0], role=role, location="Remote", remote=True, url=url,
        posted="1d", jd_text="%s Salesforce role at %s. %s" % (role, company[0], reason_t[1]),
        decision="skipped", skip_reason=reason_t[0], fit_score=fit)
    state["skipped"] += 1
    log("Skipping %s — %s" % (company[0], reason_t[0]))


def chrome_setup():
    log("Checking Chrome and opening my own tab")
    tool(CHROME + "tabs_context_mcp", {}, "Tabs in group: [{\"tabId\": 101, \"title\": \"New Tab\"}]")
    tool(CHROME + "tabs_create_mcp", {}, "Created tab 202")


def open_page(url, label):
    log(label)
    tool(CHROME + "navigate", {"url": url, "tabId": 202}, "Navigated to %s" % url.split("?")[0])
    pause(0.3, 0.8)


def jd_text(company, role):
    return ("%s is hiring a %s (remote, contract). You will build Apex and LWC, integrate with ERP systems over REST, "
            "and work with our architects on OmniStudio and CPQ. 6+ years of Salesforce development. "
            "Platform Developer II preferred. Contractors welcome." % (company, role))


# --------------------------------------------------------------------------- lanes

def try_duplicates():
    """QA knob: an engine that ignores already_contacted and tries to re-email tracker history."""
    log("Checking post from Capio Group — Salesforce Developer")
    job = act("save_job", source="posts", company="Capio Group", role="Salesforce Developer", location="Remote (US)",
              remote=True, url="https://www.linkedin.com/feed/update/urn:li:activity:1/", posted="2h",
              jd_text=jd_text("Capio Group", "Salesforce Developer"), decision="qualified", fit_score=80)
    c = act("save_contact", job_id=job.get("job_id"), name="Sneha Sambaraju", title="Recruitment Specialist",
            email="Sneha.Sambaraju@capiogroup.com", email_source="post")
    if c.get("contact_id") is not None:
        act("send_application", job_id=job["job_id"], contact_id=c["contact_id"],
            subject="Salesforce Developer role - Akshay Verma", body=body_for(("Sneha S", ""), "Salesforce Developer",
                                                                              "saw your LinkedIn post", "amkor"),
            resume=resume_for("Salesforce Developer", "amkor"))
    log("Checking post from Celestica — Senior Salesforce Developer / Admin")
    job = act("save_job", source="posts", company="Celestica Inc.", role="Sr. Salesforce Developer / Admin",
              location="Remote", remote=True, url="https://www.linkedin.com/feed/update/urn:li:activity:2/",
              posted="2h", jd_text=jd_text("Celestica", "Salesforce Developer"), decision="qualified", fit_score=75)
    if job.get("duplicate"):
        log("Celestica job already contacted (%s) — skipping" % job.get("reason"), "warn")
        return
    c = act("save_contact", job_id=job.get("job_id"), name="New Person", title="Talent Acquisition Partner",
            email="new.person@celestica.com", email_source="post")
    if c.get("contact_id") is not None:
        act("send_application", job_id=job["job_id"], contact_id=c["contact_id"],
            subject="Senior Salesforce Developer role - Akshay Verma", body=body_for(("New Person", ""),
            "Senior Salesforce Developer", "saw your LinkedIn post", "amkor"),
            resume=resume_for("Senior Salesforce Developer", "amkor"))


def lane_posts():
    say("Starting the LinkedIn posts lane: three post searches, past 24 hours.")
    chrome_setup()
    if os.environ.get("JOBHUNTER_MOCK_DUPES") == "1":
        try_duplicates()
    pages = [("salesforce developer", "Opening LinkedIn post search: \"salesforce developer\" remote hiring, past 24h"),
             ("salesforce architect", "Opening LinkedIn post search: Salesforce architect/lead, past 24h")]
    found = []
    for kw, label in pages:
        open_page("https://www.linkedin.com/search/results/content/?keywords=%s" % kw.replace(" ", "%20"), label)
        log("Expanding \"…more\" on posts and collecting emails")
        c1, c2 = pick_company(), pick_company()
        found.append(c1)
        found.append(c2)
        tool(CHROME + "javascript_tool", {"action": "javascript_exec", "tabId": 202, "text": "/* expand posts */"},
             {"moreClicked": RNG.randint(4, 9), "emails": [email_for(PEOPLE[0], c1[1])]})
        log("Reading the full post text")
        tool(CHROME + "get_page_text", {"tabId": 202}, "Feed post\n%s is hiring a Salesforce Developer (remote)..." % c1[0])
    say("Found hiring posts with emails. Qualifying each one.")
    c = found[0]
    person = RNG.choice(PEOPLE[:2] + PEOPLE[4:5])
    role = RNG.choice(ROLES)
    log("Checking post from %s — %s" % (c[0], role[0]))
    qualify_and_send("posts", "saw your LinkedIn post", c, role, [person],
                     "https://www.linkedin.com/feed/update/urn:li:activity:%d/" % RNG.randint(10**18, 10**19),
                     jd_text(c[0], role[0]), RNG.randint(70, 92))
    c = found[1]
    role = RNG.choice(ROLES)
    log("Checking post from %s — %s" % (c[0], role[0]))
    qualify_and_send("posts", "saw your LinkedIn post", c, role, [RNG.choice(PEOPLE)],
                     "https://www.linkedin.com/feed/update/urn:li:activity:%d/" % RNG.randint(10**18, 10**19),
                     jd_text(c[0], role[0]), RNG.randint(65, 88))
    skip_job("posts", found[2] if len(found) > 2 else pick_company(), "Salesforce Developer (W2)",
             "https://www.linkedin.com/feed/", SKIPS[0], 25)
    tool(CHROME + "tabs_close_mcp", {"tabId": 202}, "Closed tab 202")


def lane_jobright():
    say("Starting JobRight: US remote Salesforce jobs, Insider Connection, SalesQL.")
    chrome_setup()
    open_page("https://jobright.ai/jobs/search?value=Salesforce+Developer", "Opening JobRight search: Salesforce Developer, US remote")
    log("Collecting job cards from the list")
    cos = [pick_company() for _ in range(3)]
    tool(CHROME + "javascript_tool", {"action": "javascript_exec", "tabId": 202, "text": "/* collect cards */"},
         ["%d: %s | Salesforce Developer | Remote | %dh ago" % (i, c[0], RNG.randint(1, 20)) for i, c in enumerate(cos)])
    # 1 qualified with HR + architect
    c, role = cos[0], RNG.choice(ROLES)
    log("Opening %s — %s" % (c[0], role[0]))
    tool(CHROME + "find", {"query": "job title %s" % role[0], "tabId": 202}, "ref_12: link \"%s\"" % role[0])
    tool(CHROME + "computer", {"action": "left_click", "ref": "ref_12", "tabId": 202}, "Clicked")
    log("Reading JD: %s — %s" % (c[0], role[0]))
    tool(CHROME + "get_page_text", {"tabId": 202}, jd_text(c[0], role[0]))
    log("Insider Connection: getting LinkedIn profiles for the recruiter and the architect")
    tool(CHROME + "javascript_tool", {"action": "javascript_exec", "tabId": 202, "text": "/* capture linkedin url */"},
         ["https://www.linkedin.com/in/sarah-mitchell-1234/"])
    hr, lead = PEOPLE[0], PEOPLE[5]
    open_page("https://www.linkedin.com/in/sarah-mitchell-1234/", "Opening %s's LinkedIn profile for SalesQL" % hr[0])
    tool(CHROME + "computer", {"action": "screenshot", "tabId": 202}, "[screenshot]")
    tool(CHROME + "computer", {"action": "left_click", "coordinate": [1552, 76], "tabId": 202}, "Clicked")
    tool(CHROME + "find", {"query": "email address text in SalesQL panel", "tabId": 202}, "ref_40: \"%s\"" % email_for(hr, c[1]))
    log("Found work email for %s via SalesQL" % hr[0], "success")
    qualify_and_send("jobright", "came across the role on JobRight", c, role, [hr, lead],
                     "https://jobright.ai/jobs/info/%x" % RNG.randint(10**10, 10**11), jd_text(c[0], role[0]),
                     RNG.randint(72, 94))
    # 1 skipped (eligibility)
    skip_job("jobright", cos[1], "Salesforce Developer", "https://jobright.ai/jobs/info/%x" % RNG.randint(10**10, 10**11),
             SKIPS[0], 30)
    # 1 qualified but no email -> portal candidate
    c, role = cos[2], ROLES[0]
    log("Opening %s — %s" % (c[0], role[0]))
    tool(CHROME + "get_page_text", {"tabId": 202}, jd_text(c[0], role[0]))
    log("No Insider Connection contact with an email for %s" % c[0], "warn")
    act("already_contacted", company=c[0], role=role[0], emails=[])
    act("save_job", source="jobright", company=c[0], role=role[0], location="Remote (US)", remote=True,
        url="https://jobright.ai/jobs/info/%x" % RNG.randint(10**10, 10**11), posted="5h",
        jd_text=jd_text(c[0], role[0]), decision="skipped", skip_reason="no email found", fit_score=81,
        apply_url="https://job-boards.greenhouse.io/%s/jobs/%d" % (c[1], RNG.randint(10**6, 10**7)))
    state["skipped"] += 1
    tool(CHROME + "tabs_close_mcp", {"tabId": 202}, "Closed tab 202")


def lane_linkedin_jobs():
    say("Starting LinkedIn Jobs: US remote first, then worldwide.")
    chrome_setup()
    for source, geo, label in (("linkedin_us", "103644278", "US"), ("linkedin_world", "92000000", "worldwide")):
        open_page("https://www.linkedin.com/jobs/search/?keywords=salesforce&geoId=%s&f_WT=2" % geo,
                  "Opening LinkedIn Jobs: Salesforce, %s remote, past 24h" % label)
        log("Collecting job cards (%s)" % label)
        c1, c2 = pick_company(), pick_company()
        tool(CHROME + "javascript_tool", {"action": "javascript_exec", "tabId": 202, "text": "/* collect */"},
             ["%d | Salesforce Developer | %s | Remote" % (RNG.randint(10**9, 10**10), c1[0]),
              "%d | Salesforce Engineer | %s | Remote" % (RNG.randint(10**9, 10**10), c2[0])])
        jid = RNG.randint(10**9, 10**10)
        role = RNG.choice(ROLES)
        open_page("https://www.linkedin.com/jobs/view/%d/" % jid, "Reading JD: %s — %s" % (c1[0], role[0]))
        tool(CHROME + "get_page_text", {"tabId": 202}, jd_text(c1[0], role[0]) + "\nMeet the hiring team\n" + PEOPLE[6][0])
        person = RNG.choice([PEOPLE[1], PEOPLE[6], PEOPLE[4]])
        open_page("https://www.linkedin.com/in/%s/" % person[0].lower().replace(" ", "-"),
                  "Opening hiring-team profile (%s) for SalesQL" % person[0])
        tool(CHROME + "computer", {"action": "left_click", "coordinate": [1552, 76], "tabId": 202}, "Clicked")
        tool(CHROME + "find", {"query": "email address text in SalesQL panel", "tabId": 202},
             "ref_7: \"%s\"" % email_for(person, c1[1]))
        qualify_and_send(source, "saw the opening on LinkedIn", c1, role, [person],
                         "https://www.linkedin.com/jobs/view/%d/" % jid, jd_text(c1[0], role[0]), RNG.randint(68, 90))
        skip_job(source, c2, "Salesforce Engineer", "https://www.linkedin.com/jobs/view/%d/" % RNG.randint(10**9, 10**10),
                 SKIPS[0] if source == "linkedin_us" else SKIPS[2], 20)
    tool(CHROME + "tabs_close_mcp", {"tabId": 202}, "Closed tab 202")


def lane_portal():
    say("Starting portal prefill: Greenhouse / Lever / Ashby only, never submitting.")
    log("Listing qualified jobs that could be prefilled")
    cands = act("list_portal_candidates", limit=10, days=3).get("jobs") or []
    cand = next((j for j in cands if j.get("ats_link")), None)
    if cand is None:
        c, role = pick_company(), ROLES[0]
        apply_url = "https://job-boards.greenhouse.io/%s/jobs/%d" % (c[1], RNG.randint(10**6, 10**7))
        act("already_contacted", company=c[0], role=role[0], emails=[])
        job = act("save_job", source="manual", company=c[0], role=role[0], location="Remote (US)", remote=True,
                  url=apply_url, posted="1d", jd_text=jd_text(c[0], role[0]), decision="qualified",
                  fit_score=78, apply_url=apply_url, notes="mock portal candidate")
        cand = {"id": job.get("job_id"), "company": c[0], "role": role[0], "apply_url": apply_url}
    key = "amkor"
    resume = resume_for(cand.get("role") or "Salesforce Developer", key)
    log("Building tailored resume for %s" % cand.get("company"))
    br = act("build_resume", job_id=cand["id"], resume=resume)
    tool(CHROME + "tabs_context_mcp", {}, "Tabs in group: [{\"tabId\": 101}]")
    tool(CHROME + "tabs_create_mcp", {}, "Created tab 303")
    open_page(cand["apply_url"], "Opening %s application form (Greenhouse)" % cand.get("company"))
    tool(CHROME + "read_page", {"tabId": 303, "filter": "interactive"}, "ref_1 First Name, ref_2 Last Name, ref_3 Email, ref_9 Resume (file)")
    log("Filling name, email, phone, LinkedIn and location")
    for ref, val in (("ref_1", "Akshay"), ("ref_2", "Verma"), ("ref_3", "akshay.verma0031@gmail.com")):
        tool(CHROME + "form_input", {"ref": ref, "value": val, "tabId": 303}, "Set value")
    log("Uploading the tailored resume")
    tool(CHROME + "file_upload", {"paths": [br.get("resume_path", "")], "ref": "ref_9", "tabId": 303}, "Uploaded 1 file")
    q = ["Are you legally authorized to work in the United States? — left for you",
         "Will you now or in the future require sponsorship? — left for you",
         "EEO: gender / race / veteran / disability — left for you",
         "Privacy notice consent checkbox — left for you"]
    act("save_portal_prefill", job_id=cand["id"], ats="greenhouse", apply_url=cand["apply_url"], resume=resume,
        questions_left=q)
    state["portal"] += 1
    log("Prefilled %s Greenhouse form — ready for you to review and submit" % cand.get("company"), "success")


def _system_prompt_text():
    """The run-context block the engine passes with --append-system-prompt-file (targets live there)."""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--append-system-prompt-file" and i + 1 < len(argv):
            try:
                return Path(argv[i + 1]).read_text(encoding="utf-8")
            except OSError:
                return ""
        if a == "--append-system-prompt" and i + 1 < len(argv):
            return argv[i + 1]
    return ""


def _target_ids(pattern):
    import re
    return [int(m) for m in re.findall(pattern, _system_prompt_text(), re.M)]


def lane_board_apply():
    """Job board applications: prefill each listed job's form (like the portal lane), email ones apply by email."""
    say("Starting Job board applications: filling each listed form, never submitting.")
    ids = _target_ids(r"^\s+- (\d+) — ")
    a = _ensure()
    log("Filling %d job board applications" % len(ids))
    for jid in ids:
        row = None
        if a is not None:
            row = _ctx["conn"].execute("SELECT l.apply_url, l.url, l.apply_kind, l.platform, l.emails, j.company, j.role"
                                       " FROM listings l JOIN jobs j ON j.id=l.job_id WHERE l.job_id=? ORDER BY l.id DESC"
                                       " LIMIT 1", (jid,)).fetchone()
        if row is None:
            continue
        company, role = row["company"], row["role"]
        if row["apply_kind"] == "email" and row["emails"]:
            em = row["emails"].split(",")[0]
            c = act("save_contact", job_id=jid, name="Hiring team", title="Hiring contact", email=em, email_source="post")
            if c.get("contact_id") is not None:
                act("send_application", job_id=jid, contact_id=c["contact_id"],
                    subject="%s role - Akshay Verma (7+ yrs Salesforce)" % role,
                    body=body_for(("Hiring team", ""), role, "saw the posting", "amkor"), resume=resume_for(role, "amkor"))
            continue
        url = row["apply_url"] or row["url"]
        open_page(url, "Opening %s application form (%s)" % (company, row["platform"] or "?"))
        resume = resume_for(role, "amkor")
        act("build_resume", job_id=jid, resume=resume)
        ready = row["apply_kind"] == "portal"
        q = [] if ready else ["Finish the remaining steps — left for you"]
        act("save_portal_prefill", job_id=jid, ats=row["platform"] or "", apply_url=url, resume=resume,
            questions_left=q, ready=ready)
        state["portal"] += 1


def lane_easy_apply():
    """Easy Apply: pre-screened / Job board job_ids -> prefill on LinkedIn (ready)."""
    say("Starting Easy Apply prefill.")
    for jid in _target_ids(r"^\s+- (\d+) — "):
        a = _ensure()
        row = _ctx["conn"].execute("SELECT url FROM jobs WHERE id=?", (jid,)).fetchone() if a is not None else None
        if row is None or "/jobs/view/" not in (row["url"] or ""):
            continue
        resume = resume_for("Salesforce Developer", "amkor")
        act("build_resume", job_id=jid, resume=resume)
        act("save_portal_prefill", job_id=jid, ats="linkedin_easy_apply", apply_url=row["url"], resume=resume,
            questions_left=[], ready=True)
        state["portal"] += 1


def lane_submit():
    say("Submitting only the applications Akshay approved.")
    for item in (act("get_submit_queue").get("queue") or []):
        open_page(item.get("apply_url") or "", "Opening approved form %s" % item.get("company"))
        act("mark_submitted", portal_id=item["id"], confirmation="Thanks for applying (mock)")


def lane_agencies():
    say("Pitching agencies.")
    for jid in _target_ids(r"^\s+- job_id (\d+) — "):
        a = _ensure()
        row = _ctx["conn"].execute("SELECT company FROM jobs WHERE id=?", (jid,)).fetchone() if a is not None else None
        if row is None:
            continue
        slug = "".join(ch for ch in row["company"].lower() if ch.isalnum())[:20] or "agency"
        c = act("save_contact", job_id=jid, name="Riya Recruiter", title="Talent Acquisition Lead",
                email="riya.recruiter@%s.example" % slug, email_source="SalesQL")
        if c.get("contact_id") is not None:
            act("send_application", job_id=jid, contact_id=c["contact_id"], subject="Salesforce contractor for your clients",
                body=body_for(("Riya Recruiter", ""), "Salesforce contract", "came across your agency", "amkor"),
                resume=resume_for("Salesforce Developer", "amkor"))


def lane_smoke():
    log("Engine test: Claude is connected to Job Hunter", "success")
    tool(CHROME + "tabs_context_mcp", {}, "Tabs in group: []")
    say("OK jobhunt=worked chrome=worked")


LANES = {"posts": lane_posts, "jobright": lane_jobright, "linkedin_jobs": lane_linkedin_jobs,
         "portal": lane_portal, "smoke": lane_smoke, "board_apply": lane_board_apply, "easy_apply": lane_easy_apply,
         "submit": lane_submit, "agencies": lane_agencies}


def main():
    t0 = time.time()
    out({"type": "system", "subtype": "init", "cwd": os.getcwd(), "session_id": SESSION, "tools": TOOLS,
         "mcp_servers": [{"name": "jobhunt", "status": "connected"}, {"name": "claude-in-chrome", "status": "connected"}],
         "model": "mock-claude", "permissionMode": "dontAsk", "apiKeySource": "none", "claude_code_version": "mock"})
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}, "session_id": SESSION})
    if os.environ.get("JOBHUNTER_MOCK_HANG") == "1":
        while True:
            time.sleep(1)
    if os.environ.get("JOBHUNTER_MOCK_FAIL") == "1":
        say("Starting, but something will go wrong.")
        for i in range(25):
            print("mock stderr line %d: simulated failure" % i, file=sys.stderr, flush=True)
        print("Error: simulated engine crash", file=sys.stderr, flush=True)
        sys.exit(3)
    fn = LANES.get(LANE)
    if fn is None:
        say("Unknown lane %s — nothing to do." % LANE)
    else:
        fn()
    summary = ("Lane %s finished: %d qualified, %d emails sent, %d skipped, %d portal forms."
               % (LANE, state["qualified"], state["sent"], state["skipped"], state["portal"]))
    say(summary)
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION,
         "num_turns": state["turns"], "duration_ms": int((time.time() - t0) * 1000),
         "total_cost_usd": round(RNG.uniform(0.15, 0.95), 4), "result": summary})


if __name__ == "__main__":
    main()
