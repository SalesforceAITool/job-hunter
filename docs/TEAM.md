# Job Hunter team and daily improvement loop (since 27 Nov 2026)

**Goal (Akshay, 27 Nov):** as many remote Salesforce jobs and contracts as possible from clients **outside India**
(US, UK, Europe, Australia, Middle East — paid in USD/GBP/EUR). Akshay's own company can staff extra developers, so
10 contracts are welcome, not just 1–2. India remote roles still get applied to, and Job Hunter should be among the
first applicants.

## The team

| Role | Who does it | What it owns |
|---|---|---|
| Applier | Easy Apply + board_apply + portal lanes (AI, his own Chrome) | Easy Apply, company career sites (Greenhouse/Lever/Ashby/Workable), remote boards. Outside-India jobs first. Tailored resume per JD + ATS keyword check. |
| Outreach | posts + agencies lanes (AI) | Recruiter emails from LinkedIn hiring posts (US/UK/EU searches first), agency and consultancy pitches (C2C/B2B, team available). Only printed or verified addresses. |
| Follow-up | followups lane (no AI) + outbox (no AI) | Replies/bounces in every Gmail inbox, nudges from the thread's own account, the outbox that sends everything spaced out and pauses an account for 24 h on a Gmail limit. |
| Analyst | daily scheduled review (cloud, linked to the laptop) | Yesterday's numbers, outside India first: applications, emails, replies, interviews, bounces, Gmail pauses, spend, hours the laptop was off. |
| Lead / architect | the same daily review, with sub-agents (QA reviewer, market analyst) | Finds the biggest blocker, ships 1–3 small fixes as separate commits with tests, deploys to the laptop (the app restarts itself when BUILD changes), reports to Akshay. |

## Daily loop

1. Stage `data/jobhunter.db` (+ `-wal`), `data/logs/app.log` and the newest engine logs from the laptop.
2. Analyse the last 24 h and the trend (see the Analyst row). Outside India first in every table.
3. Health: app on the newest build (`app.log`, `/api/version` via the page), LinkedIn signed in on the Job Hunter
   Chrome, Gmail accounts paused or not, outbox size, errors per lane, AI spend vs budget.
4. A QA sub-agent reviews the code paths behind any number that looks wrong; a market sub-agent checks new channels
   when outside-India volume is low.
5. Ship the top fixes: separate commits, tests green (14 known fixture/server failures excepted), push, copy changed
   files to `C:\Users\DELL\Documents\JobHunter`, bump `jh/__init__.py` BUILD and `web/app.js` APP_BUILD.
6. Report to Akshay (Hinglish, short): numbers, what changed, anything only he can do.

## Rules that never change
- No passwords, app passwords, verification codes or CAPTCHAs typed by the AI; Akshay enters those himself.
- Resume facts only from what Akshay confirmed; never invent skills, years, clients or team size.
- LinkedIn only in the locked Job Hunter Chrome (deviceId in config `engine.chrome_device_id`).
- No architect roles; jobs asking at most 7 years; remote only.
- Gmail: keep each personal account to a small daily volume; Google's policy forbids using several accounts to get
  around its limits, so extra accounts are for replies and threads, and real volume needs a proper business domain
  mailbox (SPF/DKIM) — say so rather than rotating accounts to evade a limit.
- Every feature its own commit; code in GitHub SalesforceAITool/job-hunter; `data/` never committed.
