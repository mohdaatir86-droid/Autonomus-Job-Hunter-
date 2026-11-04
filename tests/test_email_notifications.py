import asyncio
import httpx
import pytest
import notify


@pytest.fixture(autouse=True)
def email_environment(monkeypatch):
    for key in ['ALERT_CHANNEL','RESEND_API_KEY','EMAIL_FROM','EMAIL_TO','ALERT_MIN_SCORE']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('EMAIL_TO','owner@example.com')


def test_email_is_default_and_does_not_fall_back_to_whatsapp(monkeypatch):
    monkeypatch.setenv('WHATSAPP_PHONE','fixture')
    monkeypatch.setenv('WHATSAPP_CALLMEBOT_APIKEY','fixture')
    assert notify.notify_status()['channel']=='email'
    assert notify.notify_configured() is False


def test_missing_email_credentials_skip_delivery():
    result=asyncio.run(notify.send_notification('Fixture alert'))
    assert result['sent'] is False
    assert result['provider']=='resend'


def test_email_delivery_payload_and_secret_redaction(monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY','fixture-secret-not-production')
    monkeypatch.setenv('EMAIL_FROM','Job Hunter <alerts@example.com>')
    payloads=[]
    async def post(self,url,**kwargs):
        assert url=='https://api.resend.com/emails'
        assert kwargs['headers']['Authorization']=='Bearer fixture-secret-not-production'
        payloads.append(kwargs['json'])
        return httpx.Response(200,json={'id':'fixture-email-id'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    result=asyncio.run(notify.send_notification('Fixture body'))
    assert result['sent'] is True
    assert payloads[0]['to']==['owner@example.com']
    assert payloads[0]['text']=='Fixture body'
    assert 'fixture-secret' not in str(notify.notify_status())+str(result)


def test_email_failure_is_reported_without_provider_body(monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY','fixture-secret')
    async def post(self,url,**kwargs):
        return httpx.Response(429,json={'message':'sensitive fixture-secret detail'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    result=asyncio.run(notify.send_notification('Fixture'))
    assert result['sent'] is False
    assert result['status_code']==429
    assert 'sensitive' not in str(result)
    assert 'fixture-secret' not in str(result)


def test_digest_uses_email_and_filters_threshold(monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY','fixture-secret')
    monkeypatch.setenv('ALERT_MIN_SCORE','80')
    sent=[]
    async def send(text):
        sent.append(text)
        return {'sent':True,'provider':'resend'}
    monkeypatch.setattr(notify,'send_notification',send)
    result=asyncio.run(notify.notify_high_match_jobs([
        {'title':'Operations Manager','match_score':90,'url':'https://example.com/jobs/1'},
        {'title':'Low match','match_score':50},
    ]))
    assert result['count']==1
    assert 'Operations Manager' in sent[0]
    assert 'Low match' not in sent[0]


def test_invalid_email_address_blocks_send(monkeypatch):
    monkeypatch.setenv('RESEND_API_KEY','fixture-secret')
    monkeypatch.setenv('EMAIL_TO','owner@example.com\nBcc:other@example.com')
    assert notify.notify_configured() is False


def test_authenticated_dashboard_test_sends_email(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setenv('PORTAL_ACCESS_TOKEN','fixture-owner-email-key-'*3)
    monkeypatch.setenv('RESEND_API_KEY','fixture-resend-key')
    deliveries=[]
    async def post(self,url,**kwargs):
        deliveries.append(kwargs['json'])
        return httpx.Response(200,json={'id':'fixture-id'})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with TestClient(main.app,raise_server_exceptions=True) as client:
        assert client.post('/api/notify/test').status_code==401
        response=client.post('/api/notify/test',headers={'Authorization':'Bearer '+'fixture-owner-email-key-'*3})
        assert response.status_code==200
        assert response.json()['provider']=='resend'
        assert deliveries[0]['to']==['owner@example.com']
