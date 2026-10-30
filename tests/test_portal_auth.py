import main


def test_google_portal_session_is_signed_and_expires(monkeypatch):
    monkeypatch.setenv("PORTAL_ACCESS_TOKEN", "p" * 48)
    issued_at = 1_800_000_000

    token = main._issue_portal_session("Candidate@Example.com", now=issued_at)
    assert token
    assert main._read_portal_session(token, now=issued_at + 60) == {
        "email": "candidate@example.com",
        "iat": issued_at,
        "exp": issued_at + main.PORTAL_SESSION_SECONDS,
    }
    assert main._read_portal_session(token, now=issued_at + main.PORTAL_SESSION_SECONDS + 1) is None
    assert main._read_portal_session(token[:-1] + ("A" if token[-1] != "A" else "B"), now=issued_at + 60) is None


def test_google_portal_session_is_bound_to_saved_profile_email(monkeypatch):
    monkeypatch.setenv("PORTAL_ACCESS_TOKEN", "p" * 48)
    session = {"email": "candidate@example.com"}
    monkeypatch.setattr(main, "get_profile", lambda: {"email": "Candidate@Example.com"})
    assert main._portal_session_matches_profile(session)

    monkeypatch.setattr(main, "get_profile", lambda: {"email": "someone-else@example.com"})
    assert not main._portal_session_matches_profile(session)


def test_google_portal_session_requires_strong_private_key(monkeypatch):
    monkeypatch.setenv("PORTAL_ACCESS_TOKEN", "too-short")
    assert main._issue_portal_session("candidate@example.com") is None
