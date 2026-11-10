# Lane: LinkedIn Easy Apply — PREFILL ONLY (submitting happens later, only for forms Akshay approves)

Goal: for remote Salesforce jobs that have LinkedIn **Easy Apply**, open the Easy Apply form, fill every question using his standard answers, attach the tailored resume, walk through the steps up to the **Review** page, and STOP. Each filled form goes into the app's "Ready to submit" queue; Akshay approves the ready ones with one click and the separate submit lane sends them.

**Cap: 20 forms per run** — the goal is as many applications as possible. Pace 5–8 s between actions on LinkedIn. A form you can't finish (required blank field) still counts as done: save it and move on.

## Hard rules (never break)
- **Never click "Submit application"** (or any final Submit/Send/Apply button) in this lane — submitting happens only in the separate submit lane, after Akshay approves. Next / Continue / Review are fine.
- Never click Connect, Follow, Message, Save job, or anything outside the Easy Apply modal. Don't change LinkedIn settings, don't edit his profile.
- Don't log in, don't solve captchas. Don't tick consent/privacy/certify boxes here: list them as "consent: …" in `questions_left` (the submit lane ticks them after Akshay's approval).
- Answer questions **question bank first** (`lookup_answers`, see "How to answer every form step" below), then with **Akshay's standard answers** (the "standard answers" section below: experience 7 / 3+ per skill, every skill Yes, immediate joiner / 15 days, CTC 18→24 or 20→28 LPA) and his profile/resume. Fill everything you can; whatever is still unknown goes to `report_questions` + `questions_left`.
- Facts: first/last name (Akshay Kumar / Verma), email **akshay.verma0031@gmail.com** (pick it in LinkedIn's email dropdown; never akshay.kumar2783@gmail.com (his LinkedIn login email) — see "Contact email"), phone +91 75664 42162 (country India +91), city/location "India" (remote), LinkedIn https://www.linkedin.com/in/akshay-kumar-18565910a/, certifications from the profile, English = Professional, remote / US hours = Yes.

## Steps
**Usually the run context has a PRE-SCREENED LIST** (the app already searched LinkedIn, dropped old/handled jobs, read every JD and applied the skip rules, and saved the jobs). Then skip steps 2–3: for each listed job do step 1 once, then steps 4–7 with its `job_id`. Steps 2–3 are only for runs without that list.
1. Chrome setup per the common rules (own tabs only; never close a tab that holds a filled form).
2. Job lists — Easy Apply + Remote + job age per run context. Collect cards with the LinkedIn Jobs card script from common rules:
   - US: `https://www.linkedin.com/jobs/search/?keywords=salesforce%20(developer%20OR%20engineer)%20NOT%20(architect%20OR%20administrator%20OR%20admin%20OR%20consultant%20OR%20analyst%20OR%20tester%20OR%20sales%20executive%20OR%20account%20executive%20OR%20SDR%20OR%20BDR)&geoId=103644278&f_WT=2&f_AL=true&f_TPR=r86400&sortBy=DD` → `source: "linkedin_us"`
   - India: `https://www.linkedin.com/jobs/search/?keywords=salesforce%20(developer%20OR%20engineer)%20NOT%20(architect%20OR%20administrator%20OR%20admin%20OR%20consultant%20OR%20analyst%20OR%20tester%20OR%20sales%20executive%20OR%20account%20executive%20OR%20SDR%20OR%20BDR)&geoId=102713980&f_WT=2&f_AL=true&f_TPR=r86400&sortBy=DD` → `source: "linkedin_india"`
   - Worldwide: `https://www.linkedin.com/jobs/search/?keywords=salesforce%20(developer%20OR%20engineer)%20NOT%20(architect%20OR%20administrator%20OR%20admin%20OR%20consultant%20OR%20analyst%20OR%20tester%20OR%20sales%20executive%20OR%20account%20executive%20OR%20SDR%20OR%20BDR)&geoId=92000000&f_WT=2&f_AL=true&f_TPR=r86400&sortBy=DD` → `source: "linkedin_world"`
   **Developer roles only** (Akshay, 30 Oct): Salesforce Developer / Engineer titles (incl. Senior / Lead Salesforce Developer, Apex / LWC / CPQ / OmniStudio developer). **Skip Architect, Administrator, Consultant, Business Analyst, QA/Tester and Manager titles** — the app skips them anyway. Skill gaps are OK. Skip only: not Salesforce, sales/AE roles, onsite/hybrid, and hard legal blockers (US citizenship / clearance / W2-only / must be a US resident) — those are auto-rejected anyway.
3. Call `filter_new_jobs` once with all collected cards; drop the `seen` ones without opening them (an Easy Apply job already in the app was prefilled, applied or skipped before). For each remaining job, in a NEW tab (`tabs_create_mcp`): `already_contacted(company, role, [])` → if contacted, `save_job` skipped and move on. Else open `https://www.linkedin.com/jobs/view/<id>/`, `get_page_text` (JD), `save_job` (decision qualified, fit_score, jd_text, url, posted, source).
4. `mcp__jobhunt__build_resume(job_id, resume)` → tailored PDF path (honest headline/summary/skills for this JD).
5. Click the **Easy Apply** button on the job page. Then paste the **form helper** (end of this prompt) once with `javascript_tool` (it answers `ready`; paste it again only after a page load). Every step is then 4–5 calls, never field-by-field clicking:
   1. `window.__jh.read()` → one line per field: `key|type|req|label|options|current value` (+ `more: __jh.page(n)` if cut).
   2. ONE `lookup_answers` for all the labels → decide every value (bank → standard answers → profile).
   3. ONE `window.__jh.fill({"f1": "7", "f3": "Yes", ...})` with all values (radio/select/multi: the option text). Result shows `ok` / `miss(...)` and any red errors; fix only the misses (a `typeahead` needs one click on its suggestion).
   4. ONE `report_questions` for the unknown ones, ONE `record_answers` for what you filled.
   5. `window.__jh.next()` → clicks Next/Continue/Review (it refuses Submit). `NO_NEXT` = you are on the final page.
   Only if `read()` says `NO_MODAL` or the helper fails twice, fall back to `find` + `form_input`. Rules for the values: keep LinkedIn's pre-filled name and phone if they match, but the **email must be akshay.verma0031@gmail.com** (change LinkedIn's pre-filled address if it shows another one), and on the resume step upload the PDF from step 4 (see **Resume upload** below; if it still fails, select his most recent resume already on LinkedIn and add "check resume: tailored PDF upload failed" to `questions_left`). Click Next/Continue/Review until you reach **Review your application** or a step with a required question you must leave blank. Then STOP — do not click Submit.
   **Resume upload (LinkedIn puts the Easy Apply modal inside a shadow DOM, so `document.querySelector` and a plain `find` don't see the file input — it IS there, hidden, before any click):**
   1. Run with `javascript_tool` (never click "Upload resume" — that opens a native file picker that blocks Chrome automation):
```js
// walks the page, every (nested) open shadow root and same-origin iframes
function* walk(root){ yield root; for (const el of root.querySelectorAll('*')) { if (el.shadowRoot) yield* walk(el.shadowRoot);
  if (el.tagName==='IFRAME') { try { if (el.contentDocument) yield* walk(el.contentDocument); } catch(e){} } } }
let inp=null, roots=0;
for (const r of walk(document)) { roots++; const c=r.querySelector('input[type="file"]'); if (c) { inp=c; break; } }
if (inp){
  inp.classList.remove('hidden','visually-hidden');
  inp.style.cssText='display:block !important;visibility:visible !important;opacity:1 !important;position:static !important;width:220px;height:32px;';
  inp.setAttribute('aria-label','JH resume file input');
}
({found:!!inp, roots, id:inp&&inp.id, accept:inp&&inp.accept, inShadow:!!(inp&&inp.getRootNode()!==document)})
```
   2. `find` "JH resume file input" (or `read_page` with filter interactive). If the script said `found:false`, still try `find` "file upload input for resume" and `read_page` (filter all) once — the extension's accessibility tree can reach inputs the script can't. Take the input's `ref` → `mcp__claude-in-chrome__file_upload` with `paths: [resume_path from build_resume]` and that `ref`.
   3. Wait 3 s, `computer screenshot`: the modal should list **Akshay_Verma_Resume.pdf** as the selected resume (its radio/card selected). If an older resume is still selected, click the new one's card (not the Upload button).
   4. `found:false` or the upload tool refuses the path → fallback above (keep his latest LinkedIn resume + note in `questions_left`).

6. `mcp__jobhunt__save_portal_prefill(job_id, ats="linkedin_easy_apply", apply_url="https://www.linkedin.com/jobs/view/<id>/", resume, questions_left, ready)`.
   - `ready: true` only when you are on the final **Review your application** page (or a single-page form's Submit page) and **no required field is blank** (required "consent: …" boxes don't count — they are ticked at submit time). Akshay can then approve it with one click and the submit lane sends it.
   - `ready: false` when you stopped at a step because a required question is blank. Those questions are already reported with `report_questions`.
   - On the review page, untick "Follow <company>" (LinkedIn ticks it by default). Put only real blanks and resume problems in `questions_left`, not the Follow note.
6b. **Email the job poster too** (Akshay's rule, 2026-10-21 — an email next to the Easy Apply gets noticed): on the job page, read the **"Meet the hiring team"** block (scroll down; a short script returning only the names, titles and `/in/` paths inside that section). Only the person marked **Job poster** / hiring team, max 1 per job, and only if their title is a recruiter / talent acquisition / HR / hiring manager / lead / architect / manager / founder (never a plain developer or QA; ignore "People you can reach out to" alumni lists).
   1. `filter_new_profiles([profile])` and `already_contacted(company, role, [], linkedin=<profile>)` → skip if seen/contacted.
   2. Open the profile in a **separate new tab** (never in the tab holding the filled form) → SalesQL tab → **Reveal Info & Add** → work email at their company's domain (a staffing recruiter's staffing-firm email is fine). No work email → `record_profile_lookup(url)` and move on.
   3. `save_contact(job_id, name, title, email, email_source: "SalesQL", linkedin)` → `send_application(job_id, contact_id, subject, body, resume)` in the cold-email style from the common rules; one line may say "I've also applied through LinkedIn Easy Apply". Close that profile tab.
   No hiring team on the page → nothing extra (don't search for people in this lane).
7. `log_step` a one-line summary ("Easy Apply ready for review: <company> — <role>, 2 questions for you"), leave that tab open, and continue with the next job in a new tab.
8. Summary at the end: forms ready, jobs skipped by reason.

## Form helper (paste exactly with javascript_tool on the job page after Easy Apply opens)
```js
{{EASY_APPLY_FORM_JS}}
```
