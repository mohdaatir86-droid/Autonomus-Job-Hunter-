from browser_worker import _allowed_url, ALLOWED_HOSTS


def test_greenhouse_and_lever_allowed():
    assert _allowed_url("https://boards.greenhouse.io/acme/jobs/123")
    assert _allowed_url("https://jobs.lever.co/acme/abc")


def test_http_and_foreign_hosts_blocked():
    assert not _allowed_url("http://boards.greenhouse.io/acme/jobs/123")
    assert not _allowed_url("https://evil.example.com/apply")
    assert not _allowed_url("https://naukri.com/job/123")
    assert not _allowed_url("javascript:alert(1)")


def test_allowed_hosts_set():
    assert "greenhouse.io" in ALLOWED_HOSTS
    assert "jobs.lever.co" in ALLOWED_HOSTS
