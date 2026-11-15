# Autonomous Job Hunter — release continuation

## Current project

- Repository: `mohdaatir86-droid/Autonomus-Job-Hunter-`
- Canonical workspace: https://autonomous-job-hunter-docker.onrender.com
- Existing Render workspace: `tea-dbc7ulrrjlhs73b4vjg0`
- Existing Render service: `srv-dbfqlirl550s738v1drg`
- Existing Vercel project: `prj_DQaTsQnXsKoBdHx4GJQt6xuWwJcc`
- Vercel team: `team_0Y8wp3QBt9XCneVXL4Zi0h5P`
- Vercel entry: https://autonomous-job-hunter-ui.vercel.app

## Established baseline (15 November 2026)

PR #40 was merged as `0b8b1530dc3b7fc8fb0ef4af6d6c0233c80f2b9e`. Its Render
deploy `dep-dbs8imdckfvc73db5veg` was live. GitHub PostgreSQL and browser CI passed
on that merge. Scheduled discovery runs on the same commit succeeded. The signed-in
portal reported Gmail alerts working and the latest scheduled hunt fetched 465
listings and saved 32 new records. A Gulf-only refresh returned 17 current-profile
roles, 0 new and 17 updated, preserving their original dates and application
statuses. These are observed snapshots, not current-count guarantees.

The Vercel production alias was still serving an obsolete static UI with its
private API rewrite pointed at the old native-Python service. The tracked
`vercel.json` now routes that compatibility link to the current canonical workspace.
This deliberately keeps Google/Gmail OAuth state and cookies on the working
origin and makes future UI updates follow the Render release automatically.
Deploy this config through the EXISTING Vercel project, not a new one.

## Changes in the current release

- Direct Noon operations/logistics import using published cards and the careers
  page's public job-detail JSON. Match IDs/titles, explicit countries, active state,
  original posting dates, closing dates and profile experience. Bound page size,
  requests and concurrency; never access candidate or application APIs.
- Source catalog: 14 configured direct imports, 7 indexed boards and 1 external
  link (Kitopi). These are configurations, not successful-provider counts.
- Gulf refresh reloads source health and readiness alongside the list/counts,
  and its changed browser resource has a new version.
- Vercel canonical routing removes stale copied UI and the incorrect API backend.

## Release gates

Run the full local suite, PostgreSQL/browser CI, JS syntax and diff checks.
Then merge the exact tested PR head, monitor the existing Render deploy to live,
and deploy/verify the existing Vercel project. Confirm private routes remain
locked, Gmail/profile readiness, a Gulf-only refresh, relevant Noon imports and
original application links. The Vercel entry must resolve to the canonical
workspace, including source/profile deep links.

Local browser launch is restricted in this executor (Chromium singleton socket /
ptrace denied). Use the GitHub CI browser fixture result and the attached live
browser for production verification; do not represent fixture emails as real sends.

## Remaining platform limits

Kitopi's official careers link still leads to a 404 Lever board; keep its status
external until an employer-authorised source is verified. Some RSS/global feeds
are bounded and partial. SerpApi has intermittent per-query timeouts/quota limits.
Google testing-mode grants may require later reconnects. Applications remain
manual on original employer pages; opening a link never marks it applied.
Render free-service sleep pauses in-process preparation/research timers; GitHub
discovery wakes the service every six hours. Do not promise continuous background
operation while the service sleeps.

## Preserve boundaries

Keep LinkedIn Autonomous and other projects separate. Preserve authentication,
CSRF, durable PostgreSQL, private PDFs, application history, reviewed outreach,
duplicate/suppression checks, reply/opt-out stops and shared limits. Do not send
recruiter email, activate campaigns, spend enrichment credits or submit job forms
while verifying this release. Check live state before changing policies.
