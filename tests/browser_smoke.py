"""Run: python tests/browser_smoke.py. Uses only fixtures; never sends real email."""
import os,re,shutil,socket,subprocess,sys,tempfile,time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
from test_workflow_regressions import text_pdf
output=Path(os.getenv('BROWSER_SMOKE_OUTPUT','/tmp/job-hunter-browser-evidence'))
output.mkdir(parents=True,exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
base=f'http://127.0.0.1:{port}'
with tempfile.TemporaryDirectory(prefix='job-hunter-browser-') as temporary:
    environment=os.environ.copy()
    for key in ['DATABASE_URL','RESEND_API_KEY','GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET']:
        environment.pop(key,None)
    environment.update(ENABLE_SCHEDULER='false',DATABASE_PATH=str(Path(temporary)/'fixture.sqlite'),PORTAL_ACCESS_TOKEN='fixture-frontend-owner-private-key-00001',APOLLO_API_KEY='fixture-api-key',PROSPEO_API_KEY='fixture-prospeo-key',PYTHONDONTWRITEBYTECODE='1')
    server=subprocess.Popen([sys.executable,'-m','uvicorn','fixtures.frontend_app:app','--app-dir',str(root/'tests'),'--host','127.0.0.1','--port',str(port)],env=environment,cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+20
        with httpx.Client(trust_env=False,timeout=2) as client:
            while True:
                try:
                    if client.get(base).status_code==200:break
                except httpx.HTTPError:pass
                if server.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Fixture server did not become ready')
                time.sleep(.2)
        with sync_playwright() as p:
            engine=os.getenv('BROWSER_ENGINE','chromium')
            executable=(os.getenv('CHROMIUM_EXECUTABLE_PATH') or shutil.which('chromium') or shutil.which('google-chrome')) if engine=='chromium' else None
            browser=getattr(p,engine).launch(headless=True,**({'executable_path':executable} if executable else {}))
            context=browser.new_context(**(p.devices['iPhone 13'] if engine=='webkit' else {'viewport':{'width':1440,'height':1100},'device_scale_factor':1}))
            page=context.new_page()
            errors=[]
            protected_reads=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            private_paths=['/api/jobs','/api/stats','/api/settings','/api/sources','/api/operational-readiness']
            page.on('request',lambda request:protected_reads.append(request.url) if any(request.url.split('?')[0]==base+path for path in private_paths) else None)
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) else route.abort())
            page.goto(base+'/#settings',wait_until='networkidle')
            expect(page.locator('#missionDiscovery')).to_have_text('Sign in required')
            assert protected_reads==[], 'Signed-out page must not fetch private candidate data'
            details=page.locator('#profileConnect details')
            if not details.evaluate('(e)=>e.open'):details.locator('summary').click()
            page.locator('#portalTokenInput').fill('fixture-frontend-owner-private-key-00001')
            page.locator('#connectPortalBtn').click()
            expect(page.locator('#profileForm')).to_be_visible()
            page.locator('#profileName').fill('Fixture Candidate')
            page.locator('#profileEmail').fill('candidate@example.com')
            page.locator('#profileExperience').fill('Led multi-site warehouse operations and inventory planning.')
            page.locator('#saveProfileBtn').click()
            page.locator('#resumeFile').set_input_files({'name':'fixture-resume.pdf','mimeType':'application/pdf','buffer':text_pdf('Fixture Candidate Operations Manager warehouse inventory')})
            page.locator('#uploadResumeBtn').click()
            expect(page.locator('#profileState')).to_have_text('PROFILE READY')
            expect(page.locator('#missionResume')).to_have_text('Resume ready')
            page.locator('#searchPreferences summary').click()
            page.locator('#targetDays').fill('7')
            page.get_by_role('button',name='Save search preferences',exact=True).click()
            expect(page.locator('#filterPosted')).to_have_value('7')
            page.reload(wait_until='networkidle')
            expect(page.locator('#profileState')).to_have_text('PROFILE READY')
            expect(page.locator('#filterPosted')).to_have_value('7')
            page.locator('.tab[data-tab="jobs"]').click()
            page.locator('#huntBtn').click()
            expect(page.locator('#jobCountBadge')).to_have_text('3 shown')
            expect(page.locator('#missionDiscovery')).to_have_text('3 matching jobs')
            expect(page.locator('#missionApply')).to_have_text('Drafts only')
            page.screenshot(path=str(output/'jobs-desktop.png'),full_page=True)
            page.locator('#jobsBody button').filter(has_text='Prepare draft').first.click()
            expect(page.locator('#draftDialog')).to_be_visible()
            expect(page.locator('#draftBody')).to_contain_text('Fixture Candidate')
            page.locator('#draftDialog button').filter(has_text='Close').click()
            page.locator('.tab[data-tab="applications"]').click()
            expect(page.locator('#applicationsBody')).to_contain_text('Example Logistics')
            page.locator('.tab[data-tab="jobs"]').click()
            page.locator('#jobsBody button').filter(has_text='Find recruiter').first.click()
            expect(page.locator('#tab-outreach')).to_be_visible()
            expect(page.locator('#outreachJob')).not_to_have_value('')
            page.locator('#outreachDomain').fill('example.com')
            expect(page.locator('#outreachProvider')).to_have_value('prospeo')
            page.once('dialog',lambda d:d.accept())
            page.get_by_role('button',name='Search hiring contacts',exact=True).click()
            expect(page.locator('#apolloResults')).to_contain_text('Recruiter Fixture')
            page.once('dialog',lambda d:d.accept())
            page.locator('#apolloResults button').click()
            expect(page.locator('#outreachContacts')).to_contain_text('recruiter@example.com')
            page.locator('#outreachContacts button').filter(has_text='Verify').click()
            for element in ['verifyEmail','verifyEmployer','verifyRole']:page.locator('#'+element).check()
            page.locator('#verifyTimezone').fill('Asia/Kolkata')
            page.locator('#verifyNote').fill('Fixture professional source checked for business email, current employer and recruitment role.')
            page.get_by_role('button',name='Save verification',exact=True).click()
            expect(page.locator('#outreachContacts')).to_contain_text('Reviewed')
            page.locator('#outreachContacts button').filter(has_text='Prepare email').click()
            expect(page.locator('#outreachComposeDialog')).to_be_visible()
            expect(page.locator('#outreachBody')).to_have_value(re.compile('Fixture Candidate'))
            expect(page.locator('#outreachSendBtn')).to_be_disabled()
            page.locator('#approveEmailReview').check()
            page.locator('#approveEmailRelevance').check()
            page.get_by_role('button',name='Approve this email',exact=True).click()
            expect(page.locator('#outreachSendBtn')).to_be_enabled()
            page.evaluate("async()=>{await privateApi('/api/outreach/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paused:false,business_start:0,business_end:24})})}")
            page.locator('#outreachSendBtn').click()
            expect(page.locator('#outreachDraftState')).to_contain_text('Accepted by Gmail')
            page.locator('#outreachComposeDialog button').filter(has_text='Close').click()
            page.screenshot(path=str(output/'outreach-desktop.png'),full_page=True)
            page.locator('#campaignName').fill('Fixture future opportunities')
            page.locator('#campaignKind').select_option('introduction')
            page.get_by_role('button',name='Create draft campaign',exact=True).click()
            expect(page.locator('#campaignList')).to_contain_text('Fixture future opportunities')
            page.locator('#outreachContacts button').filter(has_text='Prepare email').click()
            expect(page.locator('#outreachBody')).to_have_value(re.compile('future operations opportunities'))
            expect(page.locator('#outreachAttachment')).to_contain_text('fixture-resume.pdf')
            page.locator('#outreachComposeDialog button').filter(has_text='Close').click()
            page.get_by_role('button',name='Use latest PDF',exact=True).click()
            expect(page.locator('.toast').last).to_contain_text('Latest PDF attached to 3 unsent drafts')
            page.get_by_role('button',name='Preview / approve',exact=True).click()
            expect(page.locator('#campaignPreviewEmails')).to_contain_text('Follow-up 2')
            expect(page.locator('#campaignPreviewFacts')).to_contain_text('First follow-up: 5 working days')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'campaign-preview-mobile.png'),full_page=True)
            page.locator('#campaignPreviewDialog button').filter(has_text='Close').click()
            page.get_by_text('Ongoing international job search',exact=True).click()
            expect(page.locator('#programStatus')).to_contain_text('PAUSED')
            expect(page.locator('#programTargets')).to_have_value(re.compile('noon.com'))
            page.locator('#programTargets').fill('Example Logistics | example.com | https://example.com/careers')
            page.get_by_role('button',name='Start daily research & drafts',exact=True).click()
            expect(page.locator('#programStatus')).to_contain_text('ON · DAILY RESEARCH')
            page.get_by_role('button',name='Research now',exact=True).click()
            expect(page.locator('#programShortlists')).to_contain_text('1 hiring leads')
            page.get_by_role('button',name='I got a job — stop outreach',exact=True).click()
            expect(page.locator('#programStatus')).to_contain_text('JOB FOUND · STOPPED')
            page.set_viewport_size({'width':1440,'height':1100})
            for width,height in [(375,667),(390,844),(430,932)]:
                page.set_viewport_size({'width':width,'height':height})
                for name in ['jobs','outreach','applications','sources','settings']:
                    page.locator('.tab[data-tab="'+name+'"]').click()
                    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'), f'Mobile overflow: {name} at {width}'
                    if name=='outreach':assert page.locator('#apolloResults .conn-card').first.bounding_box()['height']<300, 'Recruiter card text is squeezed on mobile'
            page.locator('.tab[data-tab="jobs"]').click()
            page.evaluate('document.activeElement.blur(); window.scrollTo({top:0,behavior:"instant"})')
            expect(page.locator('.brand')).to_be_in_viewport()
            page.screenshot(path=str(output/'jobs-mobile.png'),full_page=True)
            page.route('**/api/operational-readiness',lambda route:route.fulfill(status=503,json={'detail':'Fixture outage'}))
            page.get_by_role('button',name='Refresh system readiness').click()
            expect(page.locator('#missionGmail')).to_have_text('Unknown')
            expect(page.locator('#missionGmailNote')).to_have_text('Could not verify Gmail connection')
            expect(page.locator('#missionResumeNote')).to_have_text('Could not verify resume status')
            expect(page.locator('#missionApplyNote')).to_have_text('Could not verify application policy')
            page.unroute('**/api/operational-readiness')
            page.get_by_role('button',name='Refresh system readiness').click()
            expect(page.locator('#missionGmail')).to_have_text('Connected')
            # A slow old filter response must not overwrite the latest search.
            page.locator('.tab[data-tab="jobs"]').click()
            pending=[]
            def filter_route(route):
                if 'q=stale' in route.request.url:
                    pending.append(route)
                    return
                return route.fulfill(status=200,json={'items':[{'id':999,'title':'Latest filter role','company':'Latest company','country':'India','location':'Noida','source':'Fixture','url':'https://example.com/jobs/latest','match_score':80,'status':'new','match_reasons':[]}]})
            filter_pattern=re.compile(re.escape(base)+r'/api/jobs\?.*')
            page.route(filter_pattern,filter_route)
            page.locator('#jobSearch').fill('stale')
            with page.expect_request(re.compile(r'.*q=stale.*')):
                page.locator('#jobSearch').press('Enter')
            page.locator('#jobSearch').fill('latest')
            page.locator('#jobSearch').press('Enter')
            expect(page.locator('#jobsBody')).to_contain_text('Latest filter role')
            assert len(pending)==1
            pending[0].fulfill(status=200,json={'items':[{'id':998,'title':'Stale filter role','company':'Stale company','country':'India','location':'Noida','source':'Fixture','url':'https://example.com/jobs/stale','match_score':80,'status':'new','match_reasons':[]}]})
            page.wait_for_load_state('networkidle')
            expect(page.locator('#jobsBody')).to_contain_text('Latest filter role')
            expect(page.locator('#jobsBody')).not_to_contain_text('Stale filter role')
            page.unroute(filter_pattern)
            # Signing out must clear both visible and hidden private data, including dialogs.
            page.locator('.tab[data-tab="settings"]').click()
            page.locator('#changeTokenBtn').click()
            expect(page.locator('#profileForm')).to_be_hidden()
            expect(page.locator('#profileName')).to_have_value('')
            expect(page.locator('#profileEmail')).to_have_value('')
            expect(page.locator('#missionDiscovery')).to_have_text('Sign in required')
            expect(page.locator('#jobsBody')).not_to_contain_text('Example Logistics')
            expect(page.locator('#applicationsBody')).not_to_contain_text('Example Logistics')
            expect(page.locator('#outreachContacts')).not_to_contain_text('recruiter@example.com')
            expect(page.locator('#outreachQueue')).not_to_contain_text('recruiter@example.com')
            expect(page.locator('#draftBody')).to_have_text('')
            expect(page.locator('#outreachBody')).to_have_value('')
            assert page.evaluate("localStorage.getItem('jobHunterPortalAccessToken')") is None
            before=len(protected_reads)
            page.locator('.tab[data-tab="sources"]').click()
            page.get_by_role('button',name='Refresh workspace').click()
            page.wait_for_load_state('networkidle')
            assert len(protected_reads)==before, 'Signed-out refresh must not fetch private endpoints'
            # Simulate an existing Google session on a fresh context. Requests still
            # hit fixture APIs, with the fixture owner credential injected by routing.
            google_context=browser.new_context(viewport={'width':1440,'height':1100})
            google_page=google_context.new_page()
            google_page.on('pageerror',lambda e:errors.append(str(e)))
            def google_route(route):
                if not route.request.url.startswith(base):return route.abort()
                if route.request.url==base+'/api/auth/session':
                    return route.fulfill(status=200,json={'authenticated':True,'email':'candidate@example.com'})
                headers={**route.request.headers,'Authorization':'Bearer fixture-frontend-owner-private-key-00001'}
                return route.continue_(headers=headers)
            google_page.route('**/*',google_route)
            google_page.goto(base+'/#applications',wait_until='networkidle')
            expect(google_page.locator('#applicationsBody')).to_contain_text('Example Logistics')
            expect(google_page.locator('#profileName')).to_have_value('Fixture Candidate')
            assert google_page.evaluate("localStorage.getItem('jobHunterPortalAccessToken')") is None
            google_page.locator('.tab[data-tab="settings"]').click()
            google_page.locator('#signOutGoogleBtn').click()
            expect(google_page.locator('#profileName')).to_have_value('')
            expect(google_page.locator('#applicationsBody')).not_to_contain_text('Example Logistics')
            google_context.close()
            assert not errors,errors
            print(f'PASS ({engine}): signed-out privacy, auth-first hydration, jobs, profile/PDF, applications, Apollo search/reveal, verified contact, approved Gmail send (mocked), readiness refresh/outage/recovery, five views at 375/390/430px, sign-out cache clearing and restored Google-session application view; no JavaScript errors')
            browser.close()
    finally:
        if sys.platform=='win32':
            # The venv launcher starts a child Python process; stop both before
            # TemporaryDirectory removes the SQLite database on Windows.
            subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:
            server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait()
