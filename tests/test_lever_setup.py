import asyncio
import httpx
import job_sources
import job_hunter
import main


def test_verified_default_boards_work_without_lever_sites(monkeypatch):
    monkeypatch.delenv('LEVER_SITES',raising=False)
    monkeypatch.setattr(job_sources,'get_settings',lambda:job_hunter.DEFAULT_SETTINGS)
    requested=[]
    def response(request):
        requested.append(request.url.path.rsplit('/',1)[-1])
        return httpx.Response(200,json=[{'id':'fixture','text':'Operations Manager','categories':{'location':'Dubai'},'hostedUrl':'https://jobs.lever.co/fixture/1','createdAt':1780000000000}])
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            return await job_sources.fetch_lever(client,10)
    jobs,error=asyncio.run(run())
    assert error is None
    assert set(requested)=={'meesho','lalamove'}
    assert jobs


def test_relevant_roles_are_selected_before_per_board_limit(monkeypatch):
    monkeypatch.setenv('LEVER_SITES','fixture')
    monkeypatch.setattr(job_sources,'get_settings',lambda:job_hunter.DEFAULT_SETTINGS)
    rows=[{'id':str(i),'text':'Software Engineer','categories':{'location':'San Francisco, USA'},'hostedUrl':f'https://jobs.lever.co/fixture/{i}'} for i in range(15)]
    rows.append({'id':'gulf','text':'Operations Manager','categories':{'location':'Dubai'},'hostedUrl':'https://jobs.lever.co/fixture/gulf'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=rows))) as client:
            return await job_sources.fetch_lever(client,10)
    jobs,error=asyncio.run(run())
    assert error is None
    assert any(job['source_id']=='gulf' for job in jobs)
    assert len(jobs)<=10


def test_catalog_does_not_require_env_when_defaults_exist(monkeypatch):
    monkeypatch.delenv('LEVER_SITES',raising=False)
    monkeypatch.setattr(main,'get_gmail_status',lambda:{'connected':False})
    monkeypatch.setattr(job_sources,'get_source_report',lambda:{'sources':[{'name':'Lever company boards','status':'missing_configuration','detail':'LEVER_SITES not set'}]})
    lever=next(source for source in main.sources()['sources'] if source['name']=='Lever company boards')
    assert lever['configured'] is True
    assert lever['status']=='unknown'
