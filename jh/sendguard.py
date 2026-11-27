"""Sending safety for Gmail (Akshay, 27 Nov: "solve all those bounces ... use multiple email addresses").

What went wrong in November: one young Gmail account sent ~20 emails in a 90-second burst (follow-ups with
attachments to many outside addresses) and Gmail answered "550 5.4.5 Daily user sending limit exceeded" — twice
(10 Nov, 26 Nov), each time for a day. Gmail also turned messages it had already accepted into "Delivery Status
Notification (Failure)" mails, which Job Hunter read as dead addresses (18 false "bounces"). On top, addresses guessed
by SalesQL bounced 29% of the time, which is exactly what makes Gmail distrust an account.

This module decides, for every email:
  * accounts()        the Gmail accounts Job Hunter may send from (Settings: the main one + email.accounts[])
  * check_recipient() refuses addresses that bounced before, domains with no mail server (DNS MX via
                      dns.google), and unverified guesses (SalesQL etc.) unless Hunter.io verifies them
  * pick()            the account to use now: not paused, under its daily / hourly cap, spaced out (min gap)
  * pause()           circuit breaker: a "sending limit" answer pauses that account for 24 h (nothing retries into
                      the wall every cycle); a wrong app password pauses it for 6 h
  * is_limit_text()   tells Gmail's "you have reached a limit" notices apart from real bounces

No AI. State lives in app_meta (k/v) so the server process and the MCP processes see the same pauses.
"""
import json
import logging
import random
import re
import time
import urllib.parse
import urllib.request

from . import db, dedupe

log = logging.getLogger("jobhunter.sendguard")

LIMIT_RX = re.compile(r"5\.4\.5|sending limit|limit for sending|daily user sending|reached a limit|rate limit|"
                      r"too many (messages|recipients)|421[ -]4\.7\.|4\.7\.28|unusual (rate|activity)|"
                      r"temporarily (rejected|deferred|blocked)", re.I)
AUTH_RX = re.compile(r"login failed|authentication|535[ -]5\.7\.8|username and password not accepted|"
                     r"application-specific password", re.I)
# a mailbox that does not exist (permanent): the address itself is wrong
DEAD_RX = re.compile(r"5\.1\.1|5\.1\.0|5\.4\.1|user unknown|no such user|does not exist|address not found|"
                     r"recipient (address )?rejected|mailbox unavailable|unknown recipient|invalid recipient|"
                     r"no mailbox|account (has been )?disabled|address couldn't be found", re.I)

# email_source values whose address was printed by the person/company or verified by a service
TRUSTED_SOURCES = {"post", "website", "import", "replies", "gmail", "hunter", "apollo", "jobright", "sheet",
                   "verified", "reply", "manual", "careers"}
GUESS_SOURCES = {"salesql", "pattern", "guess", "guessed", "inferred", ""}

DEFAULTS = {"per_account_daily": 25, "per_account_hourly": 8, "min_gap_s": 90, "account_gap_s": 240,
            "warmup_days": 7, "warmup_daily": 12, "limit_pause_h": 24, "auth_pause_h": 6}
DOH_URL = "https://dns.google/resolve?%s"
HUNTER_VERIFY = "https://api.hunter.io/v2/email-verifier?%s"
MX_TTL_S = 30 * 86400
# Job Hunter sent from an older Gmail account until the afternoon of 10 Nov (Settings: email.legacy_address)
SWITCH_TS = "2026-11-10T15:00:00+05:30"


def _norm(addr):
    return (addr or "").strip().lower()


def opts(cfg) -> dict:
    em = (cfg or {}).get("email") or {}
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        v = em.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            out[k] = v
    return out


def accounts(cfg, with_password=True) -> list:
    """[{address, app_password, from_name, daily_cap, primary}] — the main account first, then email.accounts[]."""
    em = (cfg or {}).get("email") or {}
    out, seen = [], set()
    rows = [dict(em, primary=True)] + [dict(a, primary=False) for a in (em.get("accounts") or []) if isinstance(a, dict)]
    for a in rows:
        addr = _norm(a.get("address"))
        pw = str(a.get("app_password") or "").replace(" ", "")
        if not addr or "@" not in addr or addr in seen or a.get("on") is False:
            continue
        if with_password and not pw and em.get("backend") != "file":
            continue
        seen.add(addr)
        cap = a.get("daily_cap") if not a["primary"] else em.get("daily_cap")
        out.append({"address": addr, "app_password": pw, "from_name": a.get("from_name") or em.get("from_name")
                    or "Akshay Verma", "daily_cap": int(cap) if isinstance(cap, (int, float)) and cap > 0 else None,
                    "primary": a["primary"]})
    return out


def own_addresses(cfg) -> set:
    """Every address that is Akshay's own (never a recipient, never a 'reply')."""
    em = (cfg or {}).get("email") or {}
    s = {_norm(a["address"]) for a in accounts(cfg, with_password=False)}
    for a in list(em.get("own_addresses") or []) + [em.get("legacy_address")]:
        s.add(_norm(a))
    s.add(_norm(((cfg or {}).get("profile") or {}).get("email")))
    return {x for x in s if x}


def acct_cfg(cfg, acct) -> dict:
    """cfg with email.address/app_password/from_name of this account (mailer.* takes a cfg)."""
    em = dict((cfg or {}).get("email") or {})
    em.update(address=acct["address"], app_password=acct.get("app_password") or "", from_name=acct.get("from_name")
              or em.get("from_name"))
    return dict(cfg or {}, email=em)


def sender_of(row, cfg):
    """The account an email row went out from: its sender column, else (rows from before 27 Nov) the legacy account
    for mail older than the 10 Nov switch, the main account after it. None when unknown."""
    s = _norm(row.get("sender") if hasattr(row, "get") else row["sender"])
    if s:
        return s
    em = (cfg or {}).get("email") or {}
    sent_at = (row.get("sent_at") if hasattr(row, "get") else row["sent_at"]) or ""
    if sent_at and sent_at < SWITCH_TS and _norm(em.get("legacy_address")):
        return _norm(em.get("legacy_address"))
    return _norm(em.get("address")) or None


# ------------------------------------------------------------------ app_meta helpers

def _get(conn, k, default=None):
    r = conn.execute("SELECT v FROM app_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def _set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO app_meta (k, v) VALUES (?, ?)", (k, str(v)))


def paused_until(conn, addr) -> float:
    try:
        return float(_get(conn, "mail_pause:" + _norm(addr), 0) or 0)
    except ValueError:
        return 0.0


def pause_reason(conn, addr) -> str:
    return _get(conn, "mail_pause_why:" + _norm(addr), "") or ""


def pause(conn, addr, hours, why, now=None):
    now = time.time() if now is None else now
    until = now + 3600 * float(hours)
    if until > paused_until(conn, addr):
        _set(conn, "mail_pause:" + _norm(addr), until)
        _set(conn, "mail_pause_why:" + _norm(addr), str(why)[:300])
    return until


def unpause(conn, addr):
    conn.execute("DELETE FROM app_meta WHERE k IN (?, ?)", ("mail_pause:" + _norm(addr), "mail_pause_why:" + _norm(addr)))


def is_limit_text(text) -> bool:
    return bool(LIMIT_RX.search(str(text or "")))


def is_auth_text(text) -> bool:
    return bool(AUTH_RX.search(str(text or "")))


def is_dead_text(text) -> bool:
    return bool(DEAD_RX.search(str(text or ""))) and not is_limit_text(text)


def on_send_error(conn, cfg, addr, err) -> str:
    """Circuit breaker for one failed send. -> 'limit' | 'auth' | 'dead' | 'other'."""
    o = opts(cfg)
    text = str(err or "")
    if is_limit_text(text):
        pause(conn, addr, o["limit_pause_h"], "Gmail sending limit: " + text[:200])
        return "limit"
    if is_auth_text(text):
        pause(conn, addr, o["auth_pause_h"], "Gmail login refused: " + text[:200])
        return "auth"
    if is_dead_text(text):
        return "dead"
    return "other"


# ------------------------------------------------------------------ counting

def _first_use(conn, addr, now_iso):
    k = "mail_first:" + _norm(addr)
    v = _get(conn, k)
    if not v:
        _set(conn, k, now_iso)
        return now_iso
    return v


def sent_today(conn, addr) -> int:
    today = db.today_ist().isoformat()
    return conn.execute("SELECT COUNT(*) FROM emails WHERE lower(COALESCE(sender,''))=? AND COALESCE(queued,0)=0 AND"
                        " status IN ('sent','bounced','sending') AND substr(sent_at,1,10)=?",
                        (_norm(addr), today)).fetchone()[0]


def sent_last_hour(conn, addr) -> int:
    since = db.now().timestamp() - 3600
    rows = conn.execute("SELECT sent_at FROM emails WHERE lower(COALESCE(sender,''))=? AND COALESCE(queued,0)=0 AND"
                        " status IN ('sent','bounced','sending') AND sent_at>=?",
                        (_norm(addr), db.now_iso()[:10])).fetchall()
    return sum(1 for r in rows if (db.parse_ts(r[0]) and db.parse_ts(r[0]).timestamp() >= since))


def last_send(conn, addr=None) -> float:
    k = "mail_last:" + (_norm(addr) if addr else "*")
    try:
        return float(_get(conn, k, 0) or 0)
    except ValueError:
        return 0.0


def mark_sent(conn, addr, now=None):
    now = time.time() if now is None else now
    _set(conn, "mail_last:" + _norm(addr), now)
    _set(conn, "mail_last:*", now)


def daily_cap(conn, cfg, acct) -> int:
    o = opts(cfg)
    if acct.get("daily_cap"):
        return int(acct["daily_cap"])
    first = db.parse_ts(_first_use(conn, acct["address"], db.now_iso()))
    young = first and (db.now() - first).days < int(o["warmup_days"])
    return int(o["warmup_daily"] if young else o["per_account_daily"])


def status(conn, cfg, now=None) -> list:
    """One row per account for the dashboard / report."""
    now = time.time() if now is None else now
    out = []
    file_backend = ((cfg or {}).get("email") or {}).get("backend") == "file"
    for a in accounts(cfg, with_password=False):
        pu = paused_until(conn, a["address"])
        out.append({"address": a["address"], "ready": bool(a.get("app_password")) or file_backend,
                    "sent_today": sent_today(conn, a["address"]), "daily_cap": daily_cap(conn, cfg, a),
                    "paused_until": pu if pu > now else 0,
                    "pause_reason": pause_reason(conn, a["address"]) if pu > now else ""})
    return out


def pick(conn, cfg, prefer=None, now=None, ignore_gap=False):
    """(account, None) to send from right now, or (None, reason). prefer = the address a thread must continue from."""
    now = time.time() if now is None else now
    o = opts(cfg)
    accs = accounts(cfg)
    if not accs:
        return None, "no Gmail account with an app password (Settings)"
    if prefer:
        accs = [a for a in accs if a["address"] == _norm(prefer)]
        if not accs:
            return None, "the thread's account %s has no app password here" % prefer
    if not ignore_gap and now - last_send(conn) < float(o["min_gap_s"]):
        return None, "spacing emails out (%ds between sends)" % int(o["min_gap_s"])
    ok, why = [], []
    for a in accs:
        pu = paused_until(conn, a["address"])
        if pu > now:
            why.append("%s paused until %s" % (a["address"], time.strftime("%d %b %H:%M", time.localtime(pu))))
            continue
        cap = daily_cap(conn, cfg, a)
        n = sent_today(conn, a["address"])
        if n >= cap:
            why.append("%s reached its daily cap (%d)" % (a["address"], cap))
            continue
        if sent_last_hour(conn, a["address"]) >= int(o["per_account_hourly"]):
            why.append("%s reached its hourly cap" % a["address"])
            continue
        if not ignore_gap and now - last_send(conn, a["address"]) < float(o["account_gap_s"]):
            why.append("%s sent a moment ago" % a["address"])
            continue
        ok.append((n, random.random(), a))
    if not ok:
        return None, "; ".join(why) or "no account available"
    ok.sort(key=lambda x: (x[0], x[1]))
    return ok[0][2], None


# ------------------------------------------------------------------ recipient checks

def bounced_before(conn, addr) -> bool:
    a = _norm(addr)
    return bool(conn.execute(
        "SELECT 1 FROM replies r JOIN contacts c ON c.id=r.contact_id WHERE r.kind='bounce' AND lower(trim(c.email))=?"
        " LIMIT 1", (a,)).fetchone() or _get(conn, "dead:" + a))


def domain_bounces(conn, domain) -> int:
    return conn.execute("SELECT COUNT(DISTINCT lower(c.email)) FROM replies r JOIN contacts c ON c.id=r.contact_id"
                        " WHERE r.kind='bounce' AND lower(c.email) LIKE ?", ("%@" + domain.lower(),)).fetchone()[0]


def _doh(name, rtype, fetch=None):
    url = DOH_URL % urllib.parse.urlencode({"name": name, "type": rtype})
    if fetch:
        return fetch(url)
    req = urllib.request.Request(url, headers={"Accept": "application/dns-json", "User-Agent": "JobHunter/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read(200_000).decode("utf-8", "replace") or "{}")


def has_mail_server(conn, domain, fetch=None):
    """True / False from DNS (MX, else A record), cached for 30 days; None when DNS can't be asked (no blocking)."""
    d = (domain or "").strip().lower().rstrip(".")
    if not d:
        return False
    k = "mx:" + d
    cached = _get(conn, k)
    if cached:
        try:
            ok, at = cached.split("|", 1)
            if time.time() - float(at) < MX_TTL_S:
                return ok == "1"
        except ValueError:
            pass
    try:
        doc = _doh(d, "MX", fetch)
        st = doc.get("Status")
        if st not in (0, 3):           # SERVFAIL etc.: don't decide
            return None
        ok = st == 0 and any(a.get("type") == 15 for a in doc.get("Answer") or [])
        if not ok and st == 0:
            doc = _doh(d, "A", fetch)
            ok = doc.get("Status") == 0 and any(a.get("type") == 1 for a in doc.get("Answer") or [])
    except Exception as e:
        log.info("DNS check for %s failed: %s", d, e)
        return None
    _set(conn, k, "%d|%d" % (1 if ok else 0, int(time.time())))
    return ok


def hunter_verify(cfg, addr, fetch=None):
    """'valid' | 'invalid' | 'accept_all' | 'unknown' | None (no key / no answer)."""
    key = str(((cfg or {}).get("email_finder") or {}).get("hunter_api_key") or "").strip()
    if not key:
        return None
    url = HUNTER_VERIFY % urllib.parse.urlencode({"email": addr, "api_key": key})
    try:
        if fetch:
            doc = fetch(url)
        else:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "JobHunter/1.0"}),
                                        timeout=25) as r:
                doc = json.loads(r.read(200_000).decode("utf-8", "replace") or "{}")
    except Exception as e:
        log.info("hunter verify failed: %s", e)
        return None
    d = (doc or {}).get("data") or {}
    st = d.get("status") or d.get("result")
    if st in ("valid", "deliverable"):
        return "valid"
    if st in ("invalid", "undeliverable"):
        return "invalid"
    if st in ("accept_all", "risky"):
        return "accept_all"
    return "unknown" if st else None


def check_recipient(conn, cfg, addr, email_source="", fetch=None, verify=None) -> tuple:
    """(ok, reason). Never blocks on a network error; blocks what is known to bounce."""
    a = dedupe.normalize_email(addr)
    if not a or not dedupe.valid_email(a):
        return False, "not a valid email address"
    if a in own_addresses(cfg):
        return False, "that is one of Akshay's own addresses"
    if re.match(r"^(no-?reply|noreply|do-?not-?reply|mailer-daemon|postmaster)", a):
        return False, "a no-reply address"
    if bounced_before(conn, a):
        return False, "this address bounced before"
    domain = a.split("@", 1)[1]
    em = (cfg or {}).get("email") or {}
    if em.get("verify_recipients") is False:
        return True, None                # Settings switch: DNS / verifier checks off
    if em.get("backend") == "file" and fetch is None and verify is None:
        return True, None                # tests / file backend: no DNS, no verifier
    mx = has_mail_server(conn, domain, fetch)
    if mx is False:
        _set(conn, "dead:" + a, "no mail server")
        return False, "%s has no mail server (DNS)" % domain
    src = (email_source or "").strip().lower()
    if src in TRUSTED_SOURCES:
        return True, None
    # a guess (SalesQL, pattern ...): only when a verifier says it exists
    res = (verify or hunter_verify)(cfg, a) if verify is not False else None
    if res == "invalid":
        _set(conn, "dead:" + a, "hunter invalid")
        return False, "Hunter.io says this address does not exist"
    if res == "valid":
        return True, None
    if domain_bounces(conn, domain):
        return False, "unverified guess at a domain that bounced before (%s)" % domain
    if res == "accept_all":
        return True, None
    if src in GUESS_SOURCES and res is None:
        return False, ("unverified %s address (SalesQL guesses bounced 29%% in November): use an address printed in the "
                       "post or on the company site, a verified one from find_email, or the job's portal"
                       % (email_source or "guessed"))
    return True, None


# ------------------------------------------------------------------ one-time data fix (27 Nov)

def fix_limit_bounces(conn) -> int:
    """Gmail's "sending limit" notices were stored as bounces (10 Nov, 26 Nov): a burst of >= 5 "bounces" inside three
    minutes is the limit, not dead addresses. Turn them back: replies.kind 'limit', the nudge rows 'failed' (never
    delivered), the initial back to 'sent', contacts nudge-able again. Runs once (app_meta)."""
    if _get(conn, "fix_limit_bounces_v1"):
        return 0
    rows = conn.execute("SELECT id, contact_id, received_at FROM replies WHERE kind='bounce' ORDER BY received_at").fetchall()
    stamps = [(r["id"], r["contact_id"], db.parse_ts(r["received_at"])) for r in rows]
    burst = set()
    for rid, cid, t in stamps:
        if not t:
            continue
        near = [x for x in stamps if x[2] and abs((x[2] - t).total_seconds()) <= 180]
        if len(near) >= 5:
            burst.update(x[0] for x in near)
    n = 0
    for rid, cid, t in stamps:
        if rid not in burst:
            continue
        conn.execute("UPDATE replies SET kind='limit' WHERE id=?", (rid,))
        if cid:
            conn.execute("UPDATE emails SET status='failed', error='Gmail sending limit (not delivered)' WHERE contact_id=?"
                         " AND status='bounced' AND kind<>'initial'", (cid,))
            conn.execute("UPDATE emails SET status='sent' WHERE contact_id=? AND status='bounced' AND kind='initial'", (cid,))
            first = conn.execute("SELECT MIN(sent_at) FROM emails WHERE contact_id=? AND kind='initial'", (cid,)).fetchone()[0]
            recent = bool(first and first >= SWITCH_TS)   # threads started before 10 Nov stay closed (6+ weeks old)
            if recent and not conn.execute("SELECT 1 FROM replies WHERE contact_id=? AND kind='bounce'", (cid,)).fetchone():
                conn.execute("UPDATE contacts SET stop_nudges=0 WHERE id=?", (cid,))
                conn.execute("UPDATE jobs SET status='contacted' WHERE status='bounced' AND id=(SELECT job_id FROM contacts"
                             " WHERE id=?)", (cid,))
        n += 1
    _set(conn, "fix_limit_bounces_v1", db.now_iso())
    return n
