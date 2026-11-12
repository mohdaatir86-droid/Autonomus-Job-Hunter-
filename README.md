# Autonomous Job Hunter

A private operations-leadership job workspace for India and the Gulf. It discovers and ranks vacancies, imports Gmail job-alert links, prepares resume-grounded drafts, and tracks applications. Applications are reviewed and submitted by the owner on employer sites.

## Production

- Repository: `mohdaatir86-droid/Autonomus-Job-Hunter-`
- Active Render service: `autonomous-job-hunter-docker`
- Dashboard: https://autonomous-job-hunter-docker.onrender.com
- Runtime: the checked-in Dockerfile; PostgreSQL via `DATABASE_URL`
- Discovery schedule: `.github/workflows/discovery.yml`, every six hours with short-lived GitHub OIDC identity. No owner access key is stored in Actions.
- Legacy n8n integration remains optional; run only one external discovery schedule.

The obsolete native-Python deployment Blueprint and migration-era browser setup guide were removed. Do not create a duplicate service. Product Hunter, LinkedIn Autonomous and the crypto project have independent repositories and are outside this project's deployment.

## Features and limits

- Public feeds: Arbeitnow, Remotive, Jobicy, Greenhouse and Lever employer boards.
- Optional Google Jobs through SerpApi; provider quota and coverage limits apply.
- Read-only Gmail alert imports from supported boards, with HTML links preserved, bounded pagination and posting ages anchored to email receipt time. Unknown posting dates do not become fresh merely because an alert was imported.
- Scoring for operations, cluster, city, warehouse, fulfilment, supply-chain and QSR leadership. A score is a keyword heuristic, not a hiring probability.
- Private profile, PDF resume, saved search preferences, drafts and application tracking.
- Optional AI assistance and Resend self-alert emails.
- Optional Apollo recruiter discovery and separately approved Gmail outreach; no messages are sent without review and approval.
- Greenhouse/Lever browser assistance is a dry run. Automatic job-form submission is disabled in code.
- Job-board results are aggregated discovery, not direct authenticated Naukri/LinkedIn/Shine/Foundit integrations.

## Local setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
# Set a random PORTAL_ACCESS_TOKEN of at least 32 characters securely.
ENABLE_SCHEDULER=false .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Use SQLite locally when `DATABASE_URL` is unset. Use persistent PostgreSQL for production. Environment files are not loaded automatically: configure the process environment or use uvicorn's `--env-file` option with an ignored local file.

## Authentication

Workspace APIs, job links, saved status, source reports, settings and diagnostics require the owner's private access key or a verified Google session. Public `/health` exposes only minimal liveness metadata. Never commit credentials, resumes, personal profile data or downloaded OAuth JSON files.

For Google sign-in, use a Web OAuth client with this JavaScript origin:

```text
https://autonomous-job-hunter-docker.onrender.com
```

Register both redirect URIs on that same client:

```text
https://autonomous-job-hunter-docker.onrender.com/api/auth/google/session
https://autonomous-job-hunter-docker.onrender.com/api/gmail/callback
```

Set `GOOGLE_CLIENT_ID` and the matching `GOOGLE_CLIENT_SECRET` securely in Render; set `GOOGLE_REDIRECT_URI` to the Gmail callback above. Google portal login and Gmail mailbox consent are separate steps. Connect the same Gmail email saved in the private profile. Revoked Gmail consent is reported as requiring reconnection rather than remaining falsely connected.

## Scheduling

The GitHub discovery workflow runs at minute 17 every six hours UTC. It also runs once when its implementation is merged to main and can be run manually from GitHub Actions. The backend verifies GitHub's signature, issuer, audience, expiry, immutable repository/owner IDs, exact main-branch workflow and allowed trigger. Forks, pull requests, other workflows and wrong audiences are rejected.

Keep `ENABLE_INTERNAL_HUNT_SCHEDULER=false` for this deployment. `ENABLE_SCHEDULER=true` may still enable the separate draft-preparation and reviewed-outreach workers. In-process workers run only while the Render service is awake; a sleeping free service cannot promise continuous draft preparation. GitHub scheduled runs can be delayed and public-repository schedules can be disabled after 60 days without activity; check Actions when runs stop.

The optional n8n route uses a distinct 32+ character `N8N_AUTOMATION_TOKEN`; it never accepts the owner key. Do not run n8n and GitHub discovery schedules simultaneously.

## Validation and operations

```bash
ENABLE_SCHEDULER=false .venv/bin/python -m pytest -q
.venv/bin/python -m pip check
node --check static/app.js
```

CI provisions disposable PostgreSQL and runs the browser fixture workflow. Tests mock mail, enrichment and job providers; passing tests do not prove delivery or provider quota availability.

See [RUNBOOK.md](RUNBOOK.md) for secure configuration, deployment and Gmail setup; [OUTREACH.md](OUTREACH.md) for recruiter review/approval; [VERIFICATION.md](VERIFICATION.md) for audit evidence and limitations.
