# Autonomous Job Hunter

A deployable MVP for discovering and scoring **operations leadership** vacancies tailored to a QSR / Q-commerce / dark-store / multi-unit operations profile (India, Gulf and Canada).

This repository is intentionally independent of Product Hunter.

## Target profile (current defaults)

| Field | Value |
|-------|--------|
| **Roles** | Senior Cluster Manager, Cluster Manager, City / Area Operations Manager, Head of Operations, Warehouse / Fulfilment / Dark Store Manager, Supply Chain / Last-Mile Operations Manager, QSR Multi-Unit Manager |
| **Countries** | India, United Arab Emirates, Saudi Arabia, Qatar, Kuwait, Oman, Canada |
| **Experience** | 8+ years multi-unit operations, dark stores, Q-commerce, QSR expansion |
| **Key strengths scored** | Store launches, SLA, shrinkage control, large team leadership (450+), 3PL, SOPs, inventory |

These defaults are editable through `POST /api/settings` and the dashboard profile section.

## Current capabilities

- Pulls public job listings from **Arbeitnow**, **Remotive**, and **Jobicy** APIs.
- Optional company career-board integrations using public **Greenhouse** and **Lever** endpoints.
- Normalizes job records and deduplicates them by job URL.
- Scores jobs against operations, fulfilment, warehouse, supply chain, quick-commerce and cluster-management roles (weighted for multi-unit / QSR / dark-store experience).
- Filters and ranks by target country and match score.
- Stores job records, first/last seen timestamps, run history, and manually maintained application status in SQLite when the configured database path is persistent.
- Provides a responsive dashboard, JSON API, keyword search, country/source coverage, and interactive API documentation at `/docs`.
- Runs discovery every 15 minutes by default when the internal scheduler is enabled. Production hands discovery to n8n every 6 hours; draft preparation runs every 30 minutes only when the private profile is complete and the user turns the policy on.
- **Application preparation**: after the private profile has a name, email and PDF resume, creates role-specific drafts for review. **Submission stays disabled.**
- Optional form help is limited to HTTPS Greenhouse and Lever pages, fills supported visible fields, and always leaves review and submission to the user.
- The dashboard's private setup card lets you connect with the portal access key, save your profile and upload a PDF resume. Personal details and resume text are sent to the application backend; they are never committed to this repository or sent to n8n.

## Run locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

On Windows, activate with `.venv\\Scripts\\activate`.

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## API endpoints

- `GET /health` — service health
- `GET /api/stats` — totals, country/source breakdown, latest run
- `GET /api/jobs?min_score=35&limit=100` — scored jobs
- `POST /api/hunt` — run discovery immediately from the portal
- `POST /api/integrations/n8n/hunt` — authenticated discovery trigger for n8n
- `GET /api/sources` — source configuration and limitations
- `GET /api/settings` and `POST /api/settings` — supported preferences
- `POST /api/jobs/{id}/status` — set `saved`, `applied_manually`, `interview`, `rejected`, `offer`, or `not_relevant`
- `GET/POST /api/profile` — candidate profile (requires `PORTAL_ACCESS_TOKEN`)
- `POST /api/profile/resume` — upload a text-based PDF resume (max 8 MB)
- `POST /api/automation/prepare` and `POST /api/auto-apply/queue` — prepare drafts for review; require a complete private profile
- `GET/POST /api/automation/settings` — save the match threshold and draft limit
- `POST /api/profile/resume` — upload PDF resume
- `POST /api/applications/draft/{job_id}` — prepare cover letter + guidance for a job
- `GET /api/applications` — list prepared drafts

Example manual run:

```bash
curl -X POST http://127.0.0.1:8000/api/hunt
```

Optional company board slugs can be set as comma-separated environment variables:

- `GREENHOUSE_BOARDS=company_slug,another_slug`
- `LEVER_SITES=company_slug,another_slug`

Private profile endpoints require:

```bash
PORTAL_ACCESS_TOKEN=your-long-random-secret
```

The n8n discovery endpoint uses a separate `N8N_AUTOMATION_TOKEN` of at least 32 characters. Store it in Render and in the n8n HTTP templated custom-auth credential that sends `Authorization: Bearer <token>`; never reuse the portal token. The endpoint runs discovery, normalization, deduplication and scoring. In production, n8n owns the six-hour discovery schedule; set `ENABLE_INTERNAL_HUNT_SCHEDULER=false` on the web service to avoid duplicate hunts. The backend's separate 30-minute draft schedule stays idle until the user enables it and the profile has a name, email and PDF resume.

## Render deployment

The active production service is `autonomous-job-hunter-docker` on Render, using the Dockerfile and PostgreSQL when `DATABASE_URL` is configured. Do not create a second service from the legacy `render.yaml` without reviewing its settings first.

1. Open the existing service in Render and manage configuration under **Environment**.
2. Set a strong, unique `PORTAL_ACCESS_TOKEN` directly in Render; never commit it or paste it into chat.
3. Configure provider keys (for example `SERPAPI_API_KEY`) only in Render. If a key returns HTTP 401, replace it with a newly issued valid key from the provider.
4. Complete Gmail OAuth with the callback URL `https://autonomous-job-hunter-docker.onrender.com/api/gmail/callback` after setting the Google OAuth client ID and secret.
5. After deployment, check `/health`, `/health/db`, `/health/browser`, `/api/sources`, `/api/stats`, and `/api/gmail/status`.

If your Render plan does not support persistent disks, use managed PostgreSQL before relying on long-term tracking.

## Important limits and safety

- **Application submission is disabled.** The service finds, scores and tracks vacancies and prepares drafts; it does not log into job sites or submit applications.
- Greenhouse/Lever form help only fills supported visible fields; LinkedIn, Naukri and Shine are discovery-only. No CAPTCHA/OTP bypass, fabricated answers, or automatic acceptance of legal declarations is implemented.
- A future application workflow must use official APIs or explicitly permitted methods and pause for human input on assessments, consent, work authorization, salary commitments, or legal questions.
- Public job APIs do not cover every employer, country, or job board. Results depend on provider availability and data coverage.
- The current match score is a keyword heuristic, not a prediction of hiring success.
- Keep candidate contact information private. Do not commit personal resumes or secrets to the repository.

## Next milestones

1. Stronger India-focused sources (authorized Naukri / LinkedIn routes or job-alert email parsing).
2. Automated tests, provider health metrics, retry/backoff, and alerting.
3. Persistent storage verification on the live Render service.
4. Ranking history snapshots and meaningful freshness metrics.
5. Optional AI-assisted resume PDF generation (still human-reviewed before any apply).
6. Permitted application preparation/submission only after each source's rules are validated.

## Changelog (recent)

- Candidate profile defaults blank to avoid publishing personal information in this public repository.
- Scoring tuned for QSR multi-unit, dark-store, cluster and expansion roles.
- Target markets include India, UAE, Saudi Arabia, Qatar, Kuwait, Oman and Canada.
- Richer cover-letter generation that references real achievements when relevant.
- Default search window widened to 3 days; India salary floor set to ₹20 LPA.
