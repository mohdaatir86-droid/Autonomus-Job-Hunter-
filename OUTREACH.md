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

### Ongoing international research

The private `/api/outreach/program` settings persist in the same database. The dashboard starts with evidenced noon, talabat and Careem employer domains. This opt-in programme makes at most one Prospeo company search per local day under the existing shared lookup caps. It rotates the explicitly listed employers, dates cached shortlists and never guesses emails, enriches a person automatically, approves a batch or sends email. Reveal and verify relevant contacts using the existing controls. Reviewed, unsuppressed contacts at listed employers can become draft-only introductions, with one initial company draft across campaign history. Previously contacted companies are not repeatedly mailed. Add evidenced target companies to broaden research; the programme does not expand its scope silently.

The programme runs from the existing outreach tick (including authenticated discovery wakes), independently of the send-only pause. A persistent daily reservation and database lease prevent overlapping or restarted workers from repeating provider attempts. Failed or interrupted calls remain visible and are not blindly retried. Pausing daily research stops future research; Global stop disables research and revokes queued sending approvals. **I got a job** persists the stop until the owner explicitly restarts. An offer or interview is never treated as proof the user has secured a job.

New profile/PDF versions continue to invalidate email approvals. Previously prepared drafts retain their old attachment hash and remain blocked until the current PDF is attached and the exact content is reviewed again. The ongoing programme does not grant permission for conversational replies or remove finite batch approval.

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


## Prospeo alternative

Set `PROSPEO_API_KEY` only in the existing Render service's secure environment settings; no key is stored in the browser or repository. Choose Prospeo in Recruiter outreach. A missing key, plan restriction, exhausted credits or rate limit is a blocked action, not a successful connection. No account or paid plan is created automatically. The free plan has limited API access and may restrict non-business registration addresses; eligibility remains account-specific.

Search uses the documented `/search-person` endpoint, one page for an exact employer website and hiring titles. It can charge one credit per returned page. Only names, hiring titles and matched current employers are shortlisted. Revealing calls `/enrich-person` with verified-email-only and mobile enrichment disabled, with an additional credit confirmation. Person ID, current company, hiring title and employer email domain must match before storage; contacts still require responsibility review before approval.

Searches and reveals reserve the shared database-backed daily/monthly lookup budget (defaults 5/day, 50/month), including Apollo reveals. Prospeo search cache and namespaced reservations persist across restarts. Repeated or concurrent requests never repeat an ambiguous provider operation automatically. A cached search is explicitly dated and must be checked for current employment. Provider-side deduplication can lower the actual charge; local limits conservatively count attempted lookups. Search results never contain emails or phone numbers. No change to sending approvals, global pause, Gmail OAuth or PDF requirements.

Tests: `python -m pytest -q tests/test_prospeo.py`; the fixture browser flow uses mocked Prospeo and Gmail only. Live access remains unverified until a key and eligible account are configured. Never spend provider credits merely to test.
