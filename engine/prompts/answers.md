# Akshay's standard answers for application forms (Easy Apply and portal forms)

## How to answer every form step (question bank first)
1. On each step, list every question/field you see: the label exactly as shown, its type (text, number, yes_no, choice, multi, date, textarea, checkbox) and its options.
2. Call `mcp__jobhunt__lookup_answers` **once for the whole step** with that list.
   - `found: true` → fill `answer` (for a select/radio/range use `option`; if the field has options and `option` is null, no option fits his answer → treat the question as unknown and report it). These are answers Akshay typed in his Questions tab (or stable standard answers learned from earlier forms); they win over everything below, including the "still leave blank" list.
   - `status: "mine"` → he wants that one left blank: leave it, add it to `questions_left`.
   - `candidates` → near matches; use one only if it asks exactly the same thing (same skill, same country, same currency, same time unit). Otherwise treat as unknown.
3. Everything not found → answer from the standard answers below and his profile/resume.
4. What is still unanswered (including the "still leave blank" list, except consent/terms/privacy checkboxes) → **one** `mcp__jobhunt__report_questions(job_id, questions)` call per step with label, type, options and required. Leave those fields blank, add them to `questions_left`, and **keep going** — never wait for Akshay. Next run, his answers come back from `lookup_answers`.
5. After filling the step, call `mcp__jobhunt__record_answers(job_id, answers)` **once** with EVERY field you filled or kept on that step (question, answer, field_type, options, source = bank / standard / profile / resume / linkedin / ai — be honest about the source: only standard/profile/resume answers are learned into the bank; `per_job: true` for salary/CTC chosen from the JD, dates and free text). This is Akshay's answer log (Excel) and teaches the bank the stable answers. Skip name/email/phone fields.
6. If a required blank field stops "Next", save the prefill as described in your lane and move straight on to the next job.

## Contact email (always)
- **Email on every application is akshay.verma0031@gmail.com** (Akshay, 30 Oct). Never akshay.kumar2783@gmail.com (his LinkedIn login email) or any other address, even if LinkedIn or the form pre-fills it. In LinkedIn Easy Apply the email is a dropdown of the addresses on his LinkedIn account: select
  akshay.verma0031@gmail.com there. If that address is not in the dropdown, do not submit: put
  "email: add akshay.verma0031@gmail.com to your LinkedIn account (Settings > Sign in & security > Email addresses)" in
  `questions_left` and save with `ready: false`. In portal forms type akshay.verma0031@gmail.com in every email field.

These are Akshay's own answers, given by him for every application form. Fill them in directly. Do **not** leave these questions for him and do **not** add them to `questions_left`.

For **form questions about Salesforce-ecosystem skills**, these answers take priority over the "skills he does NOT have" list in the common rules — Akshay confirmed on 2026-10-19 that he has worked on all Salesforce clouds/tools: Marketing Cloud / Pardot / Account Engagement, Financial Services Cloud, Field Service, CG Cloud, nCino, Vlocity / Communications Cloud, MuleSoft, Tableau CRM / CRM Analytics, Conga/Apttus CLM, Veeva CRM, Regrello. **Non-Salesforce** technologies (ServiceNow, Workday, SAP development, Oracle CPQ, Snowflake, Java/.NET backend, etc.) are NOT covered: answer those honestly from his resume (No / 0) or leave them blank and report them. The email text and the resume PDF still follow the common rules.

## Experience
- **Total / overall experience**, "years of Salesforce experience", "years of Salesforce development" → **7** (text: "7 years").
- **Years with any specific Salesforce skill, cloud or tool** (Apex, LWC, Aura, Visualforce, SOQL, Flows, Sales/Service/Experience/Health/Commerce/Data/Marketing/Financial Services/Field Service Cloud, CPQ, OmniStudio, Vlocity, Agentforce, Einstein, integrations/REST/SOAP, MuleSoft, Copado/Gearset/DevOps/CI-CD, Git, JavaScript, Heroku, Data Loader, admin/config, etc.) → **3** (text: "3+ years"). If the field has ranges, pick the range that contains 3+ (e.g. "3–5 years"). Exception: Apex, LWC, SOQL, Flows, Salesforce administration/configuration/development as a whole → **7**. **3 and 7 are minimums:** if LinkedIn or the form already shows a higher number (from his saved LinkedIn answers), keep the higher number — never lower a prefilled value.
- **"Do you have experience with X?" / "Are you familiar with X?" / "Have you worked on X?"** for any Salesforce skill, cloud, tool or related tech → **Yes**.
- Self-rating sliders/scales for these skills → a strong value (e.g. 4 of 5, 8 of 10, "Advanced").

## Notice period and start date
- Notice period dropdown/radio → **Immediately / Immediate joiner** (or the smallest option such as "0–15 days", "15 days or less").
- Notice period as a number of days → **15**. As text → "Immediate joiner (can start within 15 days)".
- "Earliest start date" (date field) → the date **15 days from today** (see Run context for today's date).
- "Are you serving notice?" → No. "Can you join immediately?" → Yes.

## Salary (INR, lakhs per annum)
Pick the pair by the experience the JD asks for:
- JD's required/minimum experience is **5 years or less** (e.g. "3–5 years", "5+ years", "4 years") → **current CTC 18 LPA, expected CTC 24 LPA**.
- Otherwise (JD asks 6+ years, senior/lead/architect with more than 5 years, or doesn't say) → **current CTC 20 LPA, expected CTC 28 LPA**.
Write it in the unit the field asks: "LPA"/lakhs → 18 / 24 (or 20 / 28); rupees per year → 1800000 / 2400000 (or 2000000 / 2800000); free text → "18 LPA" etc.
**Non-INR salary or rate fields** (Akshay decided 2026-10-21: competitive offshore rates, the goal is maximum interview calls):
- Hourly / contract rate → **USD 30/hour** (range fields: 28–35; EUR 28/hour, GBP 24/hour).
- Monthly → **USD 5,000**.
- Annual / full-time expected salary in USD → **60,000** (range fields: 55,000–65,000). EUR 55,000, GBP 47,000, CAD 80,000, AUD 90,000, SGD 80,000.
- Dropdown of bands → the band that contains this number; if every band is higher, pick the lowest band. Free text → "USD 30/hour on C2C (flexible)" or "USD 60,000 per year (flexible, remote from India)".
- "Current salary" in a foreign currency → leave it blank and list it in `questions_left`.

## Other questions — answer from his profile and resume
- Trailhead / Trailblazer profile link → https://www.salesforce.com/trailblazer/akumar2783 (fill it whenever a form asks for a Trailhead link or certification verification link).
- Location / city / "Where are you based?" → **India** (Akshay decided 2026-10-21). City autocomplete: type "India" and pick the suggestion that is just "India"; if only cities are offered, leave it blank and list it in `questions_left`.
- Name, email, phone, LinkedIn, current company (Forcesap IT Solution, since Oct 2023), current title (Salesforce Consultant), certifications (the 11 in the common rules), location "India" (remote) — from the common rules / profile.
- Comfortable with remote work / US hours / contract or full-time → **Yes**.
- Authorized to work in **India** → Yes. English proficiency → Professional / Fluent.
- Any other question you can answer from his resume/profile → answer it. No need to leave those for review.

## Work authorization, sponsorship, relocation (Akshay decided 2026-10-20: answer truthfully, never leave blank)
- "Are you (legally) authorized to work in the **US** / UK / EU / Canada / Australia / the country of this job?" (job outside India) → **No**. For **India** → Yes.
- "Will you now or in the future require (visa) sponsorship?" for a job outside India → **Yes**. For an India job → No.
- Free-text work-status questions → "I'm based in India and not authorized to work in the US. I work remotely from India as a contractor (C2C via my company, Trysyntx IT Solution Pvt. Ltd.), so no sponsorship is needed for remote work; I would need sponsorship to work in the US."
- Citizenship → Indian. Security clearance → No / None.
- "Are you able to work in <country> without relocation / are you located in <country>?" → **No**. "Willing to relocate?" yes/no → **No**; free text → "Open to relocation only with visa sponsorship; otherwise remote from India in US hours."
- W2 / C2C / employment type: "C2C / contract" when offered; never claim W2 eligibility.

## EEO / voluntary self-identification (Akshay decided 2026-10-20)
- Gender, race/ethnicity, veteran, disability, sexual orientation, pronouns, age range → the "decline" option: **"I don't wish to answer" / "Decline to self-identify" / "Prefer not to say"**. If a required field has no decline option, leave it blank and list it in `questions_left`.

## Consent / certification / privacy-notice checkboxes (Akshay decided 2026-10-20)
- In the **prefill** lanes (easy_apply, portal): never tick them. List each required one in `questions_left` as `"consent: <checkbox label> (ticked at submit after your approval)"`. These do NOT make the form not-ready: a form whose only blanks are such consent boxes is `ready: true`.
- In the **submit** lane only, for an application Akshay approved: tick exactly the required "I certify the information is true" / "I acknowledge the privacy notice" / "I agree to the processing of my data for this application" boxes, then submit. Never tick marketing / newsletter / "keep me updated" / talent-community boxes.

## Still leave blank, report with `report_questions`, and list in `questions_left`
- Postal address / ZIP fields (never invent an address).
- Anything you truly cannot answer from his profile/resume.
