# Recruiter outreach campaigns

The existing FastAPI/Render service serves the private dashboard and outreach API. No separate site, database service or unrelated repository was created.

## What is implemented

- Employer-domain Apollo search for recruitment/HR and operations hiring roles. A searched person must be explicitly shortlisted for a work-email reveal. Personal email, phone and waterfall enrichment are not requested. Current employer, hiring responsibility and recipient timezone require owner verification with a professional source.
- Enrichment reservations are database-backed and serialized. Defaults allow 5 lookups/day and 50/month in the configured timezone. Attempts, missing results and uncertain provider requests remain recorded; they are not automatically repeated. Successful contacts are cached. These caps count lookup reservations, not an assertion of actual Apollo billing. Check account credits in Apollo.
- Original PDF storage in the private database, with SHA-256 integrity, version, filename, timestamp and size. The upload pipeline still validates a text-based PDF up to 8 MB. Resume bytes are excluded from profile/preview JSON. An authenticated, no-store download is available under `/api/outreach/resume`.
- Exact PDF MIME attachments for initial emails and follow-ups. Missing, stale or corrupt attachments block sending. Existing extracted resume text alone cannot be reconstructed into the original PDF: upload the current PDF once after this upgrade.
- Candidate-authored experience sentences are ranked against the vacancy and copied into concise emails. Facts and metrics are never generated from company names or inferred from job requirements. All previews are editable.
- Vacancy applications and future-opportunity introductions are distinct campaigns. Introductions do not create synthetic vacancies or imply an opening exists.
- “Prepare up to 5 matched contacts” uses current job-search preferences and existing verified contacts, and reports employers needing contact research. It spends no Apollo credits and sends no emails.
- A finite campaign preview includes the current profile, PDF metadata, saved targeting, exact contacts, all initial emails, exact follow-ups and rules. Activation requires the owner to confirm the profile and exact preview hash. It never authorizes unbounded new contacts. Changed profile, resume, targeting, contacts, emails or rules require renewed review. Automatic generation/sending to arbitrary future Apollo contacts is not enabled.
- First contact per company, duplicate recipient reservations, a shared daily attempt cap, verified recipient business hours, database leases and atomic claims coordinate workers. Defaults: global pause on, auto-send-approved off, 5 total attempts/day, Asia/Kolkata daily reset, recipient-local weekdays 09:00–18:00, at most two follow-ups after 5 then 7 working days.
- Gmail receipts retain message/thread IDs. Follow-ups preserve subject, `In-Reply-To`, `References` and Gmail thread ID. Any incoming reply stops follow-ups, including out-of-office and uncertain replies. Related delivery notices are also checked conservatively. Bounce/opt-out suppresses the contact across re-imports. A failed Gmail reply check prevents follow-up dispatch.
- Replies are categorized as interview, resume requested, rejection, out-of-office, bounce, opt-out or needs review. Suggested responses are drafts for manual review; conversational replies are not auto-sent. Classification is heuristic, not a guarantee.
- Global pause stops new sends. Global stop also clears queued approvals and pauses campaigns; already in-flight Gmail requests cannot be recalled. Sent and uncertain requests are never automatically retried. Interrupted `sending` rows become `delivery_unknown` after ten minutes.

## Secure setup

1. Add `APOLLO_API_KEY` in the existing Render service's secure environment settings. Apollo account/API permissions are required. Do not paste credentials in chat or commit them.
2. Keep Google OAuth client ID/secret and the existing Gmail callback. In Google Auth Platform, enable `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/gmail.send`. Use the portal's **Enable Gmail sending** to grant these scopes to the same address as the saved profile. Read-only consent cannot send; send-only consent cannot monitor replies.
3. Confirm persistent storage. PostgreSQL stores the PDF and queue together, including with an external managed PostgreSQL provider. For SQLite on Render, configure `DATABASE_PATH` on a real attached persistent disk and only then set `OUTREACH_SQLITE_PERSISTENT=true`. Sending on Render is blocked without PostgreSQL or this explicit disk confirmation. Do not set the flag for an ephemeral filesystem.
4. Verify the profile and upload the latest text-based PDF. An existing DOCX or extracted text is not an attachable PDF.
5. Verify real contacts and recipient timezone, create a draft campaign, prepare 1–5 initial emails, inspect the exact preview including follow-ups and activate the batch. Then unpause globally and enable automatic sending of approved emails if desired. Manual initial sending also requires approval and respects all caps/hours.

## Scheduling and recovery

The web scheduler runs one outreach tick per minute while awake. The existing authenticated GitHub discovery route also schedules a bounded background outreach tick after discovery; its OIDC authorization scope is unchanged, and no public sending route was added. Keep the obsolete n8n discovery workflow inactive. Database leases prevent overlapping outreach passes.

Free Render sleep still delays processing until the next wake. Due times, campaign approvals, reservations, replies, suppressions and PDF bytes persist in the configured durable database. A GitHub wake is six-hourly, so free hosting does not guarantee minute-accurate delivery or immediate reply synchronization. Follow-ups recheck Gmail before dispatch after waking. An always-running service is needed for continuous processing; no paid plan is selected automatically.

A successful send means **Accepted by Gmail**, not confirmed inbox delivery. Timeouts, cancellation after a send claim, server errors or missing receipts require checking Gmail Sent, not resending blindly.

## Verification

```
ENABLE_SCHEDULER=false python -m pytest -q
node --check static/app.js
node --check static/outreach_campaigns.js
python tests/browser_smoke.py
```

All test emails, recipients, Apollo responses and Gmail messages are fixtures. PostgreSQL tests require a disposable `TEST_POSTGRES_URL` ending in `_test`; never use production. Browser smoke covers the campaign preview and iPhone widths. API keys, granted live Gmail scopes, actual Apollo eligibility and real recipient previews must be checked privately; tests do not establish them.
