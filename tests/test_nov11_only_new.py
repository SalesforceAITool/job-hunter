"""Akshay, 11 Nov: remember everything already handled and send the AI only NEW work (saves tokens).

* Easy Apply: a no-AI check of LinkedIn's public Easy Apply list; jobs seen before are never queued again, and a
  cycle with nothing new runs no Easy Apply AI at all. The list unreadable -> the AI searches as before.
* Posts: every post the posts lane read is remembered; a page with nothing new is skipped.
* Forms that stopped only on the old application email go back to the Easy Apply lane once.
"""
import json
import sys
import time
import unittest
from pathlib import Path

from test_backend_helpers import TempData
from jh import actions, config, db, engine, runner, seen, watch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))


def card(i, title="Salesforce Developer", company="Acme"):
    return ('<li><div class="base-card" data-entity-urn="urn:li:jobPosting:%d">'
            '<h3 class="base-search-card__title">%s</h3><h4 class="base-search-card__subtitle">%s</h4>'
            '<span class="job-search-card__location">India</span><time datetime="2026-11-11">5 minutes ago</time></div></li>'
            % (i, title, company))


class FakeList:
    """LinkedIn's public list: returns whatever cards are 'posted' right now."""

    def __init__(self):
        self.cards, self.calls, self.status = [], [], 200

    def __call__(self, url):
        self.calls.append(url)
        return (self.status, "".join(self.cards)) if self.status == 200 else (self.status, "")


class OnlyNewEasyApplyTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        cfg = config.load()
        cfg["li_fast"] = dict(cfg["li_fast"], every_min=10, start_hour=0, end_hour=24, lanes=["easy_apply", "posts"],
                              gap_after_min=0, per_run=10)
        cfg["profile"]["email"] = "akshay.verma2784@gmail.com"
        config.save(cfg)
        self.calls = []
        self.lst = FakeList()
        self.w = watch.Watcher(None, start_run=lambda trig, **k: self.calls.append(k) or len(self.calls),
                               probe_fetch=self.lst, sleep=lambda s: None)

    def tick(self, t):
        self.w.state["next_li_fast"] = 0
        return self.w.li_fast_tick(self.conn, t, local_hour=12)

    def test_new_jobs_go_to_the_ai_once(self):
        self.lst.cards = [card(4400000001), card(4400000002, company="Beta"), card(4400000003, "Account Executive")]
        res = self.tick(time.time())
        self.assertEqual(res["kind"], "li_fast")
        kw = self.calls[0]
        self.assertEqual(kw["lanes"], ["easy_apply", "posts"])
        self.assertEqual(sorted(t["url"] for t in kw["targets"]),
                         ["https://www.linkedin.com/jobs/view/4400000001/", "https://www.linkedin.com/jobs/view/4400000002/"])
        st = dict(self.conn.execute("SELECT ext_id, status FROM watch_seen").fetchall())
        self.assertEqual(st, {"4400000001": "sent", "4400000002": "sent", "4400000003": "ignored"})
        # 10 minutes later the same cards are still listed: nothing new -> only the posts pass, no Easy Apply AI
        res = self.tick(time.time() + 600)
        self.assertEqual(self.calls[1]["lanes"], ["posts"])
        self.assertNotIn("targets", self.calls[1])
        # one new job appears -> only that one goes to the AI
        self.lst.cards.append(card(4400000004, company="Gamma"))
        self.tick(time.time() + 1200)
        self.assertEqual([t["company"] for t in self.calls[2]["targets"]], ["Gamma"])

    def test_nothing_new_and_easy_apply_only_starts_no_run(self):
        cfg = config.load(); cfg["li_fast"]["lanes"] = ["easy_apply"]; config.save(cfg)
        self.lst.cards = [card(4400000001)]
        self.tick(time.time())
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.tick(time.time() + 600))
        self.assertEqual(len(self.calls), 1)                      # no AI run, no tokens
        n = self.conn.execute("SELECT COUNT(*) FROM events WHERE message LIKE 'LinkedIn checked without AI%'").fetchone()[0]
        self.assertEqual(n, 1)
        self.tick(time.time() + 1200)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE message LIKE 'LinkedIn checked without AI%'")
                         .fetchone()[0], 1)                       # said once, not every 10 minutes

    def test_jobs_already_in_job_hunter_are_not_queued(self):
        actions.save_job(self.conn, 1, "easy_apply", "linkedin_india", "Acme", "Salesforce Developer",
                         url="https://www.linkedin.com/jobs/view/4400000001/")
        self.lst.cards = [card(4400000001)]
        cfg = config.load(); cfg["li_fast"]["lanes"] = ["easy_apply"]; config.save(cfg)
        self.assertIsNone(self.tick(time.time()))
        self.assertEqual(self.conn.execute("SELECT status FROM watch_seen").fetchone()[0], "known")

    def test_list_unreadable_falls_back_to_the_ai_search(self):
        self.lst.status = 429
        self.tick(time.time())
        self.assertEqual(self.calls[0]["lanes"], ["easy_apply", "posts"])
        self.assertNotIn("targets", self.calls[0])                # the AI searches LinkedIn itself, as before
        self.assertFalse(self.w.state["probe"]["ok"])

    def test_probe_can_be_turned_off(self):
        cfg = config.apply_update(config.load(), {"li_fast": {"probe": False}})
        config.save(cfg)
        self.lst.cards = [card(4400000001)]
        self.tick(time.time())
        self.assertEqual(self.lst.calls, [])
        self.assertNotIn("targets", self.calls[0])

    def test_window_covers_the_gap_since_the_last_check(self):
        self.lst.cards = [card(4400000001)]
        t = time.time()
        self.tick(t)
        self.assertIn("f_TPR=r86400", self.lst.calls[0])          # first check: the whole day
        self.tick(t + 600)
        self.assertIn("f_TPR=r3600", self.lst.calls[-1])          # then about an hour

    def add_stuck_form(self, ext, email="akshay.verma0031@gmail.com", extra=None):
        j = actions.save_job(self.conn, 1, "easy_apply", "linkedin_india", "Stuck%s" % ext, "Salesforce Developer",
                             url="https://www.linkedin.com/jobs/view/%s/" % ext)["job_id"]
        qs = ["email: add %s to your LinkedIn account (Settings > Sign in & security > Email addresses)" % email] + (extra or [])
        now = db.now_iso()
        self.conn.execute("INSERT INTO portal_apps (job_id, ats, apply_url, status, questions_json, created_at, updated_at,"
                          " ready) VALUES (?,?,?,?,?,?,?,0)", (j, "linkedin_easy_apply",
                                                                "https://www.linkedin.com/jobs/view/%s/" % ext,
                                                                "needs_input", json.dumps(qs), now, now))
        return j

    def test_forms_stuck_on_the_old_email_are_retried_once(self):
        j1 = self.add_stuck_form(4488394646)
        j2 = self.add_stuck_form(4487462533, extra=["check resume: tailored PDF upload failed"])
        self.add_stuck_form(4487000000, email="akshay.verma2784@gmail.com")   # the current email: retrying won't help
        self.lst.cards = [card(4400000009, company="Fresh")]
        self.tick(time.time())
        tg = self.calls[0]["targets"]
        self.assertEqual([t.get("job_id") for t in tg], [j1, j2, None])
        self.assertEqual(tg[2]["company"], "Fresh")
        self.tick(time.time() + 600)                             # handed back once only
        self.assertEqual(self.calls[1]["lanes"], ["posts"])

    def test_stuck_forms_still_go_when_the_list_is_unreadable(self):
        j1 = self.add_stuck_form(4488394646)
        self.lst.status = 429
        self.tick(time.time())
        self.assertEqual([t.get("job_id") for t in self.calls[0]["targets"]], [j1])

    def test_lane_sends_retries_straight_to_the_ai(self):
        j1 = self.add_stuck_form(4488394646)
        seen_cfg = {}

        class Eng:
            def run_lane(self, run_id, lane, cfg, emit, stop):
                seen_cfg.update(cfg)
                return {"status": "done"}
        cfg = dict(config.load(), _targets=[{"job_id": j1, "company": "Stuck", "role": "Salesforce Developer",
                                             "url": "https://www.linkedin.com/jobs/view/4488394646/"}])
        cfg.setdefault("engine", {})["mock"] = False
        import threading
        results = {}
        rid = self.conn.execute("INSERT INTO runs (started_at, status, trigger, stats_json) VALUES (?, 'running', 'li_fast', '{}')",
                                (db.now_iso(),)).lastrowid
        emit = runner._Emitter(None, rid)
        orig = runner._mock_mode
        runner._mock_mode = lambda c: False
        try:
            runner._run_li_lane(Eng(), "easy_apply", rid, cfg, emit, threading.Event(), None, results)
        finally:
            runner._mock_mode = orig
        self.assertEqual([t["job_id"] for t in seen_cfg["_targets"]], [j1])
        t = engine.compose_system_prompt("easy_apply", str(engine.PROMPTS_DIR / "easy_apply.md"), rid,
                                         targets=seen_cfg["_targets"], li_fast=5)
        self.assertIn("PRE-SCREENED LIST", t)
        self.assertIn("%d — Salesforce Developer" % j1, t)
        self.assertNotIn("FAST APPLY", t)                        # nothing to search with a ready list


class PostMemoryTests(TempData, unittest.TestCase):
    U1 = "https://www.linkedin.com/feed/update/urn:li:activity:7393000000000000001/"
    U2 = "https://www.linkedin.com/feed/update/urn:li:activity:7393000000000000002/"

    def run_row(self):
        return self.conn.execute("INSERT INTO runs (started_at, status, trigger, stats_json) VALUES (?, 'running', 'li_fast',"
                                 " '{}')", (db.now_iso(),)).lastrowid

    def test_posts_read_once(self):
        r1 = self.run_row()
        res = seen.filter_posts(self.conn, r1, [self.U1, self.U2, self.U1])
        self.assertEqual((res["new"], res["seen"]), ([self.U1, self.U2], 1))
        self.assertEqual(seen.filter_posts(self.conn, r1, [self.U1])["new"], [])      # same run: already listed
        seen.close_posts(self.conn, r1, True)
        r2 = self.run_row()
        self.assertEqual(seen.filter_posts(self.conn, r2, [self.U1, self.U2])["new"], [])

    def test_failed_lane_gives_its_posts_back(self):
        r1 = self.run_row()
        seen.filter_posts(self.conn, r1, [self.U1])
        seen.close_posts(self.conn, r1, False)
        self.assertEqual(seen.filter_posts(self.conn, self.run_row(), [self.U1])["new"], [self.U1])

    def test_saved_posts_count_as_read(self):
        actions.save_job(self.conn, 1, "posts", "posts", "Acme", "Salesforce Developer", url=self.U1)
        self.assertEqual(seen.filter_posts(self.conn, self.run_row(), [self.U1, self.U2])["new"], [self.U2])

    def test_slug_links_and_links_without_an_id(self):
        u = "https://www.linkedin.com/posts/jane_hiring-activity-7393000000000000001-AbCd"
        self.assertEqual(seen.post_id(u), "7393000000000000001")
        res = seen.filter_posts(self.conn, self.run_row(), ["https://www.linkedin.com/feed/"])
        self.assertEqual(res["no_id"], 1)

    def test_tool_and_prompt(self):
        import jobhunt_mcp as m
        ctx = m.Ctx()
        ctx.ensure()
        ctx.run_id, ctx.lane = self.run_row(), "posts"
        res, err = m.call_tool(ctx, "new_posts", {"urls": [self.U1]})
        self.assertFalse(err)
        self.assertEqual(res["new"], [self.U1])
        t = engine.compose_system_prompt("posts", str(engine.PROMPTS_DIR / "posts.md"), 1, quick=True)
        self.assertIn("new_posts", t)
        self.assertIn("skipped without get_page_text", t)


if __name__ == "__main__":
    unittest.main()
