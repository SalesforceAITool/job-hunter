# Lane: LinkedIn Jobs — US remote, India remote, Worldwide remote

Goal: Salesforce technical jobs posted in the last 24h on LinkedIn Jobs → read each JD → find the hiring-team person → email via SalesQL → send the application.

**Cap: 20 jobs total** — about 8 US, 8 India, 4 worldwide; if one list is thin, the others fill the rest.

## Steps
**Usually the run context has a PRE-SCREENED LIST** (the app already searched LinkedIn, dropped old/handled jobs, read every JD, applied the skip rules and saved the jobs). Then skip steps 2–4 and 6's `save_job`: for each listed job open its URL, read only the "Meet the hiring team" section, and do step 5 + `save_contact`/`send_application` with its `job_id`. Steps 2–4 are only for runs without that list.
1. Chrome setup per the common rules (own tab only).
2. Lists (do US first):
   - US remote (`source: "linkedin_us"`): `https://www.linkedin.com/jobs/search/?keywords=%22salesforce%22%20(developer%20OR%20engineer)%20NOT%20architect&geoId=103644278&f_WT=2&f_TPR=r86400&sortBy=R`
   - India remote (`source: "linkedin_india"`): `https://www.linkedin.com/jobs/search/?keywords=%22salesforce%22%20(developer%20OR%20engineer)%20NOT%20architect&geoId=102713980&f_WT=2&f_TPR=r86400&sortBy=R`
   - Worldwide remote (`source: "linkedin_world"`): `https://www.linkedin.com/jobs/search/?keywords=%22salesforce%22%20(developer%20OR%20engineer)%20NOT%20architect&geoId=92000000&f_WT=2&f_TPR=r86400&sortBy=R`
   Collect cards with `javascript_tool` (they lazy-load; tested):
```js
await new Promise(r=>setTimeout(r,4000));
const L=[...document.querySelectorAll('li[data-occludable-job-id]')];
for (const c of L){ c.scrollIntoView(); await new Promise(r=>setTimeout(r,250)); }
[...new Set(L.map(c=>c.getAttribute('data-occludable-job-id')+' | '+(c.innerText||'').replace(/\n+/g,' | ').slice(0,160)))]
```
   (Result is cut at ~1000 chars — if needed run it again with `.slice(6,12)` on the final array, or shorten each row to 110 chars.)
3. Title filter: **Salesforce Developer / Engineer roles only** (Akshay, 30 Oct; incl. Senior/Lead developer, Apex/LWC/CPQ/OmniStudio developer). Skip Architect, Admin, Consultant, BA, QA and Manager titles. Drop sales/AE, marketing, QA-only, PM-only, junior/fresher, non-Salesforce, and anything not remote. Drop country-locked remote outside the US and India (Canada/UK/EU/Australia/Middle East included) (e.g. "Ukraine (Remote)", "Brazil (Remote)" usually mean must live there) unless the JD says worldwide/contractors welcome — save those as skipped `"remote only for <country>"`. Titles that are obviously not Salesforce-technical need not be saved.
4. Call `filter_new_jobs` once with all kept cards (company, role, `https://www.linkedin.com/jobs/view/<id>/`). Never open the `seen` ones (they don't count toward the cap); `retry` ones get only the hiring-team + SalesQL step. For each `new` job (until the cap): `already_contacted(company, role, [])` → if contacted, save skipped and move on. Otherwise `log_step` → open `https://www.linkedin.com/jobs/view/<id>/` → `get_page_text` = the JD. Apply the skip rules. Note **"Meet the hiring team"** names/titles; get their profile paths with a short script returning only `a[href*="/in/"]` paths inside that section (e.g. `/in/jane-doe-123/`), never full URLs with query strings. If the JD text contains a Greenhouse/Lever/Ashby link put it in `apply_url` (never click Apply / Easy Apply).
5. Contacts (max 2 per job: the hiring-team recruiter/poster, plus an architect/lead/manager if shown): open each profile `https://www.linkedin.com/in/<slug>/`, wait ~6 s, screenshot, click the purple **SalesQL** tab on the right edge (~x=1552, y=76; confirm on the screenshot; "Skip tour" if a tour shows), click **Reveal Info & Add**, wait 5 s, `find` "email address text in SalesQL panel", take the **work email at the company domain** (ignore personal emails and phone numbers). `email_source: "SalesQL"`.
   - "Log in to SalesQL" / terms → stop SalesQL for the lane, warn via `log_step`, don't log in.
   - SalesQL shows no work email, or no hiring team is shown → "When there is no email" in the common rules (find_email with the company domain + the hiring person's name, then the company's careers page). Only if that finds nothing: `save_job` skipped `"no email found"` with `notes: "needs contact lookup: <company>, HR + Salesforce architect"`.
6. `save_job` (correct `source`, fit_score, full JD, `url` = `https://www.linkedin.com/jobs/view/<id>/`, `posted`) → `save_contact` → `send_application` (cold-email style from the common rules; hook from the JD; HR: fit; leader: technical detail + ask to pass it on).
7. Pace 3–5 s between page loads. Read only — never click Connect, Follow, Message, Save, Apply or Easy Apply. Stop on login/captcha. Close your tab at the end.
8. Summary: US vs worldwide jobs seen, qualified, emails sent, skipped by reason.
