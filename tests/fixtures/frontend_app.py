"""Fixture-only browser app. All email, Apollo and discovery calls are mocked."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import httpx
import main,job_sources,job_hunter,notify,gmail_alerts,outreach
import outreach_engine
import prospeo_provider
from datetime import datetime,timezone
outreach_engine.utcnow=lambda:datetime(2026,11,13,5,tzinfo=timezone.utc)
async def sources(settings):
    rows=[]
    for i,title in enumerate(['Operations Manager','Senior Cluster Manager','Area Manager']):
        rows.append(job_sources._clean_job({'id':str(i),'title':title,'company':'Example Logistics','location':'Noida, India','url':f'https://example.com/jobs/{i}','description':'Dark store warehouse operations inventory SLA KPI last mile delivery team leadership','date':job_hunter.now_iso()},'Fixture public board'))
    return rows,[],[{'name':'Fixture public board','status':'working','fetched':3}],{}
async def skip_notify(jobs):return {'sent':False,'skipped':True}
def gmail_status():return {'connected':True,'oauth_configured':True,'send_authorized':True,'email':'candidate@example.com','missing':[],'hint':'Fixture connection'}
async def token():return 'fixture-token'
async def apollo(path,params):
    if path.endswith('api_search'):return {'people':[{'id':'fixture-person','name':'Recruiter Fixture','title':'Talent Acquisition','organization':{'name':'Example Logistics'}}]}
    return {'person':{'id':'fixture-person','name':'Recruiter Fixture','email':'recruiter@example.com','email_status':'verified','title':'Talent Acquisition','linkedin_url':'https://linkedin.com/in/fixture','organization':{'name':'Example Logistics'}}}
async def post(self,url,**kwargs):
    if url.endswith('/messages/send'):return httpx.Response(200,json={'id':'fixture-gmail-message'})
    return httpx.Response(503,json={})
job_sources.fetch_public_sources=sources
notify.notify_high_match_jobs=skip_notify
main.get_gmail_status=gmail_status
gmail_alerts.get_gmail_status=gmail_status
gmail_alerts._access_token=token
outreach._apollo=apollo
httpx.AsyncClient.post=post
app=main.app

async def prospeo(path,params):
    person={'person_id':'fixture-person','full_name':'Recruiter Fixture','current_job_title':'Talent Acquisition','linkedin_url':'https://linkedin.com/in/fixture'}
    company={'name':'Example Logistics','domain':'example.com'}
    if path=='search-person':return {'results':[{'person':person,'company':company}]}
    person['email']={'email':'recruiter@example.com','status':'VERIFIED','revealed':True}
    return {'person':person,'company':company}
prospeo_provider.request=prospeo

# Dedicated free Gulf refresh is mocked independently of the normal hunt.
import gulf_sources
async def gulf_fixture(client,settings):
    row=job_sources._clean_job({'id':'gulf-fixture','title':'Sr. Manager Logistics Operations','company':'Gulf Fixture Employer','location':'Doha, Qatar','url':'https://jobs.smartrecruiters.com/DeliveryHero/123','description':'Warehouse operations and multi-site logistics leadership. Inventory SLA KPI. Review qualification and work-authorisation requirements.','date':'2026-01-01'},'SmartRecruiters/DeliveryHero')
    return [gulf_sources.mark(row,'https://jobs.smartrecruiters.com/oneclick-ui/fixture',job_hunter.now_iso())],None
gulf_sources.fetch_gulf_sources=gulf_fixture
