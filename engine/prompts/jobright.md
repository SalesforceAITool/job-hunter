# Lane: JobRight (`source: "jobright"`)

Goal: US remote Salesforce technical jobs on JobRight → read each JD → find the right people via **Insider Connection** → get their LinkedIn profile → read their email with the **SalesQL** panel → send the application.

**Cap: 8 jobs** opened per run (US remote, posted within ~7 days, newest first).

## Steps
1. Chrome setup per the common rules (own tab only).
2. Job lists (JobRight already removes US-citizen and clearance jobs with these flags). Open in this order until you have 8 candidate jobs:
   - `https://jobright.ai/jobs/search?value=Salesforce+Developer&searchType=job_title&country=US&jobTaxonomyList=%5B%7B%22taxonomyId%22%3A%2200-00-00%22%2C%22title%22%3A%22Salesforce+Developer%22%7D%5D&isH1BOnly=false&excludeStaffingAgency=false&excludeSecurityClearance=true&excludeUsCitizen=true&refresh=true&position=0&sortCondition=1`
   - `https://jobright.ai/jobs/search?value=Salesforce+Architect&searchType=job_title&country=US&jobTaxonomyList=%5B%7B%22taxonomyId%22%3A%2200-00-00%22%2C%22title%22%3A%22Salesforce+Architect%22%7D%5D&isH1BOnly=false&excludeStaffingAgency=false&excludeSecurityClearance=true&excludeUsCitizen=true&refresh=true&position=0&sortCondition=1`
   - `https://jobright.ai/jobs/recommend` (his saved filters)
   Cards lazy-load inside a scrolling div. Collect them with `javascript_tool`:
```js
await new Promise(r=>setTimeout(r,6000));
for(let i=0;i<5;i++){ document.querySelectorAll('div').forEach(d=>{if(d.scrollHeight>d.clientHeight+500 && d.clientHeight>300) d.scrollTop+=3000}); await new Promise(r=>setTimeout(r,1500)); }
const all=[...document.querySelectorAll('div')].filter(d=>{const t=d.innerText||''; return /ago/.test(t) && /Why This Job Is A Match/.test(t) && t.length<1500;});
all.filter(d=>!all.some(o=>o!==d && d.contains(o))).map((c,i)=>i+': '+c.innerText.replace(/\n+/g,' | ').replace(/ \| \/ \| [^|]*/,'').slice(0,160))
```
   (Result is cut at ~1000 chars — if there are many cards, run it again returning `.slice(6,12)` etc.)
   Keep **Remote** cards with a Salesforce technical title. Call `screen_jobs(source: "jobright", items)` once with all kept cards (company, role, JobRight job URL, location, the card text): open only its `work` list; for `retry` ones only look for contacts (Insider Connection → SalesQL). For each `new` card call `already_contacted(company, role, [])` before opening it; contacted → `save_job` skipped `"already contacted"` (no need to open it). Cards you drop by title (not Salesforce technical) need not be saved.
3. Open a card: click its title element (find the card whose text contains the company, then click the leaf element whose text equals the title — `find` + `computer left_click`, or a short script). If a detail panel covers the list, press Escape first. The page becomes `/jobs/info/<id>`; the job `url` is `https://jobright.ai/jobs/info/<id>`.
4. Read the JD with `get_page_text` (the JD runs from "Overview" to "Company data provided"). Apply the skip rules (US work authorization / no sponsorship, citizenship, clearance, W2 only, onsite/hybrid, US-resident only, not a Salesforce job). Try to read the original **Apply** link with a script that returns only the href host and path of the apply anchor (do NOT click Apply — JobRight autofill is off-limits); if it's Greenhouse/Lever/Ashby put it in `apply_url`.
5. **Insider Connection** section: note "Hiring Manager <name>" if shown, and each "Beyond Your Network" / "From Your Previous Company" person (name + title). Choose up to 3 who are HR/TA/recruiter, hiring manager, architect, tech lead, engineering manager/director (never plain developers/QA). For each chosen person: click their **View** button, then capture the LinkedIn URL without opening a tab (tested):
```js
window.__cap=[]; const ac=HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click=function(){window.__cap.push(this.href);};
const wo=window.open; window.open=u=>{window.__cap.push(String(u)); return null;};
[...document.querySelectorAll('button')].find(x=>/linkedin-icon/i.test(x.outerHTML)).click(); await new Promise(r=>setTimeout(r,1500));
[...document.querySelectorAll('button,a')].find(b=>/View Linkedin Profile/i.test(b.innerText||'')).click(); await new Promise(r=>setTimeout(r,4000));
HTMLAnchorElement.prototype.click=ac; window.open=wo; window.__cap
```
   The modal is a connection-message helper — close it (Escape); **never send a connection request**.
6. **SalesQL** for each captured profile: `log_step` → navigate your tab to the LinkedIn profile → wait ~6 s → `computer screenshot` → click the purple **SalesQL** tab on the right edge of the page (about x=1552, y=76 in a 1568-wide viewport; confirm on the screenshot; skip any "Get Started" tour with "Skip tour"). The panel shows "N emails found". Click **Reveal Info & Add**, wait 5 s, then `find` "email address text in SalesQL panel" to read the full email. Prefer the **work email at the company's domain**; ignore personal/other-company emails and all phone numbers. `email_source: "SalesQL"`, `linkedin` = the profile URL.
   - Panel says "Log in to SalesQL" or shows terms to accept → stop using SalesQL for the rest of the lane, `log_step` (level warn) "SalesQL needs login in Chrome", do not log in or accept.
   - SalesQL has no email → you may use JobRight's envelope icon (limited daily credits) once per job (`email_source: "JobRight"`).
   - Then go back to the JobRight job page for the next person/job.
7. `save_job` (`source: "jobright"`, fit_score, full JD) → for each person with an email: `save_contact` → `send_application` (cold-email style from the common rules; hook from the JD; HR: fit; architect/lead/manager: technical detail + ask to pass the resume on). No email for anyone → `save_job` skipped `"no email found"` (with `apply_url` if seen).
8. Pace 3–5 s between page loads; read-only on LinkedIn. Close your tab at the end.
9. Summary: jobs seen, qualified, emails sent, skipped by reason, SalesQL reveals used.
