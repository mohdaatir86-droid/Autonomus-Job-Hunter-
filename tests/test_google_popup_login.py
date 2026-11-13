"""Browser-bound popup sign-in; credentials/certificates are fixture-only."""
import pytest
import main,application_workflow
from test_private_workspace import client


def prepare(client,monkeypatch,**claims):
    application_workflow.save_profile({'email':'owner@example.com'})
    config=client.get('/api/auth/google/config')
    assert config.headers['cache-control']=='no-store'
    cookie=config.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    nonce=config.json()['nonce']
    monkeypatch.setattr(main,'_verify_google_credential',lambda token:{'email':'owner@example.com','email_verified':True,'sub':'fixture-owner','nonce':nonce,**claims})
    return {'credential':'fixture-signed-credential','login_nonce':nonce}


def test_popup_login_creates_private_session_and_consumes_nonce(client,monkeypatch):
    data=prepare(client,monkeypatch)
    response=client.post('/api/auth/google/session',json=data,headers={'Origin':'http://testserver'})
    assert response.status_code==200,response.text
    assert response.json()=={'authenticated':True}
    assert client.get('/api/auth/session').json()['authenticated'] is True
    assert client.get('/api/profile').status_code==200
    assert client.post('/api/auth/google/session',json=data,headers={'Origin':'http://testserver'}).status_code==403
    assert client.post('/api/settings',json={}).status_code==403, 'Normal session mutation CSRF protection stays enabled'


@pytest.mark.parametrize('alteration',['wrong_origin','missing_origin','wrong_cookie','missing_cookie','wrong_token_nonce','expired_nonce','malformed_body'])
def test_popup_login_rejects_unbound_or_expired_tokens(client,monkeypatch,alteration):
    data=prepare(client,monkeypatch)
    headers={'Origin':'http://testserver'}
    if alteration=='wrong_origin':headers['Origin']='https://unrelated.example'
    if alteration=='missing_origin':headers={}
    if alteration=='wrong_cookie':client.cookies.clear();client.cookies.set(main.GOOGLE_LOGIN_COOKIE,'wrong',path='/api/auth/google/session')
    if alteration=='missing_cookie':client.cookies.clear()
    if alteration=='wrong_token_nonce':monkeypatch.setattr(main,'_verify_google_credential',lambda token:{'email':'owner@example.com','email_verified':True,'sub':'fixture-owner','nonce':'different'})
    if alteration=='expired_nonce':
        actual=main.time.time()
        monkeypatch.setattr(main.time,'time',lambda:actual+601)
    if alteration=='malformed_body':data=[]
    r=client.post('/api/auth/google/session',json=data,headers=headers)
    assert r.status_code in {401,403,422},r.text
    assert client.get('/api/auth/session').json()=={'authenticated':False}
    assert client.get('/api/profile').status_code==401


@pytest.mark.parametrize('claims',[{'email':'other@example.com'},{'email_verified':False},{'sub':''}])
def test_popup_login_preserves_identity_and_profile_binding(client,monkeypatch,claims):
    data=prepare(client,monkeypatch,**claims)
    r=client.post('/api/auth/google/session',json=data,headers={'Origin':'http://testserver'})
    assert r.status_code==401
    assert client.get('/api/auth/session').json()=={'authenticated':False}


def test_legacy_redirect_retains_double_submit_csrf_validation(client,monkeypatch):
    prepare(client,monkeypatch)
    client.cookies.clear()
    r=client.post('/api/auth/google/session',data={'credential':'fixture','g_csrf_token':'missing-cookie'},follow_redirects=False)
    assert r.status_code==303
    assert 'google_signin=failed' in r.headers['location']
