# Gulf opportunity discovery

The Overview market switch offers a separate Gulf shortlist across UAE, Saudi
Arabia, Qatar, Kuwait, Oman and Bahrain. It does not overwrite saved preferences.

## Coverage

The private source catalog contains 22 entries. Thirteen entries have direct public
imports: Delivery Hero (publisher-provided brands including Talabat), Careem,
Lalamove, GMG, Aramex, Al-Futtaim, DP World, Landmark Group, Snoonu, Amazon, Alshaya, DHL and Majid Al Futtaim. SmartRecruiters uses its documented Posting API.
Greenhouse, Lever and employer RSS need no API key. Aramex RSS contains a bounded
selection and is explicitly reported as partial coverage. Zero matching vacancies
is valid and must not be filled with unrelated jobs.

Seven job boards retain their existing Google Jobs / authorised email-alert
coverage. Two other employer pages, Noon and Kitopi, are external career links,
not direct integrations. Kitopi's employer-linked Lever board returned 404 during
inspection; it is deliberately not presented as working. Site presence in the
catalog never promises comprehensive coverage or that every job is open.

The existing discovery schedule imports the new feeds. No additional scheduler
is installed. The private POST `/api/gulf/refresh` refreshes only public Gulf
feeds: no paid search, enrichment, Gmail reads, notifications or outreach tick.
All discovery entry points share a database lease, with a ten-minute expiry for
worker recovery, in addition to the existing process lock.

## Shortlist and evidence

Senior physical operations, stores, logistics, fulfilment and supply-chain scope
is required. Explicit junior, technical, sales, finance and analytical role titles
are excluded. Total years are read from the saved profile's explicit experience
summary. Published minimum years are lower bounds, not maximums; explicit total
experience requirements above the candidate or upper bounds below the candidate
are excluded. Undisclosed qualifications, salary, languages and work authorisation
are flagged for manual review; no eligibility is invented.

A published employer-feed check carries its UTC check time and expires after
36 hours. The Gulf view includes older publication dates only while this evidence
is fresh. Publication dates are never replaced with discovery dates. This is not
an application guarantee: vacancies can close after a check. Partial or failed
fetches do not infer that all older vacancies closed. Expired records disappear
from the open view but application history remains available through status
filters in the saved-target view.

The original job detail and exact employer application URL appear in a private,
mobile dialog. Apply links open the publisher's application page. The user submits
there and explicitly changes the portal status afterward. No job form is filled
or submitted, and opening a link never marks a job applied.

## Validation

Mock tests cover public APIs, pagination, country validation, detail failures,
RSS dates/locations and unsafe XML, relevance and experience requirements,
private API access, URL safety, evidence expiry, duplicate refreshes, preserved
history and source reports, and discovery lease recovery. Browser fixtures mock
all Gulf imports and test the detail/apply flow and 375px layout, alongside the
existing 375/390/430px smoke checks. No tests contact recruiters or paid providers.

Oracle employer imports use documented public CE published-vacancy search and
ById details, scoped to the two fixed employer sites and published requisition IDs.
Only external job description / responsibilities / qualifications are retained.
Gulf country codes must agree between listing and detail. Invalid, future or
expired posting dates are excluded; explicit closing times override the 36-hour
evidence lifetime. Operations search is bounded at 400 rows and 30 shortlisted
details per employer, with partial coverage reported. Provider groups run together
with individual 60-second budgets so a slow source does not block other imports.

Snoonu uses the public active-job endpoint linked from its official Keka embed
script. Tenant and host are fixed; no candidate or recruiter API is accessed.
Structured experience ranges are checked alongside full descriptions, locations
use explicit country codes, and invalid/future publication dates are excluded.
Technical leadership titles remain outside the operations shortlist. Recognised
store/regional leadership titles receive operations-role scoring only where the
job description confirms physical operations context.

Amazon imports use the public job-search JSON with explicit Gulf ISO3 codes and
original basic/preferred qualifications. Alshaya imports use the employer's
published listing links, job fields and an Apply now control. Phenom employers
(DHL and Majid Al Futtaim) use public HTML-embedded JSON only, with a bounded
60-result global scan and separate external active-job detail validation. These
scans are explicitly partial and can return zero relevant jobs; search teasers
and hidden closed-job templates alone never establish availability. No webpage
JavaScript is executed. Noon and Kitopi remain external links until a reliable
employer-authorised direct source is verified.

## Dashboard scope and source follow-up (14 November 2026)

The private statistics endpoint accepts the same country, search, source, score,
status and Gulf scope as the vacancy list, before pagination. Gulf totals reuse
its publication-evidence and current-profile checks. The disabled posting-window
selector describes the 36-hour check window; switching back restores the saved
posting window. Request sequencing and authentication epochs discard delayed
market responses and prevent signed-out data from being restored. Original
posting dates and available closing dates remain visible in the detail dialog.

Noon's own company page links to https://joinnoon.com/ . The public operations
search is https://joinnoon.com/jobs?department=Operations+%26+Logistics . It was
verified as an employer career search with published roles, but no direct import
is implemented. Its listings must not be counted as imported jobs. Kitopi's
https://www.kitopi.com/careers remains the original career destination; a reliable
current employer-authorised public feed could not be verified. Search-index
snapshots or a similarly named ATS tenant do not establish current availability.
