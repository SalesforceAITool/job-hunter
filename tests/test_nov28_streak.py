"""28 Nov: 9 "Delivery Status Notification (Failure)" mails, each 0-1 s after its send, for 9 consecutive sends from
one young Gmail account; stored as dead addresses although Gmail refused the next send with 5.4.5."""
import unittest
from datetime import timedelta

from test_backend_helpers import TempData
from jh import db, followups, sendguard

ACC = "jobs.one@example.com"


class StreakTests(TempData, unittest.TestCase):
    def _send(self, i, addr, t, status="sent"):
        jid = self.conn.execute("INSERT INTO jobs (company, role, status) VALUES (?,?, 'contacted')",
                                ("Co%d" % i, "Salesforce Developer")).lastrowid
        cid = self.conn.execute("INSERT INTO contacts (job_id, email) VALUES (?,?)", (jid, addr)).lastrowid
        eid = self.conn.execute("INSERT INTO emails (job_id, contact_id, kind, status, sent_at, sender) VALUES"
                                " (?,?, 'initial', ?, ?, ?)", (jid, cid, status, t.isoformat(), ACC)).lastrowid
        return jid, cid, eid

    def setUp(self):
        super().setUp()
        self.t0 = (db.now() - timedelta(hours=3)).replace(microsecond=0)

    def _bounces(self, addrs, dead=False):
        return [{"failed_addr": a, "subject": "Delivery Status Notification (Failure)",
                 "date": (self.t0 + timedelta(minutes=4 * i, seconds=1)).isoformat(), "limit": False, "dead": dead}
                for i, a in enumerate(addrs)]

    def test_consecutive_instant_failures_are_an_account_streak(self):
        addrs = ["a@one.example", "b@two.example", "c@gmail.com", "d@four.example"]
        for i, a in enumerate(addrs):
            self._send(i, a, self.t0 + timedelta(minutes=4 * i))
        b = self._bounces(addrs)
        self.assertEqual(followups._account_streak(self.conn, ACC, b), {0, 1, 2, 3})
        self.assertEqual(followups._account_streak(self.conn, "other@example.com", b), set())

    def test_a_delivered_send_in_between_breaks_the_streak(self):
        addrs = ["a@one.example", "b@two.example", "ok@three.example", "c@four.example", "d@five.example"]
        for i, a in enumerate(addrs):
            self._send(i, a, self.t0 + timedelta(minutes=4 * i))
        b = [x for x in self._bounces(addrs) if x["failed_addr"] != "ok@three.example"]
        self.assertEqual(followups._account_streak(self.conn, ACC, b), set())   # runs of 2 only

    def test_dead_mailbox_notices_never_count(self):
        addrs = ["a@one.example", "b@two.example", "c@three.example"]
        for i, a in enumerate(addrs):
            self._send(i, a, self.t0 + timedelta(minutes=4 * i))
        self.assertEqual(followups._account_streak(self.conn, ACC, self._bounces(addrs, dead=True)), set())

    def test_slow_notices_are_real_bounces(self):
        addrs = ["a@one.example", "b@two.example", "c@three.example"]
        for i, a in enumerate(addrs):
            self._send(i, a, self.t0 + timedelta(minutes=4 * i) - timedelta(minutes=10))
        self.assertEqual(followups._account_streak(self.conn, ACC, self._bounces(addrs)), set())

    def test_stored_bounce_is_undone_and_requeued(self):
        addrs = ["a@one.example", "b@two.example", "c@gmail.com"]
        rows = [self._send(i, a, self.t0 + timedelta(minutes=4 * i)) for i, a in enumerate(addrs)]
        b = self._bounces(addrs)
        counts = {"bounces": 0}
        ev = []
        emit = lambda *a, **k: ev.append(a)                  # noqa: E731
        for x in b:                                          # what build .2 did: dead-address bounces
            followups._mark_bounce(self.conn, x, emit, counts)
        self.assertEqual(counts["bounces"], 3)
        cfg = dict(self.cfg, email={"backend": "smtp", "address": ACC, "app_password": "a" * 16})
        streak = followups._account_streak(self.conn, ACC, b)
        self.assertEqual(streak, {0, 1, 2})
        for j, x in enumerate(b):
            followups._mark_limit(self.conn, cfg, {"address": ACC}, x, emit, counts, streak=j in streak)
        for jid, cid, eid in rows:
            e = self.conn.execute("SELECT status, queued, next_try FROM emails WHERE id=?", (eid,)).fetchone()
            self.assertEqual((e["status"], e["queued"]), ("sending", 1))       # never delivered: goes out again
            self.assertGreaterEqual(db.parse_ts(e["next_try"]), self.t0 + timedelta(hours=24))
            self.assertEqual(self.conn.execute("SELECT stop_nudges FROM contacts WHERE id=?", (cid,)).fetchone()[0], 0)
            self.assertEqual(self.conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0], "contacted")
            self.assertEqual(self.conn.execute("SELECT kind FROM replies WHERE contact_id=?", (cid,)).fetchone()[0],
                             "limit")
        # the same notices read again on the next run: not bounces any more
        counts = {"bounces": 0}
        for x in b:
            followups._mark_bounce(self.conn, x, emit, counts)
        self.assertEqual(counts["bounces"], 0)


if __name__ == "__main__":
    unittest.main()
