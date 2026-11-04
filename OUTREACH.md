# Recruiter outreach and redesigned workspace

The existing Render app now serves the frontend and API together. No separate Vercel project or frontend build is required. `dashboard.html` loads `static/app.css` and `static/app.js`; same-origin authentication and CSRF protections are retained.

## Workspace

- **Job matches:** saved-market summary, real source metrics, filters, pagination, application drafts and a direct **Find recruiter** action carrying the selected role into outreach.
- **Recruiter outreach:** company-specific Apollo search, work-email enrichment, contact verification, editable drafts, explicit approval, Gmail sending and sending rules.
- **Applications:** existing application drafts and editable tracking statuses.
- **Connections:** current provider configuration and source reports.
- **Profile & rules:** private sign-in, profile/resume, search preferences, self-alert email testing and draft preparation rules.

Mobile layouts turn table rows into cards. Empty/error states explain next actions; fixture counts and sample recruiters are never included in production data.

## Activate Apollo

Add `APOLLO_API_KEY` in the existing Render service's secure environment settings, then save/redeploy. The key must be eligible for People API Search (Apollo documents master-key/account eligibility requirements). Never paste it into chat, source files or the browser.

Search requires an employer's actual domain (for example `meesho.com`) and finds recruiter/HR roles, 10 leads per request. Search itself does not return email addresses. **Reveal work email — uses credits** invokes one enrichment only after confirmation. Saved Apollo contacts are reused on subsequent reveals rather than re-enriched. Personal email, phone and waterfall enrichment are not requested. Do not assume an email pattern or substitute an invented email when enrichment returns none.

Official contracts checked during implementation:

- https://docs.apollo.io/reference/people-api-search
- https://docs.apollo.io/reference/people-enrichment

Apollo's documented domain match can include previous employers. A provider's `verified` email status does not verify current hiring responsibility. Contacts remain unreviewed until the owner checks the business address, current employer and recruiting role against a professional source and records a verification note.

Other providers' legitimately obtained business contacts can be entered manually with a professional source URL. There is no arbitrary website scraper, bulk personal-email harvesting or inferred-address generator.

## Authorize Gmail sending

The existing Google OAuth client ID and secret are reused. In Google Auth Platform → Data access, include these scopes for the personal application:

- `https://www.googleapis.com/auth/gmail.readonly` for the existing job-alert import.
- `https://www.googleapis.com/auth/gmail.send` for reviewed application emails.

Keep the existing callback `https://autonomous-job-hunter-docker.onrender.com/api/gmail/callback`. Add the connecting account as a test user if the Google app is in Testing.

In the portal, sign into the private profile and click **Enable Gmail sending** in Recruiter outreach. Reconnect the same Gmail address saved in the candidate profile and approve Google's requested permissions. A previous read-only connection cannot send mail. Actual granted scopes are stored; absent/reduced permission leaves sending disabled. Google testing-mode token expiry and production verification requirements continue to apply.

Resend remains the separate self-alert delivery provider. Its `onboarding@resend.dev` sender is not used for recruiter outreach. No Gmail password or app password is needed.

## Review and sending

1. Choose a real discovered role and employer domain; search Apollo or enter a sourced contact.
2. Reveal one work email if needed and verify current responsibility.
3. Prepare an email. The template uses the candidate's actual profile/skills; it does not invent achievements or claim a resume attachment.
4. Review/edit the exact sender, recipient, subject and body. Saving edits clears approval. Confirm recipient relevance and approve the saved email.
5. Click **Send approved email**, or explicitly enable **Automatically send emails I approve**. The scheduled worker processes approved drafts once per minute while `ENABLE_SCHEDULER=true`. A paused scheduler still allows manual sending. No draft is auto-approved.

The default is automatic sending off and five attempts per UTC day; the owner can choose 1–20. UTC daily limits reset at 05:30 IST. Per-recipient reservations prevent concurrent sends across jobs/processes; a sent or uncertain delivery has a seven-day cooldown. Sent, in-flight and ambiguous drafts are not automatically retried. Definite provider rejections require fresh approval before a retry; they still count toward the daily attempt limit.

`sent` means Gmail returned a message ID, not independently confirmed inbox delivery. The UI labels it **Accepted by Gmail**. A timeout/5xx/missing receipt becomes `delivery_unknown`; check Gmail Sent rather than blindly retrying.

Every draft includes an opt-out link. GET displays a confirmation without changing state; POST suppresses that recipient and cancels queued emails. Suppression also survives re-importing the same normalized address. Already in-flight provider requests cannot be recalled. There are no automated follow-ups or reply/consent classification in this version.

The candidate is the portal's existing single private profile (candidate ID 1). All contact, queue and policy APIs require the owner key/session and CSRF for session mutations. Only the opaque-token unsubscribe confirmation is public. Other profile/contact changes invalidate prior approval before dispatch.

The original resume PDF is not retained by the existing upload pipeline, so this version sends profile-based text without attachments. The draft accurately offers to share the resume on request.

## Validation and operations

```bash
ENABLE_SCHEDULER=false python -m pytest -q
python tests/browser_smoke.py
```

The browser smoke test launches its own local server and temporary SQLite database, blocks external browser requests and mocks Apollo/Gmail. It never contacts real recruiters or consumes credits. Set `CHROMIUM_EXECUTABLE_PATH` if Chromium is not discoverable; otherwise install the Playwright Chromium runtime. Screenshots go to `/tmp/job-hunter-browser-evidence` by default (`BROWSER_SMOKE_OUTPUT` overrides it).

PostgreSQL integration/concurrency tests require `TEST_POSTGRES_URL` pointing to a disposable database ending in `_test`, never production. The tests mutate fixture profile/job/queue data and reset the fixture daily counter. CI supplies a dedicated PostgreSQL service.

Startup applies migration `002_outreach.sql` for granted Gmail scopes and creates additive outreach tables. Existing profile/job data are preserved. No actual Apollo enrichment or Gmail delivery was performed during development; activation requires the owner's secure API key, Google consent and review of real recipients.
