"""Skip rules in plain Python — so the AI never spends tokens reading a JD only to reject it.

screen(title, text, location, india_role=False) -> (ok: bool, reason: str)

Only HIGH-CONFIDENCE patterns skip a job (a wrong skip loses an application; a missed skip only costs the
AI a few tokens, because the AI still applies the full rules from common.md). The reasons use the exact
texts the AI uses, so reports and the "seen" store stay consistent.
"""
import re

from .watch import TITLE_BAD, TITLE_OK

_US = r"(?:the\s+)?(?:u\.?\s?s\.?a?\.?|united\s+states|america)"
US_AUTH = re.compile(
    r"(?:must|need\s+to|required\s+to|should)\s+be\s+(?:(?:legally|currently)\s+){0,2}(?:authori[sz]ed|eligible)\s+to\s+work\s+"
    r"(?:for\s+any\s+employer\s+)?in\s+" + _US +
    r"|(?:u\.?s\.?|united\s+states)\s+citizens?\s+or\s+(?:lawful\s+)?permanent\s+residents?" +
    r"|(?:u\.?s\.?|united\s+states)\s+citizen(?:s|ship)?\s+(?:only|is\s+required|required|is\s+mandatory)"
    r"|(?:must|required\s+to)\s+(?:be|hold)\s+(?:a\s+)?(?:u\.?s\.?\s+)?citizen(?:ship)?\b"
    r"|\bcitizens?\s+only\b|green\s*card\s+holders?\s+only|\bgc\s+holders?\b|\b(?:usc|gc)\s*/\s*(?:gc|usc)\b"
    r"|security\s+clearance|\bpublic\s+trust\b|\bts\s*/\s*sci\b|(?:active\s+)?secret\s+clearance|clearance\s+(?:is\s+)?required"
    r"|\bw-?2\s+only\b|\bonly\s+w-?2\b|\bno\s+(?:c2c|corp[\s-]+to[\s-]+corp|1099)\b"
    r"|(?:must|required\s+to)\s+(?:reside|live|be\s+located|be\s+based)\s+(?:with)?in\s+" + _US +
    r"|\b(?:u\.?s\.?|us)[\s-]+residents?\s+only|open\s+(?:only\s+)?to\s+(?:u\.?s\.?|us)\s+residents"
    r"|\blocals?\s+only\b|\blocal\s+to\s+(?:the\s+)?(?:state\s+of\s+)?(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new\s+hampshire|new\s+jersey|new\s+mexico|new\s+york|north\s+carolina|north\s+dakota|ohio|oklahoma|oregon|pennsylvania|rhode\s+island|south\s+carolina|south\s+dakota|tennessee|texas|utah|vermont|virginia|washington|west\s+virginia|wisconsin|wyoming|tx|ca|ny|nj|fl|ga|il|va|nc|wa|ma|pa|oh|mi|az|co|dc|dmv|dfw|bay\s+area|nyc)\b|\bonly\s+locals?\b"
    r"|\b(?:in[\s-]person|onsite|on-site|face[\s-]to[\s-]face)\s+interview\s+(?:is\s+)?(?:required|mandatory)", re.I)

# "no sponsorship" only blocks him when the job is in the US (a worldwide contractor role may say it about visas)
SPONSOR = re.compile(r"(?:unable|not\s+able|cannot|can\s*not|will\s+not|won'?t|does\s+not|do\s+not)\s+(?:to\s+)?"
                     r"(?:provide\s+|offer\s+)?sponsor|no\s+(?:visa\s+)?sponsorship|sponsorship\s+(?:is\s+)?not\s+"
                     r"(?:available|provided|offered)", re.I)
US_LOC = re.compile(r"(?i:united\s+states)|\bUSA?\b|\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|"
                    r"MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b")

ONSITE = re.compile(
    r"\bhybrid\s+(?:role|position|job|work(?:ing)?|model|schedule|arrangement|opportunity)\b|\(hybrid\)|\bthis\s+is\s+a\s+hybrid\b"
    r"|\b(?:\d|one|two|three|four)\s+days?\s+(?:a|per|each)\s+week\s+(?:in|at|from)\s+(?:the|our)\s+office"
    r"|\bin[\s-]office\s+(?:\d|two|three|four)\s+days|\bwork\s+from\s+(?:the\s+)?office\b|\bwfo\b"
    r"|\bon-?site\s+(?:role|position|job|only|is\s+required|required|work\s+is\s+required)\b|\(on-?site\)"
    r"|\b100%\s+on-?site\b|\bfully\s+on-?site\b|\brelocation\s+(?:is\s+)?(?:required|mandatory)", re.I)

LANG = re.compile(r"\b(?:fluent|fluency|native|business[\s-]level|proficien\w*|excellent|strong)\s+(?:in\s+)?"
                  r"(german|french|spanish|portuguese|dutch|italian|japanese|polish|arabic|mandarin|korean|swedish|danish|"
                  r"norwegian|finnish|czech|hebrew|turkish)\b"
                  r"|\b(german|french|spanish|portuguese|dutch|italian|japanese|polish|arabic|mandarin|korean)\s+"
                  r"(?:language\s+)?(?:is\s+)?(?:required|mandatory|a\s+must|essential)", re.I)

COUNTRIES = ("united kingdom|uk|england|ireland|germany|france|spain|portugal|italy|netherlands|belgium|poland|czechia|"
             "czech republic|romania|bulgaria|hungary|greece|sweden|norway|denmark|finland|switzerland|austria|"
             "canada|mexico|brazil|argentina|colombia|chile|peru|costa rica|australia|new zealand|philippines|"
             "singapore|malaysia|japan|south korea|uae|united arab emirates|saudi arabia|qatar|israel|turkey|"
             "south africa|egypt|nigeria|kenya|pakistan|bangladesh|sri lanka|vietnam|indonesia|thailand|ukraine|"
             "serbia|croatia|lithuania|latvia|estonia|slovakia|slovenia")
_COUNTRY = re.compile(r"\b(%s)\b" % COUNTRIES, re.I)
OPEN_WORLD = re.compile(r"\bindia\b|anywhere|worldwide|world-wide|\bglobal(?:ly)?\b|any\s+country|any\s+location|"
                        r"\bapac\b|\basia\b|international\s+contractors?|from\s+any\s+time\s?zone", re.I)
LOCKED_REMOTE = re.compile(r"(?:remote|work\s+from\s+home)\s*(?:\(|-|–|—|,)?\s*(?:only\s+)?(?:with)?in\s+(?:the\s+)?(%s)\b\s*(?:only)?"
                           r"|(?:must|need\s+to|required\s+to)\s+(?:be\s+)?(?:reside|live|be\s+located|be\s+based|based)\s+"
                           r"(?:with)?in\s+(?:the\s+)?(%s)\b|\b(latam|emea|europe|eu)[\s-]+only\b" % (COUNTRIES, COUNTRIES), re.I)


_NEG_BEFORE = re.compile(r"\b(?:no|not|non-?|without|never)\s+(?:an?\s+|any\s+)?$", re.I)
_SOFT_AFTER = re.compile(r"^.{0,40}?(?:\bnot\s+(?:required|needed|necessary|mandatory)|\ba\s+plus\b|\bplus\b|preferred|"
                         r"nice[\s-]to[\s-]have|\bbonus\b|advantage|optional|\bonce\s+a\b|occasional|quarterly|"
                         r"for\s+local)", re.I | re.S)


def _hard(rx, body):
    """First match of rx that is not negated ("no security clearance") or softened ("Spanish is a plus")."""
    for m in rx.finditer(body):
        if _NEG_BEFORE.search(body[max(0, m.start() - 16):m.start()]) or _SOFT_AFTER.search(body[m.end():m.end() + 60]):
            continue
        return m
    return None


def _place(p):
    p = p.strip()
    return p.upper() if len(p) <= 4 else p.title()


DEV_TITLE = re.compile(r"develop|engineer|programmer|\bdev\b|\bapex\b|\blwc\b|coder", re.I)


def role_reason(title):
    """Akshay, 30 Oct: "apply only for the Salesforce Developer" — architect / admin / consultant / analyst / QA /
    manager titles are skipped unless the title is also a developer/engineer role (config roles = "developer")."""
    try:
        from . import config
        if str(config.load().get("roles") or "developer") != "developer":
            return ""
    except Exception:
        pass
    if DEV_TITLE.search(title or ""):
        return ""
    return "not a fit: developer roles only (Akshay)"


ARCH_TITLE = re.compile(r"\barchitect", re.I)
# "8+ years of experience", "8-10 yrs experience", "minimum 8 years", "at least 8 years of Salesforce experience",
# "experience: 8+ years", "experience of 8 years"
_YEARS = re.compile(
    r"(?:(?:minimum|min\.?|at\s+least|over|more\s+than)\s+(?:of\s+)?)?"
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|\u2013|to)\s*(\d{1,2})\s*\+?\s*)?(?:years?|yrs?)\b(?:'s)?"
    r"(?=[^.\n]{0,60}?\bexperience)"
    r"|experience\s*(?:of|:|-)?\s*(?:minimum\s+|at\s+least\s+)?(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|\u2013|to)\s*\d{1,2}\s*)?(?:years?|yrs?)\b",
    re.I)


def _cfg_value(key, default):
    try:
        from . import config
        return config.load().get(key, default)
    except Exception:
        return default


def architect_reason(title):
    """Akshay, 10 Nov: "not for the architect level" - every Architect title is skipped, even "Architect/Developer"."""
    from .config import _as_bool
    if _as_bool(_cfg_value("skip_architect", True)) and ARCH_TITLE.search(title or ""):
        return "not a fit: architect level (Akshay)"
    return ""


def years_required(text):
    """Minimum years of experience the JD asks for (the highest lower bound it states), or None.
    "8-10 years" -> 8, "5+ years Salesforce, 8+ years overall" -> 8. "Preferred"/"a plus" mentions don't count."""
    best = None
    for m in _YEARS.finditer(text or ""):
        if _SOFT_AFTER.search(text[m.end():m.end() + 60]):
            continue
        n = int(m.group(1) or m.group(3))
        if 1 <= n <= 30:
            best = n if best is None else max(best, n)
    return best


def experience_reason(text):
    """Akshay, 10 Nov: apply only when the job asks for at most 6-7 years (config max_years_required, default 7)."""
    try:
        cap = min(30, max(1, int(_cfg_value("max_years_required", 7) or 7)))
    except (TypeError, ValueError, OverflowError):
        cap = 7
    n = years_required(text)
    if n is not None and n > cap:
        return "not a fit: needs %d+ years (Akshay applies up to %d)" % (n, cap)
    return ""


def title_reason(title):
    t = title or ""
    a = architect_reason(t)
    if a:
        return a
    if TITLE_BAD.search(t):
        return "not a fit: sales role" if re.search(r"\bsales\b|account|sdr|bdr|business development", t, re.I) \
            else "not a fit: not a salesforce technical role"
    if not TITLE_OK.search(t):
        return "not a fit: not a salesforce role"
    return role_reason(t)


def screen(title, text="", location="", india_role=None):
    """(ok, reason). text = JD text (may be empty: then only the title/location are checked)."""
    r = title_reason(title)
    if r:
        return False, r
    loc = location or ""
    body = (text or "")[:20000]
    blob = "%s\n%s" % (loc, body)
    if india_role is None:
        india_role = bool(re.search(r"\bindia\b", loc, re.I))
    if re.search(r"\((?:hybrid|on-?site)\)", loc, re.I):
        return False, "onsite/hybrid"
    if body:
        r = experience_reason(body)
        if r:
            return False, r
        if _hard(ONSITE, body) and not re.search(r"(?:fully|100%)\s+remote|remote[\s-]first", body, re.I):
            return False, "onsite/hybrid"
        if not india_role:
            m = _hard(US_AUTH, body) or (SPONSOR.search(body) if US_LOC.search(loc) and not re.search(
                r"\bindia\b|worldwide|international\s+contractors?", body, re.I) else None)
            if m:
                return False, "requires US work authorization (%s)" % re.sub(r"\s+", " ", m.group(0)).strip()[:60]
        m = _hard(LANG, body)
        if m:
            return False, "requires %s" % (m.group(1) or m.group(2)).capitalize()
    m = LOCKED_REMOTE.search(blob)
    if m and not re.search(r"\bindia\b|anywhere|worldwide|\bglobal\b", body, re.I):
        place = next(g for g in m.groups() if g)
        if not re.fullmatch(r"(?i)(?:the\s+)?(?:us|usa|united states)", place):
            return False, "remote only for %s" % _place(place)
    # a worldwide-search card whose only location is another country, and a JD that never says it is open
    cm = _COUNTRY.fullmatch(loc.strip()) or _COUNTRY.fullmatch(re.sub(r"\s*\(remote\)\s*$", "", loc.strip(), flags=re.I))
    if cm and body and not OPEN_WORLD.search(body):
        return False, "remote only for %s" % _place(cm.group(1))
    return True, ""


# LinkedIn's public (guest) job list does not honour its remote filter any more: on Akshay's data 53 of 92 guest jobs
# with a city-level location ("Lexington, KY", "Bengaluru, Karnataka, India") turned out onsite/hybrid, and only 3
# were prefilled; remote jobs carry a country-level location or "(Remote)". So a city-level guest job must say it is
# remote in its JD, or it is skipped here for free instead of by the AI.
_REMOTE_WORD = re.compile(r"\bremote\b|work(?:ing)?\s+from\s+home|\bwfh\b|home[\s-]based|telecommut|distributed\s+team|"
                          r"anywhere\s+in\s+(?:india|the\s+world)|location\s*:\s*(?:pan\s+)?india\b", re.I)


def city_level(location):
    loc = (location or "").strip()
    return "," in loc and not re.search(r"remote|anywhere|worldwide", loc, re.I)


def guest_remote_check(location, text):
    """(ok, reason) for a LinkedIn guest-list job: city-level location + a JD that never says remote -> onsite."""
    if city_level(location) and text and not _REMOTE_WORD.search(text):
        return False, "onsite/hybrid (city location, JD never says remote)"
    return True, ""
