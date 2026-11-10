"""Normalization of company/role names and the already-contacted check (SPEC §1)."""
import re

_SOURCE_MARKERS = re.compile(r"\((?:jobright|linkedin(?: post| jobs?)?|indeed|wellfound)\)", re.I)
_GENDER = re.compile(r"\((?:m|f|w|d|x)(?:\s*/\s*(?:m|f|w|d|x)){1,3}\)", re.I)
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")

COMPANY_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "pvt", "private", "plc", "corp",
                    "corporation", "co", "gmbh", "ag", "llp", "lp", "sa", "bv", "pty", "srl"}
ROLE_DROP = {"remote", "us", "usa", "india", "worldwide", "global"}
ROLE_SYNONYMS = {"sr": "senior", "jr": "junior", "dev": "developer", "engg": "engineer", "mgr": "manager"}

EMAIL_RE = re.compile(r"(?!\.)(?!.*\.\.)[A-Za-z0-9._%+'-]+(?<!\.)@(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}")
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "live.com",
             "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "rediffmail.com", "zoho.com", "gmx.com"}
_LINKEDIN_SLUG = re.compile(r"(?:^|linkedin\.com)/?in/([^/?#\s]+)", re.I)

# An email row in one of these states means the address may already have received mail:
# 'sending' is the claim row written just before SMTP (it stays if the process dies mid-send).
_CS_SQL = "('sent','bounced','sending')"


def _base(text: str) -> list:
    t = (text or "").lower().replace("&", " and ")
    t = _SOURCE_MARKERS.sub(" ", t)
    t = _GENDER.sub(" ", t)
    t = _PUNCT.sub(" ", t).replace("_", " ")
    return _SPACES.sub(" ", t).strip().split(" ") if t.strip() else []


def normalize_company(name: str) -> str:
    """'CGI Inc. (LinkedIn)' -> 'cgi'; 'DX Future Tech Pvt. Ltd.' -> 'dx future tech'."""
    toks = _base(name)
    while toks and toks[-1] in COMPANY_SUFFIXES:
        toks.pop()
    return " ".join(toks)


_PARENS = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")


def normalize_role(role: str) -> str:
    """'Sr. Salesforce Developer (Remote)' -> 'senior salesforce developer'.

    Parenthesized notes are dropped ('Salesforce Developer (US remote; public-sector consulting)' is the same
    job as 'Salesforce Developer'): tracker rows carry such annotations, postings usually don't.
    """
    stripped = _PARENS.sub(" ", role or "")
    toks = [ROLE_SYNONYMS.get(t, t) for t in (_base(stripped) or _base(role))]
    kept = [t for t in toks if t not in ROLE_DROP]
    return " ".join(kept or toks)


def norm_key(company: str, role: str) -> str:
    return normalize_company(company) + "|" + normalize_role(role)


def normalize_email(email: str) -> str:
    return (email or "").strip().strip("<>").lower()


def valid_email(email: str) -> bool:
    e = (email or "").strip()
    return bool(EMAIL_RE.fullmatch(e)) and len(e) <= 254


def linkedin_slug(url: str) -> str:
    """'https://www.linkedin.com/in/Jane-Doe-123/?x=1' or '/in/jane-doe-123/' -> 'jane-doe-123' ('' if none)."""
    from urllib.parse import unquote
    m = _LINKEDIN_SLUG.search((url or "").strip())
    return unquote(m.group(1)).strip().lower() if m else ""


def email_contacted(conn, email):
    """Row (contact_id, sent_at, company) if this address was ever really emailed, else None."""
    e = normalize_email(email)
    if not e:
        return None
    return conn.execute(
        "SELECT c.id AS contact_id, MIN(m.sent_at) AS sent_at, j.company FROM contacts c"
        " JOIN emails m ON m.contact_id=c.id LEFT JOIN jobs j ON j.id=c.job_id"
        " WHERE lower(trim(c.email))=? AND m.status IN " + _CS_SQL + " GROUP BY c.id LIMIT 1", (e,)).fetchone()


def job_contacted(conn, company, role):
    """Row for an existing job with the same normalized company+role that was contacted, else None."""
    key = norm_key(company, role)
    if key.startswith("|") or key.endswith("|"):
        return None
    return conn.execute(
        "SELECT j.id, j.company, j.role, j.status, (SELECT MIN(sent_at) FROM emails m WHERE m.job_id=j.id"
        " AND m.status IN " + _CS_SQL + ") AS sent_at FROM jobs j WHERE j.norm_key=? AND"
        " (j.status IN ('contacted','replied','bounced','closed') OR EXISTS (SELECT 1 FROM emails m"
        " WHERE m.job_id=j.id AND m.status IN " + _CS_SQL + ")) ORDER BY j.id LIMIT 1", (key,)).fetchone()


def linkedin_contacted(conn, url):
    """Row (email, sent_at, company) if the same LinkedIn profile was already emailed (under any address)."""
    slug = linkedin_slug(url)
    if not slug:
        return None
    for r in conn.execute("SELECT c.email, c.linkedin, MIN(m.sent_at) AS sent_at, j.company FROM contacts c"
                          " JOIN emails m ON m.contact_id=c.id LEFT JOIN jobs j ON j.id=c.job_id"
                          " WHERE COALESCE(c.linkedin,'')<>'' AND m.status IN " + _CS_SQL + " GROUP BY c.id"):
        if linkedin_slug(r["linkedin"]) == slug:
            return r
    return None


def already_contacted(conn, company, role, emails=None, linkedin=None) -> dict:
    """{'contacted': bool, 'reason': str}. Dry-run sends do not count as contacted."""
    if linkedin:
        row = linkedin_contacted(conn, linkedin)
        if row:
            return {"contacted": True, "reason": "LinkedIn profile already contacted on %s (%s)"
                    % ((row["sent_at"] or "")[:10], row["company"] or "?")}
    for e in emails or []:
        row = email_contacted(conn, e)
        if row:
            return {"contacted": True, "reason": "email %s already contacted on %s (%s)"
                    % (normalize_email(e), (row["sent_at"] or "")[:10], row["company"] or "?")}
    if company and role:
        row = job_contacted(conn, company, role)
        if row:
            return {"contacted": True, "reason": "same company+role already contacted: %s / %s (%s, %s)"
                    % (row["company"], row["role"], row["status"], (row["sent_at"] or "")[:10] or "no date")}
    return {"contacted": False, "reason": ""}
