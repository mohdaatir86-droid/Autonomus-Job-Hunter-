from portal_registry import get_portal_registry, portal_support_level, PORTAL_REGISTRY


def test_registry_has_priority_portals():
    reg = get_portal_registry()
    portals = reg["portals"]
    for key in ("greenhouse", "lever", "naukri", "linkedin", "indeed", "bayt", "gulftalent"):
        assert key in portals
        assert "discovery" in portals[key]
        assert "submission" in portals[key]


def test_no_portal_claims_full_submission_by_default():
    for key, meta in PORTAL_REGISTRY.items():
        assert meta.get("submission") is False, f"{key} must not claim submission until verified"


def test_support_levels():
    assert portal_support_level("greenhouse") == "partial"
    assert portal_support_level("naukri") == "discovery_only"
    assert portal_support_level("unknown_xyz") == "unsupported"


def test_safety_block_in_registry_response():
    reg = get_portal_registry()
    assert reg["safety"]["live_submission_enabled"] is False
    assert "partial_application" in reg["summary"]
