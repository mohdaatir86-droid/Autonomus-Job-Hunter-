"""Apollo integration safety regressions for Job Hunter."""
import asyncio
import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import outreach


def test_apollo_enrichment_rejects_unverified_business_email():
    async def fake_apollo(path, params):
        return {"person": {"email": "recruiter@example.com", "email_status": "unverified"}}
    class FakeDB:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, *args): return self
        def fetchone(self): return None
    with patch.object(outreach, "connect", return_value=FakeDB()), patch.object(outreach, "_apollo", side_effect=fake_apollo), patch.object(outreach, "create_contact") as save, patch("outreach_engine.claim_enrichment",return_value="example.com"), patch.dict(os.environ,{"APOLLO_API_KEY":"fixture"}):
        with pytest.raises(HTTPException) as err:
            asyncio.run(outreach.enrich_apollo({"apollo_id": "abc123", "confirm_credit_use": True}))
        assert err.value.status_code == 422
        save.assert_not_called()


def test_apollo_enrichment_requires_credit_confirmation():
    with pytest.raises(HTTPException) as err:
        asyncio.run(outreach.enrich_apollo({"apollo_id": "abc123"}))
    assert err.value.status_code == 409
