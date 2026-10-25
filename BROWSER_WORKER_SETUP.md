# Job Hunter Browser Worker Setup

## Runtime
The existing Render service was created with the native Python runtime. A Dockerfile is now included to install a Playwright-compatible Chromium runtime. To use it, create/reconfigure a Render Docker web service using this repository's Dockerfile. Do not change the existing service until the new service is healthy and has the required environment variables.

## Safe initial configuration
Set:
- BROWSER_WORKER_ENABLED=true
- BROWSER_ALLOW_SUBMIT=false
- AUTHENTICATED_BOARD_SUBMIT_ENABLED=false

The worker stays in dry-run mode by default. Keep BROWSER_ALLOW_SUBMIT=false until a test application flow has been validated end-to-end.

## Public ATS forms
Greenhouse/Lever pages are supported only when the URL is an HTTPS page on the allowlist. CAPTCHA, OTP, login walls, sensitive/legal questions, unknown required fields, and unsupported hosts stop the run.

## Login-required job boards
Naukri, LinkedIn, Indeed, Foundit, GulfTalent and Bayt require a user-authorized browser session. Do not provide portal passwords or OTPs to the API. Provision a Playwright storage-state JSON file securely into the browser runtime, then set BROWSER_STORAGE_STATE_PATH to its absolute path. Treat this file as a password: it contains session cookies and must never be committed to GitHub or exposed in logs. If the session expires, sign in manually and replace the state file. This app does not bypass CAPTCHA, MFA, access controls, or portal restrictions.

The authenticated-board submission switch is separate and defaults off. Before enabling it for any portal, verify the portal's current terms, supported form flow, and a successful test submission. LinkedIn/Naukri page structure may change; a generic form filler is not a guarantee of compatibility.

## Validation checklist
1. Check /health and Render logs after deployment.
2. Confirm Playwright can launch Chromium using the Docker runtime.
3. Run one known Greenhouse/Lever application in dry-run mode.
4. Confirm the worker stops on required unknown/legal fields.
5. Confirm authenticated session loads without asking the service for credentials.
6. Only after all tests pass, consider enabling submission for that specific supported flow.
