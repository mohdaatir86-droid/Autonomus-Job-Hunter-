# Job Hunter Browser Worker Setup

## Runtime status

The **existing** Render service uses the **native Python** runtime. Playwright Chromium cannot be installed there (no root for `playwright install --with-deps`).

A Dockerfile is included that uses the official Playwright Python image (`mcr.microsoft.com/playwright/python:v1.64.1-noble`). The `playwright` package is pinned to `1.64.1` to match.

## Safe migration path (do not break the live service)

1. Keep the current Python service running.
2. Create a **new** Render Web Service → **Docker** from this repository.
3. Copy every environment variable from the live service (especially `DATABASE_URL` / Postgres, `PORTAL_ACCESS_TOKEN`, AI keys, Gmail OAuth).
4. Set on the Docker service:
   - `BROWSER_WORKER_ENABLED=true`
   - `BROWSER_ALLOW_SUBMIT=false`  (keep false until end-to-end validation)
   - `AUTHENTICATED_BOARD_SUBMIT_ENABLED=false`
5. Deploy and open `/health` and `/health/browser`.
6. Required acceptance: `/health/browser` must report `chromium_launchable: true` and a Chromium version.
7. Run one Greenhouse or Lever draft in **dry-run** mode only.
8. Only after confirmation detection works, consider enabling submit for that specific flow.

## Public ATS forms

Greenhouse / Lever public application URLs are allow-listed. The worker:

- Fills only fields that map to the verified candidate profile
- Stops on CAPTCHA, OTP, login walls, legal/sensitive questions, unknown required fields
- Never invents work authorization, salary, or demographic answers
- Requires explicit `BROWSER_ALLOW_SUBMIT=true` to click submit
- Verifies confirmation text before recording success

## Login-required boards (Naukri, LinkedIn, Indeed, Foundit, GulfTalent, Bayt)

These require a user-authorized Playwright storage-state file. **Do not** paste passwords or OTPs into chat or the API.

1. On a trusted machine, log in manually with Playwright and export `storage_state.json`.
2. Provision the file into the container via a secret volume or secure upload path.
3. Set `BROWSER_STORAGE_STATE_PATH` to that absolute path.
4. Treat the file as a password; never commit it.
5. Authenticated submission remains off until each portal’s flow is tested independently.

## Validation checklist

- [ ] `/health` returns ok
- [ ] `/health/browser` → `chromium_launchable: true`
- [ ] `/api/portals` shows accurate capability flags
- [ ] Dry-run fills Greenhouse/Lever form fields from profile
- [ ] CAPTCHA / legal fields cause a clean stop + event
- [ ] Duplicate draft/job does not double-submit
- [ ] Success notification only after confirmation text is detected

## Safety defaults

| Variable | Default | Meaning |
|----------|---------|---------|
| `BROWSER_WORKER_ENABLED` | false | Worker will not start |
| `BROWSER_ALLOW_SUBMIT` | false | Fill only; no submit click |
| Live applications | false | Hard-coded in health and policy |
