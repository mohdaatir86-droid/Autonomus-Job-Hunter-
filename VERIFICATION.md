# Full audit and cleanup — 13 November 2026

## Scope

Reviewed the Job Hunter backend, source ingestion/scoring, Gmail OAuth, private workspace APIs, application state, recruiter approval/sending, browser dry runs, dashboard, tests, Docker configuration and operating documentation. The separate Product Hunter, LinkedIn Autonomous and crypto repositories are independent projects.

## Confirmed fixes

- Owner authentication now protects job links, tracking status, preferences, source reports, readiness and database diagnostics. Public health reports only liveness metadata; it no longer queries the private database/profile.
- Dashboard data loads after authentication, handles Google-session deep links, clears all candidate content on logout/expired authentication, and rejects stale in-flight responses. Latest job filters win; coverage uses full aggregates rather than a capped list.
- Gmail consent revocation cannot remain falsely connected. Failed refreshes never reuse expired access tokens. Configuration errors, revoked grants and transient outages are distinguished without leaking credentials. Callback defaults target the active service; cancelled consent returns to Connections.
- Google credential verification runs outside the async event loop. Malformed OAuth payloads and Unicode authentication input fail safely.
- Email-relative posting ages are anchored to each message's receipt time. Per-link city/age context avoids borrowing evidence from another listing. Bounded recent-mail pagination reports incomplete coverage; parse failures remain retryable, and message acknowledgements commit with job persistence.
- Greenhouse ranks relevant jobs before caps and does not treat edits as new postings. Distinct requisitions sharing a title/employer/city are preserved; stable IDs/canonical URLs deduplicate genuine repeats. Provider cancellation no longer leaks unawaited coroutine objects.
- Atomic application-attempt creation and browser claims prevent duplicate workers. Browser setup/navigation failures become retryable and close partial resources. Unknown required controls block form readiness.
- Conditional outreach edit/approval writes cannot overwrite in-flight sends. Sender changes invalidate approvals while preserving unsent draft edits. Concurrent draft creation handles uniqueness conflicts.
- GitHub Actions replaces the expired n8n discovery schedule using signed short-lived OIDC identities scoped to immutable repository/owner IDs, main branch, exact workflow, audience, issuer and time bounds. It cannot access private profile APIs. Ambiguous POST failures are not automatically retried.
- Docker uses exec to forward shutdown signals to Uvicorn.

## Cleanup

Removed the obsolete native-Python render.yaml Blueprint and BROWSER_WORKER_SETUP.md migration guide, which encouraged duplicate services and described unsupported submission flows. Consolidated supported setup into README.md/RUNBOOK.md; replaced historical verification claims with this report. CI definitions/operator documents are excluded from the production image. Runtime modules, tests, SQL migrations and the migration utility have current uses and were retained.

## Local validation

203 tests passed; two disposable-PostgreSQL tests skipped because TEST_POSTGRES_URL was not configured locally. Python dependency consistency, JavaScript syntax and git whitespace checks passed. New tests use fixture mail/providers and generated test signing keys; no recruiter emails or job applications were sent by these tests.

The expanded browser smoke could not launch Chromium in this executor because its singleton socket/ptrace operations were denied. GitHub CI provisions PostgreSQL and runs the browser smoke; its result must be checked before deployment. CI/deployment results are reported in the pull request and session rather than inferred from local tests.

## Operational limits

- SerpApi last returned HTTP 429. Code cannot create provider quota; coverage remains degraded until quota is available or the owner changes providers.
- GitHub schedules can be delayed; public-repository schedules can stop after 60 days of inactivity. Check Actions when discovery stops. Keep the expired n8n workflow inactive to avoid duplicate hunts if its subscription is restored.
- Render free-service sleep pauses in-process draft/outreach timers. Discovery wakes the service externally; uninterrupted draft preparation requires an always-running service.
- Gmail scope consent and testing-mode token expiry remain controlled by Google. Reconnect when the private Connections panel reports revoked consent.
- Browser assistance is a temporary dry run; automatic job-form submission remains disabled. Live Apollo/Gmail outreach still requires recipient verification and explicit email approval.
- The legacy native-Python Render service was not deleted: available metadata does not establish that it has no unique credentials/data or remaining clients. No unrelated repository was removed.
