"""Question bank: application-form questions Akshay answers once in the dashboard.

Engine flow (Easy Apply / portal lanes):
  lookup(conn, [{question, field_type?, options?}])  -> per question: confident answer or near candidates
  report(conn, run_id, lane, job_id, [...])           -> unknown questions land in the "Questions" tab
Dashboard: list_questions / save_answer / add_question / delete_question.

Matching is deliberately conservative: two questions match only if their normalized text is the
same, or they are very close AND share the same content words. "Years with CPQ?" never matches
"Years with Apex?" (different content word). Near misses come back as `candidates` so the AI can
decide whether one is really the same question.
"""
import csv
import io
import json
import re
from difflib import SequenceMatcher

from . import db

STATUSES = ("open", "answered", "mine")   # mine = always leave this one for Akshay
FIELD_TYPES = ("text", "number", "yes_no", "choice", "multi", "date", "textarea", "checkbox", "other")
MAX_Q = 600
MAX_A = 4000

_STOP = set("""a an the of in on at to for from by with about into over and or but if then than so as is are was were be
been being am do does did done have has had having you your yours we our us i me my it its this that these those
please kindly select choose enter provide indicate tell let describe briefly any some all each which what whats who
whom whose when where why how will would shall should can could may might must there here currently current
mandatory required optional field question answer below above following many much total overall
professional relevant hands work working setting environment role position job company organization""".split())
# words that change the meaning even though they're short or look like filler: always content words
_KEEP = {"usa", "uk", "eu", "visa", "h1b", "w2", "c2c", "1099", "ctc", "lpa", "usd", "inr", "eur", "gbp",
         "year", "month", "week", "day", "notice", "salary", "rate", "hourly", "annual", "expected", "current",
         "no", "not", "never", "without", "ii", "iii", "iv"}
# applied BEFORE lower-casing: the country "US"/"U.S." vs the pronoun "us"
_CASE_ALIASES = [(r"\bU\.\s?S\.\s?A\.?|\bU\.\s?S\.?(?=\W|$)|\bUSA\b|\bUS\b", " usa "),
                 (r"\bU\.\s?K\.?(?=\W|$)", " uk "), (r"\bE\.\s?U\.?(?=\W|$)", " eu "), (r"(?i)\bunited states( of america)?\b", " usa "),
                 (r"(?i)\b(in|within|to|across|from|outside|inside) the us\b", r"\1 the usa")]
_ALIASES = [
    (r"\(\s*(required|optional|mandatory)\s*\)|\*", " "), (r"\(\s*yes\s*/\s*no\s*\)", " "),
    (r"\(\s*e\.?\s*g\.?[^)]*\)", " "),
    (r"\byrs?\b", "years"), (r"\bexp\b", "experience"), (r"\bsfdc\b", "salesforce"), (r"\bsf\b", "salesforce"),
    (r"\blightning web components?\b", "lwc"), (r"\bcompensation\b", "salary"), (r"\bpay\b", "salary"),
    (r"\bwork authori[sz]ation\b", "authorized work"), (r"\bauthori[sz]ed\b", "authorized"),
    (r"\bsponsor(ship|ed|ing)?\b", "sponsorship"), (r"\bh-?1-?b\b", "h1b"), (r"\bremotely\b", "remote"),
    (r"\bexpectations?\b", "expected"), (r"\bdesired\b", "expected"), (r"\bexperienced\b", "experience"),
    (r"\bworked (with|on|in)\b", "experience with"), (r"\bhands-on\b", " "),
    (r"\bhave you completed the following level of education\b", "do you have"),
    (r"\btime ?zones? hours\b", "hours"),
    (r"\b(open to|okay with|ok with|comfortable with|comfortable|willing to)\b", "willing to"),
    (r"\bwhen can you (start|join)\b|\bearliest (possible )?(start|joining) date\b|\bavailable to (start|join)\b", "start date"),
    (r"\bdo not\b|\bdon t\b|\bdon't\b", "not"),
    (r"\be-?mail\b", "email"), (r"\bphone number\b", "phone"), (r"\bmobile( number)?\b", "phone"),
    (r"\bone\b", "1"), (r"\btwo\b", "2"), (r"\bthree\b", "3"), (r"\bfour\b", "4"), (r"\bfive\b", "5"),
    (r"\bsix\b", "6"), (r"\bseven\b", "7"), (r"\beight\b", "8"), (r"\bten\b", "10"),
]
_SUFFIXES = ("ations", "ation", "ating", "ated", "ate", "ings", "ing", "ed", "ly", "s")


def normalize(q: str) -> str:
    t = q or ""
    for pat, rep in _CASE_ALIASES:
        t = re.sub(pat, rep, t)
    t = t.lower()
    t = re.sub(r"(?<=\w)'(?=\w)", " ", t)        # don't -> don t
    for pat, rep in _ALIASES:
        t = re.sub(pat, rep, t)
    t = re.sub(r"[^\w+#]+", " ", t).replace("_", " ")   # keeps non-Latin letters (Hindi etc.)
    return re.sub(r"\s+", " ", t).strip()


def _stem(w: str) -> str:
    if w in _KEEP or w.isdigit() or not w.isascii():
        return w
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4 and not (suf == "s" and w.endswith("ss")):
            return w[: -len(suf)]
    return w


def content_words(norm: str) -> set:
    out = set()
    for w in norm.split():
        if w in _KEEP or w.isdigit() or (w not in _STOP and len(w) > 1) or (len(w) == 1 and not w.isascii()):
            out.add(_stem(w))
    return out


def similarity(a_norm: str, b_norm: str) -> float:
    """0..1. Identical normalized text -> 1.0. Otherwise blends word overlap and character similarity,
    capped at 0.99 (an identical question always wins a tie) and at 0.89 when either side has a content
    word the other lacks (CPQ vs Apex, 1 vs 2, no benefits vs benefits)."""
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    ca, cb = content_words(a_norm), content_words(b_norm)
    if not ca or not cb:
        return round(SequenceMatcher(None, a_norm, b_norm).ratio() * 0.8, 3)
    inter = len(ca & cb)
    jacc = inter / len(ca | cb)
    seq = SequenceMatcher(None, " ".join(sorted(ca)), " ".join(sorted(cb))).ratio()
    score = min(0.65 * jacc + 0.35 * seq, 0.99)
    if ca != cb:
        score = min(score, 0.89)   # a differing content word can never be a confident match
    return round(score, 3)


CONFIDENT = 0.9    # auto-use the stored answer
CANDIDATE = 0.55   # show to the AI as "maybe the same question"


def _options(v):
    if v is None or v == "" or v == []:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            v = [x.strip() for x in v.split("|")]
    if not isinstance(v, (list, tuple)):
        v = [v]
    return [str(x).strip()[:200] for x in v if x is not None and not isinstance(x, (dict, list)) and str(x).strip()][:50]


_YES = re.compile(r"^\s*(yes|y|true|i am|i do|i have|i can|i will|sure|absolutely|definitely)\b", re.I)
_NO = re.compile(r"^\s*(no|n|false|i am not|i do not|i not|i don t|i have not|i can not|i cannot|i will not|not)\b", re.I)
_NEUTRAL = re.compile(r"^\s*(n a|na|not applicable|none of the above|none|not sure|prefer not|decline)\b", re.I)


def _polarity(text: str):
    t = normalize(text)
    if _NEUTRAL.match(t):
        return None
    if _NO.match(t):
        return "no"
    if _YES.match(t):
        return "yes"
    return None


def _num_range(text: str):
    """'3-5 years' -> (3,5); '5+ years'/'more than 5' -> (5, inf); 'less than 1 month'/'under 2' -> (-inf, 1 excl)."""
    t = (text or "").lower().replace("–", "-").replace("—", "-")
    t = re.sub(r"(?<=\d),(?=\d)", "", t)                              # 1,00,000 / 50,000
    t = re.sub(r"[$€£₹]\s*(?=\d)", "", t)                              # "$50,000 - $100,000"
    t = re.sub(r"(\d+(?:\.\d+)?)\s*k\b", lambda m: str(float(m.group(1)) * 1000), t)   # $50k
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), float(m.group(2)), True
    m = re.search(r"(\d+(?:\.\d+)?)\s*\+|(?:more than|over|above|at least|minimum of|min\.?)\s*(\d+(?:\.\d+)?)", t)
    if m:
        v = float(m.group(1) or m.group(2))
        lo = v + (1e-9 if re.search(r"more than|over|above", t) and not m.group(1) else 0)
        return lo, float("inf"), True
    m = re.search(r"(?:less than|under|below|fewer than)\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float("-inf"), float(m.group(1)) - 1e-9, True
    m = re.search(r"(?:up to|max(?:imum)?(?: of)?)\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float("-inf"), float(m.group(1)), True
    m = re.fullmatch(r"\D*?(\d+(?:\.\d+)?)\D*", t)
    if m:
        v = float(m.group(1))
        return v, v, False
    return None


def _unit(text: str):
    t = (text or "").lower()
    for u in ("year", "month", "week", "day", "hour"):
        if re.search(r"\b%ss?\b" % u, t):
            return u
    return None


_CUR = (("inr", r"₹|\binr\b|\brs\.?|\blpa\b|\blakhs?\b|\blacs?\b|\brupees?\b"), ("usd", r"\$|\busd\b|\bdollars?\b"),
        ("eur", r"€|\beur\b|\beuros?\b"), ("gbp", r"£|\bgbp\b|\bpounds?\b"))


def _currency(text: str):
    t = (text or "").lower()
    return next((c for c, rx in _CUR if re.search(rx, t)), None)


def best_option(answer: str, options, question: str = "") -> str:
    """The option that best fits a stored answer ('' if none fits — then the field is treated as unknown).
    `question` (the form's label) supplies the unit/currency when the stored answer is a bare number."""
    opts = _options(options)
    if not opts or answer is None or not str(answer).strip():
        return ""
    answer = str(answer)
    a = normalize(answer)
    for o in opts:
        if normalize(o) == a:
            return o
    # yes / no questions: only ever pick an option with the same polarity
    pa = _polarity(answer)
    if pa:
        same = [o for o in opts if _polarity(o) == pa]
        if len(same) == 1:
            return same[0]
        if same:
            opts = same
        elif any(_polarity(o) for o in opts):
            return ""
    # numbers: the option whose range contains the answer (same unit if both say one)
    ra = _num_range(answer)
    if ra and ra[0] != ra[1] and ra[1] == float("inf") and ra[0] != float("-inf"):
        ra = (ra[0], ra[0], ra[2])          # "3+ years" -> place it by its lower bound (never over-claim)
    if ra and ra[0] == ra[1]:
        # unit: the answer's, else the form label's / matched bank question's; a bare notice period is in days
        v, ua = ra[0], _unit(answer) or _unit(question) or ("day" if re.search(r"\bnotice\b", question or "", re.I) else None)
        ca = _currency(answer) or _currency(question)
        if any(_currency(o) and ca and _currency(o) != ca for o in opts):
            return ""                       # 28 LPA vs "$100,000+": different currency, leave for Akshay
        if v >= 100000 and any(re.search(r"\blpa\b|\blakhs?\b|\blacs?\b", o, re.I) for o in opts):
            v = v / 100000.0               # rupees per year vs "15-20 LPA" options
        hits = []
        for o in opts:
            ro = _num_range(o)
            if not ro:
                continue
            uo = _unit(o)
            if ua and uo and ua != uo:
                continue
            if ro[0] <= v <= ro[1]:
                hits.append((ro[1] - ro[0], 0 if ro[0] == v else 1, o))   # tie: the range that starts at v
        if hits:
            if not ua and len({_unit(h[2]) for h in hits if _unit(h[2])}) > 1:
                return ""                   # "3" vs ["3 months", "3 years"]: unit unknown -> leave for Akshay
            return sorted(hits)[0][2]       # the tightest range containing the value
        if any(_num_range(o) for o in opts):
            return ""
    # whole-word containment ("15 days" in "Within 15 days"), never substrings ("male" in "female")
    if a:
        cands = [o for o in opts if re.search(r"(?<!\w)%s(?!\w)" % re.escape(a), normalize(o))]
        if len(cands) == 1:
            return cands[0]
        # word-prefix match for longer words ("Immediate joiner" -> "Immediately"); short words never
        heads = [w[:6] for w in a.split() if len(w) >= 6]
        if heads:
            cands = [o for o in opts if any(ow.startswith(hd) for hd in heads for ow in normalize(o).split())]
            if len(cands) == 1:
                return cands[0]
    scored = sorted(((SequenceMatcher(None, a, normalize(o)).ratio(), o) for o in opts), reverse=True)
    return scored[0][1] if scored[0][0] > 0.85 else ""


def _flag(v, default=True) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() not in ("false", "0", "no", "n", "")


def _clean_items(questions):
    out = []
    for item in questions or []:
        if isinstance(item, str):
            item = {"question": item}
        if not isinstance(item, dict):
            continue
        raw = item.get("question")
        if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
            continue
        q = str(raw).strip()[:MAX_Q]
        if not q or not normalize(q):
            continue
        ft = str(item.get("field_type") or "text").strip().lower()
        out.append({"question": q, "field_type": ft if ft in FIELD_TYPES else "other",
                    "options": _options(item.get("options")), "required": _flag(item.get("required"), True)})
    return out[:40]


def lookup(conn, questions) -> dict:
    """For each question: {question, found, answer, option, score, matched_question, status, candidates}.
    found=True only for a confident match to an ANSWERED entry. status 'mine' = leave blank for Akshay."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, question, norm, answer, status FROM questions WHERE status IN ('answered','mine')")]
    results, used = [], []
    for item in _clean_items(questions):
        n = normalize(item["question"])
        scored = sorted(((similarity(n, r["norm"]), r) for r in rows), key=lambda x: -x[0])
        res = {"question": item["question"], "found": False, "answer": None, "option": None, "score": 0.0,
               "matched_question": None, "status": None, "candidates": []}
        if scored and scored[0][0] >= CONFIDENT:
            sc, r = scored[0]
            res.update(score=sc, matched_question=r["question"], status=r["status"])
            if r["status"] == "answered":
                res.update(found=True, answer=r["answer"])
                if item["options"]:
                    res["option"] = best_option(r["answer"], item["options"], item["question"] + " | " + r["question"]) or None
                used.append(r["id"])
        res["candidates"] = [{"question": r["question"], "answer": r["answer"] if r["status"] == "answered" else None,
                              "status": r["status"], "score": sc}
                             for sc, r in scored[:3] if CANDIDATE <= sc < CONFIDENT]
        results.append(res)
    if used:
        conn.execute("UPDATE questions SET times_used=times_used+1 WHERE id IN (%s)" % ",".join("?" * len(used)), used)
    return {"results": results, "found": sum(1 for r in results if r["found"]), "total": len(results)}


def report(conn, run_id, lane, job_id, questions) -> dict:
    """Record questions the engine could not answer (upsert by normalized text). Returns their ids."""
    company = None
    if job_id:
        row = conn.execute("SELECT company FROM jobs WHERE id=?", (job_id,)).fetchone()
        company = row["company"] if row else None
    now = db.now_iso()
    ids, new = [], 0
    for item in _clean_items(questions):
        n = normalize(item["question"])
        row = conn.execute("SELECT * FROM questions WHERE norm=?", (n,)).fetchone()
        if row:
            jobs = json.loads(row["jobs_json"] or "[]")
            if job_id and job_id not in jobs:
                jobs = (jobs + [job_id])[-20:]
            opts = item["options"] or json.loads(row["options_json"] or "[]")
            conn.execute("UPDATE questions SET times_seen=times_seen+1, jobs_json=?, last_company=COALESCE(?, last_company),"
                         " last_lane=?, options_json=?, updated_at=? WHERE id=?",
                         (json.dumps(jobs), company, lane, json.dumps(opts), now, row["id"]))
            ids.append(row["id"])
        else:
            cur = conn.execute(
                "INSERT OR IGNORE INTO questions (question, norm, field_type, options_json, answer, status, times_seen,"
                " times_used, jobs_json, last_company, last_lane, source, created_at, updated_at)"
                " VALUES (?,?,?,?,NULL,'open',1,0,?,?,?,'engine',?,?)",
                (item["question"], n, item["field_type"], json.dumps(item["options"]),
                 json.dumps([job_id] if job_id else []), company, lane, now, now))
            if cur.rowcount:
                ids.append(cur.lastrowid)
                new += 1
            else:   # another writer inserted it a moment ago
                r2 = conn.execute("SELECT id FROM questions WHERE norm=?", (n,)).fetchone()
                conn.execute("UPDATE questions SET times_seen=times_seen+1, updated_at=? WHERE id=?", (now, r2["id"]))
                ids.append(r2["id"])
    write_exports(conn)
    return {"ok": True, "ids": ids, "new": new}


def _row(r) -> dict:
    d = dict(r)
    d["options"] = json.loads(d.pop("options_json") or "[]")
    d["jobs"] = json.loads(d.pop("jobs_json") or "[]")
    d.pop("norm", None)
    return d


def list_questions(conn, status=None, q=None) -> list:
    sql, args = "SELECT * FROM questions", []
    where = []
    if status in STATUSES:
        where.append("status=?"); args.append(status)
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(question LIKE ? ESCAPE '\\' OR answer LIKE ? ESCAPE '\\')"); args += ["%" + esc + "%"] * 2
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, times_seen DESC, id DESC LIMIT 1000"
    return [_row(r) for r in conn.execute(sql, args)]


def counts(conn) -> dict:
    c = {s: 0 for s in STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) n FROM questions GROUP BY status"):
        if r["status"] in c:
            c[r["status"]] = r["n"]
    return c


def save_answer(conn, qid, answer=None, status=None, question=None) -> dict:
    row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        raise KeyError(qid)
    if status is not None and status not in STATUSES:
        raise ValueError("status must be one of %s" % (STATUSES,))
    ans = row["answer"] if answer is None else (str(answer).strip()[:MAX_A] or None)
    if status is None:
        status = "answered" if ans else ("mine" if row["status"] == "mine" else "open")
    if status == "answered" and not ans:
        raise ValueError("answer is empty")
    qtext, norm = row["question"], row["norm"]
    if question is not None and str(question).strip():
        qtext = str(question).strip()[:MAX_Q]
        norm = normalize(qtext)
        if not norm:
            raise ValueError("question is empty")
        clash = conn.execute("SELECT id FROM questions WHERE norm=? AND id<>?", (norm, qid)).fetchone()
        if clash:
            raise ValueError("the same question already exists (#%s)" % clash["id"])
    now = db.now_iso()
    conn.execute("UPDATE questions SET question=?, norm=?, answer=?, status=?, updated_at=?,"
                 " answered_at=CASE WHEN ?='answered' THEN ? ELSE answered_at END WHERE id=?",
                 (qtext, norm, ans, status, now, status, now, qid))
    write_exports(conn)
    return _row(conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone())


def add_question(conn, question, answer=None, status=None, field_type="text") -> dict:
    q = str(question or "").strip()[:MAX_Q]
    n = normalize(q)
    if not n:
        raise ValueError("question is empty")
    row = conn.execute("SELECT id FROM questions WHERE norm=?", (n,)).fetchone()
    if row:
        return save_answer(conn, row["id"], answer=answer, status=status)
    ans = (str(answer).strip()[:MAX_A] or None) if answer is not None else None
    st = status if status in STATUSES else ("answered" if ans else "open")
    if st == "answered" and not ans:
        raise ValueError("answer is empty")
    now = db.now_iso()
    cur = conn.execute(
        "INSERT INTO questions (question, norm, field_type, options_json, answer, status, times_seen, times_used,"
        " jobs_json, source, created_at, updated_at, answered_at) VALUES (?,?,?,'[]',?,?,0,0,'[]','user',?,?,?)",
        (q, n, field_type if field_type in FIELD_TYPES else "text", ans, st, now, now, now if st == "answered" else None))
    write_exports(conn)
    return _row(conn.execute("SELECT * FROM questions WHERE id=?", (cur.lastrowid,)).fetchone())


def delete_question(conn, qid) -> bool:
    ok = conn.execute("DELETE FROM questions WHERE id=?", (qid,)).rowcount > 0
    if ok:
        write_exports(conn)
    return ok


# --------------------------------------------------------------------------- filled-answer log + learning

ANSWER_SOURCES = ("bank", "standard", "profile", "resume", "linkedin", "ai")
LEARN_SOURCES = ("standard", "profile", "resume")
# answers that change from job to job: logged, never learned into the bank
_PER_JOB = re.compile(r"salary|ctc|compensation|\brate\b|hourly|stipend|\bpay\b|start date|joining date|date of joining|"
                      r"available to start|availability date|why |cover letter|describe|tell us|explain|anything else|"
                      r"additional information|summary|message to|note to|about yourself|this role|this company|our company", re.I)
_NO_LEARN_TYPES = ("textarea", "date", "checkbox")
# legal / eligibility / EEO answers depend on the job's country and are Akshay's call: never learned
_SENSITIVE = re.compile(r"authori[sz]|sponsor|visa\b|h-?1-?b|citizen|green card|right to work|eligib|legally|clearance|"
                        r"relocat|background check|drug|criminal|convict|gender|race\b|ethnic|veteran|disabilit", re.I)


def per_job(question, field_type="text", flag=False) -> bool:
    return (bool(flag) or (field_type in _NO_LEARN_TYPES) or bool(_PER_JOB.search(question or ""))
            or bool(_SENSITIVE.search(question or "")))


def record(conn, run_id, lane, job_id, answers) -> dict:
    """Log every answer the engine filled on a form; learn the stable ones into the bank
    (status answered, source 'agent') when the bank has no answer for that question yet."""
    now = db.now_iso()
    logged = learned = 0
    for item in answers or []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()[:MAX_Q]
        a = str(item.get("answer") if item.get("answer") is not None else "").strip()[:MAX_A]
        n = normalize(q)
        if not n or not a:
            continue
        ft = str(item.get("field_type") or "text").strip().lower()
        ft = ft if ft in FIELD_TYPES else "other"
        src = str(item.get("source") or "ai").strip().lower()
        src = src if src in ANSWER_SOURCES else "ai"
        # only rule/fact-based answers are learned; AI judgement and LinkedIn prefills are logged, never learned
        learn = src in LEARN_SOURCES and not per_job(q, ft, _flag(item.get("per_job"), False))
        did = 0
        if learn:
            row = conn.execute("SELECT id, status, answer FROM questions WHERE norm=?", (n,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO questions (question, norm, field_type, options_json, answer, status, times_seen, times_used,"
                    " jobs_json, last_company, last_lane, source, created_at, updated_at, answered_at)"
                    " VALUES (?,?,?,?,?,'answered',1,1,?,(SELECT company FROM jobs WHERE id=?),?,'agent',?,?,?)",
                    (q, n, ft, json.dumps(_options(item.get("options"))), a, json.dumps([job_id] if job_id else []),
                     job_id, lane, now, now, now))
                did = 1
            elif row["status"] == "open":     # the engine found the answer after all (e.g. from his resume)
                conn.execute("UPDATE questions SET answer=?, status='answered', source='agent', updated_at=?, answered_at=?"
                             " WHERE id=?", (a, now, now, row["id"]))
                did = 1
        conn.execute("INSERT INTO form_answers (job_id, run_id, lane, question, norm, answer, field_type, source, learned,"
                     " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (job_id, run_id, lane, q, n, a, ft, src, did, now))
        logged += 1
        learned += did
    write_exports(conn)
    return {"ok": True, "logged": logged, "learned": learned}


def form_answers_for_job(conn, job_id) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT question, answer, source, field_type, created_at FROM form_answers WHERE id IN"
        " (SELECT MAX(id) FROM form_answers WHERE job_id=? GROUP BY norm) ORDER BY id", (job_id,))]


def _csv(rows, header) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        # formula guard; v[:1] must be non-empty ("" in "=+-@" is True and would turn empty cells into "'")
        w.writerow(["" if v is None else ("'" + v if isinstance(v, str) and v[:1] and v[:1] in "=+-@" else v) for v in r])
    return "\ufeff" + buf.getvalue()   # BOM so Excel opens it as UTF-8


def export_bank_csv(conn) -> str:
    rows = conn.execute("SELECT question, answer, status, field_type, source, times_seen, times_used, last_company,"
                        " updated_at FROM questions ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id")
    st = {"open": "waiting for you", "answered": "answered", "mine": "always ask me"}
    return _csv(([r["question"], r["answer"], st.get(r["status"], r["status"]), r["field_type"],
                  {"agent": "learned from a form", "engine": "asked by a form", "user": "added by you"}.get(r["source"], r["source"]),
                  r["times_seen"], r["times_used"], r["last_company"], (r["updated_at"] or "")[:16].replace("T", " ")]
                 for r in rows),
                ["Question", "Answer", "Status", "Type", "Origin", "Seen in forms", "Auto-filled", "Last company", "Updated"])


def export_log_csv(conn) -> str:
    rows = conn.execute("SELECT f.created_at, j.company, j.role, f.lane, f.question, f.answer, f.source, f.learned"
                        " FROM form_answers f LEFT JOIN jobs j ON j.id=f.job_id ORDER BY f.id DESC")
    return _csv(([(r["created_at"] or "")[:16].replace("T", " "), r["company"], r["role"],
                  {"easy_apply": "Easy Apply", "portal": "Portal form"}.get(r["lane"], r["lane"]), r["question"], r["answer"],
                  r["source"], "yes" if r["learned"] else ""] for r in rows),
                ["When", "Company", "Role", "Where", "Question", "Answer filled", "Answer from", "Saved to bank"])


def write_exports(conn):
    """Keep data/answers_bank.csv and data/form_answers_log.csv in step with the DB (best effort)."""
    try:
        from . import config
        d = config.data_dir()
        for name, text in (("answers_bank.csv", export_bank_csv(conn)), ("form_answers_log.csv", export_log_csv(conn))):
            tmp = d / (name + ".tmp")
            tmp.write_text(text, encoding="utf-8", newline="")
            try:
                tmp.replace(d / name)
            except OSError:      # file open in Excel on Windows: keep the old one, try next time
                tmp.unlink(missing_ok=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- one-time seed
# Questions seen in his Easy Apply runs on 2026-10-19 (from the run logs). Answered ones are the values the
# agent filled from his standard answers; open ones are what the forms asked him. Applied once per database.
SEED_VERSION = "2026-10-19"
SEED = [
    ("Will you now or in the future require sponsorship for employment?", None, "open", "yes_no"),
    ("What is your annual salary expectation (USD)?", None, "open", "number"),
    ("Are you willing to undergo a background check and drug screening?", None, "open", "yes_no"),
    ("Are you willing to take a drug test, per local law?", None, "open", "yes_no"),
    ("How many years of total work experience do you have?", "7", "answered", "number"),
    ("Notice period (in days)", "15", "answered", "number"),
    ("Are you an immediate joiner?", "Yes", "answered", "yes_no"),
    ("Are you comfortable working remotely?", "Yes", "answered", "yes_no"),
    ("Are you comfortable with a contractual role, 8 hours per day, remotely?", "Yes", "answered", "yes_no"),
    ("How many years of experience do you have with Apex, LWC, SOQL and Flows?", "7", "answered", "number"),
    ("How many years of experience do you have with Experience Cloud?", "3", "answered", "number"),
    ("How many years of experience do you have with REST/SOAP APIs and asynchronous Apex?", "3", "answered", "number"),
    ("How many years of experience do you have with Field Service Lightning (FSL)?", "3", "answered", "number"),
]


def seed_once(conn) -> int:
    if conn.execute("SELECT 1 FROM app_meta WHERE k='qa_seed' AND v=?", (SEED_VERSION,)).fetchone():
        return 0
    now = db.now_iso()
    n = 0
    for q, a, st, ft in SEED:
        norm = normalize(q)
        if conn.execute("SELECT 1 FROM questions WHERE norm=?", (norm,)).fetchone():
            continue
        conn.execute("INSERT INTO questions (question, norm, field_type, options_json, answer, status, times_seen, times_used,"
                     " jobs_json, source, created_at, updated_at, answered_at) VALUES (?,?,?,?,?,?,1,0,'[]',?,?,?,?)",
                     (q, norm, ft, json.dumps(["Yes", "No"] if ft == "yes_no" else []), a, st,
                      "agent" if st == "answered" else "engine", now, now, now if st == "answered" else None))
        n += 1
    conn.execute("INSERT OR REPLACE INTO app_meta (k, v) VALUES ('qa_seed', ?)", (SEED_VERSION,))
    write_exports(conn)
    return n


QA_NORM_VERSION = "4"   # bump whenever normalize() changes


def renormalize(conn) -> int:
    """Recompute questions.norm after a normalize() change; merge rows that now collide
    (keeps the answered/'mine' row, adds up the counters). Once per version."""
    if conn.execute("SELECT 1 FROM app_meta WHERE k='qa_norm' AND v=?", (QA_NORM_VERSION,)).fetchone():
        return 0
    rows = [dict(r) for r in conn.execute("SELECT * FROM questions ORDER BY id")]
    groups = {}
    for r in rows:
        groups.setdefault(normalize(r["question"]) or r["norm"], []).append(r)
    changed = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE questions SET norm='~tmp~' || id")
        for n, rs in groups.items():
            rank = {"answered": 0, "mine": 1, "open": 2}
            rs.sort(key=lambda r: (rank.get(r["status"], 3), -(1 if r["source"] == "user" else 0), r["id"]))
            keep, drop = rs[0], rs[1:]
            seen = sum(r["times_seen"] or 0 for r in rs)
            used = sum(r["times_used"] or 0 for r in rs)
            jobs = []
            for r in rs:
                for j in json.loads(r["jobs_json"] or "[]"):
                    if j not in jobs:
                        jobs.append(j)
            conn.execute("UPDATE questions SET norm=?, times_seen=?, times_used=?, jobs_json=? WHERE id=?",
                         (n, seen, used, json.dumps(jobs[-20:]), keep["id"]))
            for r in drop:
                conn.execute("DELETE FROM questions WHERE id=?", (r["id"],))
            changed += len(drop) + (1 if keep["norm"] != n else 0)
        conn.execute("INSERT OR REPLACE INTO app_meta (k, v) VALUES ('qa_norm', ?)", (QA_NORM_VERSION,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return changed
