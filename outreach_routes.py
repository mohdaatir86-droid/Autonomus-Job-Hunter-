"""Private outreach API plus capability-protected public opt-out confirmation."""
import html
import os
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import HTMLResponse
import outreach
import gmail_alerts
import outreach_engine as engine
import resume_store
from fastapi.responses import Response

router=APIRouter()

@router.get('/api/outreach/status')
def status():
    gmail=gmail_alerts.get_gmail_status()
    return {'apollo_configured':bool(os.getenv('APOLLO_API_KEY','').strip()),'gmail':{key:gmail.get(key) for key in ['connected','oauth_configured','send_authorized']},'policy':outreach.policy(),'review_required':True,'automatic_followups':True,'resume':resume_store.get(),'rules':engine.settings(),'scheduler_enabled':os.getenv('ENABLE_SCHEDULER','true').lower()=='true'}

@router.get('/api/outreach/contacts')
def contacts():return {'items':outreach.list_records('outreach_contacts')}

@router.post('/api/outreach/contacts')
def add_contact(payload:dict):return outreach.create_contact(payload)

@router.post('/api/outreach/contacts/{contact_id}/verify')
def verify(contact_id:int,payload:dict):return outreach.verify_contact(contact_id,payload)

@router.post('/api/outreach/contacts/{contact_id}/suppress')
def suppress(contact_id:int):return outreach.suppress_contact(contact_id)

@router.post('/api/outreach/apollo/search')
async def search(payload:dict):return await outreach.search_apollo(payload)

@router.post('/api/outreach/apollo/enrich')
async def enrich(payload:dict):return await outreach.enrich_apollo(payload)

@router.get('/api/outreach/drafts')
def drafts():return {'items':outreach.list_records('outreach_drafts')}

@router.post('/api/outreach/drafts')
def draft(payload:dict):
    if type(payload.get('contact_id')) is not int:raise HTTPException(422,'Choose a contact.')
    if payload.get('campaign_id') is not None and type(payload['campaign_id']) is not int:raise HTTPException(422,'Choose a valid campaign.')
    return outreach.create_draft(payload['contact_id'],payload.get('job_id'),campaign_id=payload.get('campaign_id'),target_company=payload.get('target_company',''),target_domain=payload.get('target_domain',''))

@router.post('/api/outreach/drafts/{draft_id}/edit')
def edit(draft_id:int,payload:dict):return outreach.edit_draft(draft_id,payload)

@router.post('/api/outreach/drafts/{draft_id}/approve')
def approve(draft_id:int,payload:dict):return outreach.approve_draft(draft_id,payload)

@router.post('/api/outreach/drafts/{draft_id}/send')
async def send(draft_id:int):return await outreach.send_draft(draft_id)

@router.post('/api/outreach/dispatch')
async def dispatch():return await engine.tick()

@router.post('/api/outreach/policy')
def policy(payload:dict):return outreach.save_policy(payload)

@router.get('/outreach/unsubscribe',response_class=HTMLResponse)
def unsubscribe_form(token:str=''):
    outreach.unsubscribe_contact(token)
    return HTMLResponse('<!doctype html><meta name="viewport" content="width=device-width"><title>Stop job enquiries</title><h1>Stop further job enquiries</h1><p>Confirm that you do not want further recruitment enquiries from this candidate.</p><form method="post"><input type="hidden" name="token" value="'+html.escape(token,quote=True)+'"><button>Stop further emails</button></form>',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

@router.post('/outreach/unsubscribe')
async def unsubscribe(request:Request):
    form=await request.form()
    contact_id=outreach.unsubscribe_contact(str(form.get('token') or ''))
    outreach.suppress_contact(contact_id)
    return HTMLResponse('<!doctype html><title>Emails stopped</title><h1>Further job enquiries have been stopped.</h1>',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@router.get('/api/outreach/overview')
def overview():return engine.overview()

@router.post('/api/outreach/rules')
def rules(payload:dict):return engine.save_settings(payload)

@router.post('/api/outreach/stop')
def stop():return engine.global_stop()

@router.get('/api/outreach/resume')
def resume():
    item=resume_store.get(include_bytes=True)
    if not item:raise HTTPException(404,'Upload the original PDF in Profile & rules first.')
    return Response(item['content'],media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="'+item['filename']+'"','Cache-Control':'no-store'})

@router.post('/api/outreach/drafts/{draft_id}/resume')
def bind_resume(draft_id:int):
    item=resume_store.get()
    if not item:raise HTTPException(409,'Upload your original PDF first.')
    with outreach.connect() as db:
        draft=outreach._row(db,'outreach_drafts',draft_id)
        if draft['state'] not in {'draft','approved','failed'}:raise HTTPException(409,'Only unsent drafts may change attachments.')
        changed=db.execute("UPDATE outreach_drafts SET resume_sha=?,state='draft',approval_hash='',body=?,updated_at=? WHERE id=? AND state IN ('draft','approved','failed') AND updated_at=?",(item['sha256'],draft['body'].replace('I can share my resume on request.','I’ve attached my resume.'),outreach.now(),draft_id,draft['updated_at']))
        if changed.rowcount!=1:raise HTTPException(409,'Draft changed; reload before binding the PDF.')
    return {'attached':item,'approval_required':True}

@router.post('/api/outreach/campaigns')
def campaign(payload:dict):return engine.create_campaign(payload)

@router.get('/api/outreach/campaigns/{campaign_id}/preview')
def preview(campaign_id:int):return engine.campaign_preview(campaign_id)

@router.post('/api/outreach/campaigns/{campaign_id}/activate')
def activate(campaign_id:int,payload:dict):return engine.activate_campaign(campaign_id,payload)

@router.post('/api/outreach/campaigns/{campaign_id}/pause')
def pause(campaign_id:int):
    with outreach.connect() as db:db.execute("UPDATE outreach_campaigns SET state='paused',updated_at=? WHERE id=?",(outreach.now(),campaign_id))
    return {'paused':True}

@router.post('/api/outreach/replies/sync')
async def replies():return await engine.sync_replies()


@router.post('/api/outreach/prepare-batch')
def prepare_batch(payload:dict):return engine.prepare_batch(payload)
