# Autonomous Job Hunter

A deployable MVP for discovering and scoring **operations leadership** vacancies tailored to a QSR / Q-commerce / dark-store / multi-unit operations profile (India + Gulf).

This repository is intentionally independent of Product Hunter.

## Target profile (current defaults)

| Field | Value |
|-------|--------|
| **Roles** | Senior Cluster Manager, Cluster Manager, City / Area Operations Manager, Head of Operations, Warehouse / Fulfilment / Dark Store Manager, Supply Chain / Last-Mile Operations Manager, QSR Multi-Unit Manager |
| **Countries** | India (priority), United Arab Emirates, Saudi Arabia, Qatar, Kuwait, Oman |
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
- Runs scheduled discovery every 15 minutes by default.
- **Application preparation**: generates role-specific cover letter + keyword checklist + resume guidance from the candidate profile. **Submission is disabled.**
- Candidate profile is pre-seeded with the primary candidate details (editable, protected by `PORTAL_ACCESS_TOKEN`).

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
- `POST /api/hunt` — run discovery immediately
- `GET /api/sources` — source configuration and limitations
- `GET /api/settings` and `POST /api/settings` — supported preferences
- `POST /api/jobs/{id}/status` — set `saved`, `applied_manually`, `interview`, `rejected`, `offer`, or `not_relevant`
- `GET/POST /api/profile` — candidate profile (requires `PORTAL_ACCESS_TOKEN`)
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

## Render deployment

1. In Render, select **New + → Blueprint** and connect this repository.
2. Render reads `render.yaml`, installs dependencies, and starts FastAPI.
3. Keep the persistent disk enabled. SQLite data may be lost across restarts if stored on an ephemeral filesystem.
4. Set `PORTAL_ACCESS_TOKEN` in the Render environment.
5. After deployment, check `/health` and `/docs`, then run `POST /api/hunt` and review `/api/stats`.

If your Render plan does not support persistent disks, use managed PostgreSQL before relying on long-term tracking.

## Important limits and safety

- **Application submission is disabled.** The service finds, scores and tracks vacancies and prepares drafts; it does not log into job sites or submit applications.
- No CAPTCHA/OTP bypass, fabricated answers, or automatic acceptance of legal declarations is implemented.
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

- Pre-seeded candidate profile for primary user (editable).
- Scoring tuned for QSR multi-unit, dark-store, cluster and expansion roles.
- Canada removed from default target countries (India + Gulf focus).
- Richer cover-letter generation that references real achievements when relevant.
- Default search window widened to 3 days; India salary floor set to ₹20 LPA.
