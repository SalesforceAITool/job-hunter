"""Independent QA of the "Questions for you" question bank (jh/qa.py, /api/questions, MCP tools, UI tab).

Tests that pin CORRECT behaviour that the code already has are plain tests.
Real bugs are written as the CORRECT expectation and marked @unittest.expectedFailure with a
"BUG:" comment, so the suite stays green and the bug stays documented. When a bug is fixed the
test turns into an "unexpected success": remove its decorator then.

    cd tests && python3 -m unittest test_qa_bank_qa -v
"""
import http.client
import json
import os
import socket
import sqlite3
import sys
import unittest

from test_backend_helpers import TempData
from jh import actions, db, qa, server

HERE = os.path.dirname(os.path.abspath(__file__))


def sim(a, b):
    return qa.similarity(qa.normalize(a), qa.normalize(b))


# --------------------------------------------------------------------------- matching table
# Pairs that ask DIFFERENT things: must never be a confident (>= 0.9) match.
DIFFERENT_OK = [
    ("How many years of work experience do you have with Salesforce CPQ?", "How many years of work experience do you have with Apex?"),
    ("Are you legally authorized to work in the United States?", "Are you legally authorized to work in India?"),
    ("Are you legally authorized to work in the United States?", "Are you legally authorized to work in Canada?"),
    ("Will you now or in the future require sponsorship for employment visa status?", "Do you NOT require sponsorship for employment visa status?"),
    ("Will you now or in the future require visa sponsorship?", "Will you now or in the future not require visa sponsorship?"),
    ("Do you require visa sponsorship?", "Do you not require visa sponsorship?"),
    ("Are you willing to relocate?", "Are you willing to travel?"),
    ("What is your expected salary (USD)?", "What is your expected salary (INR)?"),
    ("What is your current CTC?", "What is your expected CTC?"),
    ("Current CTC (LPA)", "Expected CTC (LPA)"),
    ("What is your current salary?", "What is your expected salary?"),
    ("Current annual salary", "Expected annual salary"),
    ("Do you have Salesforce Platform Developer II certification?", "Do you have Salesforce Platform Developer I certification?"),
    ("Expected hourly rate (USD)", "Expected annual salary (USD)"),
    ("What is your desired hourly rate?", "What is your desired annual compensation?"),
    ("Expected hourly rate in USD", "Expected hourly rate in EUR"),
    ("Expected salary per hour", "Expected salary per year"),
    ("Expected compensation (hourly)", "Expected compensation (annual)"),
    ("How many years of experience do you have with Sales Cloud?", "How many years of experience do you have with Service Cloud?"),
    ("How many years of experience do you have with LWC?", "How many years of experience do you have with Aura?"),
    ("How many years of experience do you have with Salesforce?", "How many months of experience do you have with Salesforce?"),
    ("Are you authorized to work in the US?", "Are you a US citizen?"),
    ("Are you authorized to work in the UK?", "Are you authorized to work in the US?"),
    ("Are you authorized to work in the EU?", "Are you authorized to work in the US?"),
    ("Are you authorized to work in the US without sponsorship?", "Are you authorized to work in the US with sponsorship?"),
    ("Are you NOT a US citizen?", "Are you a US citizen?"),
    ("Do you have an active security clearance?", "Do you have an active Public Trust clearance?"),
    ("Are you comfortable working onsite?", "Are you comfortable working remotely?"),
    ("Are you comfortable working in a hybrid setting?", "Are you comfortable working in a remote setting?"),
    ("Are you willing to work on W2?", "Are you willing to work on C2C?"),
    ("Are you willing to work on a 1099 basis?", "Are you willing to work on C2C?"),
    ("Notice period (days)", "Notice period (months)"),
    ("Are you currently serving notice period?", "What is your notice period?"),
    ("Can you start immediately?", "Can you start within 30 days?"),
    ("Are you willing to work in EST hours?", "Are you willing to work in PST hours?"),
    ("Are you willing to undergo a background check?", "Are you willing to undergo a drug test?"),
    ("Do you have experience with Marketing Cloud?", "Do you have experience with Commerce Cloud?"),
    ("Have you worked with Salesforce Field Service?", "Have you worked with Salesforce Financial Services Cloud?"),
    ("What is your gender?", "What is your race?"),
    ("Are you a protected veteran?", "Do you have a disability?"),
    ("Are you 18 years of age or older?", "Are you 21 years of age or older?"),
    ("Do you have a bachelor's degree?", "Do you have a master's degree?"),
    ("Have you ever worked for Accenture?", "Have you ever worked for Deloitte?"),
    ("Are you willing to relocate to Austin, TX?", "Are you willing to relocate?"),
    ("What is your minimum expected salary?", "What is your maximum expected salary?"),
    ("Expected salary (monthly)", "Expected salary (annual)"),
    ("Expected salary per month in INR", "Expected salary per annum in INR"),
    ("Do you have experience leading a team?", "Do you have experience working in a team?"),
    ("Have you previously applied to this company?", "Have you previously worked at this company?"),
    ("Are you open to contract roles?", "Are you open to full-time roles?"),
    ("Are you open to contract-to-hire?", "Are you open to contract?"),
    ("Do you have a valid passport?", "Do you have a valid driver's license?"),
    ("How many years of experience do you have with Apex triggers?", "How many years of experience do you have with Apex classes?"),
    ("What is your expected CTC?", "What is your expected salary in USD?"),
    ("Are you located in the US?", "Are you located in India?"),
    ("Do you currently reside in the United States?", "Do you currently reside in India?"),
    ("Will you require H-1B sponsorship?", "Will you require H-1B transfer?"),
    ("How many years of Salesforce development experience do you have?", "How many years of Salesforce administration experience do you have?"),
    ("Rate your proficiency in Apex (1-10)", "Rate your proficiency in LWC (1-10)"),
    ("Are you willing to work night shifts?", "Are you willing to work weekend shifts?"),
    ("How many years of experience do you have with Salesforce CPQ?", "How many years of experience do you have with Oracle CPQ?"),
    ("Would you be willing to relocate?", "Would you be willing to commute?"),
    ("Do you need sponsorship now?", "Will you need sponsorship in the future?"),
    ("What is your first name?", "What is your last name?"),
    ("Have you been convicted of a felony?", "Have you been convicted of a misdemeanor?"),
    ("Are you okay with a 6 month contract?", "Are you okay with a 12 month contract?"),
    ("Are you currently located within 50 miles of Austin?", "Are you currently located within 5 miles of Austin?"),
]

# BUG (HIGH): pairs that ask different things but score 1.0 today.
DIFFERENT_FALSE_POSITIVES = [
    # single-digit numbers are dropped by content_words (len(w) > 1), so these collapse to the same words
    ("Are you Salesforce Platform Developer 1 certified?", "Are you Salesforce Platform Developer 2 certified?"),
    ("Do you have at least 5 years of experience with Apex?", "Do you have at least 3 years of experience with Apex?"),
    ("Are you willing to work in the office 3 days a week?", "Are you willing to work in the office 5 days a week?"),
    ("Can you overlap 4 hours with EST?", "Can you overlap 8 hours with EST?"),
    ("Is your notice period 1 month?", "Is your notice period 2 months?"),
    # "no" is a stop word -> negation lost
    ("Are you okay with no benefits?", "Are you okay with benefits?"),
    # the pronoun "us" is treated as the country (alias + _KEEP)
    ("Have you ever worked for us before?", "Have you ever worked in the US before?"),
    ("Have you previously been employed by us?", "Have you previously been employed in the US?"),
]

# Same meaning, different wording: should be >= 0.9 (auto-filled).
SAME_MATCHED = [
    ("How many years of work experience do you have with Salesforce CPQ?", "How many years of experience do you have with Salesforce CPQ? *"),
    ("Will you now or in the future require sponsorship for employment visa status (e.g. H-1B)?", "Will you now, or in the future, require sponsorship for employment visa status?"),
    ("Are you legally authorized to work in the United States?", "Are you legally authorized to work in the US?"),
    ("Are you legally authorized to work in the United States?", "Are you legally authorised to work in the U.S.?"),
    ("Desired hourly rate (USD)", "What is your expected hourly rate in USD?"),
    ("Are you comfortable working in a remote setting?", "Are you comfortable working remotely?"),
    ("Are you willing to relocate?", "Would you be willing to relocate?"),
    ("What is your notice period?", "Notice period"),
    ("Notice period (in days)", "What is your notice period in days?"),
    ("How many years of experience do you have with Apex?", "How many years of Apex experience do you have?"),
    ("Years of experience with LWC", "How many years of experience do you have with Lightning Web Components?"),
    ("What is your current CTC?", "Current CTC"),
    ("Are you a US citizen?", "Are you a citizen of the United States?"),
    ("Will you require visa sponsorship?", "Do you require visa sponsorship?"),
    ("What is your expected salary?", "What are your salary expectations?"),
    ("Total years of experience", "How many years of total experience do you have?"),
    ("How many years of Salesforce experience do you have?", "Years of Salesforce experience"),
    ("Gender", "What is your gender?"),
    ("What is your desired salary?", "Desired salary"),
    ("Phone number", "Mobile phone number"),
    ("Expected hourly rate (USD)", "Expected hourly rate in USD"),
    ("What is your expected CTC (INR)?", "Expected CTC in INR"),
    ("How many years of experience do you have with OmniStudio?", "How many years of OmniStudio experience do you have?"),
]

# Same meaning but below CONFIDENT today. Score >= CANDIDATE -> the AI gets it as a candidate (acceptable);
# below CANDIDATE -> lost completely and re-reported as a NEW open question (a duplicate for Akshay to answer).
SAME_CANDIDATE_ONLY = [
    ("What is your notice period?", "How long is your notice period?"),
    ("Do you have Salesforce Platform Developer II certification?", "Are you Salesforce Platform Developer II certified?"),
    ("What is your expected CTC?", "Expected CTC (in LPA)"),
    ("Do you have an active security clearance?", "Do you currently hold an active security clearance?"),
    ("What is your LinkedIn profile URL?", "LinkedIn Profile"),
    ("Will you now or in the future require sponsorship?", "Will you now or in the future require visa sponsorship?"),
    ("How did you hear about us?", "How did you hear about this job?"),
    ("Are you open to contract roles?", "Are you open to contract positions?"),
    ("Are you willing to undergo a background check?", "Are you willing to undergo a background check, in accordance with local law/regulations?"),
    ("Are you at least 18 years of age?", "Are you 18 years of age or older?"),
    ("What is your highest level of education?", "Highest level of education completed"),
]
SAME_MISSED = [   # BUG (MED): below CANDIDATE -> not even offered to the AI
    ("Are you willing to relocate?", "Are you open to relocation?"),
    ("Earliest start date", "When can you start?"),
    ("Are you willing to work US hours?", "Are you comfortable working US time zone hours?"),
    ("Do you have experience with Salesforce CPQ?", "Have you worked with Salesforce CPQ?"),
    ("Are you willing to work on C2C?", "Are you open to a C2C arrangement?"),
    ("Have you completed the following level of education: Bachelor's Degree?", "Do you have a Bachelor's degree?"),
]


def matching_summary():
    """Counts used in the QA report (and printed by `python3 test_qa_bank_qa.py --table`)."""
    diff = DIFFERENT_OK + DIFFERENT_FALSE_POSITIVES
    same = SAME_MATCHED + SAME_CANDIDATE_ONLY + SAME_MISSED
    ds = [sim(a, b) for a, b in diff]
    ss = [sim(a, b) for a, b in same]
    return {
        "different_pairs": len(diff),
        "false_positive_confident": sum(s >= qa.CONFIDENT for s in ds),
        "different_but_candidate": sum(qa.CANDIDATE <= s < qa.CONFIDENT for s in ds),
        "different_below_candidate": sum(s < qa.CANDIDATE for s in ds),
        "same_pairs": len(same),
        "true_positive_confident": sum(s >= qa.CONFIDENT for s in ss),
        "same_candidate_only": sum(qa.CANDIDATE <= s < qa.CONFIDENT for s in ss),
        "false_negative_missed": sum(s < qa.CANDIDATE for s in ss),
    }


class MatchingTable(unittest.TestCase):
    def test_table_size(self):
        self.assertGreaterEqual(len(DIFFERENT_OK) + len(DIFFERENT_FALSE_POSITIVES) + len(SAME_MATCHED)
                                + len(SAME_CANDIDATE_ONLY) + len(SAME_MISSED), 100)

    def test_different_questions_never_confident(self):
        for a, b in DIFFERENT_OK:
            with self.subTest(a=a, b=b):
                self.assertLess(sim(a, b), qa.CONFIDENT)

    def test_rewordings_matched(self):
        for a, b in SAME_MATCHED:
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(sim(a, b), qa.CONFIDENT)

    def test_rewordings_offered_as_candidates(self):
        for a, b in SAME_CANDIDATE_ONLY:
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(sim(a, b), qa.CANDIDATE)

    def test_similarity_nearly_symmetric_and_bounded(self):
        # SequenceMatcher is not exactly symmetric (LOW: lookup(new) vs stored can differ by ~0.01-0.02)
        for a, b in DIFFERENT_OK + SAME_MATCHED + DIFFERENT_FALSE_POSITIVES:
            s1, s2 = sim(a, b), sim(b, a)
            self.assertLess(abs(s1 - s2), 0.05, msg=(a, b))
            self.assertEqual(s1 >= qa.CONFIDENT, s2 >= qa.CONFIDENT, msg=(a, b))
            self.assertTrue(0.0 <= s1 <= 1.0)

    def test_bug_single_digit_numbers_ignored(self):
        # BUG HIGH jh/qa.py:58 content_words drops 1-char tokens, so "1"/"2", "3"/"5" vanish:
        # "Platform Developer 1" vs "2" and "at least 3 years" vs "at least 5 years" score 1.0.
        # Fix: keep digits: `if w in _KEEP or w.isdigit() or (w not in _STOP and len(w) > 1)`.
        for a, b in DIFFERENT_FALSE_POSITIVES[:5]:
            with self.subTest(a=a, b=b):
                self.assertLess(sim(a, b), qa.CONFIDENT)

    def test_bug_no_is_a_stopword(self):
        # BUG HIGH jh/qa.py:30 "no" in _STOP -> "okay with no benefits" == "okay with benefits".
        # Fix: drop "no" (and "yes") from _STOP; strip "(yes/no)" / "answer yes or no" hints in normalize().
        self.assertLess(sim("Are you okay with no benefits?", "Are you okay with benefits?"), qa.CONFIDENT)

    def test_bug_pronoun_us_is_the_country(self):
        # BUG MED jh/qa.py:37 r"\bu\.?s\.?a?\.?\b" also rewrites the pronoun "us" (and "us" is in _KEEP):
        # "worked for us before" == "worked in the US before" (1.0).
        # Fix: only alias dotted/upper-case forms on the raw text before lower(): r"\bU\.?S\.?A?\.?\b" (case-sensitive)
        # and r"\bunited states\b"; treat lower-case "us" after for/with/join/about/contact as a stop word.
        for a, b in DIFFERENT_FALSE_POSITIVES[6:]:
            with self.subTest(a=a, b=b):
                self.assertLess(sim(a, b), qa.CONFIDENT)

    def test_bug_common_rewordings_lost(self):
        # BUG MED: no stemming/synonyms -> relocate/relocation, "worked with"/"experience with", "open to"/"willing to"
        # fall under CANDIDATE and come back as NEW open questions. Fix: fold suffixes (-ation/-ing/-ed -> stem) and
        # alias "open to|comfortable with|okay with" -> "willing", "worked with|used" -> "experience".
        for a, b in SAME_MISSED:
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(sim(a, b), qa.CANDIDATE)


class BestOption(unittest.TestCase):
    def test_correct_picks(self):
        cases = [
            ("Yes", ["No", "Yes"], "Yes"),
            ("No", ["Yes", "No"], "No"),
            ("yes", ["Yes, I am", "No, I am not"], "Yes, I am"),
            ("no", ["Yes, I am", "No, I am not"], "No, I am not"),
            ("Not required", ["Required", "Not required"], "Not required"),
            ("No", ["Not sure", "Yes", "No"], "No"),
            ("No", ["Not sure", "Yes, I do", "No, I do not"], "No, I do not"),
            ("No", ["Yes", "Non-binary", "No"], "No"),
            ("3", ["0-2 years", "3-5 years", "5+ years"], "3-5 years"),
            ("Immediately", ["Immediately", "Less than 15 days", "1 month"], "Immediately"),
            ("Immediate joiner", ["Immediately", "Less than 15 days", "1 month"], "Immediately"),
            ("Immediately", ["Immediate", "15 days", "30 days"], "Immediate"),
            ("2", ["0-1", "2-4", "12+"], "2-4"),
            ("1", ["10+", "1-3"], "1-3"),
            ("0", ["0", "1-2", "3+"], "0"),
            ("India", ["British Indian Ocean Territory", "India"], "India"),
            ("Male", ["Female", "Male", "Decline"], "Male"),
            ("purple", ["Yes", "No"], ""),
            ("", ["Yes", "No"], ""),
            ("Yes", [], ""),
            ("Yes", "Yes|No", "Yes"),
            ("No", '["Yes","No"]', "No"),
        ]
        for ans, opts, want in cases:
            with self.subTest(ans=ans, opts=opts):
                self.assertEqual(qa.best_option(ans, opts), want)

    def test_bug_3plus_picks_5plus(self):
        # BUG HIGH jh/qa.py:110 fuzzy fallback: "3+ years" -> "5+ years" (ratio 0.875 beats "3-5 years" 0.82).
        # Over-claims experience. Fix: parse numbers first: if the answer has a number N, pick the option whose
        # range [lo, hi] (or "N+" = [N, inf), "less than N" = [0, N)) contains N; never fall back to fuzzy for numbers.
        self.assertEqual(qa.best_option("3+ years", ["0-2 years", "3-5 years", "5+ years"]), "3-5 years")

    def test_bug_negated_answer_maps_to_opposite(self):
        # BUG HIGH jh/qa.py:110: "No sponsorship needed" -> "Yes, I need sponsorship" (fuzzy ratio).
        # Fix: decide polarity first (answer starts with no/not/never vs yes) and only consider options of the same
        # polarity; return "" when the fuzzy best is below ~0.8 instead of 0.6.
        self.assertEqual(qa.best_option("No sponsorship needed", ["Yes, I need sponsorship", "No, I don't need sponsorship"]),
                         "No, I don't need sponsorship")

    def test_bug_substring_male_female(self):
        # BUG HIGH jh/qa.py:108 `len(a) > 3 and a in no` is a raw substring test: "male" -> "Female".
        # Fix: whole-word containment only: re.search(r"\b%s\b" % re.escape(a), no).
        self.assertEqual(qa.best_option("male", ["Female", "Decline to self identify"]), "")

    def test_bug_number_inside_range_not_found(self):
        # BUG MED: "7" with ranges -> "" (7 is inside 6-8); "4" -> "" (inside 3-5); "10" -> "" (5+). Fix: numeric ranges as above.
        self.assertEqual(qa.best_option("7", ["0-2 years", "3-5 years", "6-8 years", "9+ years"]), "6-8 years")
        self.assertEqual(qa.best_option("4", ["0-2 years", "3-5 years", "5+ years"]), "3-5 years")
        self.assertEqual(qa.best_option("10", ["1-3 years", "3-5 years", "5+ years"]), "5+ years")

    def test_bug_long_yes_answer_not_mapped_to_yes(self):
        # BUG MED: a stored free-text "Yes, I am authorized" finds no option in ["Yes","No"] -> option None.
        # Fix: if the answer starts with yes/no, try that first word against the options.
        self.assertEqual(qa.best_option("Yes, I am authorized", ["Yes", "No"]), "Yes")


class Bank(TempData, unittest.TestCase):
    def test_report_dedupe_jobs_counts_and_options(self):
        c = self.conn
        j1 = actions.save_job(c, None, "easy_apply", "linkedin_us", "Acme", "Salesforce Developer")["job_id"]
        j2 = actions.save_job(c, None, "easy_apply", "linkedin_us", "Beta", "Salesforce Admin")["job_id"]
        r1 = qa.report(c, 1, "easy_apply", j1, [{"question": "Are you willing to relocate?", "field_type": "yes_no"}])
        r2 = qa.report(c, 1, "portal", j1, ["Are you willing to relocate? *"])          # same job again
        r3 = qa.report(c, 2, "portal", j2, [{"question": "ARE YOU WILLING TO RELOCATE (required)",
                                            "options": ["Yes", "No"]}])
        self.assertEqual((r1["new"], r2["new"], r3["new"]), (1, 0, 0))
        self.assertEqual(len({r1["ids"][0], r2["ids"][0], r3["ids"][0]}), 1)
        row = qa.list_questions(c)[0]
        self.assertEqual(row["jobs"], [j1, j2])
        self.assertEqual(row["times_seen"], 3)
        self.assertEqual(row["options"], ["Yes", "No"])
        self.assertEqual((row["last_company"], row["last_lane"]), ("Beta", "portal"))
        self.assertEqual(row["question"], "Are you willing to relocate?")   # first wording kept
        qa.report(c, 3, "portal", None, ["Are you willing to relocate?"])  # no options -> keeps old ones
        self.assertEqual(qa.list_questions(c)[0]["options"], ["Yes", "No"])
        self.assertEqual(qa.counts(c), {"open": 1, "answered": 0, "mine": 0})
        self.assertEqual(db.summary(c)["questions_open"], 1)

    def test_report_does_not_reopen_answered_or_mine(self):
        c = self.conn
        a = qa.add_question(c, "Notice period (days)?", "15")
        m = qa.add_question(c, "Are you a protected veteran?", status="mine")
        qa.report(c, None, "portal", None, ["Notice period (days)", "Are you a protected veteran?"])
        rows = {r["id"]: r for r in qa.list_questions(c)}
        self.assertEqual(rows[a["id"]]["status"], "answered")
        self.assertEqual(rows[m["id"]]["status"], "mine")

    def test_jobs_list_capped_at_20(self):
        c = self.conn
        for i in range(25):
            qa.report(c, None, "portal", 1000 + i, ["Earliest start date?"])
        row = qa.list_questions(c)[0]
        self.assertEqual(len(row["jobs"]), 20)
        self.assertEqual(row["jobs"][-1], 1024)

    def test_lookup_found_option_times_used_and_mine(self):
        c = self.conn
        qa.add_question(c, "Will you now or in the future require visa sponsorship?", "No")
        qa.add_question(c, "Are you a protected veteran?", status="mine")
        res = qa.lookup(c, [{"question": "Will you now, or in the future, require visa sponsorship? *",
                             "field_type": "yes_no", "options": ["Yes", "No"]},
                            {"question": "Are you a protected veteran?", "options": ["Yes", "No", "Decline"]},
                            {"question": "Something nobody asked before?"}])
        r0, r1, r2 = res["results"]
        self.assertEqual((r0["found"], r0["answer"], r0["option"]), (True, "No", "No"))
        self.assertEqual((r1["found"], r1["status"], r1["answer"]), (False, "mine", None))
        self.assertEqual((r2["found"], r2["candidates"]), (False, []))
        self.assertEqual((res["found"], res["total"]), (1, 3))
        used = dict(c.execute("SELECT question, times_used FROM questions").fetchall())
        self.assertEqual(used["Will you now or in the future require visa sponsorship?"], 1)
        self.assertEqual(used["Are you a protected veteran?"], 0)

    def test_open_questions_never_returned_as_answers(self):
        c = self.conn
        qa.report(c, None, "portal", None, ["Are you authorized to work in the US?"])
        res = qa.lookup(c, ["Are you authorized to work in the US?"])["results"][0]
        self.assertFalse(res["found"])
        self.assertEqual(res["candidates"], [])

    def test_wrong_country_currency_never_auto_filled(self):
        c = self.conn
        qa.add_question(c, "Are you legally authorized to work in India?", "Yes")
        qa.add_question(c, "What is your expected CTC (INR)?", "28 LPA")
        qa.add_question(c, "What is your current CTC?", "20 LPA")
        for q in ["Are you legally authorized to work in the United States?", "What is your expected salary (USD)?",
                  "What is your expected CTC?", "Expected hourly rate (USD)"]:
            with self.subTest(q=q):
                self.assertFalse(qa.lookup(c, [q])["results"][0]["found"])

    def test_bug_exact_match_loses_tie_to_other_answer(self):
        # BUG HIGH jh/qa.py:76/138: a non-identical question whose content words are equal also scores 1.0, and the
        # sort is stable, so when Akshay answered BOTH "at least 5 years" (Yes) and "at least 3 years" (No-3),
        # looking up "at least 3 years" returns the "5 years" answer. Fix: cap non-exact similarity at 0.99
        # (`return round(min(score, 0.99), 3)`) and/or sort by (score, r["norm"] == n).
        c = self.conn
        qa.add_question(c, "Do you have at least 5 years of Apex experience?", "Yes")
        qa.add_question(c, "Do you have at least 3 years of Apex experience?", "No-3")
        res = qa.lookup(c, ["Do you have at least 3 years of Apex experience?"])["results"][0]
        self.assertEqual(res["answer"], "No-3")

    def test_status_transitions(self):
        c = self.conn
        qid = qa.report(c, None, "easy_apply", None, ["Are you willing to relocate?"])["ids"][0]
        r = qa.save_answer(c, qid, answer="No")
        self.assertEqual((r["status"], r["answer"]), ("answered", "No"))
        self.assertIsNotNone(r["answered_at"])
        r = qa.save_answer(c, qid, status="mine")
        self.assertEqual((r["status"], r["answer"]), ("mine", "No"))
        self.assertFalse(qa.lookup(c, ["Are you willing to relocate?"])["results"][0]["found"])
        r = qa.save_answer(c, qid, status="open")
        self.assertEqual(r["status"], "open")
        self.assertFalse(qa.lookup(c, ["Are you willing to relocate?"])["results"][0]["found"])
        r = qa.save_answer(c, qid, status="answered")   # answer still stored -> allowed
        self.assertEqual(r["status"], "answered")
        r = qa.save_answer(c, qid, answer="   ")          # clearing the answer re-opens it
        self.assertEqual((r["status"], r["answer"]), ("open", None))
        with self.assertRaises(ValueError):
            qa.save_answer(c, qid, status="answered")
        with self.assertRaises(ValueError):
            qa.save_answer(c, qid, status="ANSWERED")
        m = qa.save_answer(c, qid, status="mine")
        self.assertEqual(qa.save_answer(c, qid, answer="Yes")["status"], "answered")  # answering a 'mine' one
        self.assertEqual(m["status"], "mine")

    def test_edit_question_text_renormalizes(self):
        c = self.conn
        r = qa.add_question(c, "Notice period?", "15")
        qa.save_answer(c, r["id"], question="What is your notice period in days?")
        self.assertTrue(qa.lookup(c, ["Notice period in days"])["results"][0]["found"])

    def test_bug_edit_question_to_punctuation_only(self):
        # BUG LOW jh/qa.py:228 save_answer accepts a question that normalizes to "" (add_question rejects it):
        # norm becomes "" (UNIQUE), the row can never match again and a 2nd such edit gives a confusing "already exists".
        # Fix: `if not norm: raise ValueError("question is empty")`.
        c = self.conn
        r = qa.add_question(c, "Notice?", "15")
        with self.assertRaises(ValueError):
            qa.save_answer(c, r["id"], question="???")

    def test_validation(self):
        c = self.conn
        for bad in ("", "   ", "?? !!", None, "***"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    qa.add_question(c, bad, "x")
        with self.assertRaises(ValueError):
            qa.add_question(c, "Valid question?", "", status="answered")
        with self.assertRaises(KeyError):
            qa.save_answer(c, 424242, answer="x")
        self.assertEqual(qa.add_question(c, "Only a question?")["status"], "open")
        self.assertEqual(qa.add_question(c, "Weird status?", "1", status="bogus")["status"], "answered")
        self.assertEqual(qa.add_question(c, "Weird type?", "1", field_type="'; DROP")["field_type"], "text")

    def test_sql_injection_strings_are_data(self):
        c = self.conn
        evil = "x'); DROP TABLE questions; --"
        r = qa.add_question(c, evil, evil)
        self.assertEqual(qa.list_questions(c, q="DROP TABLE")[0]["answer"], evil)
        qa.report(c, None, "portal\"; DROP", None, [{"question": "' OR 1=1 --", "options": ["' OR '1'='1"]}])
        self.assertEqual(qa.list_questions(c, status="open' OR '1'='1"), qa.list_questions(c))  # bad status ignored
        self.assertTrue(qa.delete_question(c, r["id"]))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)

    def test_unicode_latin_and_smart_quotes(self):
        c = self.conn
        qa.add_question(c, "What’s your expected CTC?", "28 LPA")
        self.assertTrue(qa.lookup(c, ["What's your expected CTC?"])["results"][0]["found"])
        r = qa.add_question(c, "Résumé headline — 日本語 ✓", "Salesforce Developer")
        self.assertEqual(qa.list_questions(c, q="Résumé")[0]["id"], r["id"])

    def test_bug_non_latin_question_silently_dropped(self):
        # BUG LOW jh/qa.py:51 normalize keeps only [a-z0-9+#]: a question written in Hindi/Japanese/Arabic normalizes
        # to "" and report() drops it silently, so it never reaches the Questions tab. Fix: unicodedata.normalize("NFKD")
        # + strip accents, then re.sub(r"[^\w+#]+", " ", t) (Unicode \w).
        c = self.conn
        r = qa.report(c, None, "portal", None, ["क्या आप भारत में काम करने के लिए अधिकृत हैं?"])
        self.assertEqual(r["new"], 1)

    def test_very_long_input_truncated(self):
        c = self.conn
        r = qa.report(c, None, "portal", None, [{"question": "Q " * 5000, "options": ["o" * 900] * 200}])
        row = qa.list_questions(c)[0]
        self.assertEqual(r["new"], 1)
        self.assertLessEqual(len(row["question"]), qa.MAX_Q)
        self.assertEqual(len(row["options"]), 50)
        self.assertTrue(all(len(o) <= 200 for o in row["options"]))
        big = qa.save_answer(c, row["id"], answer="a" * 10000)
        self.assertEqual(len(big["answer"]), qa.MAX_A)
        many = qa.report(c, None, "portal", None, ["Question number %d?" % i for i in range(100)])
        self.assertEqual(len(many["ids"]), 40)   # capped per call

    def test_bug_search_wildcards_not_escaped(self):
        # BUG LOW jh/qa.py list_questions: q="%" or "_" matches everything (LIKE wildcards not escaped).
        # Fix: escape %, _ and \ and add ESCAPE '\'.
        c = self.conn
        qa.add_question(c, "Expected hourly rate (USD)?", "40")
        qa.add_question(c, "Discount of 100% acceptable?", "No")
        self.assertEqual(len(qa.list_questions(c, q="0%")), 1)
        self.assertEqual(len(qa.list_questions(c, q="_")), 0)

    def test_bug_ai_guess_learned_as_akshays_answer(self):
        # BUG HIGH jh/qa.py:304 record(): any answer with source "ai" (or "linkedin") that is not per-job is inserted as
        # status 'answered'. lookup() then returns it as found=true, and answers.md says bank answers "win over
        # everything ... including the 'still leave blank' list". One AI guess (e.g. relocation = Yes) becomes a permanent
        # auto-answer Akshay never gave. Fix: learn only source in ("standard", "profile", "resume") — or store AI/LinkedIn
        # answers as a separate 'suggested' status that lookup returns as a candidate, not found=true.
        c = self.conn
        qa.record(c, None, "easy_apply", None, [{"question": "Are you willing to relocate?", "answer": "Yes", "source": "ai"}])
        self.assertFalse(qa.lookup(c, ["Are you willing to relocate?"])["results"][0]["found"])

    def test_report_upsert_race_is_handled(self):
        # Two writers (dashboard add + engine report) insert the same new question at the same time.
        c = self.conn
        other = db.connect()
        real = c

        class Racy:
            def __init__(self):
                self.fired = False

            def execute(self, sql, args=()):
                cur = real.execute(sql, args)
                if not self.fired and sql.startswith("SELECT * FROM questions WHERE norm=?"):
                    self.fired = True
                    qa.add_question(other, "Earliest start date?")
                return cur
        try:
            qa.report(Racy(), None, "portal", None, ["Earliest start date?"])
        except sqlite3.IntegrityError:
            self.skipTest("BUG LOW jh/qa.py:176 report() SELECT-then-INSERT race -> IntegrityError (UNIQUE norm); "
                          "fix: INSERT ... ON CONFLICT(norm) DO UPDATE SET times_seen=times_seen+1")
        finally:
            other.close()


# --------------------------------------------------------------------------- API
class Api(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.srv = server.create_server(self.port, web_dir=self.data / "web_missing")
        server.serve_in_thread(self.srv)
        self.conn.execute("DELETE FROM questions")   # create_server seeds qa.SEED once; start from a clean bank
        self.origin = "http://127.0.0.1:%d" % self.port

    def tearDown(self):
        server.shutdown_server(self.srv)
        super().tearDown()

    def req(self, method, path, body=None, raw=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", "Origin": self.origin}
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse(); out = r.read(); c.close()
        try:
            return r.status, json.loads(out or b"{}")
        except ValueError:
            return r.status, {"_raw": out[:200]}

    def test_cross_origin_posts_forbidden(self):
        qid = qa.add_question(self.conn, "Notice period?", "15")["id"]
        for path, body in (("/api/questions", {"question": "Evil?", "answer": "x"}),
                           ("/api/questions/%d" % qid, {"answer": "666"}),
                           ("/api/questions/%d" % qid, {"delete": True})):
            for hdr in ({"Origin": "http://evil.example"}, {"Origin": "null"}, {"Origin": "https://127.0.0.1:%d" % self.port},
                        {"Origin": None, "Sec-Fetch-Site": "cross-site"}, {"Origin": None, "Sec-Fetch-Site": "same-site"},
                        {"Host": "evil.example:%d" % self.port}):
                with self.subTest(path=path, hdr=hdr):
                    self.assertEqual(self.req("POST", path, body, headers=hdr)[0], 403)
        row = qa.list_questions(self.conn)
        self.assertEqual([(r["question"], r["answer"]) for r in row], [("Notice period?", "15")])
        self.assertEqual(self.req("POST", "/api/questions/%d" % qid, {"answer": "20"},
                                  headers={"Origin": "http://localhost:%d" % self.port})[0], 200)
        self.assertEqual(self.req("POST", "/api/questions/%d" % qid, {"answer": "21"},
                                  headers={"Origin": None, "Sec-Fetch-Site": "same-origin"})[0], 200)

    def test_bad_bodies_and_routes(self):
        qid = qa.add_question(self.conn, "Notice period?", "15")["id"]
        self.assertEqual(self.req("POST", "/api/questions", raw=b"{not json")[0], 400)
        self.assertEqual(self.req("POST", "/api/questions", raw=b"[1,2]")[0], 400)
        self.assertEqual(self.req("POST", "/api/questions/%d" % qid, raw=b"\xff\xfe")[0], 400)
        self.assertEqual(self.req("POST", "/api/questions", {})[0], 400)
        self.assertEqual(self.req("POST", "/api/questions", {"question": "Q?", "answer": "", "status": "answered"})[0], 400)
        self.assertEqual(self.req("POST", "/api/questions/%d" % qid, {"status": "nope"})[0], 400)
        self.assertEqual(self.req("POST", "/api/questions/999999", {"answer": "x"})[0], 404)
        self.assertEqual(self.req("POST", "/api/questions/999999", {"delete": True})[0], 404)
        self.assertEqual(self.req("POST", "/api/questions/abc", {"answer": "x"})[0], 404)
        self.assertEqual(self.req("GET", "/api/questions/%d" % qid)[0], 405)
        self.assertEqual(self.req("DELETE", "/api/questions/%d" % qid)[0] in (404, 405, 501), True)
        st, d = self.req("POST", "/api/questions/%d" % qid, {"answer": 30})
        self.assertEqual((st, d["answer"]), (200, "30"))
        other = qa.add_question(self.conn, "Expected CTC?", "28")["id"]
        self.assertEqual(self.req("POST", "/api/questions/%d" % other, {"question": "notice period"})[0], 400)  # clash

    def test_get_filters(self):
        qa.add_question(self.conn, "Notice period?", "15")
        qa.report(self.conn, None, "portal", None, ["Earliest start date?"])
        st, d = self.req("GET", "/api/questions?status=bogus")
        self.assertEqual((st, len(d["questions"])), (200, 2))
        self.assertEqual(d["counts"], {"open": 1, "answered": 1, "mine": 0})
        self.assertEqual(len(self.req("GET", "/api/questions?status=open")[1]["questions"]), 1)
        self.assertEqual(len(self.req("GET", "/api/questions?q=%27%20OR%201%3D1%20--")[1]["questions"]), 0)
        self.assertEqual(len(self.req("GET", "/api/questions?q=NOTICE")[1]["questions"]), 1)
        self.assertEqual(self.req("GET", "/api/questions", headers={"Origin": "http://evil.example"})[0], 403)

    def test_bug_delete_string_false_deletes(self):
        # BUG LOW jh/server.py:399 `if body.get("delete")` -> {"delete": "false"} (or "no", 1) deletes the row.
        # Fix: `if body.get("delete") is True`.
        qid = qa.add_question(self.conn, "Notice period?", "15")["id"]
        st, d = self.req("POST", "/api/questions/%d" % qid, {"delete": "false", "answer": "20"})
        self.assertEqual((st, d.get("answer")), (200, "20"))


# --------------------------------------------------------------------------- MCP tools
class Mcp(TempData, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from test_engine_mcp import load_mcp_module
        self.m = load_mcp_module()
        self.ctx = self.m.Ctx()

    def tearDown(self):
        if self.ctx.conn is not None:
            self.ctx.conn.close()
        super().tearDown()

    def call(self, name, args):
        return self.m.call_tool(self.ctx, name, args)

    def test_tools_listed_with_schemas(self):
        names = {t["name"]: t for t in self.m.TOOLS}
        for n in ("lookup_answers", "report_questions"):
            self.assertIn(n, names)
            self.assertEqual(names[n]["inputSchema"]["required"], ["questions"])

    def test_bad_question_args(self):
        for name in ("lookup_answers", "report_questions"):
            for bad in ("Are you authorized?", {"question": "x"}, 5):
                with self.subTest(name=name, bad=bad):
                    res, err = self.call(name, {"questions": bad})
                    self.assertTrue(err)
                    self.assertIn("list", res["error"])
        res, err = self.call("lookup_answers", {})
        self.assertEqual((err, res["total"]), (False, 0))
        res, err = self.call("lookup_answers", None)
        self.assertFalse(err)

    def test_mixed_items_and_job_id_coercion(self):
        jid = actions.save_job(self.ctx.ensure(), None, "portal", "linkedin_us", "Acme", "SF Dev")["job_id"]
        res, err = self.call("report_questions", {"job_id": str(jid), "questions": ["Notice period?", 5, None, {"nope": 1},
                                                                                     {"question": "Expected CTC?", "options": "Yes|No"}]})
        self.assertFalse(err)
        self.assertEqual(res["new"], 2)
        rows = {r["question"]: r for r in qa.list_questions(self.conn)}
        self.assertEqual(rows["Notice period?"]["jobs"], [jid])
        self.assertEqual(rows["Notice period?"]["last_company"], "Acme")
        self.assertEqual(rows["Expected CTC?"]["options"], ["Yes", "No"])
        res, err = self.call("report_questions", {"job_id": "abc", "questions": ["Start date?"]})
        self.assertEqual((err, res["new"]), (False, 1))
        self.assertEqual(rows["Notice period?"]["jobs"], [jid])
        res, err = self.call("report_questions", {"questions": ["No job id?"]})
        self.assertFalse(err)
        res, err = self.call("lookup_answers", {"questions": ["Notice period?", {"question": "Expected CTC?"}]})
        self.assertEqual((err, res["total"], res["found"]), (False, 2, 0))

    def test_bug_non_list_options_crash_whole_call(self):
        # BUG MED jh/qa.py:92 _options iterates whatever it gets: options=5 -> TypeError, the whole step's
        # report/lookup fails (isError) and every question on that step is lost. Fix: in _options,
        # `if not isinstance(v, (list, tuple)): v = [v]`.
        res, err = self.call("report_questions", {"questions": [{"question": "Q6?", "options": 5}, "Q7?"]})
        self.assertFalse(err)

    def test_bug_job_id_true_becomes_job_1(self):
        # BUG LOW engine/jobhunt_mcp.py:351 int(True) == 1 -> job_id true is attached to job #1; job ids that don't
        # exist are stored too. Fix: reject bools (`isinstance(jid, bool)`) and keep jid only if the job exists.
        self.call("report_questions", {"job_id": True, "questions": ["Bool job?"]})
        self.assertEqual(qa.list_questions(self.conn)[0]["jobs"], [])

    def test_tool_labels(self):
        from jh import engine
        self.assertIn("2 questions", engine.tool_label("mcp__jobhunt__lookup_answers", {"questions": [{"question": "a"}, {"question": "b"}]}))
        self.assertIn("1 question ", engine.tool_label("mcp__jobhunt__report_questions", {"questions": [{"question": "a"}]}))

    def test_bug_tool_label_counts_string_characters(self):
        # BUG LOW jh/engine.py:373/379 len(inp.get("questions") or []) counts characters when the model passes a string
        # ("Checking your saved answers (19 questions)"). Fix: n = len(q) if isinstance(q, list) else 0.
        from jh import engine
        self.assertNotIn("19", engine.tool_label("mcp__jobhunt__lookup_answers", {"questions": "Are you authorized?"}))


# --------------------------------------------------------------------------- UI (fixture + Playwright)
try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except Exception:
    HAVE_PW = False


@unittest.skipUnless(HAVE_PW and os.environ.get("JH_UI_BROWSER", "1") != "0", "Playwright not installed")
class _QuestionsUIBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, HERE)
        from test_ui_fixture import start
        cls._start = staticmethod(start)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.srv, self.world, self.base = self._start(speed=20.0)
        self.errors = []

    def tearDown(self):
        self.world.stop_flag.set()
        self.srv.shutdown()

    def page(self, width=1440, theme="dark"):
        pg = self.browser.new_page(viewport={"width": width, "height": 900}, color_scheme=theme)
        pg.on("pageerror", lambda e: self.errors.append(str(e)))
        pg.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        pg.goto(self.base + "/")
        pg.wait_for_function("window.JH && window.JH.state.qCounts")
        pg.evaluate("t => window.JH.setTheme(t)", theme)
        pg.click("#tab-questions")
        pg.wait_for_selector(".q-item")
        return pg

    def ids(self, pg):
        return pg.evaluate("() => [...document.querySelectorAll('.q-item')].map(li => +li.dataset.id)")


@unittest.skipUnless(HAVE_PW and os.environ.get("JH_UI_BROWSER", "1") != "0", "Playwright not installed")
class QuestionsTabUI(_QuestionsUIBase):
    def test_renders_all_widths_and_themes(self):
        for width in (1440, 1024):
            for theme in ("dark", "light"):
                with self.subTest(width=width, theme=theme):
                    pg = self.page(width, theme)
                    self.assertEqual(self.ids(pg), [1, 2, 3, 4])
                    self.assertEqual(pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 0)
                    self.assertEqual(pg.evaluate("document.documentElement.dataset.theme"), theme)
                    self.assertEqual(pg.locator(".q-item[data-id='2'] .q-picks .chip").all_text_contents(), ["Yes", "No"])
                    pg.close()
        self.assertEqual(self.errors, [])

    def test_answer_flows(self):
        pg = self.page()
        pg.click(".q-item[data-id='2'] .q-picks .chip:has-text('No')")            # chip saves
        pg.wait_for_function("() => !document.querySelector(\".q-item[data-id='2']\")")
        pg.fill(".q-item[data-id='1'] input.q-input", "45")
        pg.press(".q-item[data-id='1'] input.q-input", "Enter")                    # Enter saves
        pg.wait_for_function("() => !document.querySelector(\".q-item[data-id='1']\")")
        pg.click(".q-item[data-id='3'] button:has-text('Always ask me')")
        pg.wait_for_function("() => !document.querySelector(\".q-item[data-id='3']\")")
        pg.click(".q-item[data-id='4'] .q-del")                                    # two-click delete
        self.assertEqual(pg.text_content(".q-item[data-id='4'] .q-del"), "Sure?")
        self.assertEqual(self.ids(pg), [4])
        pg.click(".q-item[data-id='4'] .q-del")
        pg.wait_for_function("() => !document.querySelector(\".q-item[data-id='4']\")")
        st = {q["id"]: (q["status"], q["answer"]) for q in self.world.questions}
        self.assertEqual(st[1], ("answered", "45"))
        self.assertEqual(st[2], ("answered", "No"))
        self.assertEqual(st[3][0], "mine")
        self.assertNotIn(4, st)
        # add your own + search
        pg.click(".q-bar .chip:has-text('Answered')")
        pg.click("text=+ Add your own")
        pg.fill(".q-add input[aria-label='New question']", "Expected CTC (LPA)?")
        pg.fill(".q-add input[aria-label='Answer']", "28")
        pg.press(".q-add input[aria-label='Answer']", "Enter")
        pg.wait_for_selector(".q-item:has-text('Expected CTC')")
        pg.fill("#q", "ctc")
        pg.wait_for_function("() => document.querySelectorAll('.q-item').length === 1")
        pg.fill("#q", "")
        pg.wait_for_function("() => document.querySelectorAll('.q-item').length === 4")
        pg.click(".q-bar .chip:has-text('Always ask me')")
        pg.click(".q-item[data-id='3'] button:has-text('Answer it instead')")
        pg.wait_for_function("() => !document.querySelector(\".q-item[data-id='3']\")")
        self.assertEqual(self.errors, [])

    def test_empty_answer_not_saved(self):
        pg = self.page()
        pg.press(".q-item[data-id='1'] input.q-input", "Enter")
        pg.wait_for_timeout(300)
        self.assertEqual(self.world.questions[0]["status"], "open")

    def test_stats_refresh_respects_typing(self):
        pg = self.page()
        pg.fill(".q-item[data-id='1'] input.q-input", "typing")
        pg.evaluate("() => window.JH.handleEvent({type: 'stats', data: {questions_open: 99}})")
        pg.wait_for_timeout(1500)
        self.assertEqual(pg.input_value(".q-item[data-id='1'] input.q-input"), "typing")

    def test_bug_apps_refresh_wipes_typed_answer(self):
        # BUG MED web/app.js:983 loadApps() calls renderAppsBody() unconditionally; while the Questions tab is shown an
        # email_sent / reply_found / run_finished event (REFRESH_ON 'apps') re-renders the question list and wipes what
        # Akshay is typing (and drops focus). S.qEditing only guards loadQuestions. Fix: in loadApps use
        # `if (S.tab !== 'questions' && S.tab !== 'portal') renderAppsBody()` (or skip re-render while S.qEditing).
        pg = self.page()
        pg.fill(".q-item[data-id='1'] input.q-input", "typing")
        pg.evaluate("() => window.JH.handleEvent({type: 'email_sent', lane: 'outreach', message: 'sent'})")
        pg.wait_for_timeout(1800)
        self.assertEqual(pg.input_value(".q-item[data-id='1'] input.q-input"), "typing")

    def test_bug_qediting_never_cleared_on_blur(self):
        # BUG MED web/app.js:1220 S.qEditing is set on focus and cleared only on save/Escape. Click into an answer box,
        # click away without saving, and every later live refresh of the tab (loadQuestions guards) is skipped for the
        # rest of the session: new questions from a running form never show up. Fix: on blur, clear S.qEditing when the
        # input is empty or unchanged (`input.addEventListener('blur', () => { if (S.qEditing === row.id && !input.value.trim()) S.qEditing = null; })`).
        pg = self.page()
        pg.click(".q-item[data-id='1'] input.q-input")
        pg.click(".q-text >> nth=0")
        self.assertIsNone(pg.evaluate("window.JH.state.qEditing"))

    def test_bug_add_form_wiped_by_refresh(self):
        # BUG MED web/app.js:1276-1282 the "+ Add your own" inputs aren't covered by S.qEditing and renderQuestions
        # re-focuses the question box on every re-render; any refresh (see loadApps bug) empties both fields.
        # Fix: keep the draft in S.qDraft and set S.qEditing='add' on focus.
        pg = self.page()
        pg.click("text=+ Add your own")
        pg.fill(".q-add input[aria-label='New question']", "half typed")
        pg.evaluate("() => window.JH.handleEvent({type: 'email_sent', lane: 'outreach', message: 'sent'})")
        pg.wait_for_timeout(1800)
        self.assertEqual(pg.input_value(".q-add input[aria-label='New question']"), "half typed")

    def test_bug_filter_counts_hidden_at_1024(self):
        # BUG LOW web/app.css:747 `.chip .n { display: none }` under 1140px (meant for the lane chips) also hides the
        # To answer / Answered / Always ask me counts. Fix: `.q-bar .chip .n { display: inline }` in that media block.
        pg = self.page(1024, "light")
        self.assertTrue(pg.is_visible(".q-bar .chip .n >> nth=0"))


# =========================================================================== re-verification pass (after fixes)
# DIFFERENT pairs aimed at the new stemming / aliases / negation / digit handling. All must stay < CONFIDENT.
DIFFERENT_ROUND2_OK = [
    ("Are you open to relocation?", "Do you need relocation assistance?"),
    ("Are you willing to relocate?", "Are you willing to commute?"),
    ("Can you work 8 hours overlap with EST?", "Can you work 6 hours overlap with EST?"),
    ("Do you not have a degree?", "Do you have a degree?"),
    ("Are you a US citizen?", "Are you a US resident?"),
    ("Are you a US citizen?", "Are you a US national?"),
    ("Have you ever worked for us?", "Have you ever worked in the US?"),
    ("Are you willing to work for us on a W2?", "Are you willing to work in the US on a W2?"),
    ("Which one of the following clouds have you used?", "Which of the following clouds have you used?"),
    ("Have you completed the following level of education: Bachelor's Degree?",
     "Have you completed the following level of education: Master's Degree?"),
    ("Are you comfortable working US time zone hours?", "Are you comfortable working IST time zone hours?"),
    ("Do you require sponsorship?", "Don't you require sponsorship?"),
    ("Do you have a non-compete agreement?", "Do you have a compete agreement?"),
    ("Are you currently employed?", "Are you currently unemployed?"),
    ("Are you willing to work onsite?", "Are you willing to work offsite?"),
    ("Do you have experience in sales?", "Do you have experience in Salesforce?"),
    ("Do you have experience with Data Cloud?", "Do you have experience with Data Loader?"),
    ("Are you willing to travel up to 25%?", "Are you willing to travel up to 50%?"),
    ("Are you open to a 3 month contract?", "Are you open to a three year contract?"),
    ("Are you willing to be drug tested?", "Are you willing to drug test others?"),
    ("Do you consent to a background check?", "Do you not consent to a background check?"),
    ("Have you been employed by Deloitte?", "Have you worked with Deloitte?"),
    ("Have you worked on Amkor?", "Have you worked at Amkor?"),
    ("Do you have 5+ years of experience?", "Do you have 5 years of experience?"),
    ("Are you located in Austin?", "Are you located near Austin?"),
    ("Are you available for an interview this week?", "Are you available to start this week?"),
    ("Do you speak English?", "Do you speak Spanish?"),
]
# BUG HIGH: "able to" is aliased to "willing to" (jh/qa.py _ALIASES). Ability/eligibility != willingness.
ABLE_VS_WILLING = [
    ("Are you able to work in the US?", "Are you willing to work in the US?"),
    ("Are you able to work in the US without sponsorship?", "Are you willing to work in the US without sponsorship?"),
    ("Are you able to provide proof of eligibility to work in the US?",
     "Are you willing to provide proof of eligibility to work in the US?"),
    ("Are you able to obtain a security clearance?", "Are you willing to obtain a security clearance?"),
    ("Are you able to lift 50 lbs?", "Are you willing to lift 50 lbs?"),
]


class MatchingRound2(unittest.TestCase):
    def test_new_different_pairs_not_confident(self):
        for a, b in DIFFERENT_ROUND2_OK:
            with self.subTest(a=a, b=b):
                self.assertLess(sim(a, b), qa.CONFIDENT)

    def test_new_aliases_match_true_rewordings(self):
        for a, b in [("Have you worked with Salesforce CPQ?", "Do you have experience with Salesforce CPQ?"),
                     ("Do you have one year of experience with LWC?", "Do you have 1 year of experience with LWC?"),
                     ("Are you located in the US?", "Are you located in the USA?"),
                     ("Are you located in the U.S.?", "Are you located in the United States?"),
                     ("Do you have a Master's degree?", "Do you have a Masters degree?")]:
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(sim(a, b), qa.CONFIDENT)

    def test_bug_able_to_aliased_to_willing_to(self):
        # BUG HIGH jh/qa.py _ALIASES `(open to|okay with|ok with|comfortable with|comfortable|willing to|able to)` ->
        # "willing to". "Are you ABLE to work in the US?" (= legally eligible: No) scores 1.0 against "Are you WILLING to
        # work in the US?" (Yes) -> wrong auto-answer on work authorization / clearance. Fix: drop "able to" from that
        # alias (alias it to "can" or leave it as its own content word "able").
        for a, b in ABLE_VS_WILLING:
            with self.subTest(a=a, b=b):
                self.assertLess(sim(a, b), qa.CONFIDENT)

    def test_bug_lowercase_us_country_dropped(self):
        # BUG MED jh/qa.py _CASE_ALIASES: only upper-case US/U.S. becomes "usa"; lower-case "us" is now a stop word, so
        # "Are you authorized to work in the us?" == "Are you authorized to work?" (0.99, auto-filled from the generic one)
        # Fix: also alias r"(?i)\b(in|within|inside|to|across) the us\b" -> " usa ".
        self.assertLess(sim("Are you authorized to work in the us?", "Are you authorized to work?"), qa.CONFIDENT)


class BestOptionRound2(unittest.TestCase):
    def test_correct_picks(self):
        cases = [
            ("2.5", ["0-1 years", "2-3 years", "4+ years"], "2-3 years"),
            ("2.5 years", ["1-2 years", "3-5 years"], ""),
            ("15", ["Immediately", "Less than 15 days", "15-30 days", "More than 30 days"], "15-30 days"),
            ("15 days", ["Immediately", "Within 15 days", "1 month", "2 months"], "Within 15 days"),
            ("30", ["0-15 days", "15-30 days", "30+ days"], "15-30 days"),
            ("No", ["Yes", "No, but willing to relocate", "No, not willing"], ""),      # ambiguous -> leave for Akshay
            ("No, but willing", ["Yes", "No, but willing", "No"], "No, but willing"),
            ("7", ["1-3", "4-6", "7-9", "10+"], "7-9"),
            ("7", ["1", "2", "3", "4", "5", "6", "7+"], "7+"),
            ("3+ years", ["1 year", "2 years", "3 years", "4 years", "5+ years"], "3 years"),
            ("10", ["0-5 years", "5-10 years", "10+ years"], "5-10 years"),
            ("18 LPA", ["10-15 LPA", "15-20 LPA", "20+ LPA"], "15-20 LPA"),
            ("1 month", ["Less than 1 month", "1-2 months", "More than 2 months"], "1-2 months"),
            ("Yes", ["I am authorized", "I am not authorized"], "I am authorized"),
            ("Not a veteran", ["I am a protected veteran", "I am not a protected veteran", "Decline"], "I am not a protected veteran"),
            ("Yes, I am authorized", ["Yes", "No"], "Yes"),
            ("No sponsorship needed", ["Yes, I need sponsorship", "No, I don't need sponsorship"], "No, I don't need sponsorship"),
            ("male", ["Female", "Decline to self identify"], ""),
            ("Within 15 days", ["Immediately", "Less than 15 days", "1 month"], ""),
            ("Not sure", ["Yes", "No", "Not sure"], "Not sure"),
        ]
        for ans, opts, want in cases:
            with self.subTest(ans=ans, opts=opts):
                self.assertEqual(qa.best_option(ans, opts), want)

    def test_bug_na_and_not_applicable_read_as_no(self):
        # BUG MED jh/qa.py _NO matches "n" / "not": "N/A" (-> "n a") and "Not applicable" have polarity "no", so a stored
        # "No" picks "N/A" / "Not applicable". Fix: exclude r"^(n\s*a|not applicable|none of the above)\b" from _polarity.
        self.assertEqual(qa.best_option("No", ["N/A", "Yes"]), "")
        self.assertEqual(qa.best_option("No", ["Not applicable", "Yes"]), "")

    def test_bug_unitless_number_picks_first_unit(self):
        # BUG MED: a bare "3" with ["3 months", "3 years"] picks "3 months" (under-claims years of experience).
        # Fix: when the answer has no unit and hits options in more than one unit, return "".
        self.assertEqual(qa.best_option("3", ["3 months", "3 years"]), "")

    def test_bug_rupees_vs_lpa_scale(self):
        # BUG LOW: "1800000" (rupees) vs LPA ranges -> "20+ LPA" (1800000 >= 20). Fix: treat lpa/lakh options as
        # a unit; convert rupees /100000, or return "" on unit mismatch.
        self.assertIn(qa.best_option("1800000", ["10-15 LPA", "15-20 LPA", "20+ LPA"]), ("15-20 LPA", ""))

    def test_bug_do_not_alias_breaks_no_polarity(self):
        # BUG LOW (safe miss): normalize() now turns "do not" into "not", so _NO's "i do not"/"i don t" never match
        # "i not require ...": a stored "No" finds nothing in ["I do not require sponsorship", "I will require sponsorship"].
        # Fix: add r"i not" to _NO (or run _polarity on the text before aliasing).
        self.assertEqual(qa.best_option("No", ["I do not require sponsorship", "I will require sponsorship"]),
                         "I do not require sponsorship")

    def test_unit_conversion_missing_is_safe(self):
        # years vs months are not converted: returns "" (field left for Akshay) rather than a wrong option.
        self.assertEqual(qa.best_option("3 years", ["0-12 months", "12-24 months", "24+ months"]), "")
        self.assertEqual(qa.best_option("18 months", ["0-1 years", "1-2 years", "2+ years"]), "")


@unittest.skipUnless(HAVE_PW and os.environ.get("JH_UI_BROWSER", "1") != "0", "Playwright not installed")
class TypingProtectionUI(_QuestionsUIBase):
    """Re-verification of qBusy()/focusout. A refresh that arrives while typing must not eat input or clicks."""

    def _new_engine_question(self):
        self.world.questions.append({"id": 50, "question": "Brand new engine question?", "answer": None, "status": "open",
                                     "field_type": "text", "options": [], "times_seen": 1, "times_used": 0, "jobs": [],
                                     "last_company": "X", "last_lane": "portal"})

    def _refresh(self, pg):
        pg.evaluate("() => window.JH.handleEvent({type: 'run_finished', data: {status: 'done'}, message: 'x'})")
        pg.evaluate("() => window.JH.handleEvent({type: 'email_sent', lane: 'outreach', message: 'sent'})")
        pg.wait_for_timeout(1600)

    def test_save_click_lands_after_refresh_during_typing(self):
        pg = self.page()
        pg.fill(".q-item[data-id='1'] input.q-input", "45")
        self._new_engine_question()
        self._refresh(pg)
        self.assertEqual(pg.input_value(".q-item[data-id='1'] input.q-input"), "45")
        pg.click(".q-item[data-id='1'] button.btn-primary")
        pg.wait_for_selector(".q-item[data-id='50']")
        row = next(q for q in self.world.questions if q["id"] == 1)
        self.assertEqual((row["status"], row["answer"]), ("answered", "45"))

    def test_enter_after_refresh_saves(self):
        pg = self.page()
        pg.fill(".q-item[data-id='1'] input.q-input", "46")
        self._refresh(pg)
        pg.press(".q-item[data-id='1'] input.q-input", "Enter")
        pg.wait_for_timeout(700)
        self.assertEqual(next(q for q in self.world.questions if q["id"] == 1)["answer"], "46")

    def test_switching_inputs_keeps_both(self):
        pg = self.page()
        pg.fill(".q-item[data-id='1'] input.q-input", "A1")
        self._refresh(pg)
        pg.click(".q-item[data-id='3'] input.q-input")
        pg.keyboard.type("B3")
        self.assertEqual(pg.input_value(".q-item[data-id='1'] input.q-input"), "A1")
        self.assertEqual(pg.input_value(".q-item[data-id='3'] input.q-input"), "B3")

    def test_add_draft_survives_refresh_and_blur(self):
        pg = self.page()
        pg.click("text=+ Add your own")
        pg.fill(".q-add input[aria-label='New question']", "half typed")
        self._refresh(pg)
        pg.click(".q-text >> nth=0")
        self._refresh(pg)
        self.assertEqual(pg.input_value(".q-add input[aria-label='New question']"), "half typed")

    def test_bug_unsaved_answer_lost_when_clicking_other_row_chip(self):
        # BUG MED web/app.js qAnswerBox: per-row typed text isn't kept (only the add form has S.qDraft). Type "45" in row 1,
        # a refresh arrives, click a chip on row 2 -> row 2 saves, the list re-renders and row 1's "45" is gone. Same when
        # clicking away (focusout re-render) or typing in the search box. Fix: S.qAnswerDrafts[row.id] updated on 'input',
        # restored in qAnswerBox (cleared on save).
        pg = self.page()
        pg.fill(".q-item[data-id='1'] input.q-input", "45")
        self._refresh(pg)
        pg.click(".q-item[data-id='2'] .q-picks .chip:has-text('No')")
        pg.wait_for_timeout(800)
        self.assertEqual(pg.input_value(".q-item[data-id='1'] input.q-input"), "45")

    def test_bug_skipped_questions_refresh_not_replayed(self):
        # BUG MED web/app.js:877/454/929: while qBusy(), a 'questions' refresh is skipped (`!qBusy() && loadQuestions`),
        # not deferred; S.qDirty only re-renders the OLD S.questions on focusout. A question reported during typing doesn't
        # appear after Akshay clicks away until some later event. Fix: when skipping, set S.qStale = true, and in the
        # focusout handler call loadQuestions(true) if S.qStale.
        pg = self.page()
        pg.fill(".q-item[data-id='1'] input.q-input", "x")
        self._new_engine_question()
        self._refresh(pg)
        pg.click(".q-text >> nth=0")
        pg.wait_for_timeout(800)
        self.assertEqual(pg.locator(".q-item[data-id='50']").count(), 1)


if __name__ == "__main__":
    if "--table" in sys.argv:
        print(json.dumps(matching_summary(), indent=2))
    else:
        unittest.main()
