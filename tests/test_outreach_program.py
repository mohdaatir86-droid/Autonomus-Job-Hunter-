"""Recurring research uses mocked providers and can never approve or send."""
import asyncio
from datetime import timedelta

from fastapi import HTTPException
import outreach, outreach_engine as engine, outreach_program as program, prospeo_provider
from test_outreach import client, HEADERS, contact

TARGETS=[{'company':'Example','domain':'example.com','source_url':'https://example.com/careers'}]


def enable(client):
    r=client.post('/api/outreach/program',headers=HEADERS,json={'enabled':True,'targets':TARGETS})
    assert r.status_code==200,r.text


def mock_search(monkeypatch):
    calls=[]
    async def search(payload):
        calls.append(payload)
        return {'items':[{'person_id':'fixture-person'}], 'searched_at':engine.stamp()}
    monkeypatch.setattr(prospeo_provider,'search',search)
    return calls


def test_program_is_private_disabled_and_validates_targets(client,monkeypatch):
    calls=mock_search(monkeypatch)
    assert client.get('/api/outreach/program').status_code==401
    assert client.post('/api/outreach/program',json={'enabled':True}).status_code==401
    assert client.post('/api/outreach/program/run').status_code==401
    assert client.get('/api/outreach/program',headers=HEADERS).json()['enabled'] is False
    assert client.post('/api/outreach/program/run',headers=HEADERS).json()['skipped']
    assert calls==[]
    for target in [{'company':'X','domain':'gmail.com','source_url':'https://example.com'}, {'company':'X','domain':'example.com','source_url':'http://example.com'}]:
        assert client.post('/api/outreach/program',headers=HEADERS,json={'enabled':True,'targets':[target]}).status_code==422


def test_daily_limit_survives_restart_and_rolls_over_local_day(client,monkeypatch):
    calls=mock_search(monkeypatch);enable(client)
    assert client.post('/api/outreach/program/run',headers=HEADERS).status_code==200
    engine.initialize()
    assert client.post('/api/outreach/program/run',headers=HEADERS).json()['skipped']
    assert len(calls)==1
    moment=engine.utcnow()+timedelta(days=1)
    monkeypatch.setattr(engine,'utcnow',lambda:moment)
    client.post('/api/outreach/program/run',headers=HEADERS)
    assert len(calls)==2


def test_only_reviewed_contacts_become_drafts_never_approved(client,monkeypatch):
    calls=mock_search(monkeypatch);enable(client)
    contact(client,verify=True)
    result=client.post('/api/outreach/program/run',headers=HEADERS).json()
    assert result['prepared']==1 and len(calls)==1
    drafts=client.get('/api/outreach/drafts',headers=HEADERS).json()['items']
    assert len(drafts)==3 and all(d['state']=='draft' and not d['approval_hash'] for d in drafts)
    assert all(d['campaign_kind']=='introduction' and d['job_id'] is None for d in drafts)
    engine.initialize()
    moment=engine.utcnow()+timedelta(days=1);monkeypatch.setattr(engine,'utcnow',lambda:moment)
    assert client.post('/api/outreach/program/run',headers=HEADERS).json()['prepared']==0
    assert len(client.get('/api/outreach/drafts',headers=HEADERS).json()['items'])==3


def test_suppressed_or_unreviewed_contacts_do_not_become_drafts(client,monkeypatch):
    mock_search(monkeypatch);enable(client)
    c=contact(client,verify=False)
    assert client.post('/api/outreach/program/run',headers=HEADERS).json()['prepared']==0
    outreach.suppress_contact(c['id'])
    assert not client.get('/api/outreach/drafts',headers=HEADERS).json()['items']


def test_job_found_and_global_stop_persist_and_clear_approvals(client,monkeypatch):
    calls=mock_search(monkeypatch);enable(client)
    result=client.post('/api/outreach/program',headers=HEADERS,json={'job_found':True})
    assert result.json()['enabled'] is False and result.json()['job_found'] is True
    assert engine.settings()['paused'] is True
    engine.initialize()
    client.post('/api/outreach/program/run',headers=HEADERS)
    assert calls==[]
    enable(client)
    client.post('/api/outreach/stop',headers=HEADERS)
    assert program.get()['enabled'] is False


def test_overlapping_workers_only_search_once_and_stop_is_not_reversed(client,monkeypatch):
    enable(client);calls=[]
    async def search(payload):
        calls.append(payload)
        await asyncio.sleep(0)
        program.save({'job_found':True})
        return {'items':[],'searched_at':engine.stamp()}
    monkeypatch.setattr(prospeo_provider,'search',search)
    async def run():return await asyncio.gather(program.tick(),program.tick())
    asyncio.run(run())
    assert len(calls)==1 and program.get()['enabled'] is False and program.get()['job_found'] is True


def test_budget_failure_is_visible_and_not_retried_same_day(client,monkeypatch):
    enable(client);calls=[]
    async def search(payload):
        calls.append(payload);raise HTTPException(429,'Daily lookup cap reached.')
    monkeypatch.setattr(prospeo_provider,'search',search)
    assert client.post('/api/outreach/program/run',headers=HEADERS).json()['blocked']=='Daily lookup cap reached.'
    client.post('/api/outreach/program/run',headers=HEADERS)
    assert len(calls)==1 and 'cap reached' in program.get()['message']
