# Lane: Agencies — get Akshay on the bench of staffing agencies that place Salesforce contractors with US clients

The app picked the agencies (Run context: `job_id — agency — region — type — website — known people/emails`). Each one already has a saved "pitch job" (`job_id`); use it for every contact and email. **Never call `save_job`.**

Akshay sells himself as an **offshore C2C contractor**: India-based, full US hours, contract through his company **Trysyntx IT Solution Pvt. Ltd.** (GST-registered, LUT, monthly USD invoices), rate around **USD 30/hour (flexible)**, available within 15 days. He has no US work authorization: never suggest W2 or US presence.

**Cap:** the listed agencies only, at most **2 people per agency**. Pace 3–5 s between page loads. Read-only on LinkedIn.

## Steps
1. Chrome setup per the common rules (signed-in browser, own tab).
2. For each agency:
   1. **Known emails listed** (from his agency sheet, found on their LinkedIn profiles): use them first. `already_contacted(agency, role, [email])` → `save_contact(job_id, name, title, email, email_source: "sheet")` → `send_application`. If both known people are emailed, skip LinkedIn for this agency.
   2. Otherwise find the right people on LinkedIn — the people who actually place Salesforce contractors:
      - `https://www.linkedin.com/search/results/people/?keywords=<agency>%20salesforce%20(recruiter%20OR%20consultant%20OR%20contract)`
      - if nobody fits: `<agency> (recruiter OR "talent acquisition" OR "account manager" OR "delivery")`; at a small agency the founder / director / head of delivery counts.
      First page only. Keep people whose **current** company is this agency. Prefer titles with Salesforce / CRM / contract / US IT / bench / delivery. Never plain developers or QA.
   3. `filter_new_profiles` → open only `new` profiles → SalesQL → **Reveal Info & Add** → work email at the agency's domain. No work email → `record_profile_lookup(url)`.
   4. `already_contacted(agency, role, [emails], linkedin=<profile>)` → `save_contact(job_id, name, title, email, email_source: "SalesQL", linkedin)` → `send_application(job_id, contact_id, subject, body, resume)`.
   5. Nobody with an email → "When there is no email" in the common rules (find_email with the agency's website domain; then a recruitment/careers inbox on the agency's own site — for an agency a `recruitment@` / `careers@` / `jobs@` inbox is a good target). Still nothing → `set_job_status(job_id, "skipped", "no email found")` and move on (the app retries this agency in 30 days).
3. Close your tab. Summary: agencies worked, people emailed, agencies with no contact, SalesQL reveals used.

## The pitch email (not about one job — about their open and future reqs)
Use the cold-email structure from the common rules, adapted for an agency (120–160 words, plain text):
- **Hook about THEM:** e.g. "You place Salesforce talent with US clients, so I'll keep this short: I'd like to be on your bench for remote Salesforce contract roles." If you saw something real (their recent Salesforce post / req, their Salesforce desk), mention it.
- **3 "• " lines** with real facts: Agentforce in production (Nissan, ASDA) · Salesforce CPQ + SAP integration (Amkor) · 7+ years, ~20 projects, 11 certifications (Platform Developer II, Data Cloud, AI Specialist) — pick the 3 that fit their focus (CPQ desk → CPQ first).
- **Logistics line:** "India-based, full US hours, C2C through my company Trysyntx IT Solution Pvt. Ltd. (USD invoices), around USD 30/hour and flexible, available within 15 days."
  For an agency in **India** (region India: Indian consultancies that staff foreign client projects): "Based in India, remote, happy to work US/UK hours for your client projects, on contract through my company Trysyntx IT Solution Pvt. Ltd. or as you prefer, available within 15 days." No USD rate for India agencies.
- **CTA:** "If you have Salesforce reqs open with US clients, I'd be glad to jump on a quick call — or simply keep my resume on file."
- **Close:** "Either way, thanks for reading — resume attached." Then the standard signature.
- **Subject:** e.g. `Salesforce developer for your US clients — C2C, available now` or `Agentforce + CPQ contractor for your Salesforce reqs`.
- The resume (`resume` object): general Salesforce developer headline; `project_order` starts with `nissan, asda, amkor`.
