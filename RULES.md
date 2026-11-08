# Job Hunter rules

Every run follows these rules. A job is applied to only when **all** screening rules pass.

## 1. Browser

- Use only the Job Hunter Chrome (the separate Chrome kept for this work). Never use any other connected browser.
- If the Job Hunter Chrome is not connected (laptop off, asleep, or Chrome closed), skip the run.
- Open LinkedIn and confirm the expected account is signed in. If LinkedIn asks for a password or shows another account, stop and report it. Never type a password.

## 2. Search

LinkedIn Jobs, Easy Apply, posted in the last 24 hours.

- URL: `https://www.linkedin.com/jobs/search-results/?keywords=<keywords>&geoId=<geoId>&f_TPR=r86400&f_AL=true&f_WT=2&origin=JOB_SEARCH_PAGE_LOCATION_HISTORY`
  - `f_TPR=r86400` = last 24 hours (`r604800` = last week)
  - `f_AL=true` = Easy Apply
  - `f_WT=2` = remote (the new search page may ignore it, so still check each job)
  - `origin=JOB_SEARCH_PAGE_LOCATION_HISTORY` is needed, or the new search page keeps the last location
- Keywords: `"salesforce"`, `salesforce developer`, `salesforce omnistudio`, `agentforce`, `salesforce consultant`, `salesforce technical`
- Locations (`geoId`): India `102713980`, United States `103644278`, United Kingdom `101165590`, UAE `104305776`, Germany `101282230`, Switzerland `106693272`
- Job cards are elements with `componentkey="job-card-component-ref-<jobId>"`. Scroll the results list to load all cards before reading them.
- Read the full description at `https://www.linkedin.com/jobs/view/<jobId>/` (click "… more" to expand it).
- Skip cards already marked **Applied**.

## 3. Screening

Apply only if **all** are true:

- Fully remote **and** open to someone working from India (India listed, worldwide/anywhere, or an India-remote posting). "Remote" in a US/UK/EU/UAE listing does not mean India is allowed.
- No requirement for citizenship, visa, green card, W2, security clearance, local residency or local payroll outside India.
- Not hybrid or on-site.
- Not sales-only, QA-only or admin-only.
- A Salesforce development/consulting role that fits the candidate's skills.
- The candidate meets the minimum experience asked.
- Any **mandatory** certification or language (e.g. German C1) is one the candidate has.
- Not a repost of a job already applied to.

## 4. Easy Apply

- Answer only from the candidate's facts (kept privately in the scheduled task). Never invent anything.
- Use the application email the candidate chose; keep the resume LinkedIn preselects.
- If a required question can't be answered truthfully (years in an unlisted product, exact dates, salary in another currency, etc.), don't guess: close the form, choose **Save**, and list the job for the candidate to finish.
- Review the final page before submitting. Count an application only after LinkedIn confirms it ("Application sent" / job shows "Applied").
- If LinkedIn shows a CAPTCHA, security check or application limit, stop and report it. Never try to get around it.
- At most 15 applications per run. Close every tab opened during the run.

## 5. Summary (Roman Hinglish, sent to phone)

- Apply kiya: company – title – link
- Dekhna hai: company – title – link – kaunsa sawal
- Skip kiye: count + main reasons
- Koi problem (browser band, LinkedIn logout, captcha/limit)

Never say an application was sent unless LinkedIn confirmed it.
