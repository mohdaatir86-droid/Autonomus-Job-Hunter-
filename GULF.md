# Gulf opportunity discovery

The Overview market switch offers a separate Gulf shortlist across UAE, Saudi
Arabia, Qatar, Kuwait, Oman and Bahrain. It does not overwrite saved preferences.

## Coverage

The private source catalog contains 22 entries. Five entries have direct public
imports: Delivery Hero (publisher-provided brands including Talabat), Careem,
Lalamove, GMG and Aramex. SmartRecruiters uses its documented Posting API.
Greenhouse, Lever and employer RSS need no API key. Aramex RSS contains a bounded
selection and is explicitly reported as partial coverage. Zero matching vacancies
is valid and must not be filled with unrelated jobs.

Seven job boards retain their existing Google Jobs / authorised email-alert
coverage. Ten other employer pages, including Noon, are external career links,
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
