# Akshay job outreach — daily runbook (v2, laptop + Chrome, 2026-10-18)

Owner: Akshay Kumar Verma (akshay.verma0031@gmail.com). Salesforce developer, 7+ yrs, India-based, NO US work authorization, works remotely on US hours as a contractor. He has given standing permission: never ask before sending, spending Apollo/Vibe credits, or updating the tracker.

## How the daily run is executed (v3, 2026-10-18)
The daily run uses parallel agents: follow `claude/jobkit/agents/00-orchestrator.md`. Agents A (LinkedIn posts), B (JobRight + SalesQL), C (LinkedIn Jobs US + Worldwide) and D (follow-ups) run at the same time; agent E (resume + email) runs after A–C. This runbook holds the rules every agent follows. If the Agent tool is unavailable, do the same steps yourself in order.

## What changed in v2 (user decision, 2026-10-18)
The phone pipeline (MacroDroid screenshots → FolderSync → Drive OCR) is retired: the LinkedIn app could not reliably tap "…more". Jobs are now read **directly as text from Akshay's laptop Chrome** through the Claude in Chrome extension (he is logged in to LinkedIn and JobRight there). No screenshots, no OCR. Tested 2026-10-18: posts search expanded 8 "…more" buttons and returned full text with 3 emails; LinkedIn Jobs worldwide-remote returned 48 Salesforce jobs; JobRight recommend page readable with his filters.

## Inputs — three sources, all through Claude in Chrome
Load the Chrome tools with ONE ToolSearch call: `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__tabs_close_mcp`. Open your own tab, close it at the end.

If the Chrome tools are missing or the browser doesn't respond (laptop off / Chrome closed), say so at the TOP of the summary ("Laptop/Chrome band tha, aaj naye jobs nahi padhe"), fall back to any screenshots in Drive folder `1fvkZcGIqrWJUgfRT4D0aC8OAXr0CBQ8n` from the last 26h, and still do follow-ups.

If a page shows a LinkedIn/JobRight login screen, a captcha or a "security check", stop that source, do not try to solve it, and tell Akshay.

**Pace (protects his LinkedIn account):** wait 3–5 s between page loads, at most ~40 page loads per run in total. Read only. Never click Connect, Follow, Message, Easy Apply, Like or Comment.

### Source A — LinkedIn posts (main source, emails are free)
Open each URL (Posts, past 24h, latest first):
1. `https://www.linkedin.com/search/results/content/?keywords=%22salesforce%20developer%22%20remote%20hiring&datePosted=%22past-24h%22&sortBy=%22date_posted%22`
2. `https://www.linkedin.com/search/results/content/?keywords=%22salesforce%20architect%22%20OR%20%22salesforce%20lead%22%20remote%20hiring&datePosted=%22past-24h%22&sortBy=%22date_posted%22`
3. `https://www.linkedin.com/search/results/content/?keywords=salesforce%20(omnistudio%20OR%20cpq%20OR%20%22experience%20cloud%22%20OR%20%22data%20cloud%22%20OR%20agentforce)%20remote%20hiring&datePosted=%22past-24h%22&sortBy=%22date_posted%22`

On each page run this with `javascript_tool` (scrolls to load more posts, clicks every "…more", returns emails), then read the full text with `get_page_text`:
```js
await new Promise(r=>setTimeout(r,4000));
for (let i=0;i<5;i++){ window.scrollBy(0,2500); await new Promise(r=>setTimeout(r,2000)); }
const btns=[...document.querySelectorAll('button')].filter(b=>/^\s*…?\s*(see )?more\s*$/i.test(b.innerText||''));
btns.forEach(b=>b.click()); await new Promise(r=>setTimeout(r,1500));
({moreClicked:btns.length, emails:[...new Set(document.body.innerText.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g)||[])]})
```
Every post for a Salesforce technical role that shows an email gets an email to that exact address. Mention the post ("saw your LinkedIn post about the … role") and the requirements it lists. The post text is the JD for the skip rules. Ignore "open to work" posts by candidates and job-board aggregator posts with no email.

### Source B — US remote jobs
1. **JobRight**: `https://jobright.ai/jobs/recommend` (his saved filters: United States, Salesforce Developer, Contract, Remote, Past 24 hours). Read the list with `get_page_text`, then open each Salesforce job's detail page (`/jobs/info/<id>` links) and read the **Hiring Manager** name and the **Insider Connection** section (Beyond your network / From Your Previous Company).
   If the recommend page has few or no Salesforce jobs (common), use JobRight search, which filters out US-citizen and clearance jobs for you:
   `https://jobright.ai/jobs/search?value=Salesforce+Developer&searchType=job_title&country=US&jobTaxonomyList=%5B%7B%22taxonomyId%22%3A%2200-00-00%22%2C%22title%22%3A%22Salesforce+Developer%22%7D%5D&isH1BOnly=false&excludeStaffingAgency=false&excludeSecurityClearance=true&excludeUsCitizen=true&refresh=true&position=0&sortCondition=1`
   (also run it with `value=Salesforce+Architect`). Cards lazy-load inside a scrolling div; to open one, click its title element (or press Esc first if a job detail panel is covering the list). Keep only Remote cards.
   **JobRight contact flow (user rule, 2026-10-18)** — for every Salesforce job that passes the filters and skip rules:
   a. On the job page, read the JD (scroll; `get_page_text`).
   b. In **Insider Connection** click **View** on the "Beyond Your Network" person (HR / recruiter / architect / lead — never a plain developer or QA). The card shows name + title.
   c. Get that person's LinkedIn URL without opening new tabs: run this, then it returns the profile URL (it clicks the "in" icon and "View Linkedin Profile" in the modal, and catches the link instead of opening it):
```js
window.__cap=[]; const ac=HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click=function(){window.__cap.push(this.href);};
const wo=window.open; window.open=u=>{window.__cap.push(String(u)); return null;};
[...document.querySelectorAll('button')].find(x=>/linkedin-icon/i.test(x.outerHTML)).click(); await new Promise(r=>setTimeout(r,1500));
[...document.querySelectorAll('button,a')].find(b=>/View Linkedin Profile/i.test(b.innerText||'')).click(); await new Promise(r=>setTimeout(r,4000));
HTMLAnchorElement.prototype.click=ac; window.open=wo; window.__cap
```
   (If several people are listed, click View on each in turn; the modal is a connection-message helper — never send a connection request from it.)
   d. Navigate the tab to that LinkedIn profile, wait ~5 s, click the purple **SalesQL** tab on the right edge of the page (about x=1552, y=76 in a 1568-wide viewport; confirm with a screenshot), and read the **email** from the SalesQL panel. Ignore phone numbers. If the panel says "Log in to SalesQL", stop the SalesQL step and tell Akshay to log in to SalesQL in Chrome (never log in for him); fall back to Vibe/Apollo for that person.
   e. Use that email for the tailored resume + email (HR: fit; architect/lead: technical work + ask to pass the resume on). Record the SalesQL email in the tracker as "email via SalesQL".
   f. JobRight also shows an envelope icon with "N email credits available today" — use it only if SalesQL gives nothing.
2. **LinkedIn Jobs, US remote, last 24h**: `https://www.linkedin.com/jobs/search/?keywords=%22salesforce%22%20(developer%20OR%20architect%20OR%20engineer%20OR%20consultant)&geoId=103644278&f_WT=2&f_TPR=r86400&sortBy=R`

### Source C — Worldwide remote jobs
`https://www.linkedin.com/jobs/search/?keywords=%22salesforce%22%20(developer%20OR%20architect%20OR%20engineer%20OR%20consultant)&geoId=92000000&f_WT=2&f_TPR=r86400&sortBy=R`
(Remotive / Jobicy / Himalayas feeds were tested 2026-10-18 and return almost no Salesforce roles — don't use them.)

Reading a LinkedIn Jobs list: cards lazy-load, so scroll each into view, then collect id, title, company, location:
```js
await new Promise(r=>setTimeout(r,4000));
const L=[...document.querySelectorAll('li[data-occludable-job-id]')];
for (const c of L){ c.scrollIntoView(); await new Promise(r=>setTimeout(r,250)); }
[...new Set(L.map(c=>c.getAttribute('data-occludable-job-id')+' | '+(c.innerText||'').replace(/\n+/g,' | ').slice(0,160)))]
```
For each job that passes the title filter, open `https://www.linkedin.com/jobs/view/<id>/` and `get_page_text`: that is the JD (skip rules) and it often shows **"Meet the hiring team"** with the recruiter/poster name — look that person up in Vibe/Apollo first.

## What can go on his resume
- He has worked on about 20 Salesforce projects as a developer or lead, and has hands-on experience across most of the Salesforce platform. Many of those projects are not written up in the resume, to keep it short.
- When a JD asks for a Salesforce skill he holds (OmniStudio, Service/Sales/Experience Cloud, CPQ, integrations, Flows, LWC, Apex, Data Cloud, Agentforce basics, DevOps), name it confidently in the headline, summary and skills.
- OmniStudio is real hands-on work: the BCBSMA project (Blue Cross Blue Shield of Massachusetts, live at https://www.bluecrossma.org/). He built the whole member plan shopping website built with OmniScripts, FlexCards, Integration Procedures and DataRaptors. It is project key `bcbsm`. For OmniStudio JDs, put it first in `project_order`.
- Never invent employers, dates, client names, metrics, or non-Salesforce technologies.
- With BCBSMA added, the base64 is about 26 KB, so splitting gives 3 parts (part00 to part02). Join all of them.

## Notes
- Remote roles locked to one country (LATAM, EU, Philippines, Ukraine…) are skipped unless the country is India. For large companies, never email the CEO; use talent acquisition.
- Akshay does NOT apply to jobs himself. Never send him lists of jobs to apply to. Outreach is by email only; jobs with no contact are just logged as skipped.

## Saving tokens
- Use `javascript_tool` to pull compact lists; use `get_page_text` only on pages you need in full (post searches, chosen job pages).
- Check JDs yourself. Don't send a large research agent to every job.

## Daily steps
1. Collect jobs from Sources A, B, C (company, title, location/remote, posted time, poster / hiring manager / hiring team names, any email in a post, job URL).
1b. Read the project doc `claude/jobkit/pending_rows.csv` (rows Claude added from manual sends in chat). Append every data row to the tracker before deduping, treat them like any other sent row (nudges included), then overwrite `pending_rows.csv` with just the header line.
2. Dedupe against the tracker. A company+role or email already in the tracker is not contacted again.
3. Be strict: keep only remote roles whose title is a Salesforce technical role. That means Salesforce Developer, Engineer, Consultant (technical), Architect, Technical Lead or Lead, CPQ Developer, or Admin+Developer. Drop everything else without spending credits: non-Salesforce tech (SAP, Snowflake, Oracle CPQ, ServiceNow, Workday), sales or account executive roles, pure business analyst or functional roles, and marketing roles. Priority order:
   1. US
   2. Canada, UK, EU, Australia, Middle East, and others paying in USD/GBP/EUR
   3. India remote
4. Check the JD (the post text, the LinkedIn job page, or the JobRight detail page). **Skip** when it explicitly requires US citizenship/GC, a clearance or Public Trust, W2 only, "must be authorized to work in the US / no sponsorship", US residency, onsite/hybrid, or a language he doesn't speak. Log the skip reason.
5. Find contacts. Try the free routes in this order before spending credits:
   - If a LinkedIn post or the JD shows an email, use it (costs no credits).
   - If a verified email at the same company is already in the tracker, apply its pattern (e.g. first.last@) to the new person. Only do this when the pattern has been confirmed by a verified email.
   - For small companies, look for a careers, jobs, talent or HR inbox on the company website, and use it as the fallback contact.
   - If Vibe Prospecting is connected, use it before Apollo. Vibe flow: match-business (company name + domain) -> fetch-entities (prospects; filters business_id, job_department human resources / engineering / it, job_level director+, has email) -> match-prospects for the chosen people (full name + business_id works even for "Sheila K.") -> enrich-prospects (contacts, email) -> show-sample to read the emails. About 2 credits per email plus 5 per table; check the balance.
   - Otherwise use the Apollo agent `apollo_agent_find_prospects`. The instruction must be the user's verbatim words: "we need to email to right person and recruiter and hiring manager or cto ceo". Add a "Context:" line listing the companies, roles and any names read from the page. Approve Apollo-only enrichment if asked.
   - **Target the RIGHT people** (user rule, 2026-10-07): email the Hiring Manager shown on the JobRight page / the "Meet the hiring team" person on LinkedIn, plus HR/TA/recruiter and an architect / tech lead / engineering manager / director. **Never email individual developers or QA engineers** (they won't refer). Always at least one HR and one leader/architect; at small companies the CEO counts as the leader. Each person gets their own email written for their role (HR: fit; architect/leader: technical work + ask to pass the resume to the hiring team).
   - LinkedIn posts: 1 contact per job, the email in the post. Use work emails only (a gmail/outlook address written in a recruiter's own post is OK).
   - No contact found: log it as skipped with the reason.
   - **Credit budget**: about 143 lead credits per cycle (resets on the 6th). Use at most 5 Apollo credits per day. Do not reveal phone numbers.
6. Build a tailored resume:
   - Write a JSON config with `out`, `headline`, `summary`, and `skills` (6 rows of [label, text], matched to the JD).
   - Run `python3 resume2.py cfg.json`, then `qpdf --object-streams=generate --recompress-flate --compression-level=9 raw.pdf final.pdf` (~20 KB).
   - Base64 it, `split -b 12500`, Read the parts, and paste the joined base64 exactly into the Gmail `send_message` attachment (filename `Akshay_Verma_Resume.pdf`, mimeType `application/pdf`).
   - Afterwards, check the sent message's sizeEstimate (~29–31 KB). If it's well above that, the base64 was mistyped and the PDF is corrupt: reply in the same thread with a short note and a correct re-attachment.
7. Write the email:
   - Plain text, ~100 words, no bullets, sounds like a person wrote it.
   - Mention one or two specifics from the JD tied to his real work: Amkor CPQ + SAP integration, Dubai Holding LWC reporting, Siemens team of 17, Experience Cloud portals, BCBSMA OmniStudio, Data Cloud cert, CI/CD on Git/Bitbucket.
   - Say honestly that he's based in India, works US hours, and is open to a contract setup.
   - Sign off "Thanks,\nAkshay Verma\nlinkedin.com/in/akshay-kumar-18565910a".
   - Send one new email per person. Never forward.
8. Timing: send US/Canada emails between 19:30 and 23:30 IST (US business hours). Send India/Asia emails during their daytime.
9. Follow-ups:
   - (User rule, 2026-10-08: don't wait days.) If there's no reply after 1 business day, reply in the same thread with a short 2–3 line nudge, sent during the recipient's business hours. Do this for EVERY sent row, every day, without waiting for Akshay.
   - Send a second nudge 3 business days after the first nudge, then stop.
   - After each nudge, update the row's next_step (e.g. "nudge 1 sent 2026-10-08; nudge 2 due 2026-10-13").
   - Check replies with Gmail `search_threads`. If someone replied, flag it to Akshay and don't nudge.
9b. Bounce check: search Gmail for `from:mailer-daemon OR subject:"Delivery Status Notification" OR subject:Undeliverable newer_than:3d`. For every bounce, mark the row "bounced" and try the next-best contact at that company, a verified one only.
10. Tracker:
    - Keep the local `tracker.csv` (columns: date, company, role, contact, title, email, status, next_step). Add a `source` note in next_step for new rows (A-post / B-JobRight / B-LinkedIn-US / C-worldwide).
    - Re-create the Google Sheet "Job Outreach Tracker" in Drive folder `1i8jvfInJ_hXlzm0Mx9Hdc6qO513Rjng1` from the CSV, then trash the previous sheet. Drive can't edit cells.
11. Send Akshay a short Roman-Hinglish summary covering: Chrome/laptop health (first, only if something failed), replies, sent (by source A/B/C), nudges, skipped (with reasons), and credits left.

## Files (in this project under claude/jobkit/)
- `resume2.py`: resume builder (reportlab, Helvetica). Needs `orig_content.py` in the same folder.
- `orig_content.py`: his original resume content and 11 certifications.
- `tracker.csv`: snapshot of the tracker.

## Do not
- Do anything on LinkedIn except open search, job and profile pages and read them (expanding "…more" and opening the SalesQL panel are fine). No connection requests, messages, follows, likes, comments or applications.
- Solve captchas or log in for him.
- Send SMS or cold-text phone numbers.
- Use personal emails returned by Apollo.
