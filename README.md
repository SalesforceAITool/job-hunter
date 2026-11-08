# Job Hunter

Job Hunter finds new remote Salesforce jobs on LinkedIn and applies to the ones that fit, using Claude and a separate Chrome browser kept only for this work.

## How it works

1. A separate Chrome (the "Job Hunter Chrome") on the Job Hunter laptop stays signed in to LinkedIn and Gmail, with the Claude in Chrome extension connected.
2. A Claude scheduled task runs every 2 hours (9 AM–11 PM IST). Each run:
   - uses only the Job Hunter Chrome (never any other browser),
   - checks the right LinkedIn account is signed in,
   - searches LinkedIn for remote Salesforce jobs posted in the last 24 hours,
   - screens every job against [RULES.md](RULES.md),
   - applies with LinkedIn Easy Apply only when every rule passes,
   - sends a short summary to the phone.
3. If the laptop is off or the Job Hunter Chrome is closed, that run is skipped. The next run picks up again.

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
