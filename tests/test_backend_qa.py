import http.client
import json
import unittest

from test_backend_helpers import TempData
from jh import actions, db, qa, server


def sim(a, b):
    return qa.similarity(qa.normalize(a), qa.normalize(b))


class MatchTests(unittest.TestCase):
    def test_same_question_different_wording_matches(self):
        same = [
            ("How many years of work experience do you have with Salesforce CPQ?",
             "How many years of experience do you have with Salesforce CPQ? *"),
            ("Will you now or in the future require sponsorship for employment visa status (e.g. H-1B)?",
             "Will you now, or in the future, require sponsorship for employment visa status?"),
            ("Are you legally authorized to work in the United States?", "Are you legally authorized to work in the US?"),
            ("Desired hourly rate (USD)", "What is your expected hourly rate in USD?"),
            ("Are you comfortable working in a remote setting?", "Are you comfortable working remotely?"),
        ]
        for a, b in same:
            self.assertGreaterEqual(sim(a, b), qa.CONFIDENT, (a, b))

    def test_different_subject_never_confident(self):
        diff = [
            ("How many years of work experience do you have with Salesforce CPQ?",
             "How many years of work experience do you have with Apex?"),
            ("Are you legally authorized to work in the United States?", "Are you legally authorized to work in India?"),
            ("Do you have Salesforce Platform Developer II certification?",
             "Do you have Salesforce Platform Developer I certification?"),
            ("What is your expected salary (USD)?", "What is your expected salary (INR)?"),
            ("What is your current CTC?", "What is your expected CTC?"),
        ]
        for a, b in diff:
            self.assertLess(sim(a, b), qa.CONFIDENT, (a, b))

    def test_best_option(self):
        self.assertEqual(qa.best_option("Yes", ["No", "Yes"]), "Yes")
        self.assertEqual(qa.best_option("15 days", ["Immediately", "0-15 days", "1 month"]), "0-15 days")
        self.assertEqual(qa.best_option("yes", ["Yes, I am", "No, I am not"]), "Yes, I am")
        self.assertEqual(qa.best_option("purple", ["Yes", "No"]), "")


class BankTests(TempData, unittest.TestCase):
    def test_report_answer_lookup_cycle(self):
        c = self.conn
        jid = actions.save_job(c, None, "easy_apply", "linkedin_us", "Acme", "Salesforce Developer")["job_id"]
        r = qa.report(c, None, "easy_apply", jid, [
            {"question": "What is your desired hourly rate (USD)?", "field_type": "number", "required": True},
            {"question": "Will you require visa sponsorship?", "field_type": "yes_no", "options": ["Yes", "No"]},
            "  ", {"question": ""}])
        self.assertEqual(r["new"], 2)
        # seen again on another job: no duplicate, counter goes up
        jid2 = actions.save_job(c, None, "easy_apply", "linkedin_us", "Beta", "Salesforce Admin")["job_id"]
        r2 = qa.report(c, None, "easy_apply", jid2, [{"question": "What is your desired hourly rate (USD) *"}])
        self.assertEqual(r2["new"], 0)
        rows = qa.list_questions(c, "open")
        self.assertEqual(len(rows), 2)
        rate = [x for x in rows if "hourly" in x["question"]][0]
        self.assertEqual(rate["times_seen"], 2)
        self.assertEqual(rate["jobs"], [jid, jid2])
        self.assertEqual(rate["last_company"], "Beta")
        # not answered yet -> lookup finds nothing
        self.assertFalse(qa.lookup(c, ["Desired hourly rate (USD)"])["results"][0]["found"])
        qa.save_answer(c, rate["id"], answer="40")
        res = qa.lookup(c, [{"question": "Desired hourly rate (USD)", "field_type": "number"}])["results"][0]
        self.assertTrue(res["found"])
        self.assertEqual(res["answer"], "40")
        visa = [x for x in rows if "sponsorship" in x["question"]][0]
        qa.save_answer(c, visa["id"], status="mine")
        res = qa.lookup(c, [{"question": "Will you require visa sponsorship?", "options": ["Yes", "No"]}])["results"][0]
        self.assertFalse(res["found"])
        self.assertEqual(res["status"], "mine")
        self.assertEqual(qa.counts(c), {"open": 0, "answered": 1, "mine": 1})
        self.assertEqual(db.summary(c)["questions_open"], 0)
        used = c.execute("SELECT times_used FROM questions WHERE id=?", (rate["id"],)).fetchone()[0]
        self.assertEqual(used, 1)

    def test_add_edit_delete_and_validation(self):
        c = self.conn
        row = qa.add_question(c, "Notice period (days)?", "15")
        self.assertEqual(row["status"], "answered")
        self.assertEqual(qa.add_question(c, "notice period days", "0")["id"], row["id"])  # same question -> update
        self.assertEqual(qa.list_questions(c)[0]["answer"], "0")
        with self.assertRaises(ValueError):
            qa.save_answer(c, row["id"], answer="", status="answered")
        with self.assertRaises(ValueError):
            qa.add_question(c, "  ?? ", "x")
        with self.assertRaises(KeyError):
            qa.save_answer(c, 9999, answer="x")
        other = qa.add_question(c, "Expected CTC (LPA)?", "28")
        with self.assertRaises(ValueError):
            qa.save_answer(c, other["id"], question="Notice period (days)")  # would duplicate
        self.assertTrue(qa.delete_question(c, row["id"]))
        self.assertFalse(qa.delete_question(c, row["id"]))

    def test_candidates_for_near_miss(self):
        c = self.conn
        qa.add_question(c, "How many years of experience do you have with Salesforce CPQ?", "3")
        res = qa.lookup(c, ["How many years of experience do you have with Apex?"])["results"][0]
        self.assertFalse(res["found"])
        self.assertEqual(res["candidates"], [])  # different skill: not even a candidate
        qa.add_question(c, "What are your salary expectations?", "28 LPA")
        res = qa.lookup(c, ["What is your expected salary (USD)?"])["results"][0]
        self.assertFalse(res["found"])           # currency differs -> the AI must judge
        self.assertEqual(res["candidates"][0]["answer"], "28 LPA")


class RecordTests(TempData, unittest.TestCase):
    def test_log_learn_and_exports(self):
        c = self.conn
        jid = actions.save_job(c, None, "easy_apply", "linkedin_india", "UST", "Salesforce Developer")["job_id"]
        qa.report(c, None, "easy_apply", jid, ["Do you have experience with Marketing Cloud?"])  # open question
        r = qa.record(c, 7, "easy_apply", jid, [
            {"question": "How many years of experience do you have with SOAP?", "answer": "8", "field_type": "number",
             "source": "linkedin"},
            {"question": "Expected CTC (LPA)", "answer": "24", "field_type": "number", "source": "standard"},   # per job
            {"question": "Earliest start date", "answer": "2026-11-03", "field_type": "date", "source": "standard"},
            {"question": "Why do you want to join us?", "answer": "...", "field_type": "textarea", "source": "ai"},
            {"question": "Notice period", "answer": "Immediate joiner", "source": "standard", "per_job": True},
            {"question": "Do you have experience with Marketing Cloud?", "answer": "Yes", "field_type": "yes_no",
             "source": "standard"},
            {"question": "Total years of experience", "answer": "7", "source": "bank"},   # came from the bank already
            {"question": "", "answer": "x"}, {"question": "Blank answer", "answer": ""}, "junk"])
        self.assertEqual(r["logged"], 7)
        self.assertEqual(r["learned"], 1)  # Marketing Cloud (standard rule); the LinkedIn prefill is only logged
        soap = qa.lookup(c, ["Years of experience with SOAP?"])["results"][0]
        self.assertFalse(soap["found"])
        mc = [x for x in qa.list_questions(c) if "Marketing" in x["question"]][0]
        self.assertEqual((mc["status"], mc["answer"], mc["source"]), ("answered", "Yes", "agent"))
        self.assertFalse(qa.lookup(c, ["Expected CTC (LPA)"])["results"][0]["found"])
        # a learned answer never overwrites one Akshay typed
        qa.save_answer(c, mc["id"], answer="No")
        qa.record(c, 8, "easy_apply", jid, [{"question": "Do you have experience with Marketing Cloud?", "answer": "Yes"}])
        self.assertEqual(qa.lookup(c, ["Do you have experience with Marketing Cloud?"])["results"][0]["answer"], "No")
        # per-job log for the Ready-to-submit view (latest answer per question)
        ans = qa.form_answers_for_job(c, jid)
        self.assertEqual([a["answer"] for a in ans if "Marketing" in a["question"]], ["Yes"])
        # CSV files kept in the data folder, Excel-friendly
        bank = (self.data / "answers_bank.csv").read_text(encoding="utf-8")
        log = (self.data / "form_answers_log.csv").read_text(encoding="utf-8")
        self.assertTrue(bank.startswith("\ufeffQuestion,Answer,Status"))
        self.assertIn("learned from a form", bank)
        self.assertIn("UST", log)
        self.assertIn("Expected CTC (LPA),24", log)
        # formula injection is neutralised
        qa.add_question(c, "=HYPERLINK(1)", "=cmd")
        self.assertIn("'=HYPERLINK(1)", (self.data / "answers_bank.csv").read_text(encoding="utf-8"))

    def test_portal_list_has_filled_answers(self):
        c = self.conn
        jid = actions.save_job(c, None, "easy_apply", "linkedin_us", "Acme", "SF Dev")["job_id"]
        c.execute("INSERT INTO portal_apps (job_id, ats, apply_url, status, created_at, updated_at)"
                  " VALUES (?, 'linkedin_easy_apply', 'https://www.linkedin.com/jobs/view/1/', 'prefilled', '', '')", (jid,))
        qa.record(c, None, "easy_apply", jid, [{"question": "Notice period?", "answer": "15 days"}])
        row = db.portal_list(c)[0]
        self.assertEqual(row["answers"][0]["answer"], "15 days")


class SeedTests(TempData, unittest.TestCase):
    def test_seed_once_and_respects_deletes(self):
        c = self.conn
        qa.add_question(c, "Notice period (in days)", "30")       # his own answer must survive the seed
        n = qa.seed_once(c)
        self.assertEqual(n, len(qa.SEED) - 1)
        self.assertEqual(qa.lookup(c, ["Notice period (in days)"])["results"][0]["answer"], "30")
        self.assertGreaterEqual(qa.counts(c)["open"], 4)
        q = [x for x in qa.list_questions(c) if "drug test" in x["question"]][0]
        qa.delete_question(c, q["id"])
        self.assertEqual(qa.seed_once(c), 0)                       # once only: deleted stays deleted
        self.assertFalse([x for x in qa.list_questions(c) if "drug test" in x["question"]])


class QuestionsApiTests(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.srv = server.create_server(self.port, web_dir=self.data / "web_missing")
        server.serve_in_thread(self.srv)

    def tearDown(self):
        server.shutdown_server(self.srv)
        super().tearDown()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port}
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse(); raw = r.read(); c.close()
        return r.status, json.loads(raw or b"{}")

    def test_api_flow(self):
        qa.report(self.conn, None, "portal", None, [{"question": "Earliest start date?", "field_type": "date"}])
        st, d = self.req("GET", "/api/questions?status=open")
        self.assertEqual(st, 200)
        self.assertEqual(d["counts"]["open"], 1 + sum(1 for x in qa.SEED if x[2] == "open"))  # + startup seed
        qid = [x for x in d["questions"] if x["question"] == "Earliest start date?"][0]["id"]
        st, row = self.req("POST", "/api/questions/%d" % qid, {"answer": "Within 15 days"})
        self.assertEqual((st, row["status"]), (200, "answered"))
        self.assertEqual(self.req("POST", "/api/questions/%d" % qid, {"status": "bogus"})[0], 400)
        self.assertEqual(self.req("POST", "/api/questions/99999", {"answer": "x"})[0], 404)
        st, new = self.req("POST", "/api/questions", {"question": "Total years of Salesforce experience?", "answer": "7"})
        self.assertEqual((st, new["status"]), (200, "answered"))
        self.assertEqual(self.req("POST", "/api/questions", {"question": ""})[0], 400)
        st, d = self.req("GET", "/api/questions?q=Salesforce%20experience")
        self.assertEqual([x["answer"] for x in d["questions"]], ["7"])
        st, d = self.req("POST", "/api/questions/%d" % qid, {"delete": True})
        self.assertEqual((st, d.get("deleted")), (200, qid))
        st, s = self.req("GET", "/api/summary")
        self.assertIn("questions_open", s)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/questions/export.csv?kind=bank")
        r = c.getresponse(); raw = r.read(); c.close()
        self.assertEqual(r.status, 200)
        self.assertIn("text/csv", r.getheader("Content-Type"))
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertIn("Total years of Salesforce experience", raw.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
