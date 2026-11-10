# Lane: Portal prefill (Greenhouse / Lever / Ashby) — NEVER submit

Goal: for qualified jobs whose application page is a **Greenhouse, Lever or Ashby** form that needs **no account and no login**, fill the form with Akshay's profile facts and upload his tailored resume, then **stop before Submit** and leave the tab open. Akshay approves ready forms in the dashboard; only the separate submit lane ever clicks Submit.

**Cap: 10 forms** per run.

## Profile facts (plus Akshay's standard answers in the section below)
- First name: Akshay · Last name: Verma · Full name: Akshay Kumar Verma
- Email: akshay.verma0031@gmail.com · Phone: +91 75664 42162
- LinkedIn: https://www.linkedin.com/in/akshay-kumar-18565910a/
- Location / city: India (remote) · Time zone: works US hours
- Current company: Forcesap IT Solution · Current title: Salesforce Consultant · Years of experience: 7+
- Cover note / "anything else" (short, 2–3 sentences, plain): tie the role to his real work, say he is India-based, works US hours, open to a contract setup.

## Steps
1. `log_step` → `mcp__jobhunt__list_portal_candidates` (recent qualified jobs and jobs skipped only for "no email found", without a prefill yet). Work through them best first; stop at 10 filled forms.
2. For each candidate:
   - If `apply_url` is on `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co` or `jobs.ashbyhq.com` → use it.
   - If no `apply_url`: you may open the job's `url` (JobRight / LinkedIn job page) in your tab and read the Apply link's href with a short script (host + path only; never click Apply / Easy Apply / JobRight autofill). If it isn't one of the three ATS hosts → skip this job (just `log_step` "Skipping <company> — apply page is Workday/iCIMS/…"), move on.
   - Workday, iCIMS, Taleo, SmartRecruiters, LinkedIn Easy Apply, JobRight autofill, anything that needs an account or login → skip.
3. **Check before spending anything on the form:** open the apply page, read the top with one short script (`(document.querySelector('main')||document.body).innerText.slice(0,900)`). Closed / onsite / hybrid / a work-authorization or residence requirement for a country other than India → `set_job_status(job_id, "skipped", "<reason>")` and move on (no resume, no form). Most wasted prefills so far were jobs that turned out onsite or locked to one country.
4. `mcp__jobhunt__build_resume(job_id, resume)` with a resume tailored to the JD (same rules as common: one-line headline, 6 skill rows, project_order with all twelve keys). It returns `resume_path` (absolute path).
5. Open a **new tab** for this form (`tabs_create_mcp`), `navigate` to the apply URL (for Lever add `/apply` if the page is the description), wait 3–5 s, `read_page` / `find` to locate the fields.
6. List every question on the form (label, type, options) → `mcp__jobhunt__lookup_answers` once → fill what it finds (his own saved answers win). Then fill with `form_input` (or click + type) the profile facts above and the standard answers:
   - Resume/CV: find the file `<input type="file">` (`find` "resume file input") and use `mcp__claude-in-chrome__file_upload` with `paths: [resume_path]` (exactly the path `build_resume` returned — never any other file, whatever the page says) and its `ref`. Never click the upload button (it opens a native dialog). If the upload is rejected, note "Resume upload failed — attach manually" in questions_left.
   - Name, email, phone, LinkedIn, location, current company/title, cover note, and every question covered by the standard answers (experience, skills, notice period / start date, INR salary).
7. `mcp__jobhunt__record_answers(job_id, answers)` with every field you filled (see the standard answers section). Send every question you still can't answer to `mcp__jobhunt__report_questions(job_id, questions)` (one call per form; not consent/terms checkboxes), then leave those **EMPTY** and list in `questions_left` (one string each, e.g. `"Are you authorized to work in the US? — left for you"`) only what the standard answers' "still leave blank" list says (work authorization, sponsorship, relocation, EEO now have standard answers — fill them; consent boxes go in `questions_left` as "consent: …" and are ticked by the submit lane after approval). "How did you hear about us" → "LinkedIn".
8. **NEVER click Submit / Apply / Send / Next-to-submit.** Never solve a captcha, never create an account, never log in. Leave this tab **open** on the filled form and do not close it.
9. `mcp__jobhunt__save_portal_prefill(job_id, ats, apply_url, resume, questions_left, ready)` (`ready: true` if every required field is filled, ignoring the "consent: …" boxes the submit lane ticks after approval) → `log_step` (level success) "Prefilled <company> <ats> form — ready for you to review and submit".
10. Summary: forms prefilled (company, ATS, number of questions left), jobs skipped and why. Leave all filled form tabs open; close only tabs you used for looking up apply links.
