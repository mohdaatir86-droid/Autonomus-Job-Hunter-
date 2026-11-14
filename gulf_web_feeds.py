"""Bounded reads of public employer search pages; never executes website JS."""
import asyncio
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import httpx

ISO3 = {'ARE':'United Arab Emirates','SAU':'Saudi Arabia','QAT':'Qatar','KWT':'Kuwait','OMN':'Oman','BHR':'Bahrain'}


def _location(city, country):
    city = str(city or '').strip()
    return city if country.lower() in city.lower() else ', '.join(filter(None, [city, country]))


async def fetch_amazon(client, profile, settings):
    from gulf_sources import fit, mark, role_candidate
    from job_sources import _clean_job, _parse_iso
    now = datetime.now(timezone.utc).isoformat()
    jobs, errors = [], []
    try:
        for offset in (0, 100):
            response = await client.get('https://www.amazon.jobs/en/search.json', params={
                'base_query':'Operations Manager','normalized_country_code[]':list(ISO3),
                'result_limit':100,'offset':offset,'sort':'recent'}, timeout=15, follow_redirects=False)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data,dict) or data.get('error') or not isinstance(data.get('jobs'), list):
                raise ValueError('Search changed')
            for row in data['jobs']:
                country = ISO3.get(row.get('country_code'))
                path = str(row.get('job_path') or '')
                match = re.fullmatch(r'/en/jobs/(\d+)(?:/[A-Za-z0-9-]+)?', path)
                if not country or not match or not role_candidate(str(row.get('title') or '')):
                    continue
                description = '\n'.join(str(row.get(k) or '') for k in ('basic_qualifications','preferred_qualifications','description'))
                if not description.strip():
                    continue
                posted = _parse_iso(row.get('posted_date'))
                if not posted and row.get('posted_date'):
                    for pattern in ('%B %d, %Y','%b %d, %Y'):
                        try:
                            posted = datetime.strptime(' '.join(str(row['posted_date']).split()), pattern).replace(tzinfo=timezone.utc).isoformat()
                            break
                        except ValueError:
                            pass
                if row.get('posted_date') and not posted or posted and posted > now:
                    continue
                url = 'https://www.amazon.jobs' + path
                job = _clean_job({'id':match[1],'title':row['title'],'company':'Amazon',
                    'location':_location(row.get('city'),country),'description':html.unescape(description),
                    'date':posted,'url':url,'employment_type':row.get('job_schedule_type') or ''}, 'Amazon Jobs')
                if job and fit(job,profile,settings)[0]:
                    jobs.append(mark(job,url,now))
            if offset + len(data['jobs']) >= int(data.get('hits',0)) or not data['jobs']:
                break
        else:
            errors.append('Amazon: first 200 operations search results checked; coverage is partial')
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        errors.append('Amazon: public search unavailable or changed; coverage is partial')
    return jobs, errors


class _AlshayaHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[]; self.links=[]; self.description=[]; self.locations=[]; self.headings=[]; self.dates={}; self.ids=set()
    def handle_starttag(self, tag, attributes):
        attrs=dict(attributes)
        if attrs.get("id"):self.ids.add(attrs["id"])
        if tag=='a':
            self.links.append({'href':attrs.get('href',''),'text':[]})
            attrs['_anchor']=len(self.links)-1
        if tag=='h2':
            self.headings.append([]);attrs['_heading']=len(self.headings)-1
        if tag=='time':
            for _,parent in self.stack:
                classes=parent.get('class','').split()
                for key in ('open-date','close-date'):
                    if key in classes:self.dates[key]=attrs.get('datetime')
        if tag not in {'br','hr','img','input','link','meta','source','wbr','area','base','embed','param'}:
            self.stack.append((tag,attrs))
    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                self.stack=self.stack[:index];break
    def handle_data(self,data):
        if any(t in {'script','style'} for t,_ in self.stack):return
        for _,attrs in self.stack:
            if '_anchor' in attrs:self.links[attrs['_anchor']]['text'].append(data)
            if '_heading' in attrs:self.headings[attrs['_heading']].append(data)
        if any(a.get('id')=='job-details' for _,a in self.stack):self.description.append(data)
        if any('job-fields' in a.get('class','').split() for _,a in self.stack) and any('location' in a.get('class','').split() for _,a in self.stack):self.locations.append(data)


async def fetch_alshaya(client,profile,settings):
    from gulf_sources import fit, mark, role_candidate, COUNTRIES
    from job_hunter import detect_country
    from job_sources import _clean_job, _parse_iso
    checked=datetime.now(timezone.utc).isoformat()
    host='https://jobsearch.alshaya.com'
    errors=[];seen=set();candidates=[];semaphore=asyncio.Semaphore(4)
    async def page(url):
        async with semaphore:
            response=await client.get(url,timeout=15,follow_redirects=False)
            response.raise_for_status()
            if len(response.content)>3_000_000:raise ValueError('Oversized page')
            parser=_AlshayaHTML();parser.feed(response.text)
            if '/job/' not in urlsplit(url).path and 'search-results-content' not in parser.ids:raise ValueError('Listing format changed')
            if url.endswith('/cau/en/job') and 'search-results-content' not in parser.ids:raise ValueError('Listing format changed')
            return parser
    try:
        next_url=host+'/cau/en/job'
        for index in range(3):
            listing=await page(next_url)
            for link in listing.links:
                url=urljoin(host,link['href']);parts=urlsplit(url)
                label=' '.join(link['text']).strip()
                if parts.scheme=='https' and parts.netloc=='jobsearch.alshaya.com' and re.fullmatch(r'/cau/en/job/\d+/[a-z0-9-]+',parts.path) and url not in seen:
                    seen.add(url)
                    if role_candidate(label):candidates.append(url)
            following=next((urljoin(host,l['href']) for l in listing.links if re.fullmatch(r'/cau/en/listing/\?page=\d+&page-items=20',html.unescape(l['href']))),None)
            if not following:break
            next_url=following
        else:
            errors.append('Alshaya: first 60 published results checked; coverage is partial')
    except (httpx.HTTPError,ValueError,TypeError,KeyError,AttributeError):
        errors.append('Alshaya: a listing page is unavailable; coverage is partial')
    async def detail(url):
        try:
            parsed=await page(url)
            title=' '.join(parsed.headings[0]).strip() if parsed.headings else ''
            description=' '.join(parsed.description).strip()
            location=' '.join(parsed.locations).strip()
            country=detect_country(location,title,'')
            apply=any(' '.join(l['text']).strip().lower()=='apply now' for l in parsed.links)
            start=_parse_iso(parsed.dates.get('open-date'));end=_parse_iso(parsed.dates.get('close-date'))
            if parsed.dates.get('open-date') and not start:return None
            if not apply or not description or country not in COUNTRIES.values() or (start and start>checked) or (end and end<=checked):return None
            if parsed.dates.get('close-date') and not end:return None
            job=_clean_job({'id':urlsplit(url).path.split('/')[4],'title':title,'company':'Alshaya',
                'location':_location(location,country),'description':description,'date':start,'url':url},'Alshaya Careers')
            if job and fit(job,profile,settings)[0]:return mark(job,url,checked,end)
        except (httpx.HTTPError,ValueError,TypeError,KeyError,IndexError):
            errors.append('Alshaya: a relevant detail could not be verified')
        return None
    if len(candidates)>15:errors.append('Alshaya: first 15 relevant details checked; coverage is partial')
    rows=await asyncio.gather(*(detail(url) for url in candidates[:15]))
    return [row for row in rows if row],errors


def _phenom_data(content):
    marker='phApp.ddo = '
    if len(content)>5_000_000 or marker not in content:raise ValueError('Publisher data unavailable')
    value,_=json.JSONDecoder().raw_decode(content[content.index(marker)+len(marker):])
    if not isinstance(value,dict):raise ValueError('Publisher data changed')
    return value


async def fetch_phenom(client,profile,settings,name,host):
    """Reads publisher-embedded JSON only. Global scan is explicitly partial."""
    from gulf_sources import fit,mark,role_candidate,COUNTRIES
    from job_sources import _clean_job,_parse_iso
    errors=[];semaphore=asyncio.Semaphore(4);checked=datetime.now(timezone.utc).isoformat()
    async def read(url,params=None):
        async with semaphore:
            response=await client.get(url,params=params,timeout=15,follow_redirects=False)
            if response.status_code in {301,302,307,308}:
                target=urljoin(url,response.headers.get('location',''))
                parsed=urlsplit(target)
                if parsed.scheme!='https' or parsed.netloc!=host or not parsed.path.startswith('/global/en/job/'):raise ValueError('Unexpected redirect')
                response=await client.get(target,timeout=15,follow_redirects=False)
            response.raise_for_status()
            return _phenom_data(response.text)
    async def search(offset):
        try:
            data=await read(f'https://{host}/global/en/search-results',{'keywords':'operations','from':offset,'s':1})
            search=data['eagerLoadRefineSearch']
            if not isinstance(search,dict) or search.get('status') not in (200,'success') or not isinstance(search.get('data'),dict) or not isinstance(search['data'].get('jobs'),list):raise ValueError('Search unavailable')
            return search
        except (httpx.HTTPError,ValueError,TypeError,KeyError,AttributeError):
            errors.append(f'{name}: a public search page is unavailable; coverage is partial')
            return {}
    first=await search(0)
    total=int(first.get('totalHits',0));pages=[first]+await asyncio.gather(*(search(offset) for offset in range(10,min(total,60),10)))
    if total>60:errors.append(f'{name}: at most 60 global search results checked; Gulf coverage is partial')
    candidates={}
    for page in pages:
        for row in (page.get('data') or {}).get('jobs',[]):
            if row.get('country') in COUNTRIES.values() and role_candidate(str(row.get('title') or '')):
                identifier=str(row.get('jobId') if name=='Majid Al Futtaim' else row.get('jobSeqNo') or '')
                if re.fullmatch(r'[A-Za-z0-9_-]{1,120}',identifier):candidates[identifier]=row
    async def detail(identifier,row):
        url=f'https://{host}/global/en/job/{identifier}'
        try:
            data=await read(url);detail=data.get('jobDetail') or {};job=(detail.get('data') or {}).get('job') or {}
            if detail.get('status') not in (200,'success') or str(job.get('jobStatus') or '').lower() not in {'new','active','open'}:return None
            if job.get('country')!=row.get('country') or str(job.get('jobId') if name=='Majid Al Futtaim' else job.get('jobSeqNo') or '')!=identifier:return None
            if str(job.get('siteType') or '').lower() not in {'external',''}:return None
            description=job.get('description')
            if not description:return None
            posted=_parse_iso(job.get('postedDate'));end=_parse_iso(job.get('validThrough'))
            if job.get('postedDate') and not posted or job.get('validThrough') and not end:return None
            if posted and posted>checked or end and end<=checked:return None
            record=_clean_job({'id':identifier,'title':job.get('title'),'company':name,
                'location':_location(job.get('city') or job.get('location'),job['country']),
                'description':html.unescape(str(description)),'date':posted,'url':url,
                'employment_type':job.get('jobType') or ''},'Employer search/'+name)
            if record and fit(record,profile,settings)[0]:return mark(record,url,checked,end)
        except (httpx.HTTPError,ValueError,TypeError,KeyError,AttributeError):
            errors.append(f'{name}: a shortlisted public detail could not be verified')
        return None
    if len(candidates)>12:errors.append(f'{name}: first 12 relevant details checked; coverage is partial')
    rows=await asyncio.gather(*(detail(key,row) for key,row in list(candidates.items())[:12]))
    return [row for row in rows if row],errors
