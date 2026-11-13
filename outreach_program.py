"""Persistent daily research and draft preparation. Never approves or sends mail."""
import json
import re
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from database import connect, postgres_enabled
import outreach_engine as engine

DEFAULT_TARGETS = [
    {'company': 'noon', 'domain': 'noon.com', 'source_url': 'https://help.noon.com/portal/en/kb/articles/about-noon-7-3-2024'},
    {'company': 'talabat', 'domain': 'talabat.com', 'source_url': 'https://corporate.talabat.com/careers/'},
    {'company': 'Careem', 'domain': 'careem.com', 'source_url': 'https://www.careem.com/en-ae/careers/'},
]
DEFAULT = {'enabled': False, 'job_found': False, 'targets': DEFAULT_TARGETS,
           'last_day': '', 'cursor': 0, 'message': 'Paused. Daily research and draft preparation never approve or send emails.', 'shortlist': []}


def get(db=None):
    if db is None:
        with connect() as db:
            return get(db)
    row = db.execute('SELECT settings FROM outreach_program WHERE id=1').fetchone()
    return {**DEFAULT, **json.loads(row[0])} if row else dict(DEFAULT)


def write(db, value):
    db.execute('INSERT INTO outreach_program(id,settings) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET settings=excluded.settings', (json.dumps(value),))


def lock(db):
    if postgres_enabled():
        db.execute('SELECT id FROM outreach_program WHERE id=1 FOR UPDATE')
    else:
        db.execute('BEGIN IMMEDIATE')


def save(payload):
    import outreach
    with connect() as db:
        lock(db)
        value = get(db)
        for key in ('enabled', 'job_found'):
            if key in payload:
                if type(payload[key]) is not bool:
                    raise HTTPException(422, key+' must be true or false.')
                value[key] = payload[key]
        if 'targets' in payload:
            targets = payload['targets']
            if not isinstance(targets, list) or not 1 <= len(targets) <= 30:
                raise HTTPException(422, 'Provide 1–30 evidenced employer domains.')
            clean = []
            for item in targets:
                if not isinstance(item, dict):
                    raise HTTPException(422, 'Each target needs company, domain and source_url.')
                domain = str(item.get('domain') or '').strip().lower()
                name = str(item.get('company') or '').strip()
                if not name or len(name)>150 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain) or domain in {'gmail.com','yahoo.com','hotmail.com','outlook.com'}:
                    raise HTTPException(422, 'Use an actual employer website domain and company name.')
                clean.append({'company':name, 'domain':domain, 'source_url':outreach._source(item.get('source_url'))})
            if len({t['domain'] for t in clean}) != len(clean):
                raise HTTPException(422, 'Do not repeat employer domains.')
            value['targets'] = clean
            value['cursor'] %= len(clean)
        if value['job_found']:
            value['enabled'] = False
            value['message'] = 'Job found: research paused. All email sending and queued approvals stopped.'
        elif 'enabled' in payload:
            value['message'] = 'Daily research and draft preparation enabled. Exact email batches still require approval.' if value['enabled'] else 'Daily research paused. Previously approved sending is controlled separately by Global pause.'
        write(db, value)
    if value['job_found']:
        engine.global_stop()
    return get()


async def tick():
    """One budgeted company search per local day; prepare only reviewed contacts."""
    import outreach
    import prospeo_provider
    value = get()
    if not value['enabled'] or value['job_found']:
        return {'skipped':'Daily research is paused.'}
    day = engine.utcnow().astimezone(ZoneInfo(engine.settings()['timezone'])).date().isoformat()
    if value['last_day'] == day:
        return {'skipped':'Daily research already attempted today.'}
    owner = secrets.token_urlsafe(16)
    with connect() as db:
        lock(db)
        db.execute("INSERT INTO outreach_leases(name,owner,expires_at) VALUES ('program',?,?) ON CONFLICT(name) DO NOTHING",(owner,(engine.utcnow()+timedelta(minutes=10)).isoformat()))
        lease_claim = db.execute("UPDATE outreach_leases SET owner=?,expires_at=? WHERE name='program' AND (owner=? OR expires_at<?)",(owner,(engine.utcnow()+timedelta(minutes=10)).isoformat(),owner,engine.stamp()))
        if lease_claim.rowcount != 1:
            return {'skipped':'Another research worker is active.'}
        value = get(db)
        if not value['enabled'] or value['job_found'] or value['last_day'] == day:
            db.execute("DELETE FROM outreach_leases WHERE name='program' AND owner=?",(owner,))
            return {'skipped':'Research paused or already attempted today.'}
        # Persist before an external call. Interruptions never blindly spend again.
        value['last_day'] = day
        value['message'] = 'Research attempt reserved. Interrupted or uncertain requests need review; no blind retry.'
        write(db,value)
    try:
        allowed = {t['domain']:t for t in value['targets']}
        selected = []
        contacts = outreach.list_records('outreach_contacts')
        with connect() as db:
            for c in contacts:
                domain = c.get('company_domain')
                if domain not in allowed or not c['verified'] or c['suppressed'] or domain in [x['company_domain'] for x in selected]:
                    continue
                exists = db.execute('SELECT 1 FROM outreach_drafts WHERE target_domain=? AND step_number=0',(domain,)).fetchone()
                used = db.execute('SELECT 1 FROM outreach_company_usage WHERE company_key=?',(domain,)).fetchone()
                suppressed = db.execute('SELECT 1 FROM outreach_contacts WHERE email=? AND suppressed=1',(c['email'],)).fetchone()
                if not exists and not used and not suppressed:
                    selected.append(c)
                if len(selected)==5:
                    break
        prepared = 0
        if selected:
            campaign = engine.create_campaign({'name':'Daily international introductions '+day,'kind':'introduction'})
            for c in selected:
                outreach.create_draft(c['id'],None,campaign_id=campaign['id'],target_domain=c['company_domain'])
                prepared += 1
        target = value['targets'][value['cursor'] % len(value['targets'])]
        result = await prospeo_provider.search({'company_domain':target['domain'],'confirm_credit_use':True})
        message = f"Prepared {prepared} reviewed contacts as drafts. {target['company']}: {len(result['items'])} hiring leads shortlisted. Reveal and verify relevant contacts in the portal. No email approved or sent."
        with connect() as db:
            lock(db)
            current = get(db)
            # Never re-enable a programme stopped while a provider request was in flight.
            current.update(last_day=day, cursor=(value['cursor']+1)%len(value['targets']), message=message,
                           shortlist=[t for t in current.get('shortlist',[]) if t.get('domain')!=target['domain']]+[{**target,'lead_count':len(result['items']),'searched_at':result['searched_at']}])
            write(db,current)
        return {'prepared':prepared,'message':message}
    except HTTPException as exc:
        with connect() as db:
            lock(db)
            current=get(db);current['message']=str(exc.detail);write(db,current)
        return {'blocked':str(exc.detail)}
    finally:
        with connect() as db:
            db.execute("DELETE FROM outreach_leases WHERE name='program' AND owner=?",(owner,))
