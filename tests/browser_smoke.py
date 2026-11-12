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
    environment.update(ENABLE_SCHEDULER='false',DATABASE_PATH=str(Path(temporary)/'fixture.sqlite'),PORTAL_ACCESS_TOKEN='fixture-frontend-owner-private-key-00001',APOLLO_API_KEY='fixture-api-key',PYTHONDONTWRITEBYTECODE='1')
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
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) else route.abort())
            page.goto(base+'/#settings',wait_until='networkidle')
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
            page.get_by_role('button',name='Search Apollo',exact=True).click()
            expect(page.locator('#apolloResults')).to_contain_text('Recruiter Fixture')
            page.once('dialog',lambda d:d.accept())
            page.locator('#apolloResults button').click()
            expect(page.locator('#outreachContacts')).to_contain_text('recruiter@example.com')
            page.locator('#outreachContacts button').filter(has_text='Verify').click()
            for element in ['verifyEmail','verifyEmployer','verifyRole']:page.locator('#'+element).check()
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
            page.locator('#outreachSendBtn').click()
            expect(page.locator('#outreachDraftState')).to_contain_text('Accepted by Gmail')
            page.locator('#outreachComposeDialog button').filter(has_text='Close').click()
            page.screenshot(path=str(output/'outreach-desktop.png'),full_page=True)
            for width,height in [(375,667),(390,844),(430,932)]:
                page.set_viewport_size({'width':width,'height':height})
                for name in ['jobs','outreach','applications','sources','settings']:
                    page.locator('.tab[data-tab="'+name+'"]').click()
                    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'), f'Mobile overflow: {name} at {width}'
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
            assert not errors,errors
            print(f'PASS ({engine}): jobs, profile/PDF, applications, Apollo search/reveal, verified contact, approved Gmail send (mocked), readiness refresh/outage/recovery, five views at 375/390/430px; no JavaScript errors')
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
