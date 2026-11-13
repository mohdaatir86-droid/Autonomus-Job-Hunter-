"""All provider calls are fixtures; no recruiter contacts, credits or live sends."""
import asyncio
import base64
from datetime import datetime,timezone
from email import policy as email_policy
from email.parser import BytesParser

import httpx
import pytest
import outreach,outreach_engine as engine,application_workflow,resume_store
from test_outreach import client,HEADERS,contact,draft
from test_workflow_regressions import text_pdf


def campaign(client,kind='vacancy'):
    c=client.post('/api/outreach/campaigns',headers=HEADERS,json={'name':'Fixture campaign','kind':kind}).json()
    person=contact(client)
    r=client.post('/api/outreach/drafts',headers=HEADERS,json={'campaign_id':c['id'],'contact_id':person['id'],'job_id':1,'target_domain':'example.com'})
    assert r.status_code==200,r.text
    return c,r.json()


def activate(client,c):
    p=client.get(f"/api/outreach/campaigns/{c['id']}/preview",headers=HEADERS)
    assert p.status_code==200,p.text
    p=p.json()
    r=client.post(f"/api/outreach/campaigns/{c['id']}/activate",headers=HEADERS,json={'confirmed':True,'profile_confirmed':True,'preview_hash':p['preview_hash']})
    assert r.status_code==200,r.text
    return p


def gmail_receipts(monkeypatch):
    calls=[]
    async def post(self,url,**kw):
        calls.append(kw['json'])
        return httpx.Response(200,json={'id':'fixture-'+str(len(calls)),'threadId':'thread-1'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    return calls


def test_original_pdf_private_persistent_and_mime(client,monkeypatch):
    content=text_pdf('Fixture verified PDF contents')
    application_workflow.store_resume('resume.pdf',content)
    assert client.get('/api/outreach/resume').status_code==401
    assert client.get('/api/outreach/resume',headers=HEADERS).content==content
    outreach.init_outreach_db()
    assert resume_store.get()['version']==2
    d=draft(client,contact(client)['id'],True)
    calls=gmail_receipts(monkeypatch)
    assert client.post(f"/api/outreach/drafts/{d['id']}/send",headers=HEADERS).json()['state']=='sent'
    message=BytesParser(policy=email_policy.default).parsebytes(base64.urlsafe_b64decode(calls[0]['raw']))
    attachments=list(message.iter_attachments())
    assert len(attachments)==1
    assert attachments[0].get_content_type()=='application/pdf'
    assert attachments[0].get_payload(decode=True)==content
    assert message['To']=='recruiter@example.com'


def test_resume_change_actively_revokes_approval(client):
    d=draft(client,contact(client)['id'],True)
    application_workflow.store_resume('new.pdf',text_pdf('New verified resume'))
    assert client.get('/api/outreach/drafts',headers=HEADERS).json()['items'][0]['state']=='draft'
    assert client.post(f"/api/outreach/drafts/{d['id']}/approve",headers=HEADERS,json={'confirmed':True,'recipient_relevance_confirmed':True}).status_code==409
    assert client.post(f"/api/outreach/drafts/{d['id']}/resume",headers=HEADERS).status_code==200
    assert client.post(f"/api/outreach/drafts/{d['id']}/approve",headers=HEADERS,json={'confirmed':True,'recipient_relevance_confirmed':True}).status_code==200


def test_finite_campaign_preview_includes_followups_and_detects_edits(client):
    c,d=campaign(client)
    p=activate(client,c)
    assert len(p['items'])==3
    assert p['snapshot']['candidate']['resume']['filename']=='fixture.pdf'
    assert p['snapshot']['rules']['first_followup_days']==5
    assert client.post(f"/api/outreach/drafts/{d['id']}/edit",headers=HEADERS,json={'subject':'Changed subject','body':'Reviewed new body.'}).status_code==200
    r=client.post(f"/api/outreach/campaigns/{c['id']}/activate",headers=HEADERS,json={'confirmed':True,'profile_confirmed':True,'preview_hash':p['preview_hash']})
    assert r.status_code==409


def test_future_introduction_does_not_invent_vacancy_or_create_job(client):
    c,d=campaign(client,'introduction')
    assert d['job_id'] is None
    assert 'future operations opportunities at Example' in d['body']
    assert 'opening' not in d['body']
    with outreach.connect() as db:assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    activate(client,c)


def test_global_stop_business_hours_and_unknown_timezone(client,monkeypatch):
    d=draft(client,contact(client)['id'],True)
    calls=gmail_receipts(monkeypatch)
    engine.save_settings({'paused':True})
    assert client.post(f"/api/outreach/drafts/{d['id']}/send",headers=HEADERS).status_code==409
    engine.save_settings({'paused':False})
    monkeypatch.setattr(engine,'utcnow',lambda:datetime(2026,11,14,5,tzinfo=timezone.utc))
    assert client.post(f"/api/outreach/drafts/{d['id']}/send",headers=HEADERS).status_code==409
    assert not calls
    client.post('/api/outreach/stop',headers=HEADERS)
    assert client.get('/api/outreach/drafts',headers=HEADERS).json()['items'][0]['state']=='draft'


def test_one_initial_per_company(client,monkeypatch):
    first=draft(client,contact(client)['id'],True)
    second=draft(client,contact(client,'other@example.com')['id'],True)
    calls=gmail_receipts(monkeypatch)
    assert client.post(f"/api/outreach/drafts/{first['id']}/send",headers=HEADERS).json()['state']=='sent'
    assert client.post(f"/api/outreach/drafts/{second['id']}/send",headers=HEADERS).status_code==409
    assert len(calls)==1


def test_followup_threading_due_and_shared_cap(client,monkeypatch):
    c,d=campaign(client)
    outreach.save_policy({'daily_limit':1,'auto_send_approved':True})
    activate(client,c)
    calls=gmail_receipts(monkeypatch)
    first=client.post(f"/api/outreach/drafts/{d['id']}/send",headers=HEADERS)
    assert first.status_code==200,first.text
    with outreach.connect() as db:
        f=dict(db.execute('SELECT * FROM outreach_drafts WHERE parent_id=? AND step_number=1',(d['id'],)).fetchone())
    assert f['due_at'].startswith('2026-11-20')
    # A manual send cannot bypass fresh reply synchronization.
    assert client.post(f"/api/outreach/drafts/{f['id']}/send",headers=HEADERS).status_code==409
    monkeypatch.setattr(engine,'utcnow',lambda:datetime(2026,11,20,6,tzinfo=timezone.utc))
    result=asyncio.run(outreach.dispatch_approved(followups_allowed=True,checked_roots=[d['id']]))
    assert result['sent']==1,result
    assert calls[1]['threadId']=='thread-1'
    message=BytesParser(policy=email_policy.default).parsebytes(base64.urlsafe_b64decode(calls[1]['raw']))
    assert message['In-Reply-To']
    assert message['References']==message['In-Reply-To']
    assert len(list(message.iter_attachments()))==1
    assert len(calls)==2


def test_any_reply_stops_followups_and_is_private(client,monkeypatch):
    c,d=campaign(client)
    activate(client,c)
    gmail_receipts(monkeypatch)
    client.post(f"/api/outreach/drafts/{d['id']}/send",headers=HEADERS)
    with outreach.connect() as db:
        db.execute("INSERT INTO gmail_tokens(id,email,access_token,refresh_token,token_expiry,granted_scopes,updated_at) VALUES (1,'candidate@example.com','fixture','fixture','2099-01-01',?,?) ON CONFLICT(id) DO UPDATE SET granted_scopes=excluded.granted_scopes",('https://www.googleapis.com/auth/gmail.readonly',outreach.now()))
    async def get(self,url,**kw):
        if url.endswith('/messages'):return httpx.Response(200,json={'messages':[]})
        return httpx.Response(200,json={'messages':[{'id':'incoming-fixture','snippet':'Can we arrange an interview?','labelIds':['INBOX'],'payload':{'headers':[{'name':'From','value':'Recruiter <recruiter@example.com>'},{'name':'Subject','value':'Interview'}]}}]})
    monkeypatch.setattr(httpx.AsyncClient,'get',get)
    r=client.post('/api/outreach/replies/sync',headers=HEADERS)
    assert r.status_code==200,r.text
    overview=client.get('/api/outreach/overview',headers=HEADERS).json()
    assert overview['replies'][0]['category']=='interview'
    assert overview['counts']['cancelled']==2
    assert client.get('/api/outreach/overview').status_code==401
    assert client.post('/api/outreach/replies/sync',headers=HEADERS).status_code==200
    assert len(client.get('/api/outreach/overview',headers=HEADERS).json()['replies'])==1


def test_budget_reserves_ambiguous_enrichment_and_requires_shortlist(client,monkeypatch):
    engine.save_settings({'enrichment_daily':1,'enrichment_monthly':1})
    with outreach.connect() as db:
        db.execute("INSERT INTO outreach_leads(apollo_id,domain,searched_at) VALUES ('fixture-a','example.com',?)",(engine.stamp(),))
        db.execute("INSERT INTO outreach_leads(apollo_id,domain,searched_at) VALUES ('fixture-b','example.com',?)",(engine.stamp(),))
    calls=[]
    async def apollo(*args):calls.append(True);raise __import__('fastapi').HTTPException(502,'Fixture timeout')
    monkeypatch.setattr(outreach,'_apollo',apollo)
    payload={'apollo_id':'fixture-a','confirm_credit_use':True}
    assert client.post('/api/outreach/apollo/enrich',headers=HEADERS,json=payload).status_code==502
    assert client.post('/api/outreach/apollo/enrich',headers=HEADERS,json=payload).status_code==409
    assert client.post('/api/outreach/apollo/enrich',headers=HEADERS,json={'apollo_id':'fixture-b','confirm_credit_use':True}).status_code==429
    assert len(calls)==1


def test_policy_change_and_restart_do_not_expand_campaign(client):
    c,d=campaign(client)
    activate(client,c)
    outreach.init_outreach_db()
    assert engine.settings()['paused'] is False
    engine.save_settings({'enrichment_daily':4})
    assert engine.overview()['campaigns'][0]['state']=='paused'
    assert client.post(f"/api/outreach/drafts/{d['id']}/send",headers=HEADERS).status_code==409


def test_reply_classifications_conservatively_stop():
    for subject,expected in [('Out of office','out_of_office'),('Unfortunately no vacancies','rejection'),('Please share your resume','resume_requested'),('Stop emailing me','opt_out'),('Hello','needs_review')]:
        assert engine.classify_reply({'payload':{'headers':[{'name':'Subject','value':subject}]}})==expected


def test_missing_pdf_blocks_approval(client):
    with outreach.connect() as db:db.execute('DELETE FROM outreach_resume')
    d=draft(client,contact(client)['id'])
    assert 'attached' not in d['body'].lower()
    assert client.post(f"/api/outreach/drafts/{d['id']}/approve",headers=HEADERS,json={'confirmed':True,'recipient_relevance_confirmed':True}).status_code==409


def test_reply_check_failure_prevents_followup_dispatch(client,monkeypatch):
    c,d=campaign(client)
    outreach.save_policy({'auto_send_approved':True})
    activate(client,c)
    calls=gmail_receipts(monkeypatch)
    client.post(f"/api/outreach/drafts/{d['id']}/send",headers=HEADERS)
    async def failed():raise __import__('fastapi').HTTPException(503,'Fixture Gmail outage')
    monkeypatch.setattr(engine,'sync_replies',failed)
    r=client.post('/api/outreach/dispatch',headers=HEADERS)
    assert r.status_code==503
    assert len(calls)==1
    with outreach.connect() as db:assert db.execute('SELECT COUNT(*) FROM outreach_leases').fetchone()[0]==0


def test_batch_preparation_reports_missing_contacts_without_provider_calls(client,monkeypatch):
    from unittest.mock import Mock
    import job_sources
    monkeypatch.setattr(job_sources,'list_jobs',lambda **kw:[{'id':1,'company':'Example','title':'Operations Manager','url':'https://example.com/jobs/1','status':'new'}])
    provider=Mock(side_effect=AssertionError('must not consume credits'))
    monkeypatch.setattr(outreach,'_apollo',provider)
    r=client.post('/api/outreach/prepare-batch',headers=HEADERS,json={})
    assert r.status_code==200,r.text
    assert r.json()['prepared']==0
    assert r.json()['pending_contacts'][0]['company']=='Example'
    person=contact(client)
    r=client.post('/api/outreach/prepare-batch',headers=HEADERS,json={})
    assert r.status_code==200,r.text
    assert r.json()['prepared']==1
    assert len(r.json()['preview']['items'])==3
    provider.assert_not_called()


def test_daily_reset_timezone_and_business_time_boundaries(client,monkeypatch):
    # UTC Sunday evening is already Monday morning in the chosen recipient zone.
    rules=engine.settings()
    moment=datetime(2026,11,15,22,tzinfo=timezone.utc)
    engine.business_time({'timezone':'Pacific/Auckland'},rules,moment)
    with pytest.raises(__import__('fastapi').HTTPException):
        engine.business_time({'timezone':'America/New_York'},rules,moment)
    with pytest.raises(__import__('fastapi').HTTPException):
        engine.business_time({'timezone':''},rules,moment)


def test_profile_change_pauses_campaign_and_revokes_all_queued_approvals(client):
    c,d=campaign(client)
    activate(client,c)
    application_workflow.save_profile({'experience_summary':'Updated verified experience.'})
    overview=engine.overview()
    assert overview['campaigns'][0]['state']=='paused'
    assert overview['counts']['draft']==3
