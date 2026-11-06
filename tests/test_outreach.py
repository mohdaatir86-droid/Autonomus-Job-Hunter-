import re
from urllib.parse import parse_qs,urlparse
import httpx
import pytest
from fastapi.testclient import TestClient
import main,job_hunter,application_workflow,gmail_alerts,application_state,browser_worker

HEADERS={'Authorization':'Bearer '+'fixture-outreach-owner-'*3}

@pytest.fixture
def client(tmp_path,monkeypatch):
    path=str(tmp_path/'outreach.sqlite')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('DATABASE_PATH',path)
    monkeypatch.setenv('ENABLE_SCHEDULER','false')
    monkeypatch.setenv('PORTAL_ACCESS_TOKEN','fixture-outreach-owner-'*3)
    monkeypatch.setenv('GOOGLE_CLIENT_ID','fixture-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET','fixture-secret')
    monkeypatch.setenv('APOLLO_API_KEY','fixture-apollo-key')
    for module in [job_hunter,application_workflow,gmail_alerts,application_state,browser_worker]:
        monkeypatch.setattr(module,'DB_PATH',path)
    monkeypatch.setattr(gmail_alerts,'get_gmail_status',lambda:{'connected':True,'oauth_configured':True,'send_authorized':True,'email':'candidate@example.com'})
    async def token():return 'fixture-access-token'
    monkeypatch.setattr(gmail_alerts,'_access_token',token)
    with TestClient(main.app) as c:
        application_workflow.save_profile({'full_name':'Fixture Candidate','email':'candidate@example.com','headline':'Operations manager','experience_summary':'Led warehouse operations.','skills':['inventory']})
        with job_hunter.connect() as db:
            db.execute("UPDATE candidate_profile SET resume_text='Fixture verified resume' WHERE id=1")
            db.execute("INSERT INTO jobs(source,title,company,country,url,description,first_seen_at,last_seen_at) VALUES ('fixture','Operations Manager','Example','India','https://example.com/jobs/1','Warehouse inventory leadership',?,?)",(job_hunter.now_iso(),job_hunter.now_iso()))
        yield c


def contact(client,email='recruiter@example.com',verify=True):
    r=client.post('/api/outreach/contacts',headers=HEADERS,json={'email':email,'name':'Recruiter Fixture','company':'Example','title':'Talent Acquisition','source_url':'https://www.linkedin.com/in/fixture'})
    assert r.status_code==200,r.text
    item=r.json()
    if verify:
        r=client.post(f"/api/outreach/contacts/{item['id']}/verify",headers=HEADERS,json={'email_confirmed':True,'employer_confirmed':True,'role_confirmed':True,'note':'Business email and recruiting responsibility checked against public professional source.'})
        assert r.status_code==200,r.text
    return item


def draft(client,contact_id,approve=False):
    r=client.post('/api/outreach/drafts',headers=HEADERS,json={'contact_id':contact_id,'job_id':1})
    assert r.status_code==200,r.text
    item=r.json()
    if approve:
        r=client.post(f"/api/outreach/drafts/{item['id']}/approve",headers=HEADERS,json={'confirmed':True,'recipient_relevance_confirmed':True})
        assert r.status_code==200,r.text
    return item


def test_private_outreach_status(client):
    assert client.get('/api/outreach/status').status_code==401
    r=client.get('/api/outreach/status',headers=HEADERS)
    assert r.status_code==200
    assert r.json()['gmail']['send_authorized'] is True
    assert 'fixture-apollo-key' not in r.text


def test_verified_contact_truthful_draft_and_approval(client):
    person=contact(client)
    item=draft(client,person['id'],True)
    assert 'Fixture Candidate' in item['body']
    assert 'Led warehouse operations' in item['body']
    assert 'attached' not in item['body'].lower()
    assert '/outreach/unsubscribe?token=' in item['body']


def test_cannot_send_unapproved_or_unverified_contact(client):
    person=contact(client,verify=False)
    item=draft(client,person['id'])
    assert client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS).status_code==409
    assert client.post(f"/api/outreach/drafts/{item['id']}/approve",headers=HEADERS,json={'confirmed':True,'recipient_relevance_confirmed':True}).status_code==409


def test_send_once_and_record_provider_receipt(client,monkeypatch):
    item=draft(client,contact(client)['id'],True)
    calls=[]
    async def post(self,url,**kwargs):
        assert url=='https://gmail.googleapis.com/gmail/v1/users/me/messages/send'
        calls.append(kwargs)
        return httpx.Response(200,json={'id':'fixture-gmail-id','threadId':'fixture-thread'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    result=client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS)
    assert result.status_code==200,result.text
    assert result.json()['state']=='sent'
    assert result.json()['provider_id']=='fixture-gmail-id'
    assert client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS).status_code==409
    assert len(calls)==1


def test_profile_change_invalidates_approval(client):
    item=draft(client,contact(client)['id'],True)
    application_workflow.save_profile({'headline':'Updated verified headline'})
    assert client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS).status_code==409


def test_suppression_and_public_unsubscribe(client):
    person=contact(client)
    item=draft(client,person['id'],True)
    token=re.search(r'/outreach/unsubscribe\?token=([\w-]+)',item['body']).group(1)
    assert client.get('/outreach/unsubscribe',params={'token':token}).status_code==200
    assert client.post('/outreach/unsubscribe',data={'token':token}).status_code==200
    assert client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS).status_code==409
    assert client.get('/outreach/unsubscribe?token=bad').status_code==400


def test_timeout_is_ambiguous_and_never_auto_retried(client,monkeypatch):
    item=draft(client,contact(client)['id'],True)
    async def post(*args,**kwargs):raise httpx.ReadTimeout('fixture')
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    result=client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS)
    assert result.status_code==200
    assert result.json()['state']=='delivery_unknown'
    assert client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS).status_code==409


def test_apollo_search_and_explicit_credit_enrichment(client,monkeypatch):
    calls=[]
    async def post(self,url,**kwargs):
        calls.append((url,kwargs))
        if url.endswith('/mixed_people/api_search'):
            return httpx.Response(200,json={'people':[{'id':'apollo-fixture','first_name':'Recruiter','title':'Recruiter','organization':{'name':'Example'}}]})
        return httpx.Response(200,json={'person':{'id':'apollo-fixture','name':'Recruiter Fixture','email':'recruiter@example.com','email_status':'verified','title':'Recruiter','linkedin_url':'https://linkedin.com/in/fixture','organization':{'name':'Example'}}})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    r=client.post('/api/outreach/apollo/search',headers=HEADERS,json={'company_domain':'example.com'})
    assert r.status_code==200,r.text
    assert r.json()['items'][0]['apollo_id']=='apollo-fixture'
    assert client.post('/api/outreach/apollo/enrich',headers=HEADERS,json={'apollo_id':'apollo-fixture'}).status_code==409
    r=client.post('/api/outreach/apollo/enrich',headers=HEADERS,json={'apollo_id':'apollo-fixture','confirm_credit_use':True})
    assert r.status_code==200,r.text
    assert r.json()['verified'] is False
    assert r.json()['email_status']=='verified'
    assert len(calls)==2
    assert calls[1][1]['json']['reveal_personal_emails'] is False
    assert calls[1][1]['json']['reveal_phone_number'] is False
    assert calls[1][1]['json']['id'] == 'apollo-fixture'
    assert 'params' not in calls[1][1]
    assert calls[1][1]['json']['reveal_phone_number'] is False


def test_gmail_sending_requires_separate_scope_consent(client):
    r=client.post('/api/outreach/gmail/connect',headers=HEADERS)
    assert r.status_code==200,r.text
    scopes=parse_qs(urlparse(r.json()['url']).query)['scope'][0].split()
    assert 'https://www.googleapis.com/auth/gmail.send' in scopes
    assert 'https://www.googleapis.com/auth/gmail.readonly' in scopes


def test_daily_cap_and_approved_queue(client,monkeypatch):
    first=draft(client,contact(client)['id'],True)
    second=draft(client,contact(client,'second@example.com')['id'],True)
    assert client.post('/api/outreach/policy',headers=HEADERS,json={'daily_limit':1,'auto_send_approved':True}).status_code==200
    async def post(*args,**kwargs):return httpx.Response(200,json={'id':'fixture-message'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    r=client.post('/api/outreach/dispatch',headers=HEADERS)
    assert r.status_code==200,r.text
    assert r.json()['sent']==1
    states=client.get('/api/outreach/drafts',headers=HEADERS).json()['items']
    assert sorted(x['state'] for x in states)==['approved','sent']


def test_editing_approved_email_clears_approval(client):
    item=draft(client,contact(client)['id'],True)
    updated=client.post(f"/api/outreach/drafts/{item['id']}/edit",headers=HEADERS,json={'subject':'Updated verified enquiry','body':'Updated truthful email text.'})
    assert updated.status_code==200
    assert updated.json()['state']=='draft'
    assert '/outreach/unsubscribe?token=' in updated.json()['body']
    assert client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS).status_code==409


def test_readonly_gmail_cannot_send_outreach(client,monkeypatch):
    item=draft(client,contact(client)['id'],True)
    monkeypatch.setattr(gmail_alerts,'get_gmail_status',lambda:{'connected':True,'send_authorized':False,'email':'candidate@example.com'})
    assert client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS).status_code==409


def test_malformed_contact_source_returns_validation_error(client):
    response=client.post('/api/outreach/contacts',headers=HEADERS,json={'email':'recruiter@example.com','source_url':'https://['})
    assert response.status_code==422


def test_gmail_exchange_records_granted_send_scope(client,monkeypatch):
    async def post(self,url,**kwargs):return httpx.Response(200,json={'access_token':'fixture-access','refresh_token':'fixture-refresh','expires_in':3600,'scope':'https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send'})
    async def email(token):return 'candidate@example.com'
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    monkeypatch.setattr(gmail_alerts,'_fetch_profile_email',email)
    import asyncio
    assert asyncio.run(gmail_alerts.exchange_code('fixture'))['ok']
    with job_hunter.connect() as db:
        row=db.execute('SELECT granted_scopes FROM gmail_tokens WHERE id=1').fetchone()
        assert 'https://www.googleapis.com/auth/gmail.send' in row[0]


def test_repeated_apollo_reveal_reuses_saved_contact(client,monkeypatch):
    calls=[]
    async def post(self,url,**kwargs):
        calls.append(True)
        return httpx.Response(200,json={'person':{'id':'cached-person','name':'Recruiter','email':'cached@example.com','email_status':'verified','linkedin_url':'https://linkedin.com/in/fixture','organization':{'name':'Example'}}})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    for _ in range(2):
        result=client.post('/api/outreach/apollo/enrich',headers=HEADERS,json={'apollo_id':'cached-person','confirm_credit_use':True})
        assert result.status_code==200
    assert len(calls)==1


def test_definite_provider_rejection_allows_reviewed_retry(client,monkeypatch):
    item=draft(client,contact(client)['id'],True)
    responses=[httpx.Response(403,json={}),httpx.Response(200,json={'id':'fixture-retried'})]
    async def post(*args,**kwargs):return responses.pop(0)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    first=client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS)
    assert first.json()['state']=='failed'
    assert client.post(f"/api/outreach/drafts/{item['id']}/approve",headers=HEADERS,json={'confirmed':True,'recipient_relevance_confirmed':True}).status_code==200
    second=client.post(f"/api/outreach/drafts/{item['id']}/send",headers=HEADERS)
    assert second.status_code==200,second.text
    assert second.json()['state']=='sent'
