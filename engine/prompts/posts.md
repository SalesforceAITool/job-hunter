# Lane: LinkedIn posts (`source: "posts"`)

Goal: find LinkedIn **posts** hiring for ANY Salesforce role he can do, **remote only** (US, worldwide or India remote/WFH), qualify each one, and send the application by email. If the post shows an email, use it (free, best). If it does not, open the **poster's LinkedIn profile and read their email with SalesQL** (same panel as the JobRight lane).

**Cap: 5 search pages** (the five URLs below), ~45 page loads total. Apply to EVERY qualifying post — the user wants every Salesforce hiring post covered. **SalesQL cap: 15 profile reveals per run** (credits); after that, no-email posts are skipped as `"no email in post"`.

## Steps
1. Chrome setup per the common rules (`tabs_context_mcp`, then `tabs_create_mcp`; use only your tabId).
2. For each search URL in order (Posts, past 24h, latest first; the searches are broad on purpose — skip a post as `"onsite/hybrid"` unless it says remote / WFH / work from home):
   1. `https://www.linkedin.com/search/results/content/?keywords=%22salesforce%20developer%22%20hiring%20(remote%20OR%20wfh)&datePosted=%22past-24h%22&sortBy=%22date_posted%22`
   2. `https://www.linkedin.com/search/results/content/?keywords=salesforce%20(%22lead%20developer%22%20OR%20%22senior%20developer%22%20OR%20engineer%20OR%20apex%20OR%20lwc)%20hiring%20(remote%20OR%20wfh)&datePosted=%22past-24h%22&sortBy=%22date_posted%22`
   3. `https://www.linkedin.com/search/results/content/?keywords=salesforce%20(omnistudio%20OR%20cpq%20OR%20%22experience%20cloud%22%20OR%20%22data%20cloud%22%20OR%20agentforce)%20hiring%20(remote%20OR%20wfh)&datePosted=%22past-24h%22&sortBy=%22date_posted%22`
   4. `https://www.linkedin.com/search/results/content/?keywords=%22salesforce%22%20hiring%20(%22share%20your%20resume%22%20OR%20%22send%20your%20cv%22%20OR%20%22email%22)&datePosted=%22past-24h%22&sortBy=%22date_posted%22`
   5. `https://www.linkedin.com/search/results/content/?keywords=salesforce%20hiring%20(%22work%20from%20home%22%20OR%20%22pan%20india%22%20OR%20%22remote%20india%22)&datePosted=%22past-24h%22&sortBy=%22date_posted%22` (India remote roles: Indian companies replied most so far)

   `log_step` → `navigate` → run this with `javascript_tool` (it waits, scrolls to load more posts, clicks every "…more", and lists emails — tested on his Chrome):
```js
await new Promise(r=>setTimeout(r,4000));
for (let i=0;i<5;i++){ window.scrollBy(0,2500); await new Promise(r=>setTimeout(r,2000)); }
const btns=[...document.querySelectorAll('button')].filter(b=>/^\s*…?\s*(see )?more\s*$/i.test(b.innerText||''));
btns.forEach(b=>b.click()); await new Promise(r=>setTimeout(r,1500));
({moreClicked:btns.length, emails:[...new Set(document.body.innerText.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g)||[])]})
```
   Then run this with `javascript_tool` to get each post's poster and profile link (needed for SalesQL):
```js
[...document.querySelectorAll('div.feed-shared-update-v2, div[data-urn*="activity"]')].map(p=>{
  const a=p.querySelector('a.update-components-actor__meta-link, a[href*="/in/"]');
  const urn=p.getAttribute('data-urn')||(p.querySelector('[data-urn]')||{}).getAttribute?.('data-urn')||'';
  return {poster:(a?.innerText||'').split('\n')[0].trim(), profile:a?a.href.split('?')[0]:null,
          post:urn?('https://www.linkedin.com/feed/update/'+urn+'/'):null, start:(p.innerText||'').slice(0,160)};
}).filter(x=>x.poster)
```
   If it returns nothing, use `find` "author name link of the post about <role>" for the posts that need SalesQL.
   **Then call `new_posts` with every `post` link from that list** (Akshay, 11 Nov — saves tokens: posts read in an earlier run, hiring or not, are never read again). If `new` is empty → this page has nothing new: do NOT call `get_page_text`, go straight to the next search URL. Otherwise `get_page_text` and split the text into posts (each starts with "Feed post"), and work ONLY on the posts in `new` (match them by the first words / poster from the list). If the page text is a login wall or security check → stop this lane (common rules).
3. Call `screen_jobs(source: "posts", items)` once with every hiring post on the page (company, role, post link, location if stated, and the post text — first ~1200 chars). Work ONLY on its `work` list (+ `retry`); everything else is already handled or already saved as skipped. Then for each post in `work`:
   - Keep **every hiring post for a Salesforce Developer / Engineer role** (Akshay, 30 Oct: developer roles only — skip Architect, Admin, Consultant, BA, QA and Manager posts; the app skips them anyway). Ignore (don't save) "open to work" posts by candidates, bench-sales hotlists ("we have consultants available"), job-board digests and posts with no role. Save as skipped the hiring posts that fail the rules (not Salesforce, US-only/W2/clearance, onsite/hybrid, country-locked).
   - Company = the hiring company named in the post (or the poster's company if it's their own hiring). Role = the role title in the post.
   - Contact = the **email written in the post**, copied exactly; name/title = the poster (from the post header). `email_source: "post"`, `linkedin` = poster profile if known. If the post's email is a generic inbox (jobs@, careers@, hr@) use the poster's name if they are the recruiter, otherwise name "Hiring Team", title "Recruiting".
   - **No email in the post → SalesQL on the poster** (only if the post otherwise qualifies, the poster is a person — a `/in/` profile, not a company page — and the poster is a recruiter / HR / talent acquisition / hiring manager / founder / lead / architect / manager, not a plain developer or QA sharing someone else's opening):
     1. `filter_new_profiles([profile])` and `already_contacted(company, role, [], linkedin=<profile>)` first (skip if seen/contacted).
     2. `log_step` → navigate your tab to the poster's profile → wait ~6 s → `computer screenshot` → click the purple **SalesQL** tab on the right edge (about x=1552, y=76 in a 1568-wide viewport; confirm on the screenshot; "Skip tour" if a tour shows) → panel says "N emails found" → click **Reveal Info & Add** → wait 5 s → `find` "email address text in SalesQL panel".
     3. Use the **work email at the poster's company domain** (for a recruiter at a staffing firm, their staffing-firm email is fine). Ignore personal emails (gmail/yahoo/outlook...) and phone numbers. `email_source: "SalesQL"`, `linkedin` = profile URL.
     4. Order: first finish reading the whole search page and handle the posts that show an email; keep a list of the no-email posts (post text, poster, profile URL). Then do the SalesQL visits for that list one by one (save_job → save_contact → send_application right after each reveal), then go to the next search URL. Don't return to the search page in between.
     - SalesQL shows "Log in to SalesQL" or terms → stop using SalesQL for the rest of the lane, `log_step` warn "SalesQL needs login in Chrome"; do not log in or accept.
     - SalesQL finds no work email, or the poster is a company page → `save_job` (qualified, to get a `job_id`) → "When there is no email" in the common rules (find_email with the hiring company's website domain + the poster's name; then that company's careers page). Found → `save_contact` + `send_application`. Nothing → `set_job_status(job_id, "skipped", "no email found")`, `notes` = poster + profile URL.
     - SalesQL cap used up → `save_job` skipped, `skip_reason: "no email in post"`, `notes` = poster + profile URL.
   - `already_contacted(company, role, [email])` first; if contacted → skipped `"already contacted"`.
   - `save_job` with `source: "posts"`, `url` = the post link if you can get it (otherwise the search URL), `jd_text` = the full post text, `notes` = poster name and title, `posted` = e.g. "5h".
   - Qualified → `save_contact` → `send_application`. Write it in the cold-email style from the common rules (hook from the post — e.g. "Saw your post about the <role> role…" tied to something he built — then 3 "• " lines, logistics, call to action).
   - 1 contact per post (the email in the post, else the poster's SalesQL email).
4. Wait 3–5 s between the search pages. Close your tab at the end.
5. Summary: posts seen, hiring posts, emails sent (from post / from SalesQL), SalesQL reveals used, skipped by reason.
