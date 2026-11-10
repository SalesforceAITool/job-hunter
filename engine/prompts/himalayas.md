# Lane: Remote boards — find the right people and email them

The app already collected remote Salesforce jobs from **Himalayas**, **Jobicy**, **Hacker News "Who is hiring?" / "Seeking freelancer?" threads** (Y Combinator) and **company career pages** (Greenhouse / Lever / Ashby). Python already checked: Salesforce title, remote, open to India or worldwide (or unclear: then check the JD), not handled before, and the hard skip rules. The jobs are **saved**; the list is in the Run context: `job_id — source — company — role — job URL — apply link — email in post`.

For each listed job: get **1 HR/recruiter + 1 leader** at that company with a work email and send the application. **Don't call `save_job`** (the job exists); use its `job_id`.

**Cap:** the listed jobs only (max 12). Pace 3–5 s between page loads. Read-only on LinkedIn. Be frugal: no screenshots unless a click target is unclear; read pages with `get_page_text`/`find`.

## Steps
1. If any job needs LinkedIn (all except "email in post" ones): Chrome setup per the common rules (signed-in browser, own tab).
2. For each listed job:
   1. `mcp__jobhunt__get_job(job_id)` → JD text. Only if the JD shows a blocker Python missed (US work authorization / citizenship / W2 / onsite / a country lock other than India / another language) → `set_job_status(job_id, "skipped", reason)` and move on.
   2. **Email in post** (Hacker News posts often say "email jobs@company.com"): that address IS the company's published hiring contact. `already_contacted(company, role, [email])` → `save_contact(job_id, name: "<Company> hiring team", title: "Hiring team", email, email_source: "post")` → `send_application`. Skip LinkedIn for this job unless the post names a person you can also find (optional, 1 leader max).
   3. Otherwise find the company on LinkedIn: `https://www.linkedin.com/search/results/people/?keywords=<company>%20(talent%20OR%20recruiter%20OR%20%22hiring%22)` — first page only; for a leader `<company> salesforce (manager OR architect OR lead OR director)`. Keep people whose **current** company is this company: at most 1 recruiter/HR + 1 Salesforce leader (manager / architect / practice lead / director; small company: founder/CTO). Never plain developers or QA.
   4. `filter_new_profiles` with their profile URLs → open only the `new` ones. SalesQL panel → **Reveal Info & Add** → work email at the company's domain. No work email → `record_profile_lookup(url)`.
   5. `already_contacted(company, role, [emails], linkedin=<profile>)` → `save_contact(job_id, …, email_source: "SalesQL", linkedin)` → `send_application(job_id, contact_id, subject, body, resume)`. Email in the cold-email style from the common rules (hook from the JD — you may mention where you saw it: Himalayas / Jobicy / Hacker News / their careers page).
   6. Nobody with an email → "When there is no email" in the common rules (find_email with the company domain, then the company's careers page). Still nothing → `set_job_status(job_id, "skipped", "no email found")`. The Portal lane still prefills its Greenhouse/Lever/Ashby form if the apply link is one of those.
3. Close your tab. Summary: jobs worked, emails sent, skipped with reasons, SalesQL reveals used.
