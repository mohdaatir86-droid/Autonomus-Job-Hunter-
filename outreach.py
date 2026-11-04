"""Single-candidate recruiter outreach: evidenced contacts, review, and Gmail queue."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from database import connect, postgres_enabled
from application_workflow import get_profile_full
import gmail_alerts

SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'
CANDIDATE_ID = 1


def now():
    return datetime.now(timezone.utc).isoformat()


def init_outreach_db():
    identity = 'SERIAL PRIMARY KEY' if postgres_enabled() else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    with connect() as db:
        db.execute(f'''CREATE TABLE IF NOT EXISTS outreach_contacts (
            id {identity}, candidate_id INTEGER NOT NULL DEFAULT 1 CHECK(candidate_id=1),
            email TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL, apollo_id TEXT NOT NULL DEFAULT '',
            email_status TEXT NOT NULL DEFAULT 'unverified', verified INTEGER NOT NULL DEFAULT 0,
            verification_note TEXT NOT NULL DEFAULT '', suppressed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(candidate_id,email))''')
        db.execute(f'''CREATE TABLE IF NOT EXISTS outreach_drafts (
            id {identity}, candidate_id INTEGER NOT NULL DEFAULT 1 CHECK(candidate_id=1),
            contact_id INTEGER NOT NULL, job_id INTEGER NOT NULL, sender_email TEXT NOT NULL,
            recipient_email TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'draft', approval_hash TEXT NOT NULL DEFAULT '',
            provider_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sent_at TEXT,
            UNIQUE(candidate_id,contact_id,job_id),
            FOREIGN KEY(contact_id) REFERENCES outreach_contacts(id), FOREIGN KEY(job_id) REFERENCES jobs(id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_policy (
            id INTEGER PRIMARY KEY CHECK(id=1), auto_send_approved INTEGER NOT NULL DEFAULT 0,
            daily_limit INTEGER NOT NULL DEFAULT 5)''')
        db.execute('INSERT INTO outreach_policy(id) VALUES (1) ON CONFLICT(id) DO NOTHING')
        db.execute('CREATE TABLE IF NOT EXISTS outreach_recipient_usage (email TEXT PRIMARY KEY, reserved_at TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_daily_usage (
            day TEXT PRIMARY KEY, reserved INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_unsubscribe_tokens (
            token_hash TEXT PRIMARY KEY, contact_id INTEGER NOT NULL,
            FOREIGN KEY(contact_id) REFERENCES outreach_contacts(id))''')


def _email(value):
    value = str(value or '').strip().lower()
    if len(value) > 254 or '\n' in value or '\r' in value or parseaddr(value)[1] != value or not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', value):
        raise HTTPException(422, 'Enter one valid email address; addresses are never guessed.')
    return value


def _source(value):
    value = str(value or '').strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        raise HTTPException(422, "Enter a valid professional source URL.")
    if len(value) > 1500 or parsed.scheme != 'https' or not parsed.hostname or parsed.username:
        raise HTTPException(422, 'A public HTTPS professional source URL is required.')
    return value


def _serialize(row):
    result = dict(row)
    for field in ['verified','suppressed','auto_send_approved']:
        if field in result:
            result[field] = bool(result[field])
    return result


def _row(db, table, row_id):
    # Table names are internal constants, never request input.
    row = db.execute(f'SELECT * FROM {table} WHERE id=? AND candidate_id=?', (row_id,CANDIDATE_ID)).fetchone()
    if not row:
        raise HTTPException(404, 'Record not found')
    return _serialize(row)


def create_contact(payload):
    email = _email(payload.get('email'))
    source = _source(payload.get('source_url'))
    timestamp = now()
    with connect() as db:
        db.execute('''INSERT INTO outreach_contacts(candidate_id,email,name,company,title,source_url,apollo_id,email_status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id,email) DO NOTHING''',
            (CANDIDATE_ID,email,str(payload.get('name') or '')[:200],str(payload.get('company') or '')[:200],str(payload.get('title') or '')[:200],source,str(payload.get('apollo_id') or '')[:100],str(payload.get('email_status') or 'unverified')[:40],timestamp,timestamp))
        row = db.execute('SELECT * FROM outreach_contacts WHERE candidate_id=? AND email=?',(CANDIDATE_ID,email)).fetchone()
        return _serialize(row)


def verify_contact(contact_id, payload):
    if not all(payload.get(key) is True for key in ['email_confirmed','employer_confirmed','role_confirmed']):
        raise HTTPException(422, 'Confirm the business email, current employer and recruiting/hiring responsibility.')
    note = str(payload.get('note') or '').strip()
    if len(note) < 15 or len(note) > 1000:
        raise HTTPException(422, 'Record a verification note of 15–1000 characters.')
    with connect() as db:
        contact = _row(db,'outreach_contacts',contact_id)
        if contact['suppressed']:
            raise HTTPException(409, 'This recipient is suppressed; do not contact them.')
        db.execute('UPDATE outreach_contacts SET verified=1,verification_note=?,updated_at=? WHERE id=?',(note,now(),contact_id))
        return _row(db,'outreach_contacts',contact_id)


def suppress_contact(contact_id):
    with connect() as db:
        _row(db,'outreach_contacts',contact_id)
        db.execute('UPDATE outreach_contacts SET suppressed=1,updated_at=? WHERE id=?',(now(),contact_id))
        db.execute("UPDATE outreach_drafts SET state='cancelled',updated_at=? WHERE contact_id=? AND state IN ('draft','approved','failed')",(now(),contact_id))
    return {'suppressed':True}


def policy():
    with connect() as db:
        return _serialize(db.execute('SELECT * FROM outreach_policy WHERE id=1').fetchone())


def save_policy(payload):
    current = policy()
    cap = payload.get('daily_limit',current['daily_limit'])
    enabled = payload.get('auto_send_approved',bool(current['auto_send_approved']))
    if not isinstance(cap,int) or isinstance(cap,bool) or not 1 <= cap <= 20 or not isinstance(enabled,bool):
        raise HTTPException(422, 'Daily limit must be 1–20 and automatic sending must be a boolean.')
    with connect() as db:
        db.execute('UPDATE outreach_policy SET daily_limit=?,auto_send_approved=? WHERE id=1',(cap,int(enabled)))
    return policy()


def _job(db, job_id):
    row = db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:
        raise HTTPException(404,'Job not found')
    job = dict(row)
    if job['status'] in {'closed','rejected','not_relevant','offer'}:
        raise HTTPException(409,'This job is closed or already actioned; outreach is disabled.')
    return job


def create_draft(contact_id, job_id):
    profile = get_profile_full()
    if not all(str(profile.get(key) or '').strip() for key in ['full_name','email','resume_text']):
        raise HTTPException(409,'Complete your private profile and PDF resume first.')
    sender = _email(profile['email'])
    timestamp = now()
    with connect() as db:
        contact = _row(db,'outreach_contacts',contact_id)
        if contact['suppressed']:
            raise HTTPException(409,'Recipient is suppressed.')
        job = _job(db,job_id)
        existing = db.execute('SELECT * FROM outreach_drafts WHERE candidate_id=? AND contact_id=? AND job_id=?',(CANDIDATE_ID,contact_id,job_id)).fetchone()
        if existing:
            return dict(existing)
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO outreach_unsubscribe_tokens(token_hash,contact_id) VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),contact_id))
        base = os.getenv('PORTAL_PUBLIC_URL','https://autonomous-job-hunter-docker.onrender.com').rstrip('/')
        body = f"Hello {contact['name'] or 'Hiring Team'},\n\nI’m reaching out about the {job['title']} opportunity at {job['company'] or 'your organization'}.\n\n"
        summary = str(profile.get('experience_summary') or profile.get('headline') or '').strip()
        if summary:
            body += summary[:900] + '\n\n'
        text = (str(job.get('description') or '')+' '+job['title']).lower()
        skills = [skill for skill in profile.get('skills',[]) if skill.lower() in text][:3]
        if skills:
            body += 'Relevant skills from my profile: '+', '.join(skills)+'.\n\n'
        body += f"Are you the right person to speak with about this role, or could you direct me to the hiring team? I can share my resume on request.\n\nRegards,\n{profile['full_name']}\n{sender}\n\nIf you prefer no further job enquiries from me, you can opt out here:\n{base}/outreach/unsubscribe?token={token}"
        subject = f"Application enquiry: {job['title']} — {profile['full_name']}".replace('\r',' ').replace('\n',' ')[:200]
        db.execute('''INSERT INTO outreach_drafts(candidate_id,contact_id,job_id,sender_email,recipient_email,subject,body,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',(CANDIDATE_ID,contact_id,job_id,sender,contact['email'],subject,body,timestamp,timestamp))
        return dict(db.execute('SELECT * FROM outreach_drafts WHERE candidate_id=? AND contact_id=? AND job_id=?',(CANDIDATE_ID,contact_id,job_id)).fetchone())


def _fingerprint(draft,contact,profile,job):
    values = [draft['sender_email'],draft['recipient_email'],draft['subject'],draft['body'],contact['email'],contact['verified'],contact['suppressed'],contact['updated_at'],profile.get('updated_at'),profile.get('email'),job['title'],job['company'],job['url']]
    return hashlib.sha256(json.dumps(values,ensure_ascii=False).encode()).hexdigest()


def _eligible(draft,contact,profile):
    if contact['suppressed'] or not contact['verified']:
        raise HTTPException(409,'Recipient requires verification or has opted out.')
    if draft['recipient_email'] != contact['email'] or draft['sender_email'] != _email(profile.get('email')):
        raise HTTPException(409,'The sender or recipient changed. Prepare a new reviewed draft.')


def approve_draft(draft_id,payload):
    if payload.get('confirmed') is not True or payload.get('recipient_relevance_confirmed') is not True:
        raise HTTPException(422,'Review the exact recipient, role relevance, subject and body before approving.')
    profile = get_profile_full()
    with connect() as db:
        draft = _row(db,'outreach_drafts',draft_id)
        if draft['state'] not in {'draft','approved','failed'}:
            raise HTTPException(409,'This draft cannot be approved again.')
        contact = _row(db,'outreach_contacts',draft['contact_id'])
        job = _job(db,draft['job_id'])
        _eligible(draft,contact,profile)
        fingerprint = _fingerprint(draft,contact,profile,job)
        db.execute("UPDATE outreach_drafts SET state='approved',approval_hash=?,error='',updated_at=? WHERE id=?",(fingerprint,now(),draft_id))
        return _row(db,'outreach_drafts',draft_id)


def edit_draft(draft_id,payload):
    subject = str(payload.get('subject') or '').strip()
    body = str(payload.get('body') or '').strip()
    if not subject or len(subject)>200 or '\r' in subject or '\n' in subject or not body or len(body)>15000:
        raise HTTPException(422,'A single-line subject and body up to 15000 characters are required.')
    with connect() as db:
        draft = _row(db,'outreach_drafts',draft_id)
        if draft['state'] not in {'draft','approved','failed'}:
            raise HTTPException(409,'Sent, in-flight or ambiguous drafts cannot be edited/retried.')
        # Preserve the working opt-out capability even when the owner edits the text.
        link = re.search(r'https://[^\s]+/outreach/unsubscribe\?token=[\w-]+',draft['body'])
        if link and link.group(0) not in body:
            body += '\n\nStop further enquiries: '+link.group(0)
        db.execute("UPDATE outreach_drafts SET subject=?,body=?,state='draft',approval_hash='',updated_at=? WHERE id=?",(subject,body,now(),draft_id))
        return _row(db,'outreach_drafts',draft_id)


def list_records(table):
    with connect() as db:
        return [_serialize(row) for row in db.execute(f'SELECT * FROM {table} WHERE candidate_id=? ORDER BY id DESC LIMIT 200',(CANDIDATE_ID,))]


def unsubscribe_contact(token):
    if not token or len(token)>100:
        raise HTTPException(400,'Invalid unsubscribe link')
    with connect() as db:
        row = db.execute('SELECT contact_id FROM outreach_unsubscribe_tokens WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row:
        raise HTTPException(400,'Invalid unsubscribe link')
    return int(row['contact_id'])


async def send_draft(draft_id):
    profile = get_profile_full()
    gmail = gmail_alerts.get_gmail_status()
    if not gmail.get('send_authorized') or str(gmail.get('email') or '').lower() != str(profile.get('email') or '').lower():
        raise HTTPException(409,'Reconnect the profile Gmail account and approve Gmail sending permission.')
    try:
        token = await gmail_alerts._access_token()
    except (httpx.HTTPError,ValueError):
        raise HTTPException(503,'Gmail authorization could not be refreshed; reconnect your account.')
    if not token:
        raise HTTPException(409,'Connect Gmail first.')
    timestamp = now()
    with connect() as db:
        draft = _row(db,'outreach_drafts',draft_id)
        if draft['state'] != 'approved':
            raise HTTPException(409,'Only an explicitly approved draft may be sent; sent or ambiguous deliveries are not retried.')
        contact = _row(db,'outreach_contacts',draft['contact_id'])
        job = _job(db,draft['job_id'])
        _eligible(draft,contact,profile)
        if _fingerprint(draft,contact,profile,job) != draft['approval_hash']:
            raise HTTPException(409,'Profile, contact or draft changed after approval. Review and approve again.')
        cutoff = (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
        recent = db.execute("SELECT id FROM outreach_drafts WHERE recipient_email=? AND state IN ('sent','sending','delivery_unknown') AND (sent_at>=? OR updated_at>=?)",(contact['email'],cutoff,cutoff)).fetchone()
        if recent:
            raise HTTPException(409,'This recipient has a recent or uncertain delivery; no additional email will be sent.')
        claimed = db.execute("UPDATE outreach_drafts SET state='sending',updated_at=? WHERE id=? AND state='approved' AND approval_hash=?",(timestamp,draft_id,draft['approval_hash']))
        if claimed.rowcount != 1:
            raise HTTPException(409,'Another worker has claimed this email.')
        # A unique recipient reservation serializes claims across jobs/processes.
        reserved = db.execute('INSERT INTO outreach_recipient_usage(email,reserved_at) VALUES (?,?) ON CONFLICT(email) DO NOTHING',(contact['email'],timestamp))
        if reserved.rowcount != 1:
            renewed = db.execute('UPDATE outreach_recipient_usage SET reserved_at=? WHERE email=? AND reserved_at<?',(timestamp,contact['email'],cutoff))
            if renewed.rowcount != 1:
                raise HTTPException(409,'A recent outreach attempt already reserved this recipient.')
        day = timestamp[:10]
        cap = db.execute('SELECT daily_limit FROM outreach_policy WHERE id=1').fetchone()[0]
        db.execute('INSERT INTO outreach_daily_usage(day,reserved) VALUES (?,0) ON CONFLICT(day) DO NOTHING',(day,))
        usage = db.execute('UPDATE outreach_daily_usage SET reserved=reserved+1 WHERE day=? AND reserved<?',(day,cap))
        if usage.rowcount != 1:
            raise HTTPException(429,'Daily outreach limit reached; approved emails remain queued.')
    message = EmailMessage()
    message['From'] = draft['sender_email']
    message['To'] = draft['recipient_email']
    message['Subject'] = draft['subject']
    message['Message-ID'] = f"<job-hunter-outreach-{draft_id}@{urlparse(os.getenv('PORTAL_PUBLIC_URL','https://autonomous-job-hunter-docker.onrender.com')).hostname}>"
    message.set_content(draft['body'])
    state, receipt, error = 'delivery_unknown', '', 'Delivery outcome is uncertain; check Gmail Sent before any further action.'
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(gmail_alerts.GMAIL_API+'/messages/send',headers={'Authorization':'Bearer '+token},json={'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()})
        if 200 <= response.status_code < 300:
            try:
                receipt = str(response.json().get('id') or '')[:200]
            except ValueError:
                pass
            if receipt:
                state,error = 'sent',''
        elif response.status_code < 500:
            state,error = 'failed',f'Gmail rejected the request (HTTP {response.status_code}); check permission, quota and sender account.'
    except httpx.HTTPError:
        pass
    with connect() as db:
        if state == 'failed':
            db.execute('DELETE FROM outreach_recipient_usage WHERE email=? AND reserved_at=?',(draft['recipient_email'],timestamp))
        db.execute('UPDATE outreach_drafts SET state=?,provider_id=?,error=?,updated_at=?,sent_at=? WHERE id=?',(state,receipt,error,now(),now() if state=='sent' else None,draft_id))
        return _row(db,'outreach_drafts',draft_id)


async def dispatch_approved():
    if not policy()['auto_send_approved']:
        return {'sent':0,'message':'Automatic approved-email sending is disabled.'}
    with connect() as db:
        ids = [row['id'] for row in db.execute("SELECT id FROM outreach_drafts WHERE state='approved' AND candidate_id=? ORDER BY id LIMIT 20",(CANDIDATE_ID,))]
    results=[]
    for draft_id in ids:
        try:
            results.append(await send_draft(draft_id))
        except HTTPException as exc:
            if exc.status_code in {429,503}:break
            results.append({'id':draft_id,'state':'blocked','error':str(exc.detail)})
    return {'sent':sum(row['state']=='sent' for row in results),'results':results}


async def _apollo(path,params):
    key = os.getenv('APOLLO_API_KEY','').strip()
    if not key:
        raise HTTPException(503,'Add APOLLO_API_KEY securely in Render. Search requires an eligible Apollo API key.')
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post('https://api.apollo.io/api/v1/'+path,headers={'X-Api-Key':key,'Content-Type':'application/json'},params=params)
        if response.status_code>=400:
            raise HTTPException(502,f'Apollo HTTP {response.status_code}; check API-key permissions, account eligibility and credits.')
        result = response.json()
        if not isinstance(result,dict):raise ValueError()
        return result
    except (httpx.HTTPError,ValueError):
        raise HTTPException(502,'Apollo did not return a usable response. No email was sent.')


async def search_apollo(payload):
    domain = str(payload.get('company_domain') or '').strip().lower()
    if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain) or len(domain)>253 or domain in {'gmail.com','yahoo.com','outlook.com'}:
        raise HTTPException(422,'Enter the employer company domain, for example meesho.com.')
    params = [('q_organization_domains_list[]',domain),('contact_email_status[]','verified'),('page',1),('per_page',10)]
    for title in ['Recruiter','Talent Acquisition','HR Manager','Human Resources Manager']:
        params.append(('person_titles[]',title))
    data = await _apollo('mixed_people/api_search',params)
    items=[]
    for person in (data.get('people') or [])[:10]:
        identifier = str(person.get('id') or person.get('person_id') or '')
        if identifier:
            items.append({'apollo_id':identifier,'name':str(person.get('name') or person.get('first_name') or '')[:200],'title':str(person.get('title') or '')[:200],'company':str((person.get('organization') or {}).get('name') or '')[:200]})
    return {'items':items,'note':'Search does not reveal email addresses. Work-email enrichment requires confirmation and may consume Apollo credits. Company-domain matches may include former employers; verify current responsibility.'}


async def enrich_apollo(payload):
    if payload.get('confirm_credit_use') is not True:
        raise HTTPException(409,'Confirm Apollo credit use before revealing a work email.')
    identifier = str(payload.get('apollo_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identifier):
        raise HTTPException(422,'A valid Apollo person ID is required.')
    with connect() as db:
        cached = db.execute('SELECT * FROM outreach_contacts WHERE candidate_id=? AND apollo_id=?',(CANDIDATE_ID,identifier)).fetchone()
        if cached:
            return _serialize(cached)
    data = await _apollo('people/match',{'id':identifier,'reveal_personal_emails':False,'reveal_phone_number':False})
    person = data.get('person') or {}
    if not person.get('email'):
        raise HTTPException(422,'Apollo did not return a work email. No address will be guessed.')
    return create_contact({'email':person['email'],'name':person.get('name') or person.get('first_name'),'title':person.get('title'),'company':(person.get('organization') or {}).get('name'),'source_url':person.get('linkedin_url') or 'https://app.apollo.io/#/people/'+identifier,'apollo_id':identifier,'email_status':person.get('email_status') or 'unverified'})
