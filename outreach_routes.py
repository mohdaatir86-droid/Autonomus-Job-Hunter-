"""Private outreach API plus capability-protected public opt-out confirmation."""
import html
import os
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import HTMLResponse
import outreach
import gmail_alerts

router=APIRouter()

@router.get('/api/outreach/status')
def status():
    gmail=gmail_alerts.get_gmail_status()
    return {'apollo_configured':bool(os.getenv('APOLLO_API_KEY','').strip()),'gmail':{key:gmail.get(key) for key in ['connected','oauth_configured','send_authorized']},'policy':outreach.policy(),'review_required':True,'automatic_followups':False,'scheduler_enabled':os.getenv('ENABLE_SCHEDULER','true').lower()=='true'}

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
    if any(not isinstance(payload.get(key),int) or isinstance(payload.get(key),bool) for key in ['contact_id','job_id']):raise HTTPException(422,'Choose a contact and job.')
    return outreach.create_draft(payload['contact_id'],payload['job_id'])

@router.post('/api/outreach/drafts/{draft_id}/edit')
def edit(draft_id:int,payload:dict):return outreach.edit_draft(draft_id,payload)

@router.post('/api/outreach/drafts/{draft_id}/approve')
def approve(draft_id:int,payload:dict):return outreach.approve_draft(draft_id,payload)

@router.post('/api/outreach/drafts/{draft_id}/send')
async def send(draft_id:int):return await outreach.send_draft(draft_id)

@router.post('/api/outreach/dispatch')
async def dispatch():return await outreach.dispatch_approved()

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
