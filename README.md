# Job Hunter

Job Hunter finds new remote Salesforce jobs on LinkedIn and applies to the ones that fit, using Claude and a separate Chrome browser kept only for this work.

## How it works

1. A separate Chrome (the "Job Hunter Chrome") on the Job Hunter laptop stays signed in to LinkedIn and Gmail, with the Claude in Chrome extension connected.
2. The Job Hunter app starts by itself when the laptop starts and keeps the laptop awake. Every 10 minutes it runs one LinkedIn cycle: Easy Apply jobs + hiring posts (other lanes run only when ticked and Run is clicked).
3. Only new work goes to the AI (saves tokens):
   - Before each cycle the app reads LinkedIn's public Easy Apply list without AI and remembers every job it has ever seen. Jobs already seen, applied, skipped or saved are never sent to the AI again. Nothing new means no Easy Apply AI run.
   - Every LinkedIn post the posts lane has read is remembered too; a search page with nothing new is skipped.
   - If the public list can't be read, the AI searches LinkedIn itself, as before, so nothing is missed.
   - Easy Apply forms that stopped only because of the old application email are tried again once.
4. Each job is screened against [RULES.md](RULES.md) (no architect roles, at most 7 years asked), the form is filled with the tailored resume, and it is submitted automatically.
5. If the laptop is off, the next cycle after it starts looks back over the whole day.

## Files

| File | What it is |
|---|---|
| `RULES.md` | The search, screening and apply rules every run follows |

## Setup on a new laptop

1. Create a separate Chrome profile just for Job Hunter.
2. In it, sign in to Gmail and LinkedIn, and install the Claude in Chrome extension (sign in with the same Claude account).
3. Keep that Chrome open whenever the laptop is on. Optional: Chrome → `chrome://settings/manageProfile` → turn on "Create desktop shortcut", then copy that shortcut into the Windows Startup folder (`Win+R` → `shell:startup`) so it opens at sign-in.
4. Point the Claude scheduled task at that browser.

## Privacy

Personal details (salary, phone, email, account IDs) are **not** stored in this public repo. They live only in the private Claude scheduled task.
