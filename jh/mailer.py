"""Email transport: Gmail SMTP/IMAP ("smtp" backend) or .eml files ("file" backend, for tests).

file backend: sent mail -> <data>/outbox/*.eml (also acts as the "Sent Mail" folder),
incoming mail <- <data>/inbox/*.eml.
"""
import email
import email.policy
import imaplib
import logging
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path

from . import config, db

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
SENT_FOLDER = '"[Gmail]/Sent Mail"'
DRAFTS_FOLDER = '"[Gmail]/Drafts"'
# Gmail search (X-GM-RAW) for job-related inbound mail from anyone (company domains we never emailed directly:
# Easy Apply / portal applications, recruiters writing from another address)
INBOUND_RAW = ("newer_than:%dd -in:sent -in:drafts -category:promotions (interview OR assessment OR shortlisted OR"
               " availability OR application OR resume OR opportunity OR candidature OR call)")
ATTACHMENT_NAME = "Akshay_Verma_Resume.pdf"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TIMEOUT = 30
log = logging.getLogger("jobhunter.mailer")


class MailError(Exception):
    def __init__(self, msg, maybe_sent=False):
        super().__init__(msg)
        self.maybe_sent = maybe_sent  # True: the message was handed to Gmail, it may have been delivered


def _email_cfg(cfg):
    return (cfg or {}).get("email") or {}


def backend(cfg) -> str:
    return _email_cfg(cfg).get("backend", "smtp")


def _password(cfg) -> str:
    return (_email_cfg(cfg).get("app_password") or "").replace(" ", "")


def imap_date(d) -> str:
    """IMAP date (locale-independent), e.g. 18-Oct-2026."""
    if isinstance(d, str):
        d = db.ts_date(d) or db.today_ist()
    if isinstance(d, datetime):
        d = d.date()
    return "%02d-%s-%04d" % (d.day, _MONTHS[d.month - 1], d.year)


def _q(s) -> str:
    """Quote a string for IMAP SEARCH (ASCII only, no quotes/backslashes)."""
    s = re.sub(r'["\\\r\n]', " ", str(s or ""))
    s = s.encode("ascii", "ignore").decode().strip()
    return '"%s"' % s


def build_message(cfg, to, subject, body, attachment_path=None, in_reply_to=None, references=None,
                  html=None) -> EmailMessage:
    em = _email_cfg(cfg)
    addr = em.get("address") or ""
    msg = EmailMessage()
    msg["From"] = formataddr((em.get("from_name") or "Akshay Verma", addr))
    msg["To"] = " ".join(str(to or "").split())
    msg["Subject"] = " ".join(str(subject or "").split())  # header values must be one line (no CR/LF injection)
    msg["Date"] = formatdate(localtime=True)
    domain = addr.split("@")[-1] if "@" in addr else "jobhunter.local"
    msg["Message-ID"] = make_msgid(domain=domain)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    elif references:
        msg["References"] = references
    msg.set_content(body or "")
    if html:                                   # optional HTML version (the daily report); plain text stays first
        msg.add_alternative(html, subtype="html")
    if attachment_path:
        data = Path(attachment_path).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=ATTACHMENT_NAME)
    return msg


def send(cfg, to, subject, body, attachment_path=None, in_reply_to=None, references=None, html=None) -> str:
    """Send one plain-text email (optionally with an HTML version); returns our Message-ID. Raises MailError."""
    if not to or "@" not in to:
        raise MailError("invalid recipient %r" % to)
    msg = build_message(cfg, to, subject, body, attachment_path, in_reply_to, references, html=html)
    if backend(cfg) == "file":
        out = config.sub_dir("outbox")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", to)[:60]
        name = "%s_%s.eml" % (datetime.now().strftime("%Y%m%d-%H%M%S-%f"), safe)
        (out / name).write_bytes(msg.as_bytes(policy=email.policy.SMTP))
        return msg["Message-ID"]
    addr, pw = _email_cfg(cfg).get("address"), _password(cfg)
    if not addr or not pw:
        raise MailError("Gmail address / app password not set (Settings)")
    delivered = handed = False
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT, context=ssl.create_default_context()) as s:
            s.login(addr, pw)
            handed = True
            s.send_message(msg)
            delivered = True
    except smtplib.SMTPAuthenticationError as e:
        raise MailError("Gmail login failed (check the app password): %s" % e)
    except (smtplib.SMTPException, OSError) as e:
        if delivered:  # the server accepted the message; only QUIT/close failed - it IS sent
            log.warning("SMTP error after the message to %s was accepted (ignored): %s", to, e)
            return msg["Message-ID"]
        raise MailError("SMTP send failed: %s" % e,
                        maybe_sent=handed and not isinstance(e, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)))
    return msg["Message-ID"]


# ------------------------------------------------------------------ parsing helpers

def _msg_date(msg, fallback=None):
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=db.IST)
        return dt.astimezone(db.IST)
    except (TypeError, ValueError, IndexError):
        return fallback or db.now()


def _text_of(msg, limit=4000) -> str:
    """Plain text of a message (first text/plain part, else stripped html)."""
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype in ("text/plain", "text/html", "message/delivery-status") and not part.is_multipart():
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    payload = str(part.get_payload()).encode()
                txt = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if ctype == "text/html":
                txt = re.sub(r"<[^>]+>", " ", txt)
            parts.append(txt)
        if ctype == "message/delivery-status":
            for sub in part.get_payload() if isinstance(part.get_payload(), list) else []:
                parts.append(str(sub))
        if sum(len(p) for p in parts) > limit:
            break
    return "\n".join(parts)[:limit]


def _summarize(msg, fallback_date=None) -> dict:
    text = _text_of(msg)
    # drop quoted history for the snippet
    lines = []
    for ln in text.splitlines():
        if ln.startswith(">") or re.match(r"^On .+wrote:\s*$", ln.strip()):
            break
        lines.append(ln)
    snippet = re.sub(r"\s+", " ", " ".join(lines)).strip()[:300]
    return {
        "from_addr": parseaddr(msg.get("From", ""))[1].lower(),
        "from_name": " ".join(str(parseaddr(msg.get("From", ""))[0] or "").split())[:80],
        "to": [a.lower() for _, a in getaddresses(msg.get_all("To", []))],
        "subject": str(msg.get("Subject", "") or ""),
        "date": _msg_date(msg, fallback_date).replace(microsecond=0).isoformat(),
        "message_id": (msg.get("Message-ID") or "").strip(),
        "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
        "references": (msg.get("References") or "").strip(),
        "auto_submitted": (msg.get("Auto-Submitted") or "").strip().lower(),
        "x_autoreply": (msg.get("X-Autoreply") or msg.get("X-Autorespond") or "").strip().lower(),
        "precedence": (msg.get("Precedence") or "").strip().lower(),
        "snippet": snippet,
        "text": text,
        "x_failed_recipients": (msg.get("X-Failed-Recipients") or "").strip(),
    }


BOUNCE_SUBJECT = re.compile(r"delivery status notification|undeliverable|undelivered mail|"
                            r"mail delivery (failed|subsystem)|returned mail|delivery failure", re.I)


def is_bounce(info: dict) -> bool:
    frm = info.get("from_addr", "")
    return frm.startswith("mailer-daemon") or frm.startswith("postmaster") or bool(BOUNCE_SUBJECT.search(info.get("subject", "")))


def failed_address(info: dict, own_addr="") -> str:
    """Best guess at the address that bounced."""
    if info.get("x_failed_recipients"):
        return info["x_failed_recipients"].split(",")[0].strip().lower()
    text = info.get("text", "")
    m = re.search(r"(?:Final|Original)-Recipient:\s*rfc822;\s*<?([^\s>;]+@[^\s>;]+)", text, re.I)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"(?:wasn't delivered to|not delivered to|couldn't be delivered to|delivery to the following "
                  r"recipients? failed[^\n]*\n\s*)\s*<?([A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text, re.I)
    if m:
        return m.group(1).lower()
    own = (own_addr or "").lower()
    for cand in _EMAIL_IN_TEXT.findall(text):
        c = cand.lower().rstrip(".")
        if c == own or c.startswith(("mailer-daemon", "postmaster", "noreply", "no-reply")) or "google.com" in c:
            continue
        return c
    return ""


# ------------------------------------------------------------------ mailbox sessions

class _FileBox:
    """Fake mailbox over .eml files."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.inbox = config.sub_dir("inbox")
        self.sent = config.sub_dir("outbox")

    def _read(self, folder):
        out = []
        for p in sorted(Path(folder).glob("*.eml")):
            try:
                msg = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
            except Exception:
                continue
            out.append(_summarize(msg, datetime.fromtimestamp(p.stat().st_mtime, db.IST)))
        return out

    def find_replies(self, from_addr, since_date):
        since = db.ts_date(since_date) if not hasattr(since_date, "year") else since_date
        if isinstance(since, datetime):
            since = since.date()
        fa = (from_addr or "").lower()
        return [m for m in self._read(self.inbox)
                if m["from_addr"] == fa and (since is None or db.ts_date(m["date"]) >= since)]

    def find_bounces(self, since_days=7):
        cutoff = db.today_ist() - timedelta(days=since_days)
        own = _email_cfg(self.cfg).get("address", "")
        out = []
        for m in self._read(self.inbox):
            if is_bounce(m) and db.ts_date(m["date"]) >= cutoff:
                out.append({"failed_addr": failed_address(m, own), "subject": m["subject"], "date": m["date"]})
        return out

    def find_sent_message(self, to_addr, subject_hint=None):
        ta = (to_addr or "").lower()
        msgs = [m for m in self._read(self.sent) if ta in m["to"]]
        return _pick_sent(msgs, subject_hint)

    def senders_at(self, domain, days=365):
        cutoff = db.today_ist() - timedelta(days=days)
        out = {}
        for m in self._read(self.inbox):
            if m["from_addr"].endswith("@" + domain) and db.ts_date(m["date"]) >= cutoff and not is_bounce(m):
                out.setdefault(m["from_addr"], m.get("from_name") or "")
        return list(out.items())

    def recent_inbox(self, days=4):
        cutoff = db.today_ist() - timedelta(days=days)
        return [m for m in self._read(self.inbox) if db.ts_date(m["date"]) >= cutoff and not is_bounce(m)]

    def save_draft(self, msg):
        d = config.sub_dir("drafts")
        name = "%s_draft.eml" % datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        (d / name).write_bytes(msg.as_bytes(policy=email.policy.SMTP))
        return True

    def close(self):
        pass


def _pick_sent(msgs, subject_hint):
    """Newest original (non 'Re:') message, preferring subject matches."""
    if not msgs:
        return None
    msgs = sorted(msgs, key=lambda m: m["date"], reverse=True)
    if subject_hint:
        hint = subject_hint.lower()
        hit = [m for m in msgs if hint in m["subject"].lower()]
        msgs = hit or msgs
    orig = [m for m in msgs if not re.match(r"^\s*(re|fwd?)\s*:", m["subject"], re.I)]
    m = (orig or msgs)[0]
    return {"message_id": m["message_id"], "subject": m["subject"], "date": m["date"]}


class _ImapBox:
    """One IMAP session to Gmail, reused for many searches."""

    def __init__(self, cfg):
        self.cfg = cfg
        addr, pw = _email_cfg(cfg).get("address"), _password(cfg)
        if not addr or not pw:
            raise MailError("Gmail address / app password not set (Settings)")
        try:
            self.imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(),
                                          timeout=TIMEOUT)
            self.imap.login(addr, pw)
        except imaplib.IMAP4.error as e:
            raise MailError("IMAP login failed (check the app password / IMAP enabled): %s" % e)
        except OSError as e:
            raise MailError("IMAP connection failed: %s" % e)
        self._folder = None

    def _select(self, folder):
        if self._folder != folder:
            typ, data = self.imap.select(folder, readonly=True)
            if typ != "OK":
                raise MailError("cannot open folder %s: %s" % (folder, data))
            self._folder = folder

    def _search(self, folder, criteria, max_n=50):
        self._select(folder)
        typ, data = self.imap.search(None, criteria)
        if typ != "OK" or not data or not data[0]:
            return []
        ids = data[0].split()[-max_n:]
        out = []
        for i in reversed(ids):  # newest first
            typ, parts = self.imap.fetch(i, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            for p in parts:
                if isinstance(p, tuple) and len(p) == 2:
                    msg = email.message_from_bytes(p[1], policy=email.policy.default)
                    out.append(_summarize(msg))
        return out

    def find_replies(self, from_addr, since_date):
        # IMAP FROM is a substring match (ann@x.io also matches joann@x.io): keep exact senders only
        fa = (from_addr or "").strip().lower()
        return [m for m in self._search("INBOX", "(FROM %s SINCE %s)" % (_q(fa), imap_date(since_date)))
                if m["from_addr"] == fa]

    def find_bounces(self, since_days=7):
        since = imap_date(db.today_ist() - timedelta(days=since_days))
        crit = ('(SINCE %s OR FROM "mailer-daemon" OR SUBJECT "Delivery Status Notification" '
                'SUBJECT "Undeliverable")' % since)
        own = _email_cfg(self.cfg).get("address", "")
        return [{"failed_addr": failed_address(m, own), "subject": m["subject"], "date": m["date"]}
                for m in self._search("INBOX", crit, max_n=100) if is_bounce(m) or "mailer-daemon" in m["from_addr"]]

    def senders_at(self, domain, days=365):
        """[(address, name)] of people at @domain who emailed him (headers only, newest first)."""
        self._select("INBOX")
        typ, data = self.imap.search(None, "(FROM %s SINCE %s)" % (_q("@" + domain),
                                                                 imap_date(db.today_ist() - timedelta(days=days))))
        if typ != "OK" or not data or not data[0]:
            return []
        out = {}
        for i in reversed(data[0].split()[-40:]):
            typ, parts = self.imap.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
            if typ != "OK":
                continue
            for p in parts:
                if isinstance(p, tuple) and len(p) == 2:
                    name, addr = parseaddr(email.message_from_bytes(p[1]).get("From", ""))
                    addr = addr.lower()
                    if addr.endswith("@" + domain):
                        out.setdefault(addr, " ".join(str(name or "").split())[:80])
        return list(out.items())

    def recent_inbox(self, days=4):
        """Inbox mail of the last days that looks job-related (Gmail search), any sender."""
        self._select("INBOX")
        try:
            typ, data = self.imap.search(None, "X-GM-RAW", _q(INBOUND_RAW % int(days)))
        except imaplib.IMAP4.error:
            return self._search("INBOX", "(SINCE %s)" % imap_date(db.today_ist() - timedelta(days=days)), max_n=60)
        if typ != "OK" or not data or not data[0]:
            return []
        out = []
        for i in reversed(data[0].split()[-60:]):
            typ, parts = self.imap.fetch(i, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            for p in parts:
                if isinstance(p, tuple) and len(p) == 2:
                    out.append(_summarize(email.message_from_bytes(p[1], policy=email.policy.default)))
        return [m for m in out if not is_bounce(m)]

    def save_draft(self, msg):
        """Put a draft in Gmail Drafts (never sends)."""
        typ, _ = self.imap.append(DRAFTS_FOLDER, "(\\Draft)", imaplib.Time2Internaldate(time.time()),
                                  msg.as_bytes(policy=email.policy.SMTP))
        return typ == "OK"

    def find_sent_message(self, to_addr, subject_hint=None):
        ta = (to_addr or "").strip().lower()
        msgs = []
        if subject_hint:
            msgs = self._search(SENT_FOLDER, "(TO %s SUBJECT %s)" % (_q(ta), _q(subject_hint[:60])), max_n=10)
            msgs = [m for m in msgs if ta in m["to"]]  # TO is a substring match too
        if not msgs:
            msgs = [m for m in self._search(SENT_FOLDER, "(TO %s)" % _q(ta), max_n=10) if ta in m["to"]]
        return _pick_sent(msgs, None)

    def close(self):
        try:
            if self._folder:
                self.imap.close()
            self.imap.logout()
        except Exception:
            pass


class mailbox:
    """Context manager: `with mailbox(cfg) as mb: mb.find_replies(...)`. Raises MailError on connect."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.box = None

    def __enter__(self):
        self.box = _FileBox(self.cfg) if backend(self.cfg) == "file" else _ImapBox(self.cfg)
        return self.box

    def __exit__(self, *exc):
        if self.box:
            self.box.close()
        return False


# ------------------------------------------------------------------ one-shot API (SPEC)

def find_replies(cfg, from_addr, since_date) -> list:
    with mailbox(cfg) as mb:
        return mb.find_replies(from_addr, since_date)


def find_bounces(cfg, since_days=7) -> list:
    with mailbox(cfg) as mb:
        return mb.find_bounces(since_days)


def find_sent_message(cfg, to_addr, subject_hint=None):
    with mailbox(cfg) as mb:
        return mb.find_sent_message(to_addr, subject_hint)


def find_sent_message_id(cfg, to_addr, subject_hint=None):
    m = find_sent_message(cfg, to_addr, subject_hint)
    return m["message_id"] if m and m.get("message_id") else None


def test_login(cfg):
    """(ok, error) after logging in to SMTP and IMAP (file backend: checks folders are writable)."""
    if backend(cfg) == "file":
        try:
            for name in ("outbox", "inbox"):
                p = config.sub_dir(name) / ".write_test"
                p.write_text("ok", encoding="utf-8")
                p.unlink()
            return True, None
        except OSError as e:
            return False, str(e)
    addr, pw = _email_cfg(cfg).get("address"), _password(cfg)
    if not addr or not pw:
        return False, "Gmail address or app password is empty"
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT, context=ssl.create_default_context()) as s:
            s.login(addr, pw)
    except smtplib.SMTPAuthenticationError:
        return False, ("SMTP login rejected. Use a 16-letter Google App Password (Google Account > Security > "
                       "2-Step Verification > App passwords), not your normal password.")
    except (smtplib.SMTPException, OSError) as e:
        return False, "SMTP connection failed: %s" % e
    try:
        with mailbox(cfg) as mb:
            mb._select("INBOX")
    except MailError as e:
        return False, "%s (enable IMAP in Gmail settings > Forwarding and POP/IMAP)" % e
    except (imaplib.IMAP4.error, OSError) as e:
        return False, "IMAP failed: %s" % e
    return True, None
