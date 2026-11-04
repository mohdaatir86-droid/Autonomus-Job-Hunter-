# Personal Job Hunter: launch and operate

The default search is Operations Manager, Cluster Manager, Area Manager and related operations leadership roles in Delhi NCR, Uttar Pradesh, Dehradun and all six Gulf countries (UAE, Saudi Arabia, Qatar, Kuwait, Oman, Bahrain). Canada remains a supported optional market. Existing saved search preferences are preserved; sign into the private profile and save the desired countries and locations to update an existing installation.

## What works without paid services

Python 3.11+ runs the FastAPI dashboard, public job-source discovery, SQLite storage, PDF resume extraction, truthful template drafts and application tracking. AI is optional. Applications are reviewed and submitted manually on the employer website. Browser submission is disabled in code regardless of environment flags.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# Supply PORTAL_ACCESS_TOKEN securely in the process environment first.
ENABLE_SCHEDULER=false .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Do not copy the example PostgreSQL URL from `.env.example` into a working environment. Leave DATABASE_URL unset for SQLite. Environment files are not automatically loaded by the application; use the hosting service's environment editor or uvicorn's --env-file option for your own ignored local file.

## Required private setup

1. In the existing hosting service's secure environment settings, generate and set a unique random `PORTAL_ACCESS_TOKEN` of at least 32 characters. Do not send it in chat or commit it.
2. Set `DATABASE_URL` to managed PostgreSQL, or use `DATABASE_PATH` on a persistent disk. An ephemeral database loses your tracking data during redeployment.
3. Sign into the dashboard with the private key. Save your real name, email, phone, experience summary and skills. Upload a text-based PDF resume (8 MB maximum). Never put a resume in source control.
4. Open **Search preferences and email alerts**, confirm roles/countries/locations and choose salary, experience, visa preference and posting window. Current salary/experience defaults are 20 LPA and 8 years; set either to zero to turn off that restriction. Unknown values stay eligible for review. Notice period and work authorization are never invented.
5. Run one discovery. Check source status and eligible matches. Prepare a draft and review its contents. Use the job status selector after applying on the employer site.

## Email alerts selected by the owner

Email is now the default notification channel; WhatsApp is not used unless explicitly selected with `ALERT_CHANNEL=whatsapp`.

1. Create a Resend account using the same address that should receive the alerts. Generate a sending API key at https://resend.com/api-keys.
2. In the existing Render service's secure environment settings, set `RESEND_API_KEY`, `EMAIL_TO` (the owner's chosen recipient), `ALERT_CHANNEL=email` and `EMAIL_FROM=onboarding@resend.dev`.
3. The default test sender can deliver only to your own Resend account email. To send to a different recipient, verify a domain in Resend and set EMAIL_FROM to a sender on that domain. Gmail passwords and Gmail app passwords are not required for this route.
4. Deploy these code changes. Sign into the dashboard, open **Search preferences and email alerts**, and click **Send test email**. Check the inbox and spam folder. An accepted provider request does not guarantee inbox delivery.

`ALERT_MIN_SCORE` defaults to 70. Discovery emails a digest of up to five new jobs above that threshold with verified posting dates inside the configured freshness window. Missing credentials skip email; they never cause a fallback to WhatsApp. Delivery failures return a sanitized status without exposing addresses, keys or provider response bodies. There is no persistent delivery retry queue.

The recipient address belongs in private runtime settings. It is intentionally omitted from tracked source files. Cloud-environment draft settings do not update the live Render service; configure the live service separately or provide secure deployment access.

## Better India and Gulf coverage

Set `SERPAPI_API_KEY` securely to enable Google Jobs searches tailored to your saved roles and locations. Six queries per run are used for the default India/Gulf search. Provider quotas and charges apply; start with a six-hour cadence (`HUNT_INTERVAL_MINUTES=360`) and adjust only after checking your quota. Public feeds alone have limited India/Gulf coverage.

Optional `GREENHOUSE_BOARDS` and `LEVER_SITES` configure employer board slugs. Empty LEVER_SITES uses the verified public Meesho and Lalamove boards; custom slugs replace those defaults. Candidates are ranked for the saved markets and operations fit before applying each board limit. Gmail alerts are optional: set Google OAuth client ID/secret, enable Gmail API and register the exact callback URL. Save your profile email, then click **Connect Gmail job alerts** in the authenticated dashboard. Connect the same Gmail account as the profile. OAuth state is browser-bound and single-use. Google sign-in and Gmail access are separate features.

Required outbound HTTPS hosts depend on enabled providers: `www.arbeitnow.com`, `remotive.com`, `jobicy.com`, `boards-api.greenhouse.io`, optionally `api.lever.co`, `serpapi.com`, `api.resend.com` for email (legacy WhatsApp uses `api.callmebot.com` or `api.twilio.com`); Google flows additionally use `accounts.google.com`, `oauth2.googleapis.com`, `gmail.googleapis.com` and Google's token-verification certificate endpoints. Keep the platform proxy and TLS verification enabled.

## Scheduling

Set `ENABLE_SCHEDULER=true` for automatic discovery and draft preparation. Set `HUNT_INTERVAL_MINUTES=360` for six-hour discovery. Preparation runs every 30 minutes only after the profile is complete and the dashboard's draft policy is enabled. Already prepared/actioned jobs are excluded before selecting each batch.

If n8n owns discovery, set `ENABLE_INTERNAL_HUNT_SCHEDULER=false`; use its separate 32+ character `N8N_AUTOMATION_TOKEN` with POST `/api/integrations/n8n/hunt`. Never run duplicate schedules. For the internal scheduler, use a single web worker/instance; the hunt lock is process-local. Multiple replicas need a shared external scheduler and distributed hunt coordination.

## Database and deployment

Use the existing Render service; do not create a second service from the legacy `render.yaml`. The Dockerfile installs Python dependencies and includes bundled PostgreSQL migrations. PostgreSQL startup applies additive SQL files in `migrations/` under a transaction/advisory lock. The database role needs schema creation/alter privileges for first startup. Back up existing production data before deployment. The SQLite-to-PostgreSQL copier expects a bootstrapped target schema; initialize it using `database.initialize_database_schema()` before copying. Copying into a nonempty destination does not reconcile conflicting primary keys: use an empty target or plan an explicit data reconciliation.

Production endpoints that change state require owner authentication, except the separately authenticated n8n route and Google session callback. Gmail status, OAuth initiation and browser health are also private. Gmail initiation now uses POST `/api/gmail/auth`, returning the authorization URL as JSON; update any old GET-based integration.

After deployment, verify:

- `/health` returns healthy and `browser_submit_enabled=false`.
- `/health/db` returns `ok=true`, `connected=true`, and no missing tables.
- Unauthenticated POST `/api/settings`, `/api/hunt`, and `/api/notify/test` are denied.
- Authenticated `/api/setup` reports profile readiness and provider configuration without exposing credentials.
- A manual hunt reports successful sources and useful results; provider outages are separate from application health.
- A test email arrives in your chosen inbox, then a new-job digest arrives after an eligible discovery.

## Validation

```bash
ENABLE_SCHEDULER=false python -m pytest -q
python -m pip check
```

For PostgreSQL integration, set `TEST_POSTGRES_URL` to a disposable test database, never production. The test creates schema and fixture data. Local non-TLS connections must explicitly specify sslmode=disable; external application connections default to sslmode=require. CI runs both SQLite and PostgreSQL paths.

Code verification and local smoke checks do not deploy the service or publish a cloud environment. Live SerpApi/email/OAuth operation requires the owner's secure configuration and real account consent.


## Connect Gmail job-alert reading

This imports read-only job-alert emails from the chosen Gmail account. Outgoing job digests continue to use Resend.

1. Open https://console.cloud.google.com/ and select or create your project. Enable **Gmail API** under **APIs & Services → Library**.
2. In **Google Auth Platform**, configure branding/consent and the audience. For a personal app in Testing, add the Gmail account you will connect as a test user. Request `https://www.googleapis.com/auth/gmail.readonly` under data access. Testing-mode refresh tokens for sensitive Gmail scopes may expire after seven days; ongoing operation can require reconnecting or completing Google's production requirements.
3. Create an OAuth client with application type **Web application**. Set the authorized JavaScript origin to `https://autonomous-job-hunter-docker.onrender.com`. Set the authorized redirect URI exactly to `https://autonomous-job-hunter-docker.onrender.com/api/gmail/callback`. If also using Google portal sign-in, add `https://autonomous-job-hunter-docker.onrender.com/api/auth/google/session` as another redirect URI.
4. Save the OAuth client ID and secret securely in the existing Render service as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Set `GOOGLE_REDIRECT_URI` to the callback URI above. The existing strong `PORTAL_ACCESS_TOKEN` can sign OAuth state, so a separate `OAUTH_STATE_SECRET` is optional. Do not paste client secrets into chat or commit downloaded OAuth JSON files.
5. Save/redeploy, sign into the private dashboard and save the email address of the Gmail account to connect in the candidate profile. Click **Connect Gmail job alerts**, select that same Google account and approve read-only access. Account matching, browser-bound state and one-time callbacks are enforced.
6. Create operations/cluster/area-manager alerts on relevant job boards and have them delivered to that Gmail inbox. Run a hunt to import emails. Unknown posting dates are excluded from recent matches; connecting Gmail does not guarantee every message contains a usable dated vacancy.

An OAuth client cannot be created or consented to using the Resend key. The Google account owner must perform Google's account/project and consent steps. Render credentials and Google OAuth client credentials are separate.

## New workspace and recruiter outreach

See [OUTREACH.md](OUTREACH.md) for the redesigned frontend, Apollo integration, Gmail sending permission and approval/queue controls. These are separate from self-alert emails and read-only job-alert ingestion.
