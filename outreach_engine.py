"""Durable campaign policies, scoped previews, reply monitoring and follow-up queue.

No provider call in this module discovers or sends to an unreviewed recipient.
Campaign activation approves a finite snapshot of exact emails and follow-ups.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException
from database import connect, postgres_enabled
import resume_store

DEFAULTS = dict(paused=True, timezone='Asia/Kolkata', business_start=9, business_end=18,
                enrichment_daily=5, enrichment_monthly=50, followups=2,
                first_followup_days=5, final_followup_days=7)


def utcnow():
    return datetime.now(timezone.utc)


def stamp():
    return utcnow().isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def initialize():
    identity = 'SERIAL PRIMARY KEY' if postgres_enabled() else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    with connect() as db:
        if postgres_enabled():
            db.execute('SELECT pg_advisory_xact_lock(746821951)')
            db.execute('ALTER TABLE outreach_drafts ALTER COLUMN job_id DROP NOT NULL')
        else:
            db.execute('BEGIN IMMEDIATE')
            columns = db.execute('PRAGMA table_info(outreach_drafts)').fetchall()
            if any(x['name']=='job_id' and x['notnull'] for x in columns):
                sql = db.execute("SELECT sql FROM sqlite_master WHERE name='outreach_drafts'").fetchone()[0]
                sql = sql.replace('outreach_drafts', 'outreach_drafts_v2', 1).replace('job_id INTEGER NOT NULL','job_id INTEGER')
                db.execute(sql)
                names = ','.join(x['name'] for x in columns)
                db.execute(f'INSERT INTO outreach_drafts_v2({names}) SELECT {names} FROM outreach_drafts')
                db.execute('DROP TABLE outreach_drafts')
                db.execute('ALTER TABLE outreach_drafts_v2 RENAME TO outreach_drafts')
        additions = {
            'outreach_contacts': {'company_domain':"TEXT NOT NULL DEFAULT ''", 'timezone':"TEXT NOT NULL DEFAULT ''"},
            'outreach_drafts': {'campaign_id':'INTEGER', 'parent_id':'INTEGER', 'step_number':'INTEGER NOT NULL DEFAULT 0',
                'thread_id':"TEXT NOT NULL DEFAULT ''", 'rfc_message_id':"TEXT NOT NULL DEFAULT ''",
                'resume_sha':"TEXT NOT NULL DEFAULT ''", 'due_at':'TEXT', 'last_reply_check':"TEXT NOT NULL DEFAULT ''",
                'target_company':"TEXT NOT NULL DEFAULT ''", 'target_domain':"TEXT NOT NULL DEFAULT ''",
                'campaign_kind':"TEXT NOT NULL DEFAULT 'vacancy'", 'response_status':"TEXT NOT NULL DEFAULT ''"},
        }
        for table, fields in additions.items():
            if postgres_enabled():
                existing = {x[0] for x in db.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=\'public\' AND table_name=?',(table,))}
            else:
                existing = {x['name'] for x in db.execute(f'PRAGMA table_info({table})')}
            for name, definition in fields.items():
                if name not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS outreach_followup_once ON outreach_drafts(parent_id,step_number)')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS outreach_campaign_contact_once ON outreach_drafts(contact_id,campaign_id) WHERE step_number=0 AND campaign_id IS NOT NULL')
        db.execute('CREATE TABLE IF NOT EXISTS outreach_settings(id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)')
        db.execute('INSERT INTO outreach_settings(id,settings) VALUES (1,?) ON CONFLICT(id) DO NOTHING',(json.dumps(DEFAULTS),))
        db.execute(f'''CREATE TABLE IF NOT EXISTS outreach_campaigns(id {identity}, name TEXT NOT NULL,
            kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'draft', approval_hash TEXT NOT NULL DEFAULT '',
            approved_snapshot TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_company_usage(company_key TEXT PRIMARY KEY,
            draft_id INTEGER NOT NULL, reserved_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_enrichment(apollo_id TEXT PRIMARY KEY,
            state TEXT NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, updated_at TEXT NOT NULL,
            contact_id INTEGER, detail TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_leads(apollo_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL, searched_at TEXT NOT NULL)''')
        db.execute(f'''CREATE TABLE IF NOT EXISTS outreach_replies(id {identity}, root_id INTEGER NOT NULL,
            gmail_id TEXT NOT NULL UNIQUE, category TEXT NOT NULL, snippet TEXT NOT NULL,
            received_at TEXT NOT NULL, suggested_reply TEXT NOT NULL DEFAULT '')''')
        db.execute('''CREATE TABLE IF NOT EXISTS outreach_leases(name TEXT PRIMARY KEY,
            owner TEXT NOT NULL, expires_at TEXT NOT NULL)''')


def settings(db=None):
    if db is None:
        with connect() as connection:
            return settings(connection)
    row = db.execute('SELECT settings FROM outreach_settings WHERE id=1').fetchone()
    return {**DEFAULTS, **json.loads(row[0])}


def save_settings(payload):
    current = settings()
    for key in DEFAULTS:
        if key in payload:
            current[key] = payload[key]
    if type(current['paused']) is not bool:
        raise HTTPException(422,'Pause must be true or false.')
    try:
        ZoneInfo(current['timezone'])
    except (ZoneInfoNotFoundError,TypeError,ValueError):
        raise HTTPException(422,'Choose a valid IANA timezone.')
    for key, low, high in [('business_start',0,23),('business_end',1,24),('enrichment_daily',0,100),
                           ('enrichment_monthly',0,1000),('followups',0,2),('first_followup_days',1,30),('final_followup_days',1,30)]:
        if type(current[key]) is not int or not low <= current[key] <= high:
            raise HTTPException(422,f'{key} must be between {low} and {high}.')
    if current['business_start'] >= current['business_end']:
        raise HTTPException(422,'Business-hour end must follow start.')
    with connect() as db:
        previous = settings(db)
        db.execute('UPDATE outreach_settings SET settings=? WHERE id=1',(json.dumps(current),))
        # Pause/resume does not widen authority. Every other rule change needs a new preview approval.
        if {k:v for k,v in previous.items() if k!='paused'} != {k:v for k,v in current.items() if k!='paused'}:
            db.execute("UPDATE outreach_campaigns SET state='paused',approval_hash='',updated_at=? WHERE state='active'",(stamp(),))
    return current


def global_stop():
    save_settings({'paused':True})
    with connect() as db:
        db.execute("UPDATE outreach_campaigns SET state='paused',updated_at=? WHERE state='active'",(stamp(),))
        db.execute("UPDATE outreach_drafts SET state='draft',approval_hash='',updated_at=? WHERE state='approved'",(stamp(),))
    return {'paused':True,'message':'Queued approvals cleared. In-flight Gmail requests cannot be recalled.'}


def business_time(contact, rules, moment=None):
    zone = contact.get('timezone')
    if not zone:
        raise HTTPException(409,'Verify the recipient timezone before sending.')
    try:
        local = (moment or utcnow()).astimezone(ZoneInfo(zone))
    except ZoneInfoNotFoundError:
        raise HTTPException(409,'Recipient timezone is invalid.')
    if local.weekday() >= 5 or not rules['business_start'] <= local.hour < rules['business_end']:
        raise HTTPException(409,'Queued for the recipient’s local business hours (Monday–Friday).')


def working_days_after(value, days, zone):
    local = datetime.fromisoformat(value).astimezone(ZoneInfo(zone))
    while days:
        local += timedelta(days=1)
        if local.weekday()<5:
            days -= 1
    # Delivery always rechecks configured business hours, including after a weekend or outage.
    return local.astimezone(timezone.utc).isoformat()


def profile_snapshot():
    from application_workflow import get_profile_full
    from job_hunter import get_settings
    p = get_profile_full()
    return {'profile':{k:p.get(k) for k in ['full_name','email','phone','linkedin_url','headline','experience_summary','skills','updated_at']},
            'resume':resume_store.get(), 'search_preferences':get_settings()}


def context_job(db, draft):
    import outreach
    if draft.get('parent_id'):
        root=outreach._row(db,'outreach_drafts',draft['parent_id'])
        return context_job(db,root)
    if draft.get('job_id'):
        return outreach._job(db,draft['job_id'])
    if draft.get('campaign_kind') != 'introduction' or not draft.get('target_company') or not draft.get('target_domain'):
        raise HTTPException(409,'Missing target company evidence.')
    return {'title':'future operations opportunities','company':draft['target_company'],
            'url':'https://'+draft['target_domain'],'description':'','status':'new'}


def compose(profile, contact, job, kind, optout):
    name = (contact.get('name') or 'Hiring Team').split()[0]
    company = job.get('company') or contact.get('company')
    title = job.get('title')
    if kind == 'introduction':
        opening = f'I wanted to introduce myself for future operations opportunities at {company}.'
        subject = f'Operations leadership opportunities at {company} — {profile["full_name"]}'
    else:
        opening = f'I came across the {title} opening at {company} and wanted to reach out directly.'
        subject = f'{title} at {company} — {profile["full_name"]}'
    # Extract exact candidate-authored statements; rank by the actual vacancy, never invent metrics.
    summary = str(profile.get('experience_summary') or profile.get('headline') or '').strip()
    sentences = [x.strip() for x in re.split(r'(?<=[.!?])\s+|\n+',summary) if x.strip()]
    terms = set(re.findall(r'[a-z]{4,}',(str(job.get('description') or '')+' '+title).lower()))
    scored = sorted(enumerate(sentences),key=lambda x:(-len(terms & set(re.findall(r'[a-z]{4,}',x[1].lower()))),x[0]))
    selected = [s for _,s in scored[:2]]
    facts = ' '.join(selected)[:1100]
    signature = '\n'.join(str(profile.get(k) or '').strip() for k in ['full_name','email','phone','linkedin_url'] if profile.get(k))
    body = f'Hi {name},\n\n{opening}\n\n{facts}\n\nI’ve attached my resume. Would you be the right person to speak with, or could you point me towards the hiring team?\n\nRegards,\n{signature}\n\nTo stop further job enquiries: {optout}'
    return subject.replace('\n',' ').replace('\r',' ')[:200], body


def create_campaign(payload):
    kind = payload.get('kind')
    name = str(payload.get('name') or '').strip()[:150]
    if kind not in {'vacancy','introduction'} or not name:
        raise HTTPException(422,'Name the campaign and select vacancy or introduction.')
    with connect() as db:
        return dict(db.execute('INSERT INTO outreach_campaigns(name,kind,created_at,updated_at) VALUES (?,?,?,?) RETURNING *',(name,kind,stamp(),stamp())).fetchone())


def create_followups(db, root):
    import outreach
    rules = settings(db)
    for step in range(1,rules['followups']+1):
        contact = outreach._row(db,'outreach_contacts',root['contact_id'])
        name = (contact['name'] or 'Hiring Team').split()[0]
        footer = root['body'].split('Regards,')[-1] if 'Regards,' in root['body'] else root['body'].split('\n\n')[-1]
        text = ('Just following up on my earlier email. I’d welcome a brief conversation if my operations experience is relevant to your team.' if step==1 else 'One final follow-up on my earlier email. If there is no suitable opportunity at the moment, thank you for considering my profile.')
        body = f'Hi {name},\n\n{text}\n\nMy resume is attached for reference.\n\nRegards,{footer}'
        db.execute('''INSERT INTO outreach_drafts(candidate_id,contact_id,job_id,sender_email,recipient_email,subject,body,
            campaign_id,parent_id,step_number,resume_sha,target_company,target_domain,campaign_kind,created_at,updated_at)
            VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(parent_id,step_number) DO NOTHING''',
            (root['contact_id'],None,root['sender_email'],root['recipient_email'],
             root['subject'] if root['subject'].lower().startswith('re:') else 'Re: '+root['subject'],body,root['campaign_id'],root['id'],step,
             root['resume_sha'],root['target_company'],root['target_domain'],root['campaign_kind'],stamp(),stamp()))


def campaign_preview(campaign_id):
    import outreach
    snapshot = profile_snapshot()
    with connect() as db:
        c = db.execute('SELECT * FROM outreach_campaigns WHERE id=?',(campaign_id,)).fetchone()
        if not c: raise HTTPException(404,'Campaign not found.')
        c = dict(c)
        drafts = [dict(x) for x in db.execute('SELECT * FROM outreach_drafts WHERE campaign_id=? ORDER BY id',(campaign_id,))]
        contacts = [outreach._row(db,'outreach_contacts',x['contact_id']) for x in drafts]
        jobs = [{k:j.get(k) for k in ['title','company','url','status']} for j in [context_job(db,x) for x in drafts]]
        rules = settings(db)
        rules.pop('paused')
        rules.update(dict(db.execute('SELECT daily_limit,auto_send_approved FROM outreach_policy WHERE id=1').fetchone()))
    # State transitions and delivery receipts must not invalidate the approved content snapshot.
    emails = [{k:d.get(k) for k in ['id','contact_id','job_id','sender_email','recipient_email','subject','body','resume_sha','step_number','parent_id','target_domain','campaign_kind']} for d in drafts]
    scope = {'candidate':snapshot,'rules':rules,'emails':emails,'contacts':contacts,'jobs':jobs,'kind':c['kind']}
    return {'campaign':c,'snapshot':scope,'preview_hash':digest(scope),'items':drafts,'activation_required':True}


def activate_campaign(campaign_id, payload):
    import outreach
    preview = campaign_preview(campaign_id)
    if payload.get('confirmed') is not True or payload.get('profile_confirmed') is not True or payload.get('preview_hash') != preview['preview_hash']:
        raise HTTPException(409,'Review the current profile, resume, exact emails, follow-ups and campaign rules, then approve this preview.')
    roots = [d for d in preview['items'] if not d['parent_id']]
    if not roots or len(roots)>5:
        raise HTTPException(409,'First campaign activation requires a preview batch of 1–5 contacts.')
    domains = [d['target_domain'] for d in roots]
    if len(domains)!=len(set(domains)):
        raise HTTPException(409,'Use only one initial contact per company in this campaign.')
    profile = preview['snapshot']['candidate']['profile']
    with connect() as db:
        for draft in preview['items']:
            contact = outreach._row(db,'outreach_contacts',draft['contact_id'])
            outreach._eligible(draft,contact,profile)
            if not contact['timezone']:
                raise HTTPException(409,'Verify each recipient’s timezone before activation.')
            attachment = resume_store.get(db)
            if not attachment or draft['resume_sha']!=attachment['sha256']:
                raise HTTPException(409,'Upload the current PDF and regenerate the campaign preview.')
            if draft['state'] not in {'draft','approved','failed','sent'}:
                raise HTTPException(409,'Resolve cancelled or uncertain deliveries before activating.')
            if draft['state']!='sent':
                fingerprint = outreach._fingerprint(draft,contact,profile,context_job(db,draft))
                changed=db.execute("UPDATE outreach_drafts SET state='approved',approval_hash=?,updated_at=? WHERE id=? AND state IN ('draft','approved','failed') AND updated_at=?",(fingerprint,stamp(),draft['id'],draft['updated_at']))
                if changed.rowcount!=1:raise HTTPException(409,'Preview changed concurrently. Reload and approve the new preview.')
        db.execute("UPDATE outreach_campaigns SET state='active',approval_hash=?,approved_snapshot=?,updated_at=? WHERE id=?",(preview['preview_hash'],json.dumps(preview['snapshot']),stamp(),campaign_id))
    return {'active':True,'approved_emails':len(preview['items']),'paused_globally':settings()['paused']}


def validate_campaign(db, draft):
    if not draft.get('campaign_id'):
        if draft.get('parent_id'):
            raise HTTPException(409,'Follow-ups require an approved campaign.')
        return
    row = db.execute('SELECT * FROM outreach_campaigns WHERE id=?',(draft['campaign_id'],)).fetchone()
    if not row or row['state']!='active':
        raise HTTPException(409,'Campaign is not active. Review and activate its preview.')
    # Includes current search preferences, profile, attachment, contacts and exact finite email batch.
    if campaign_preview(draft['campaign_id'])['preview_hash'] != row['approval_hash']:
        raise HTTPException(409,'Campaign scope changed. Review and activate the new preview.')


def claim_enrichment(identifier):
    current = utcnow().astimezone(ZoneInfo(settings()['timezone']))
    day, month = current.date().isoformat(),current.strftime('%Y-%m')
    with connect() as db:
        if not postgres_enabled():db.execute('BEGIN IMMEDIATE')
        else:db.execute('SELECT pg_advisory_xact_lock(746821952)')
        cached = db.execute('SELECT * FROM outreach_enrichment WHERE apollo_id=?',(identifier,)).fetchone()
        if cached:
            raise HTTPException(409,'This reveal was already attempted. Check its saved result; uncertain requests are not repeated automatically.')
        limits=settings(db)
        daily=db.execute('SELECT COUNT(*) FROM outreach_enrichment WHERE day=?',(day,)).fetchone()[0]
        monthly=db.execute('SELECT COUNT(*) FROM outreach_enrichment WHERE month=?',(month,)).fetchone()[0]
        if daily>=limits['enrichment_daily'] or monthly>=limits['enrichment_monthly']:
            raise HTTPException(429,'Apollo enrichment budget reached. No reveal request was made.')
        lead=db.execute('SELECT domain FROM outreach_leads WHERE apollo_id=?',(identifier,)).fetchone()
        if not lead:raise HTTPException(409,'Shortlist this person from a company-domain Apollo search first.')
        db.execute("INSERT INTO outreach_enrichment(apollo_id,state,day,month,updated_at) VALUES (?,'pending',?,?,?)",(identifier,day,month,stamp()))
        return lead['domain']


def classify_reply(message):
    headers = {str(h.get('name','')).lower():str(h.get('value','')) for h in message.get('payload',{}).get('headers',[])}
    text = (headers.get('subject','')+' '+str(message.get('snippet') or '')).lower()
    sender = parseaddr(headers.get('from',''))[1].lower()
    if 'mailer-daemon' in sender or 'postmaster' in sender or 'multipart/report' in str(message.get('payload',{}).get('mimeType','')).lower():return 'bounce'
    if any(t in text for t in ['unsubscribe','do not contact','stop emailing','remove me']):return 'opt_out'
    if headers.get('auto-submitted','no')!='no' or any(t in text for t in ['out of office','automatic reply','on leave']):return 'out_of_office'
    if any(t in text for t in ['not proceeding','not selected','unfortunately','no vacancies','not a fit']):return 'rejection'
    if any(t in text for t in ['interview','schedule a call','availability for a call']):return 'interview'
    if any(t in text for t in ['send your resume','share your cv','share your resume']):return 'resume_requested'
    return 'needs_review'


async def sync_replies():
    import gmail_alerts
    status = gmail_alerts.get_gmail_status()
    # A send-only grant cannot read threads. Refresh performs server-side scope validation too.
    if not status.get('connected'):
        return {'checked':0,'blocked':'Connect Gmail with read and send permissions.'}
    with connect() as db:
        granted = db.execute('SELECT granted_scopes FROM gmail_tokens WHERE id=1').fetchone()
    if not granted or 'https://www.googleapis.com/auth/gmail.readonly' not in (granted[0] or '').split():
        return {'checked':0,'blocked':'Reconnect Gmail with read permission for reply tracking.'}
    token = await gmail_alerts._access_token()
    if not token:raise HTTPException(409,'Reconnect Gmail for reply tracking.')
    with connect() as db:
        roots = [dict(x) for x in db.execute("SELECT * FROM outreach_drafts WHERE step_number=0 AND state='sent' AND response_status='' ORDER BY last_reply_check,id LIMIT 5")]
    checked=0
    checked_roots=[]
    async with httpx.AsyncClient(timeout=20) as client:
        for root in roots:
            if not root['thread_id']:
                continue
            r = await client.get(gmail_alerts.GMAIL_API+'/threads/'+root['thread_id'],headers={'Authorization':'Bearer '+token},params={'format':'full'})
            if r.status_code!=200:raise HTTPException(503,'Could not check replies. Follow-up dispatch is paused until Gmail can be checked.')
            messages=r.json().get('messages',[])
            # DSNs can arrive in a new thread. Search only for system notices mentioning this
            # exact outreach recipient, after its send date, and stop conservatively on a match.
            bounce_query=f'in:anywhere after:{root["sent_at"][:10].replace("-","/")} {{from:mailer-daemon from:postmaster}} "{root["recipient_email"]}"'
            b=await client.get(gmail_alerts.GMAIL_API+'/messages',headers={'Authorization':'Bearer '+token},params={'q':bounce_query,'maxResults':10})
            if b.status_code!=200:raise HTTPException(503,'Could not check delivery notices. Follow-ups are paused.')
            for notice in b.json().get('messages',[]):
                nr=await client.get(gmail_alerts.GMAIL_API+'/messages/'+notice['id'],headers={'Authorization':'Bearer '+token},params={'format':'full'})
                if nr.status_code!=200:raise HTTPException(503,'Could not check a delivery notice. Follow-ups are paused.')
                messages.append(nr.json())
            if not isinstance(messages,list):raise HTTPException(503,'Unexpected Gmail thread response.')
            checked+=1
            checked_roots.append(root['id'])
            with connect() as db:db.execute('UPDATE outreach_drafts SET last_reply_check=? WHERE id=?',(stamp(),root['id']))
            for msg in messages:
                headers={str(h.get('name','')).lower():str(h.get('value','')) for h in msg.get('payload',{}).get('headers',[])}
                sender=parseaddr(headers.get('from',''))[1].lower()
                # SENT labels also identify messages sent by the owner under aliases.
                if msg.get('id')==root['provider_id'] or 'SENT' in msg.get('labelIds',[]) or sender==root['sender_email']:
                    continue
                category=classify_reply(msg)
                with connect() as db:
                    db.execute('''INSERT INTO outreach_replies(root_id,gmail_id,category,snippet,received_at,suggested_reply)
                        VALUES (?,?,?,?,?,?) ON CONFLICT(gmail_id) DO NOTHING''',
                        (root['id'],str(msg.get('id') or ''),category,str(msg.get('snippet') or '')[:2000],stamp(),
                         'Thank you for getting back to me. I’d be happy to discuss the opportunity. Please let me know a suitable time.' if category=='interview' else 'Thank you for your reply. [Review the message and add an accurate response before sending.]'))
                    db.execute('UPDATE outreach_drafts SET response_status=? WHERE id=?',(category,root['id']))
                    db.execute("UPDATE outreach_drafts SET state='cancelled',approval_hash='',error='Reply received; follow-ups stopped.',updated_at=? WHERE parent_id=? AND state IN ('draft','approved','failed')",(stamp(),root['id']))
                if category in {'bounce','opt_out'}:
                    import outreach
                    outreach.suppress_contact(root['contact_id'])
    return {'checked':checked,'root_ids':checked_roots}


def overview():
    from application_workflow import get_profile
    from job_hunter import get_settings
    with connect() as db:
        counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) AS n FROM outreach_drafts GROUP BY state')}
        replies=[dict(r) for r in db.execute('SELECT * FROM outreach_replies ORDER BY id DESC LIMIT 100')]
        campaigns=[dict(r) for r in db.execute('SELECT id,name,kind,state,updated_at FROM outreach_campaigns ORDER BY id DESC LIMIT 100')]
        usage=[dict(r) for r in db.execute('SELECT state,COUNT(*) AS attempts FROM outreach_enrichment GROUP BY state')]
    return {'rules':settings(),'profile':get_profile(),'search_preferences':get_settings(),'counts':counts,
            'replies':replies,'campaigns':campaigns,'enrichment_usage':usage,
            'storage': 'postgresql' if postgres_enabled() else 'sqlite — requires a persistent disk across redeployments; sending on Render is blocked until durable storage is confirmed',
            'scheduler_note':'Database-backed queue; approved work resumes on wake. External GitHub discovery also ticks the outreach queue.'}


async def tick():
    import outreach,secrets
    # Serialize the whole polling/dispatch pass across external triggers and all app instances.
    owner=secrets.token_urlsafe(16)
    with connect() as db:
        db.execute('INSERT INTO outreach_leases(name,owner,expires_at) VALUES (\'tick\',?,?) ON CONFLICT(name) DO NOTHING',(owner,(utcnow()+timedelta(minutes=10)).isoformat()))
        row=db.execute("UPDATE outreach_leases SET owner=?,expires_at=? WHERE name='tick' AND (owner=? OR expires_at<?)",(owner,(utcnow()+timedelta(minutes=10)).isoformat(),owner,stamp()))
        if row.rowcount!=1:return {'skipped':'Another outreach worker is active.'}
        db.execute("UPDATE outreach_drafts SET state='delivery_unknown',error='Worker interrupted; check Gmail Sent. No automatic retry.' WHERE state='sending' AND updated_at<?",((utcnow()-timedelta(minutes=10)).isoformat(),))
    try:
        if settings()['paused']:return {'skipped':'Outreach paused globally.'}
        reply_result=await sync_replies()
        # No read permission means no follow-up dispatch; first-contact reviewed emails can still be sent.
        return await outreach.dispatch_approved(followups_allowed=not reply_result.get('blocked'),checked_roots=reply_result.get('root_ids',[]))
    finally:
        with connect() as db:db.execute("DELETE FROM outreach_leases WHERE name='tick' AND owner=?",(owner,))


def invalidate_profile(db):
    exists = db.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='outreach_campaigns'").fetchone() if postgres_enabled() else db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='outreach_campaigns'").fetchone()
    if exists:
        db.execute("UPDATE outreach_campaigns SET state='paused',approval_hash='',updated_at=? WHERE state='active'",(stamp(),))
        db.execute("UPDATE outreach_drafts SET state='draft',approval_hash='',error='Profile or resume changed; review again.',updated_at=? WHERE state='approved'",(stamp(),))


def prepare_batch(payload):
    """Use existing scored jobs and reviewed contacts; never spends credits or sends."""
    import outreach
    from job_sources import list_jobs
    from job_hunter import get_settings
    prefs=get_settings()
    candidates=list_jobs(min_score=prefs.get('min_match_score',65),limit=100)
    contacts=[c for c in outreach.list_records('outreach_contacts') if c['verified'] and not c['suppressed']]
    normalize=lambda v:re.sub(r'[^a-z0-9]','',str(v).lower())
    selected=[]
    missing=[]
    seen=set()
    for job in candidates:
        company=normalize(job.get('company'))
        if not company or company in seen or job['status'] not in {'new','saved','shortlisted'}:continue
        seen.add(company)
        matches=[c for c in contacts if normalize(c['company'])==company]
        if not matches:
            if len(missing)<5:missing.append({'job_id':job['id'],'company':job['company'],'title':job['title'],'source_url':job['url'],'action':'Find and verify a current hiring contact at the employer’s actual domain.'})
            continue
        with connect() as db:
            used=db.execute('SELECT 1 FROM outreach_company_usage WHERE company_key=?',(matches[0]['company_domain'],)).fetchone()
            existing=db.execute('SELECT 1 FROM outreach_drafts WHERE job_id=? AND contact_id=?',(job['id'],matches[0]['id'])).fetchone()
        if used or existing:continue
        selected.append((job,matches[0]))
        if len(selected)==5:break
    if not selected:
        return {'prepared':0,'pending_contacts':missing,'blocked':'No unused relevant vacancy has a verified hiring contact. No Apollo credits were consumed.'}
    campaign=create_campaign({'name':str(payload.get('name') or 'Matched vacancy applications'),'kind':'vacancy'})
    for job,contact in selected:outreach.create_draft(contact['id'],job['id'],campaign_id=campaign['id'])
    return {'prepared':len(selected),'campaign_id':campaign['id'],'pending_contacts':missing,'preview':campaign_preview(campaign['id'])}
