import base64

import pytest

import gmail_alerts


def part(mime, body):
    return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")}}


def test_html_only_alert_keeps_link_entities_and_posting_age():
    payload = part("text/html", '''<div>Posted 3 hours ago <a
        href="https://www.naukri.com/job-listings-cluster-manager-123?x=1&amp;y=2">
        Cluster Manager</a></div><a href="https://www.naukri.com/unsubscribe">Unsubscribe</a>''')
    body = gmail_alerts._decode_body(payload)
    jobs = gmail_alerts._extract_jobs_from_email("Job alert: Cluster Manager in Noida", "alerts@naukri.com", body, "html-1")
    assert len(jobs) == 1
    assert jobs[0]["url"] == "https://www.naukri.com/job-listings-cluster-manager-123?x=1&y=2"
    assert jobs[0]["posted_at"] is not None
    assert jobs[0]["location"] == "Noida"


def test_nested_multipart_deduplicates_plain_and_html_links():
    link = "https://www.linkedin.com/jobs/view/123"
    payload = {"mimeType": "multipart/alternative", "parts": [
        part("text/plain", "Cluster Manager " + link),
        {"mimeType": "multipart/related", "parts": [part("text/html", f'<p><a href="{link}">Cluster Manager</a></p>')]},
    ]}
    jobs = gmail_alerts._extract_jobs_from_email("Cluster Manager", "jobs@linkedin.com", gmail_alerts._decode_body(payload), "multi-1")
    assert len(jobs) == 1
    assert jobs[0]["posted_at"] is None


def test_html_styles_and_scripts_do_not_create_jobs():
    body = gmail_alerts._decode_body(part("text/html", '<style>https://naukri.com/jobs/1</style><script>https://linkedin.com/jobs/2</script><p>No openings</p>'))
    assert "No openings" in body
    assert gmail_alerts._extract_jobs_from_email("Job alert", "alerts@naukri.com", body, "hidden-1") == []


@pytest.mark.parametrize("url", [
    "https://naukri.com.attacker.example/jobs/1",
    "https://attacker.example/?next=https://naukri.com/jobs/1",
    "https://naukri.com@attacker.example/jobs/1",
    "https://attacker.example/naukri.com/jobs/1",
    "https://user:password@naukri.com/jobs/1",
])
def test_board_name_outside_trusted_hostname_is_rejected(url):
    assert gmail_alerts._extract_jobs_from_email("Cluster Manager", "alerts@naukri.com", url, "bad-1") == []


def test_real_board_subdomain_is_allowed():
    jobs = gmail_alerts._extract_jobs_from_email("Cluster Manager", "alerts@naukri.com", "https://careers.naukri.com/jobs/1", "good-1")
    assert len(jobs) == 1
