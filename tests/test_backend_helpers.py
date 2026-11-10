"""Shared helpers for backend tests (no test cases here)."""
import os
import shutil
import sys
import tempfile
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests_fixture_tracker.csv"

RESUME = {
    "headline": "Salesforce Developer (7+ years) | OmniStudio, CPQ & LWC",
    "summary": "Salesforce developer with 7+ years & hands-on OmniStudio <BCBSMA> work.",
    "skills": [["Development", "Apex, LWC"], ["CPQ", "Salesforce CPQ"], ["Clouds", "Sales, Service"],
               ["Integration", "REST"], ["Platform", "Flows"], ["DevOps", "Git"]],
    "project_order": ["bcbsm", "amkor", "nonexistent"],
}


class TempData:
    """Mixin: isolated data dir + env for each test; file email backend config."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="jh_test_")
        self._old_env = {k: os.environ.get(k) for k in ("JOBHUNTER_DATA", "JOBHUNTER_DB", "JOBHUNTER_CONFIG",
                                                         "JOBHUNTER_MOCK_ENGINE")}
        os.environ["JOBHUNTER_DATA"] = self._tmp
        os.environ["JOBHUNTER_OFFLINE"] = "1"   # Himalayas & co. never hit the network in tests
        for k in ("JOBHUNTER_DB", "JOBHUNTER_CONFIG", "JOBHUNTER_MOCK_ENGINE"):
            os.environ.pop(k, None)
        from jh import config, db
        self.data = Path(self._tmp)
        self.cfg = config.load()
        self.cfg["email"]["backend"] = "file"
        self.cfg["dry_run"] = False
        config.save(self.cfg)
        self.conn = db.connect()

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def emitted(self):
        out = []

        def emit(lane, type, message, level="info", data=None):
            out.append({"lane": lane, "type": type, "message": message, "level": level, "data": data or {}})
        return out, emit


def write_inbox(data_dir, from_addr, subject, body="Thanks, let's talk.", date=None, headers=None, name=None):
    """Drop an .eml into <data>/inbox for the file mail backend."""
    from jh import db
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "akshay.verma0031@gmail.com"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(date or db.now())
    msg["Message-ID"] = make_msgid()
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    inbox = Path(data_dir) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    p = inbox / (name or ("%s.eml" % make_msgid().strip("<>").replace("@", "_").replace(".", "_")))
    p.write_bytes(msg.as_bytes())
    return p
