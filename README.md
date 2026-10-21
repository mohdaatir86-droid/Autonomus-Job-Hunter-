# Autonomous Job Hunter

A deployable MVP for discovering and scoring operations leadership vacancies. This repository is intentionally independent of Product Hunter.

## Current capabilities

- Pulls public job listings from Arbeitnow, Remotive, and Jobicy APIs.
- Optional company career-board integrations using public Greenhouse and Lever endpoints.
- Normalizes job records and deduplicates them by job URL.
- Scores jobs against operations, fulfilment, warehouse, supply chain, quick-commerce and cluster-management roles.
- Filters and ranks by target country and match score.
- Stores job records, first/last seen timestamps, run history, and manually maintained application status in SQLite.
- Provides a JSON API and interactive API documentation at /docs.
- Runs scheduled discovery every 15 minutes by default.
- Includes Render Blueprint and persistent-disk configuration.

## Target profile defaults

Countries: India, United Arab Emirates, Saudi Arabia, Qatar, Kuwait, Oman, and Canada.

Roles: Senior Cluster Manager, City Operations Manager, Operations Manager, Program Manager, Head of Operations, Warehouse/Fulfilment Manager, Regional Operations Manager, Supply Chain Manager, Dark Store Manager, and Last-Mile Operations Manager.

These defaults are editable through POST /api/settings.

## Run locally

Requires Python 3.11 or newer.

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn main:app --reload

On Windows, activate with .venv\\Scripts\\activate.

Open http://127.0.0.1:8000/docs.

## API endpoints

- GET /health — service health
- GET /api/stats — totals, country/source breakdown, latest run
- GET /api/jobs?min_score=35&limit=100 — scored jobs
- POST /api/hunt — run discovery immediately
- GET /api/sources — source configuration and limitations
- GET /api/settings and POST /api/settings — supported preferences
- POST /api/jobs/{id}/status — set saved, applied_manually, interview, rejected, offer, or not_relevant

Example manual run:

    curl -X POST http://127.0.0.1:8000/api/hunt

Optional company board slugs can be set as comma-separated environment variables:

- GREENHOUSE_BOARDS=company_slug,another_slug
- LEVER_SITES=company_slug,another_slug

## Render deployment

1. In Render, select New + → Blueprint and connect this repository.
2. Render reads render.yaml, installs dependencies, and starts FastAPI.
3. Keep the persistent disk enabled. SQLite data may be lost across restarts if stored on an ephemeral filesystem.
4. After deployment, check /health and /docs, then run POST /api/hunt and review /api/stats.

If your Render plan does not support persistent disks, use managed PostgreSQL before relying on long-term tracking.

## Important limits and safety

- **Application submission is disabled.** The service finds, scores and tracks vacancies; it does not log into job sites or submit applications.
- No CAPTCHA/OTP bypass, fabricated answers, or automatic acceptance of legal declarations is implemented.
- A future application workflow must use official APIs or explicitly permitted methods and pause for human input on assessments, consent, work authorization, salary commitments, or legal questions.
- Public job APIs do not cover every employer, country, or job board. Results depend on provider availability and data coverage.
- The current match score is a keyword heuristic, not a prediction of hiring success. Early viral/job-trend velocity is not claimed because historical snapshots are not yet implemented.
- The resume is not embedded in this public repository. Keep candidate contact information private.
- The resume says 8+ years while the experience dates may span more than nine years; verify the wording before relying on a 9+ year filter. Do not silently change candidate claims.

## Next milestones

1. Add robust country/remote parsing and country-specific salary/work-authorization preferences.
2. Add automated tests, provider health metrics, retry/backoff, and alerting.
3. Add a frontend dashboard for filters, job details, saved jobs, and run status.
4. Add ranking history snapshots and meaningful freshness/velocity metrics.
5. Add permitted application preparation/submission only after candidate profile and each source's rules are validated.
