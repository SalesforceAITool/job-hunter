"""ATS keyword check (25 Nov, idea from github.com/ai-job-search-dev/ai-job-search, tools/verify_pdf.py).

Applicant tracking systems match keywords literally. After build_resume, compare the Salesforce terms the JD asks for
with the text the resume builder actually printed (<resume>.txt next to the PDF):

    matched  - in the JD and on the resume
    missing  - in the JD, not on the resume
    can_add  - missing, but a skill Akshay has confirmed (CONFIRMED, or config "resume_confirmed_skills"):
               the AI may work these into the summary / skills rows and build again. Anything else in `missing`
               stays off the resume (no-fabrication rule).
No AI and no network: plain regexes over two texts.
"""
import html
import re

from . import config

# canonical term -> regex (case-insensitive). Hard keywords only: tools, clouds, features, methods.
TERMS = [
    ("Apex", r"\bapex\b"),
    ("Batch Apex", r"\bbatch apex\b|\bbatchable\b"),
    ("Triggers", r"\btriggers?\b"),
    ("LWC", r"\blwcs?\b|lightning web components?"),
    ("Aura", r"\baura\b"),
    ("Visualforce", r"\bvisual ?force\b"),
    ("SOQL", r"\bsoql\b"),
    ("SOSL", r"\bsosl\b"),
    ("JavaScript", r"\bjavascript\b"),
    ("Salesforce Flow", r"\bflows?\b(?! of)|flow builder"),
    ("Process Builder", r"process builder"),
    ("Validation Rules", r"validation rules?"),
    ("Approval Processes", r"approval process(es)?|advanced approvals"),
    ("Sales Cloud", r"sales cloud"),
    ("Service Cloud", r"service cloud"),
    ("Experience Cloud", r"experience cloud|community cloud"),
    ("Health Cloud", r"health cloud"),
    ("Financial Services Cloud", r"financial services cloud|\bfsc\b"),
    ("Field Service", r"field service|\bfsl\b"),
    ("Salesforce CPQ", r"\bcpq\b|\bsbqq\b"),
    ("Revenue Cloud", r"revenue cloud"),
    ("OmniStudio", r"omnistudio|omniscripts?|flexcards?|integration procedures?|dataraptors?|\bvlocity\b"),
    ("Data Cloud", r"data cloud|data 360"),
    ("Agentforce", r"agentforce"),
    ("Einstein", r"\beinstein\b"),
    ("Marketing Cloud", r"marketing cloud"),
    ("Account Engagement", r"account engagement|\bpardot\b"),
    ("Commerce Cloud", r"commerce cloud|b2b commerce|b2c commerce"),
    ("MuleSoft", r"mulesoft"),
    ("REST API", r"\brest\b"),
    ("SOAP API", r"\bsoap\b"),
    ("Platform Events", r"platform events?"),
    ("Named Credentials", r"named credentials?"),
    ("Integrations", r"\bintegrations?\b"),
    ("Data Migration", r"data migration"),
    ("Data Loader", r"data loader"),
    ("Data Modeling", r"data model"),
    ("Case Management", r"case management|case lifecycle|case life cycle"),
    ("Email-to-Case", r"email[- ]to[- ]case"),
    ("Web-to-Case", r"web[- ]to[- ]case"),
    ("Omni-Channel", r"omni-?channel"),
    ("Service Cloud Voice", r"service cloud voice|\bcti\b"),
    ("Amazon Connect", r"amazon connect"),
    ("Knowledge", r"salesforce knowledge|knowledge (base|articles?)"),
    ("Entitlements", r"entitlements?"),
    ("Milestones", r"\bmilestones?\b"),
    ("SLAs", r"\bslas?\b|service level agreements?"),
    ("Escalation Rules", r"escalation rules?"),
    ("Assignment Rules", r"assignment rules?"),
    ("Queues", r"\bqueues?\b"),
    ("Lead Management", r"lead (management|assignment|conversion|routing)"),
    ("Opportunity Management", r"opportunit(y|ies) (management|pipeline)|sales pipeline"),
    ("Forecasting", r"\bforecast(ing|s)?\b"),
    ("Territory Management", r"territory management"),
    ("Reports and Dashboards", r"reports? (and|&|&amp;) dashboards?|dashboards?"),
    ("Sharing and Security", r"sharing (rules|model|settings)|role hierarchy|permission sets?|profiles and roles"),
    ("CI/CD", r"ci/cd|continuous integration|deployment pipelines?"),
    ("Copado", r"\bcopado\b"),
    ("Gearset", r"\bgearset\b"),
    ("Git", r"\bgit\b|github|gitlab|bitbucket"),
    ("Azure DevOps", r"azure devops"),
    ("Salesforce DX", r"\bsfdx\b|salesforce dx|\bsf cli\b"),
    ("JIRA", r"\bjira\b"),
    ("Agile/Scrum", r"\bagile\b|\bscrum\b"),
    ("Heroku", r"\bheroku\b"),
]
_RX = [(name, re.compile(rx, re.I)) for name, rx in TERMS]

# Skills Akshay has said he has (profile / his own words), beyond what the master resume already prints. A JD term
# outside this list and outside the resume text is never added. Escalation rules, reports/dashboards and the AI
# web-form fill at GWM are not here: he has not confirmed them yet (25 Nov).
CONFIRMED = {"Apex", "Triggers", "Batch Apex", "LWC", "Aura", "Visualforce", "SOQL", "SOSL", "JavaScript",
             "Salesforce Flow", "Validation Rules", "Approval Processes", "Sales Cloud", "Service Cloud",
             "Experience Cloud", "Health Cloud", "Salesforce CPQ", "OmniStudio", "Data Cloud", "Agentforce",
             "REST API", "SOAP API", "Platform Events", "Named Credentials", "Integrations", "Data Migration",
             "Data Loader", "Data Modeling", "Case Management", "Email-to-Case", "Web-to-Case", "Omni-Channel",
             "Service Cloud Voice", "Amazon Connect", "Entitlements", "Milestones", "SLAs", "Assignment Rules",
             "Queues", "Copado", "Git", "Azure DevOps", "JIRA", "Agile/Scrum", "Heroku", "CI/CD",
             "Sharing and Security"}


def _plain(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))))


def terms_in(text) -> list:
    t = _plain(text)
    return [name for name, rx in _RX if rx.search(t)]


def confirmed(cfg=None) -> set:
    extra = (cfg or config.load()).get("resume_confirmed_skills")
    return CONFIRMED | ({str(x) for x in extra} if isinstance(extra, list) else set())


def coverage(jd_text, resume_text, cfg=None) -> dict:
    """-> {jd_terms, matched, missing, can_add, score} (score = % of the JD's terms that are on the resume)."""
    want = terms_in(jd_text)
    have = set(terms_in(resume_text))
    matched = [t for t in want if t in have]
    missing = [t for t in want if t not in have]
    ok = confirmed(cfg)
    return {"jd_terms": len(want), "matched": matched, "missing": missing,
            "can_add": [t for t in missing if t in ok],
            "score": round(100 * len(matched) / len(want)) if want else None}
