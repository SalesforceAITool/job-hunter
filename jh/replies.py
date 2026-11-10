"""Turn replies into scheduled interviews (architect review 2026-10-22).

classify(subject, text) -> category:
  interview     an interview is being set up (invite, slot, meeting link)
  call_request  they want to talk / ask for his number / availability
  assessment    a test, assignment or form to fill
  info_request  they ask for details (rate, CTC, notice, visa, location, updated resume)
  rejection     not moving forward
  ack           "thanks for applying / application received" (no human action needed)
  other         a human reply that is none of the above

For interview / call_request / info_request / assessment the app saves a ready-to-send DRAFT reply in his Gmail
Drafts (never sends it): his standard facts + two call windows that overlap US hours. He opens Gmail, checks it,
and presses Send.
"""
import re

CATEGORY_LABEL = {"interview": "Interview", "call_request": "Wants a call", "assessment": "Test / form",
                  "info_request": "Asks for details", "rejection": "Not moving forward", "ack": "Application received",
                  "other": "Reply"}
ACTION = ("interview", "call_request", "assessment", "info_request")

_REJECT = re.compile(r"unfortunately|not (?:be )?moving forward|decided to (?:move|proceed|go) (?:forward )?with other|"
                     r"position has been (?:filled|closed)|regret to inform|not (?:a )?(?:right )?fit|will not be progressing|"
                     r"not selected|no longer (?:open|considering)", re.I)
_INTERVIEW = re.compile(r"\binterview(?:s|ing)?\b(?!\s+(?:prep|tips))|invite you to (?:a|an|the)\s+(?:call|meeting|discussion)|"
                        r"calendly\.com|teams\.microsoft\.com/l/meetup|zoom\.us/j/|meet\.google\.com|"
                        r"(?:schedule|book|pick) (?:a |your )?(?:time|slot)|time slots?|next round|technical round|"
                        r"hiring manager would like to (?:meet|speak)", re.I)
_ASSESS = re.compile(r"\bassessment\b|coding (?:test|challenge|exercise)|hackerrank|codility|testgorilla|take[- ]home|"
                     r"\bassignment\b|online test|fill (?:out|in) (?:this|the) (?:form|questionnaire)|candidate form|"
                     r"skills? (?:matrix|sheet)|google form|forms\.gle", re.I)
_CALL = re.compile(r"\b(?:quick|short|brief|phone|a) call\b|speak (?:with|to) you|talk (?:to|with) you|connect (?:with you|on a call)|"
                   r"(?:your|best) (?:phone|contact) number|tried (?:calling|to call|reaching)|call you|"
                   r"when (?:are you|would you be) (?:available|free)|your availability|good time to (?:talk|call)", re.I)
_INFO = re.compile(r"(?:share|send|attach) (?:me |us )?(?:your )?(?:updated |latest )?(?:resume|cv)|current (?:ctc|location|salary)|"
                   r"expected (?:ctc|rate|salary|compensation)|notice period|visa|work (?:authori[sz]ation|permit)|"
                   r"(?:hourly|bill|pay) rate|\bw-?2\b|\bc2c\b|\b1099\b|relocat|(?:share|send|provide) (?:the )?(?:below |following )?details|"
                   r"(?:are|r) you (?:open|available|comfortable|willing)|please confirm|let me know (?:if|your)", re.I)
_ACK = re.compile(r"thank(?:s| you) for (?:applying|your application|your interest)|application (?:has been )?received|"
                  r"we(?:'ve| have) received your application|received your application", re.I)


def classify(subject, text) -> str:
    blob = "%s\n%s" % (subject or "", (text or "")[:3000])
    if _REJECT.search(blob):
        return "rejection"
    if _INTERVIEW.search(blob):
        return "interview"
    if _ASSESS.search(blob):
        return "assessment"
    if _CALL.search(blob):
        return "call_request"
    if _INFO.search(blob):
        return "info_request"
    if _ACK.search(blob):
        return "ack"
    return "other"


SLOTS = ("11:00 AM - 1:00 PM IST (Mon-Fri)",
         "7:00 PM - 10:00 PM IST, which is 9:30 AM - 12:30 PM US Eastern (Mon-Fri)")


def _first(name):
    n = (name or "").strip().split()
    return n[0].strip(",.").title() if n and "@" not in n[0] else "there"


def draft_text(category, name, company, role, india=False) -> str:
    """Plain-text draft reply in Akshay's voice. Short; he edits before sending."""
    hi = "Hi %s," % _first(name)
    what = (role or "the role").strip()
    if category == "interview":
        opening = ("Thank you for the invitation to interview for %s - I'm glad to move forward. If the time isn't fixed"
                   " yet, any of the windows below works for me; if you've already sent a slot or meeting link, I'll"
                   " accept it." % what)
    elif category == "call_request":
        opening = "Thanks for reaching out about %s - happy to talk. My number is +91 75664 42162, or send a meeting link." % what
    elif category == "assessment":
        opening = ("Thank you - I'll complete the assessment/form for %s and send it back within 24 hours. Please let me"
                   " know if there is a deadline." % what)
    else:
        opening = "Thank you for getting back to me about %s. Here are my details:" % what
    if india:
        facts = ["Location: India, fully remote", "Experience: 7+ years in Salesforce (Agentforce, CPQ, Apex/LWC, integrations), 11 certifications",
                 "Notice period: can join within 15 days", "Open to full-time remote or contract"]
    else:
        facts = ["Location: India, working US hours (remote)",
                 "Engagement: C2C through my company Trysyntx IT Solution Pvt. Ltd. (GST registered, USD invoices), or as you prefer",
                 "Rate: around USD 30/hour, flexible", "Availability: can start within 15 days",
                 "Experience: 7+ years in Salesforce (Agentforce in production, CPQ + SAP integration, Apex/LWC), 11 certifications"]
    body = [hi, "", opening, ""]
    if category in ("info_request", "call_request", "interview"):
        body += ["- " + f for f in facts] + [""]
    if category in ("interview", "call_request", "info_request"):
        body += ["I'm available for a call at:"] + ["- " + s for s in SLOTS] + \
                ["", "Or share a time that suits you and I'll make it work.", ""]
    body += ["Best regards,", "Akshay Kumar Verma", "+91 75664 42162 | linkedin.com/in/akshay-kumar-18565910a"]
    return "\n".join(body)


NOREPLY = re.compile(r"^(?:no-?reply|noreply|do-?not-?reply|notifications?|jobs-noreply|jobalerts?|alerts?|mailer|info|news|"
                     r"updates?|marketing|hello)[^@]*@", re.I)
NOISE_DOMAINS = ("linkedin.com", "indeed.com", "naukri.com", "glassdoor.com", "google.com", "accounts.google.com",
                 "salesql.com", "jobright.ai", "himalayas.app", "medium.com", "github.com")
_STOP = {"inc", "llc", "ltd", "limited", "pvt", "private", "corp", "corporation", "group", "global", "technologies",
         "technology", "solutions", "services", "consulting", "software", "systems", "the", "and", "company", "india",
         "labs", "digital", "infotech", "it", "tech",
         # generic words found inside many company names/domains ("cloudlabsit.com" is not "Vitim Cloud")
         "cloud", "clouds", "force", "sales", "salesforce", "data", "soft", "info", "infosolutions", "staffing",
         "talent", "people", "partners", "consultancy", "analytics", "enterprises", "ventures", "networks", "works",
         "remote", "jobs", "careers", "recruit", "recruiting", "hire", "hiring", "india", "usa", "inc."}


def company_tokens(company):
    """Distinctive lowercase tokens of a company name (>= 4 letters, not generic)."""
    toks = re.findall(r"[a-z0-9]+", (company or "").lower())
    return [t for t in toks if len(t) >= 4 and t not in _STOP]


def match_company(msg, companies):
    """companies: [(job_id, company)] -> job_id whose company the inbound message is about, else None."""
    frm = (msg.get("from_addr") or "").lower()
    dom = frm.split("@")[-1]
    head = "%s\n%s" % (msg.get("subject") or "", (msg.get("text") or "")[:800])
    head_l = head.lower()
    best = None
    for jid, comp in companies:
        toks = company_tokens(comp)
        if not toks:
            continue
        if any(t in dom for t in toks):
            return jid
        if best is None and all(re.search(r"\b%s\b" % re.escape(t), head_l) for t in toks[:2]):
            best = jid
    return best
