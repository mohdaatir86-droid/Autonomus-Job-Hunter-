"""Mock-only provider contract, credit coordination, privacy and auth regressions."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
import outreach
import outreach_engine as engine
import prospeo_provider as provider
from test_outreach import client, HEADERS


def search_result(domain='example.com'):
    return {'error': False, 'results': [
        {'person': {'person_id': 'fixture-person', 'full_name': 'Fixture Recruiter', 'current_job_title': 'Talent Acquisition Manager', 'email': {'email': 'must-not-be-returned@example.com'}, 'mobile': '+000'},
         'company': {'name': 'Example', 'domain': domain}},
        {'person': {'person_id': 'wrong-role', 'current_job_title': 'Software Engineer'}, 'company': {'domain': domain}},
        {'person': {'person_id': 'wrong-company', 'current_job_title': 'Recruiter'}, 'company': {'domain': 'other.example'}},
    ]}


def email_result(address='recruiter@example.com', status='VERIFIED', company='example.com'):
    return {'error': False, 'person': {'person_id': 'fixture-person', 'full_name': 'Fixture Recruiter',
        'current_job_title': 'Talent Acquisition Manager', 'linkedin_url': 'https://www.linkedin.com/in/fixture',
        'email': {'email': address, 'status': status, 'revealed': True}},
        'company': {'name': 'Example', 'domain': company}}


def search(client):
    return client.post('/api/outreach/prospeo/search',headers=HEADERS,json={'company_domain':'example.com','confirm_credit_use':True})


def enrich(client):
    return client.post('/api/outreach/prospeo/enrich',headers=HEADERS,json={'person_id':'fixture-person','confirm_credit_use':True})


def test_missing_key_confirmation_and_private_routes(client, monkeypatch):
    monkeypatch.delenv('PROSPEO_API_KEY',raising=False)
    for path in ['search','enrich']:
        assert client.post('/api/outreach/prospeo/'+path,json={}).status_code==401
    assert client.post('/api/outreach/prospeo/search',headers=HEADERS,json={'company_domain':'example.com'}).status_code==409
    assert search(client).status_code==503
    with outreach.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM outreach_enrichment').fetchone()[0]==0


def test_search_filters_privacy_and_restart_cache(client, monkeypatch):
    monkeypatch.setenv('PROSPEO_API_KEY','fixture-not-a-key')
    fake=AsyncMock(return_value=search_result())
    monkeypatch.setattr(provider,'request',fake)
    r=search(client)
    assert r.status_code==200,r.text
    assert len(r.json()['items'])==1
    assert 'must-not-be-returned' not in r.text and '+000' not in r.text
    payload=fake.call_args.args[1]
    assert payload['filters']['company']['websites']['include']==['example.com']
    assert 'person_job_title' in payload['filters']
    engine.initialize()
    assert search(client).json()['cached'] is True
    assert fake.await_count==1


def test_reveal_verified_work_email_only_and_no_repeat(client, monkeypatch):
    monkeypatch.setenv('PROSPEO_API_KEY','fixture-not-a-key')
    fake=AsyncMock(side_effect=[search_result(),email_result()])
    monkeypatch.setattr(provider,'request',fake)
    assert enrich(client).status_code==409  # no shortlist
    assert search(client).status_code==200
    r=enrich(client)
    assert r.status_code==200,r.text
    assert not r.json()['verified']  # human responsibility review is preserved
    assert fake.call_args.args[1]=={'only_verified_email':True,'enrich_mobile':False,'data':{'person_id':'fixture-person'}}
    assert enrich(client).json()['id']==r.json()['id']
    assert fake.await_count==2


@pytest.mark.parametrize('address,status,company',[
    ('personal@gmail.com','VERIFIED','example.com'),
    ('recruiter@example.com','UNVERIFIED','example.com'),
    ('recruiter@example.com','VERIFIED','former.example'),
    ('recruiter@other.example','VERIFIED','example.com'),
])
def test_reject_unverified_personal_or_mismatched_email(client,monkeypatch,address,status,company):
    monkeypatch.setenv('PROSPEO_API_KEY','fixture')
    fake=AsyncMock(side_effect=[search_result(),email_result(address,status,company)])
    monkeypatch.setattr(provider,'request',fake)
    assert search(client).status_code==200
    assert enrich(client).status_code==422
    assert enrich(client).status_code==409
    assert fake.await_count==2
    assert outreach.list_records('outreach_contacts')==[]


def test_shared_budget_includes_search_and_reveal(client,monkeypatch):
    monkeypatch.setenv('PROSPEO_API_KEY','fixture')
    fake=AsyncMock(side_effect=[search_result(),email_result()])
    monkeypatch.setattr(provider,'request',fake)
    engine.save_settings({'enrichment_daily':1})
    assert search(client).status_code==200
    assert enrich(client).status_code==429
    assert fake.await_count==1


def test_concurrent_search_and_uncertain_failure_not_retried(client,monkeypatch):
    monkeypatch.setenv('PROSPEO_API_KEY','fixture')
    async def failed(*args):
        await asyncio.sleep(0)
        raise HTTPException(503,'Fixture ambiguous provider failure')
    fake=AsyncMock(side_effect=failed)
    monkeypatch.setattr(provider,'request',fake)
    async def run():
        return await asyncio.gather(*(provider.search({'company_domain':'example.com','confirm_credit_use':True}) for _ in range(2)),return_exceptions=True)
    results=asyncio.run(run())
    assert sorted(r.status_code for r in results)==[409,503]
    assert search(client).status_code==409
    assert fake.await_count==1


@pytest.mark.parametrize('code,http_status,message',[
    ('INVALID_API_KEY',400,'rejected the API key'),('INSUFFICIENT_CREDITS',400,'exhausted'),
    ('PLAN_REQUIRED',400,'different plan'),('anything',429,'rate limit'),
])
def test_provider_errors_are_actionable_without_exposing_response(monkeypatch,code,http_status,message):
    monkeypatch.setenv('PROSPEO_API_KEY','fixture-secret')
    def handler(request):
        assert request.headers['X-KEY']=='fixture-secret'
        assert 'fixture-secret' not in str(request.url)
        return httpx.Response(http_status,json={'error':True,'error_code':code,'details':'fixture-secret-private-provider-body'})
    original=httpx.AsyncClient
    monkeypatch.setattr(provider.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(HTTPException) as err:
        asyncio.run(provider.request('search-person',{}))
    assert message in str(err.value.detail)
    assert 'fixture-secret' not in str(err.value.detail)
