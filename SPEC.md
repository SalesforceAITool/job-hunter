# Job Hunter — local desktop app (SPEC v1, 2026-10-18)

Owner/user: Akshay Kumar Verma (Salesforce developer, 7+ yrs, India, no US work authorization, works US hours remotely as a contractor). Personal tool, single user, runs only on his Windows 11 laptop, only when he opens it (no scheduler).

## 1. What it does
Double-click **"Job Hunter"** on the Desktop →
1. a local server starts in the background (or is reused if already running),
2. the browser opens `http://127.0.0.1:8765`,
3. a **run** starts automatically (setting `auto_run_on_open`, default true) and the dashboard shows every step live.

A run = these stages, each shown as a live "lane" in the UI:
| Lane id | Name | Who does it | What |
|---|---|---|---|
| `followups` | Follow-ups & replies | app code (IMAP/SMTP), no AI | detect replies/bounces, send due nudges |
| `posts` | LinkedIn posts | AI engine (Claude Code + his Chrome) | post searches, expand "…more", emails in posts |
| `jobright` | JobRight | AI engine | US remote Salesforce jobs, JD check, Insider Connection → LinkedIn → SalesQL email |
| `linkedin_jobs` | LinkedIn Jobs (US + Worldwide) | AI engine | job lists, JD check, hiring team → SalesQL email |
| `outreach` | Outreach | AI writes text, app builds PDF + sends | tailored resume + humanized email per contact |
| `portal` | Portal prefill | AI engine | for qualified jobs whose apply link is a Greenhouse / Lever / Ashby page: fill the form + upload the tailored resume, STOP before submit, add to "Ready to submit" queue |

`followups` runs in a background thread at the same time as the AI lanes. AI lanes run **sequentially** by default (`parallel_sources: false`) because they share one Chrome; when true, the three source lanes run as three engine processes at once. Outreach happens inside each source lane as soon as a job qualifies (the engine calls `send_application`), so applications go out continuously instead of at the end.

Already-contacted jobs/emails are always skipped (dedupe by email, and by normalized company+role).

## 2. Stack (no build step, minimal installs)
- **Backend:** Python ≥3.9 **standard library only** (`http.server.ThreadingHTTPServer`, `sqlite3`, `smtplib`, `imaplib`, `email`, `subprocess`, `threading`, `json`). Only extra package: `reportlab` (resume PDF).
- **Frontend:** one page, vanilla HTML/CSS/JS, no CDN, no framework, served from `web/`. Charts = inline SVG drawn by our JS. Live updates via **Server-Sent Events**.
- **AI engine:** Claude Code CLI in headless mode (`claude -p ... --output-format stream-json --verbose`), with the Claude in Chrome integration, plus our own stdio **MCP server** (`engine/jobhunt_mcp.py`, stdlib JSON-RPC) that gives the AI tools to read/write the app DB. Exact CLI args live in `config.json` → `engine.claude_args` so they can be fixed on the laptop without code changes.
- **Email:** Gmail via SMTP (`smtp.gmail.com:465` SSL) and IMAP (`imap.gmail.com:993`) using a Gmail **App Password** that Akshay enters himself in Settings.
- **Resume:** `engine/resume/resume2.py` + `orig_content.py` (existing builder; `python resume2.py cfg.json` writes `cfg["out"]`).

## 3. Folder layout (repo root = `jobhunter/`)
```
jobhunter/
  app.py                  # entry: start server (or reuse), open browser, auto-run
  launch_windows.vbs      # Desktop target: runs pythonw app.py hidden
  install_windows.bat     # one-time: pip install reportlab, create Desktop shortcut
  config.example.json
  jh/                     # backend package
    __init__.py
    server.py             # HTTP routes + SSE
    db.py                 # schema, migrations, queries
    events.py             # event bus (DB events table + in-memory fan-out to SSE)
    runner.py             # run orchestration, lanes, stop/cancel
    engine.py             # spawn Claude Code, parse stream-json → events
    mailer.py             # SMTP send (with attachment, threading headers), IMAP replies/bounces/sent lookup
    followups.py          # nudge rules (business days), reply/bounce detection
    resume.py             # build tailored PDF via engine/resume/resume2.py
    dedupe.py             # normalize company/role, already_contacted()
    importer.py           # import existing tracker.csv history
    envcheck.py           # Python/reportlab/claude CLI/login/Chrome/Gmail checks
    config.py             # load/save config.json (data dir)
  engine/
    jobhunt_mcp.py        # MCP stdio server (tools in §7)
    prompts/              # system + per-lane prompts (markdown)
      common.md  posts.md  jobright.md  linkedin_jobs.md
    resume/resume2.py  resume/orig_content.py
  web/
    index.html  app.css  app.js
  tests/                  # unittest (stdlib) + mocks
  data/                   # created at runtime (gitignored): jobhunter.db, config.json, resumes/, logs/
```
Data dir = `jobhunter/data/` (override with env `JOBHUNTER_DATA`).

## 4. Database (SQLite, WAL mode, `data/jobhunter.db`)
```sql
CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
  status TEXT,            -- running|done|stopped|error
  trigger TEXT,           -- open|button
  stats_json TEXT);
CREATE TABLE jobs (id INTEGER PRIMARY KEY, run_id INTEGER, source TEXT,  -- posts|jobright|linkedin_us|linkedin_world|manual|import
  company TEXT, role TEXT, location TEXT, remote INTEGER, url TEXT, posted TEXT,
  jd_text TEXT, status TEXT,   -- found|qualified|skipped|contacted|replied|bounced|closed
  skip_reason TEXT, notes TEXT, norm_key TEXT, fit_score INTEGER, apply_url TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE portal_apps (id INTEGER PRIMARY KEY, job_id INTEGER, ats TEXT, apply_url TEXT,
  status TEXT,             -- prefilled|needs_input|submitted|abandoned
  resume_path TEXT, questions_json TEXT,  -- questions the AI left for Akshay (work authorization, EEO, consent, unknown)
  created_at TEXT, updated_at TEXT);
CREATE TABLE contacts (id INTEGER PRIMARY KEY, job_id INTEGER, name TEXT, title TEXT,
  email TEXT, email_source TEXT,  -- post|SalesQL|JobRight|Vibe|Apollo|pattern|import
  linkedin TEXT, created_at TEXT);
CREATE TABLE emails (id INTEGER PRIMARY KEY, job_id INTEGER, contact_id INTEGER,
  kind TEXT,               -- initial|nudge1|nudge2
  subject TEXT, body TEXT, message_id TEXT, in_reply_to TEXT, resume_path TEXT,
  status TEXT,             -- sent|failed|bounced
  error TEXT, sent_at TEXT);
CREATE TABLE replies (id INTEGER PRIMARY KEY, contact_id INTEGER, job_id INTEGER,
  from_addr TEXT, subject TEXT, snippet TEXT, received_at TEXT, kind TEXT); -- reply|auto|bounce
CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, run_id INTEGER, lane TEXT,
  level TEXT,              -- info|success|warn|error|debug
  type TEXT, message TEXT, data_json TEXT);
CREATE INDEX ix_contacts_email ON contacts(lower(email));
CREATE INDEX ix_jobs_norm ON jobs(norm_key);
```
Times are ISO 8601 with offset, Asia/Kolkata (`+05:30`).
Derived "application status" for a contact (for the UI): `replied` > `bounced` > `nudge2` > `nudge1` > `sent`.

## 5. Events (one shape everywhere: DB row, SSE payload, engine)
```json
{"id":123,"ts":"2026-10-18T21:40:02+05:30","run_id":7,"lane":"jobright",
 "level":"info","type":"step","message":"Opened Capio Group — Salesforce Developer","data":{}}
```
`type` values: `portal_ready, run_started, run_finished, lane_started, lane_finished, step, tool, job_found, job_qualified, job_skipped, contact_found, email_sent, email_failed, nudge_sent, reply_found, bounce_found, warning, error, stats`.
`lane_finished.data = {"status":"done|error|skipped","counts":{...}}`.
`stats` is emitted by the server after any change: the full KPI object of §6 `/api/summary`.

## 6. HTTP API (JSON; server binds 127.0.0.1 only)
| Method | Path | Returns / does |
|---|---|---|
| GET | `/` , `/app.css`, `/app.js` | static UI |
| GET | `/api/summary` | KPIs: `{sent_today, sent_7d, sent_total, replies_total, reply_rate, awaiting, nudges_due, bounced, skipped_today, per_hour_current_run, last_run:{id,started_at,finished_at,status}, running:bool, by_source:{posts:n,jobright:n,linkedin_us:n,linkedin_world:n,import:n}}` |
| GET | `/api/applications?status=&source=&q=&limit=&offset=` | rows: `{contact_id, job_id, company, role, source, contact_name, contact_title, email, email_source, sent_at, last_touch_at, next_followup_at, app_status, url}` |
| GET | `/api/applications/<contact_id>` | detail: job (incl. jd_text), contact, emails[], replies[], timeline[] |
| GET | `/api/jobs?status=skipped&run_id=` | jobs list (for skipped tab) |
| GET | `/api/runs` / `/api/runs/<id>` | run history / one run with lane results |
| GET | `/api/events?run_id=&after_id=&limit=` | backlog of events |
| GET | `/api/stream` | **SSE**: `event: ev` + data = event JSON; heartbeat comment every 15 s |
| GET | `/api/charts` | `{daily:[{date, posts, jobright, linkedin_us, linkedin_world}] (last 14 days), funnel:{found, qualified, contacted, replied}}` |
| POST | `/api/run` | start a run (409 if running) → `{run_id}` |
| POST | `/api/stop` | stop the current run (kills engine processes) |
| GET/POST | `/api/settings` | read (app password masked) / save config |
| POST | `/api/settings/test-email` | SMTP+IMAP login test → `{ok, error}` |
| GET | `/api/env` | env checks: `[{id,label,ok,detail,fix}]` |
| GET | `/api/portal` | Ready-to-submit queue rows (job, ats, apply_url, status, questions) |
| POST | `/api/portal/<id>` | `{status: submitted|abandoned}` — Akshay marks it after clicking Submit himself |
| POST | `/api/import` | import `tracker.csv` (multipart or path) |

## 7. MCP tools the AI engine gets (`engine/jobhunt_mcp.py`, server name `jobhunt`)
The MCP server opens the same SQLite DB (path from env `JOBHUNTER_DB`) and writes events with the lane from env `JOBHUNTER_LANE` and run from `JOBHUNTER_RUN_ID`.
- `log_step(message, level?)` → writes a `step` event (the UI's live narration). The prompt tells the AI to call this before every page/action.
- `already_contacted(company, role, emails[])` → `{contacted:bool, reason}` (dedupe rules §1).
- `save_job(source, company, role, location, remote, url, posted, jd_text, decision, skip_reason?, notes?, fit_score?, apply_url?)` → `{job_id}`; decision `qualified|skipped`. Emits `job_qualified`/`job_skipped`.
- `save_contact(job_id, name, title, email, email_source, linkedin?)` → `{contact_id}` (refuses duplicates; refuses developer/QA titles with reason). Emits `contact_found`.
- `send_application(job_id, contact_id, subject, body, resume)` where `resume = {headline, summary, skills:[[label,text]×6], project_order:[keys]}` → app builds the PDF, sends via SMTP with `Akshay_Verma_Resume.pdf`, stores the email, emits `email_sent` (or `email_failed`), returns `{ok, email_id, error?}`. Refuses if already contacted, if `dry_run` setting is on (then it builds the PDF and records status `dry_run` without sending), or if the daily cap `max_emails_per_run` is reached.
- `save_portal_prefill(job_id, ats, apply_url, resume, questions_left[])` → app builds the tailored PDF (path returned for the Chrome file upload), stores a `portal_apps` row with status `prefilled` (or `needs_input` if questions_left), emits `portal_ready`.
- `get_rules()` → the honesty/skip/targeting rules text (also in prompts).

## 8. Engine process (per AI lane)
`jh/engine.py` builds:
```
<claude_path> -p "<lane prompt>" --output-format stream-json --verbose
  --append-system-prompt-file engine/prompts/common.md   (or inline text if file flag unsupported)
  --mcp-config <data>/mcp.json   (stdio server: python engine/jobhunt_mcp.py)
  <engine.claude_args from config, e.g. --chrome --permission-mode bypassPermissions --max-turns 200>
```
env: `JOBHUNTER_DB, JOBHUNTER_LANE, JOBHUNTER_RUN_ID`. It reads stdout line by line (JSON), and maps:
- assistant `tool_use` → `tool` event (`message` = short human label, e.g. "Chrome: navigate linkedin.com/jobs/…", "SalesQL lookup", "jobhunt: send_application")
- assistant text → `step` event (first 300 chars)
- `result` → `lane_finished` with cost/turns in data.
Stderr lines → `debug` events. Exit code ≠ 0 → lane `error` with the last stderr lines. Timeout per lane (`engine.lane_timeout_min`, default 25). Stop = terminate the process tree.
**Mock mode** (`engine.mock: true` or env `JOBHUNTER_MOCK_ENGINE=1`): instead of `claude`, run `tests/mock_claude.py` which emits realistic stream-json AND calls the MCP tool functions directly (save_job, save_contact, send_application) so the whole app can be tested without Claude, Chrome or Gmail.

## 9. Email rules (`jh/mailer.py`, `jh/followups.py`)
- From: `"Akshay Verma" <gmail_address>`. Plain text body. Attachment `Akshay_Verma_Resume.pdf` (application/pdf) on initial emails only. Set our own `Message-ID`; store it.
- Nudges: reply in thread → `Subject: Re: <original subject>`, `In-Reply-To` and `References` = original Message-ID. For imported history without a stored Message-ID, look it up via IMAP in `"[Gmail]/Sent Mail"` (`SEARCH TO "<email>" SUBJECT ...` newest first); if not found, skip the nudge with a warning.
- Schedule: nudge1 when ≥1 business day (Mon–Fri) since the initial with no reply; nudge2 when ≥3 business days since nudge1; then stop (sequence done). Nudge texts: 3–4 short human templates each, rotated; signed "Thanks,\nAkshay Verma".
- Replies: IMAP `INBOX` search `FROM "<email>" SINCE <sent date>`; auto-replies (subject has "out of office"/"automatic reply"/"auto-reply", or `Auto-Submitted` header ≠ no) are recorded as `kind=auto` and do NOT stop nudges. Bounces: `FROM "mailer-daemon"` or subject "Delivery Status Notification"/"Undeliverable" since 7 days; parse the failed address; mark email `bounced`, job `bounced`.
- Email backends: `smtp` (real) and `file` (tests: writes `.eml` to `data/outbox/`; IMAP replaced by a fake reading `data/inbox/*.eml`). Chosen by config `email.backend`.
- Safety: `dry_run` (default **true on first install** until the email test passes and Akshay turns it off in Settings), `max_emails_per_run` (default 25).

## 10. Import of existing history
`jh/importer.py` imports `tracker.csv` (columns: date, company, role, contact, title, email, status, next_step) → jobs/contacts/emails (source `import`, kind `initial`, sent_at = date 10:00 IST, message_id NULL). Rows with status starting "sent"/"replied"/"bounced" create contact+email; "replied" also adds a replies row; "skipped" creates a skipped job. next_step text with "nudge 1 sent YYYY-MM-DD"/"nudge 2 sent …" creates nudge email rows (sent_at = that date) so the follow-up schedule continues correctly; "sequence done" or "hold" marks no further nudges (job status `closed` unless replied).

## 11. UI (designer brief) — see `web/`
Feel: a real ops console (think Linear/Vercel dashboard), not a toy. Dark-first with light theme toggle, Inter-like system font stack, 8-px grid, subtle borders, one accent color, status colors only for status. Dense but calm. Everything updates live via SSE without reload; new rows animate in briefly.
Layout (desktop ≥1280 px; must stay usable at 1024):
1. **Top bar:** app name "Job Hunter", run status pill (Idle · Running 03:12 · Stopped · Error), "Run now" primary button / "Stop" when running, last run "today 21:40 · 14 min · 6 sent", Settings icon.
2. **KPI strip (6 tiles):** Sent today · Sent 7 days · Replies (count + rate) · Awaiting reply · Follow-ups due · Send rate this run (/hr). Each tile has a tiny 14-day sparkline where it makes sense.
3. **Live run panel:** 5 lane cards in a row (Follow-ups, LinkedIn posts, JobRight, LinkedIn Jobs, Outreach): state badge (queued/running/done/error/skipped), counters (found · qualified · sent · skipped), current action line (latest `step`), elapsed. Below: **activity feed** (terminal-like, monospace, newest at bottom, auto-scroll with "pause on hover", level colors, lane filter chips).
4. **Applications table:** tabs All · Awaiting · Replied · Bounced · Skipped. Columns: Company · Role · Source badge · Contact (name + title) · Email · Sent · Last touch · Next follow-up · Status chip. Search box + source filter. Click row → right **drawer**: job + JD text, contact, email(s) sent (body), replies, timeline (sent → nudge1 → nudge2 → reply).
5. **Insights row:** stacked bar chart "Applications per day (14 days)" by source; funnel Found → Qualified → Contacted → Replied; "Latest replies" list.
6. **Settings page/modal:** Gmail address, App Password (masked; link-style help text how to create one), "Test email connection" button, toggles: dry run, auto-run on open, parallel sources; numbers: max emails per run; engine: Claude path, extra args; **Environment checks** list with ✓/✗ and fix hints; "Import tracker.csv" button.
Empty states and first-run onboarding: if Gmail not set → banner "Set up Gmail to start sending" linking to Settings; if Claude CLI missing → banner with install steps.

## 12. Windows launch
- `install_windows.bat`: checks `python` / `py`, runs `python -m pip install --user reportlab`, creates Desktop shortcut "Job Hunter" → `wscript.exe launch_windows.vbs` with icon, then starts the app once.
- `launch_windows.vbs`: runs `pythonw app.py` (fallback `pyw`/`python`) with no console window.
- `app.py`: if port 8765 answers `/api/summary`, just open the browser (and POST `/api/run` if auto-run and not running); else start the server thread, open browser, start run if auto-run. Writes `data/logs/app.log`.

## 14. Portal prefill rules (lane `portal`, after the source lanes)
- Only Greenhouse (`boards.greenhouse.io`, `job-boards.greenhouse.io`), Lever (`jobs.lever.co`), Ashby (`jobs.ashbyhq.com`) application pages that need NO account and NO login. Anything else (Workday, iCIMS, Taleo, LinkedIn Easy Apply, JobRight autofill) → skip with reason.
- Fill only facts from the profile (name, email, phone, LinkedIn, location "India (remote)", resume upload via the Chrome file-upload tool, a short cover note). Leave EMPTY and list in `questions_left`: work-authorization/sponsorship, EEO/demographic, consent/terms/privacy checkboxes, salary expectations, anything not in the profile. Never tick consent boxes, never solve captchas, never create accounts.
- **Never click Submit/Apply/Send on the form.** Leave the tab open on the filled form. The UI shows the queue; Akshay reviews and clicks Submit himself, then marks it Submitted.
- Each job also gets `fit_score` 0–100 (skills match, seniority, remote/contract fit, eligibility) saved via `save_job`; the UI shows it.

## 13. Non-goals (v1)
No cloud, no multi-user, no scheduler, no LinkedIn actions other than reading (never Connect/Message/Apply/Easy Apply), no automatic form submission anywhere (portal forms are prefilled only), no phone numbers/SMS, no auto-login anywhere.

## 15. Engine CLI details (verified against Claude Code docs, 2026-10-18)
- Install (Windows, PowerShell): `irm https://claude.ai/install.ps1 | iex`; login once with `claude` (subscription, `claude auth status` → `authMethod: claude.ai`). Node.js not needed.
- The child process env MUST drop `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` (otherwise it bills the API and Chrome stays off).
- Default `engine.claude_args` (config, editable):
  `["--chrome","--permission-mode","dontAsk","--allowedTools","mcp__jobhunt__*","mcp__claude-in-chrome__*","--disallowedTools","Agent","--strict-mcp-config","--max-turns","150"]`
  plus always added by code: `-p <prompt> --output-format stream-json --verbose --mcp-config <data>/mcp.json --append-system-prompt-file <engine/prompts/common.md>`; cwd = data dir; env `ENABLE_CLAUDEAI_MCP_SERVERS=false`.
- Unverified (must be tested on the laptop via the Settings → "Engine test" button): `--chrome` inside `-p`; exact Chrome tool names (`mcp__claude-in-chrome__*` expected); `--strict-mcp-config` vs Chrome. The Engine test runs a 3-turn `claude -p` that calls `mcp__jobhunt__log_step` and the Chrome `tabs_context_mcp` tool, and reports which worked.
- stream-json lines: `{"type":"system","subtype":"init",...}`, `{"type":"assistant","message":{"content":[{"type":"text"|"tool_use",...}]}}`, `{"type":"user","message":{"content":[{"type":"tool_result",...}]}}`, final `{"type":"result","subtype":"success"|...,"total_cost_usd":..,"num_turns":..,"is_error":..}`. Ignore unknown types.
- No timeout flag: enforce `engine.lane_timeout_min` in Python; stop = terminate the process tree (Windows: `taskkill /PID <pid> /T /F`; POSIX: killpg).

## 16. Module interfaces (so the team can build in parallel)
### jh/db.py (backend)
- `connect(path=None) -> sqlite3.Connection` (row_factory=sqlite3.Row, WAL, foreign keys, creates schema; default path = data dir/jobhunter.db; env `JOBHUNTER_DB` overrides).
- `now_iso() -> str` (Asia/Kolkata +05:30).
### jh/events.py (backend)
- `emit(conn, run_id, lane, type, message, level="info", data=None) -> dict` inserts into `events` and returns the full event dict (§5). In-process subscribers (SSE) get it via `subscribe() -> queue.Queue` / `unsubscribe(q)`. Events written by OTHER processes (the MCP server) are picked up by a poller thread in the server (`events.start_db_tailer(db_path)`, every 0.5 s, by id > last seen) and fanned out the same way.
### jh/actions.py (backend) — used by both the MCP server and the mock engine
All take `conn, run_id, lane` first and emit the right events.
- `log_step(conn, run_id, lane, message, level="info") -> dict`
- `already_contacted(conn, company, role, emails) -> {"contacted": bool, "reason": str}`
- `save_job(conn, run_id, lane, source, company, role, location="", remote=True, url="", posted="", jd_text="", decision="qualified", skip_reason=None, notes=None, fit_score=None, apply_url=None) -> {"job_id": int}`
- `save_contact(conn, run_id, lane, job_id, name, title, email, email_source, linkedin=None) -> {"contact_id": int} | {"error": str}`
- `send_application(conn, run_id, lane, job_id, contact_id, subject, body, resume: dict, cfg: dict) -> {"ok": bool, "email_id": int|None, "error": str|None, "dry_run": bool}`
- `save_portal_prefill(conn, run_id, lane, job_id, ats, apply_url, resume: dict, questions_left: list, cfg) -> {"portal_id": int, "resume_path": str}`
- `build_resume_only(conn, job_id, resume: dict) -> path` (used by portal before upload)
### jh/engine.py (engine dev)
- `run_lane(run_id: int, lane: str, cfg: dict, emit: callable, stop: threading.Event) -> {"status": "done|error|stopped", "counts": dict, "cost_usd": float|None, "error": str|None}` where `emit(lane, type, message, level="info", data=None)` is provided by the runner.
- `build_command(lane, cfg, prompt_path, data_dir) -> (argv: list, env: dict)`
- `parse_stream_line(line: str) -> list[(type, message, level, data)]` (pure, unit-tested)
- `engine_smoke_test(cfg) -> {"ok": bool, "checks": [{"id","ok","detail"}]}`
### jh/runner.py (backend)
- `start_run(trigger) -> run_id` (thread): followups thread + AI lanes (sequential or parallel per config) + portal lane; emits run/lane events; computes stats. `stop_run()`. `is_running()`.
### engine/jobhunt_mcp.py (engine dev)
- stdio MCP (JSON-RPC 2.0: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `ping`), protocolVersion "2025-06-18" (echo the client's if given). Tools = §7, each a thin wrapper over `jh.actions` (adds `sys.path` to the app root). Reads env `JOBHUNTER_DB`, `JOBHUNTER_RUN_ID`, `JOBHUNTER_LANE`, `JOBHUNTER_CONFIG`.
### tests/mock_claude.py (engine dev)
- Same CLI shape as claude (ignores flags). Prints realistic stream-json over ~10–20 s per lane and performs real writes through `jh.actions` (2–4 jobs per lane, mix of qualified/skipped, contacts, send_application with the file email backend, one portal prefill in the portal lane), honoring env `JOBHUNTER_LANE`.
