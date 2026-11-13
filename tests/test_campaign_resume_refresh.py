import application_workflow, outreach, resume_store
from test_outreach import client, HEADERS
from test_outreach_campaigns import campaign, activate
from test_workflow_regressions import text_pdf


def test_batch_resume_refresh_is_private_and_needs_existing_campaign(client):
    c,_=campaign(client)
    assert client.post(f"/api/outreach/campaigns/{c['id']}/resume").status_code==401
    assert client.post('/api/outreach/campaigns/999999/resume',headers=HEADERS).status_code==404
    with outreach.connect() as db:db.execute('DELETE FROM outreach_resume')
    assert client.post(f"/api/outreach/campaigns/{c['id']}/resume",headers=HEADERS).status_code==409


def test_refresh_binds_all_unsent_followups_and_revokes_batch_approval(client):
    c,_=campaign(client);activate(client,c)
    previous=client.get('/api/outreach/drafts',headers=HEADERS).json()['items']
    application_workflow.store_resume('updated.pdf',text_pdf('Updated confirmed profile resume'))
    r=client.post(f"/api/outreach/campaigns/{c['id']}/resume",headers=HEADERS)
    assert r.status_code==200 and r.json()['updated']==3 and r.json()['approval_required']
    current=client.get('/api/outreach/drafts',headers=HEADERS).json()['items']
    assert all(d['resume_sha']==resume_store.get()['sha256'] and d['state']=='draft' and not d['approval_hash'] for d in current)
    assert [d['body'] for d in current]==[d['body'] for d in previous]
    with outreach.connect() as db:
        row=db.execute('SELECT state,approval_hash,approved_snapshot FROM outreach_campaigns WHERE id=?',(c['id'],)).fetchone()
        assert row['state']=='paused' and not row['approval_hash'] and not row['approved_snapshot']


def test_refresh_does_not_change_sent_inflight_or_other_campaigns(client):
    c,d=campaign(client);old_sha=d['resume_sha']
    other=__import__('outreach_engine').create_campaign({'name':'Other fixture','kind':'introduction'})
    with outreach.connect() as db:
        db.execute("UPDATE outreach_drafts SET state='sent' WHERE campaign_id=? AND step_number=1",(c['id'],))
        db.execute("UPDATE outreach_drafts SET state='sending' WHERE campaign_id=? AND step_number=2",(c['id'],))
        db.execute('UPDATE outreach_drafts SET campaign_id=? WHERE id=?',(other['id'],d['id']))
    application_workflow.store_resume('new.pdf',text_pdf('New confirmed PDF'))
    result=client.post(f"/api/outreach/campaigns/{c['id']}/resume",headers=HEADERS).json()
    assert result['updated']==0
    drafts=client.get('/api/outreach/drafts',headers=HEADERS).json()['items']
    assert all(row['resume_sha']==old_sha for row in drafts)
