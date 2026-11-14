# Codex handoff — finish Gulf discovery and release validation

## Project and production baseline

- Repository: `mohdaatir86-droid/Autonomus-Job-Hunter-`
- Live portal: https://autonomous-job-hunter-docker.onrender.com
- Render workspace: `tea-dbc7ulrrjlhs73b4vjg0`
- Render service: `srv-dbfqlirl550s738v1drg`
- Last verified production source: `8cacb0c2eab08c7509ef7e47dc641d87c3d681a8` (PR #39).
- Continue branch `feat/snoonu-feed-relevance`. This handoff branch is not deployed.

Production previously had 22 catalog entries, eight direct employer imports,
seven indexed boards and seven external employer links. Nine published operations/
store vacancies were visible at the last production UI check; that is an observed
snapshot, not a current-count guarantee. Read current production state afresh.
Existing architecture is FastAPI + vanilla HTML/JS + durable PostgreSQL. Preserve
it, authentication, CSRF, private PDF storage, Gmail OAuth, outreach approvals and
tracked application history. Keep LinkedIn Autonomous and other projects separate.

## Prepared changes on this branch

- Snoonu public active-job import from the first-party Keka embed endpoint.
  Explicit countries, original publication dates, full descriptions and structured
  experience ranges; technical logistics titles excluded. Public feed provenance
  was inspected in the employer's own career page/embed script.
- Amazon Jobs public search JSON restricted to Gulf ISO3 country codes, preserving
  basic/preferred qualifications and original posting dates.
- Alshaya public listing/detail parser with original Apply now control, explicit
  job location and closing-time checks; bounded published listing pages.
- DHL and Majid Al Futtaim public HTML-embedded JSON adapters. Parse JSON only,
  never execute webpage JS. Bound global scans to 60 search results and validate
  external active job details separately. These sources have partial coverage;
  do not describe them as exhaustive Gulf integrations.
- Recognise genuine store/regional operations leadership titles in scoring only
  when physical operations context exists. Exclude technical leadership titles.
  Score explicitly requested Gulf countries consistently during verified imports
  without rewriting saved country preferences.
- Correct score-chip active state when switching markets; version the changed
  Gulf JS resource to avoid mixed browser caches.

The branch catalog has thirteen direct-import configurations, seven indexed
boards, and two external links. Those are code/configuration counts, not claims
that every provider currently responds or every listing is open. Noon has no
verified reliable public direct feed. Kitopi's employer-linked Lever board
returned 404; the similarly named SmartRecruiters board was empty and was not
established as the employer's current authorised feed. Keep their status honest.

## Evidence already collected

- Latest local full suite: **318 passed, 5 PostgreSQL-only skipped**.
- JS syntax checks and `git diff --check` passed.
- Mock tests cover new API/page parsers, malformed schemas, explicit country
  requirements, dates, closed/missing-apply cases, URL/redirect restrictions,
  exclusion of internal recruiter fields, and no JS execution.
- Small read-only public probes returned relevant Snoonu, Amazon, Alshaya and
  Majid Al Futtaim records. DHL returned no matching Gulf roles in the bounded
  sample. Probe results were not seeded into production or represented as live
  portal imports.
- Full PostgreSQL CI/mobile smoke checks and deployment of this branch remain.
- No recruiter messages, job submissions or paid enrichment tests were sent.

## Finish before release

1. Read `GULF.md`, this document and the actual diff. Verify current branch/main
   heads and working tree. Review new adapter failure handling, explicit dates,
   country agreement, profile fit, public-only data access and bounded network
   behaviour. Do not merge an unreviewed or failing branch.
2. Fix the remaining Gulf dashboard summary mismatch: `loadStats()` currently
   shows the normal saved-country/three-day totals even when the Gulf view lists
   older but recently checked published jobs. Add an authenticated Gulf statistics
   scope using the same eligibility/freshness rules as the Gulf list, and guard
   stale responses on market switching/sign-out. Make the disabled posting-window
   control accurately describe the Gulf freshness rule while preserving the saved
   normal posting window. This was identified but is NOT implemented here.
3. Review indexed-source health separately from direct imports. SerpApi has had
   intermittent timeouts. Never mark indexed coverage or external career links
   as a working direct feed, or claim all Internet sites are covered.
4. Improve remaining source coverage only with a proven official/public path.
   Do not bypass sign-in/bot blocks, invent APIs/addresses, request secrets in
   chat, spend provider credits merely to test, or pad results with junior,
   technical or mismatched jobs. If a source cannot be completed, show the precise
   limitation and original career link rather than leaving a fake integration.
5. Run meaningful full checks, including PostgreSQL and the existing browser
   fixtures at 375/390/430px. Extend the mocked browser scenario to cover correct
   Gulf totals, posting-window state, market switches and sign-out races.
6. After checks pass, merge using the expected head SHA, deploy through the
   EXISTING Render service and monitor the exact deployment to `live`. Verify
   health, private endpoint authentication, source health and mobile usability.
7. Use the private **Gulf-only refresh** from the portal for real preparation.
   `/api/gulf/refresh` reads public sources only; do not substitute the full hunt
   merely to test because it also uses other configured integrations. Confirm
   newly imported jobs, duplicate prevention/history retention, and original
   application links without submitting any applications.

## Preserve existing outreach boundaries

Job applications remain manual on original employer pages. Opening a link never
marks an application submitted. Preserve existing reviewed-draft protections,
private approved PDF attachment checks, shared sending cap, persistent duplicate/
suppression records, reply/bounce/opt-out stops and scheduling coordination. Do
not activate campaigns or send recruiter mail as part of this handoff. Any final
campaign activation requires the concrete targeting/rules/recipient/content/resume
preview and the user's activation decision. Do not treat Gmail accepting a
message as inbox delivery. Do not claim authorisation, qualifications, notice
period, achievements or personal connections that the saved profile does not
confirm.

## Final report

Report implemented and verified changes, current imported job count, direct vs
indexed vs external coverage, actual provider limitations/configuration, tests,
deployment evidence and the live portal link. Distinguish production observations
from code configuration and test fixtures. List only genuinely unresolved items;
do not claim complete automation or sending without evidence.
