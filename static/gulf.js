// Public-vacancy view. Applying is always a user action on the employer site.
let gulfOpenView=false;
function selectJobMarket(gulf){
  gulfOpenView=!!gulf;
  document.getElementById('gulfMarketBtn').setAttribute('aria-pressed',String(gulfOpenView));
  document.getElementById('savedMarketBtn').setAttribute('aria-pressed',String(!gulfOpenView));
  document.getElementById('gulfRefreshBtn').hidden=!gulfOpenView;
  document.getElementById('gulfViewNote').hidden=!gulfOpenView;
  document.getElementById('filterCountry').value='';
  document.getElementById('filterPosted').disabled=gulfOpenView;
  document.getElementById('filterScore').value=gulfOpenView?'0':'65';
  document.querySelectorAll('.chip[data-score]').forEach(chip=>chip.classList.toggle('active',chip.dataset.score===document.getElementById('filterScore').value));
  document.getElementById('filterSource').value='';
  document.getElementById('filterStatus').value='';
  loadJobs();
}
async function refreshGulfJobs(){
  if(!privateConnectionReady())return;
  const button=document.getElementById('gulfRefreshBtn');button.disabled=true;button.textContent='Checking employer feeds…';
  try{const result=await privateApi('/api/gulf/refresh',{method:'POST'});
    toast(result.status==='already_running'?'Discovery is already running. Refresh the view shortly.':`${result.new_jobs||0} new Gulf roles · ${result.updated_jobs||0} updated${result.errors?.length?' · some feeds need attention':''}`);
    await Promise.all([loadJobs(),loadGulfSources()]);
  }catch(e){toast('Gulf check could not be confirmed: '+e.message,'error')}
  finally{button.disabled=false;button.textContent='Refresh Gulf jobs'}
}
async function loadGulfSources(){
  if(!privateConnectionReady())return;
  try{const data=await privateApi('/api/gulf/sources');
    document.getElementById('gulfSourcesNote').textContent=data.note+' Feed status: '+(data.feed?.status||'not checked')+(data.feed?.detail?' · '+data.feed.detail:'');
    document.getElementById('gulfSourcesGrid').innerHTML=(data.items||[]).map(item=>'<a class="gulf-source" href="'+esc(item.url)+'" target="_blank" rel="noopener noreferrer"><strong>'+esc(item.name)+' ↗</strong><span>'+esc(item.mode==='direct'?'Direct employer feed':item.mode==='indexed'?'Indexed coverage':'External career page')+'</span><small>'+esc(item.note)+'</small></a>').join('');
  }catch(e){document.getElementById('gulfSourcesNote').textContent='Could not load Gulf sources: '+e.message}
}
async function viewJob(jobId){
  const epoch=authEpoch;
  try{const job=await privateApi('/api/jobs/'+Number(jobId));if(epoch!==authEpoch||!privateConnectionReady())return;
    document.getElementById('jobDetailTitle').textContent=job.title;
    document.getElementById('jobDetailCompany').textContent=[job.company,job.location].filter(Boolean).join(' · ');
    document.getElementById('jobDetailEvidence').textContent=job.published_recently_checked?'Listed as published in employer feed · checked '+ago(job.gulf_evidence.checked_at)+'. Availability can change.':'Current application availability has not been verified. Check the original listing.';
    document.getElementById('jobDetailDate').textContent='Posted: '+(job.posted_at?new Date(job.posted_at).toLocaleDateString():'Not published')+' · Salary: '+(job.salary||'Not published');
    document.getElementById('jobDetailFit').textContent=(job.fit_notes||[]).join('\n');
    document.getElementById('jobDetailDescription').textContent=job.description||'Read the full requirements on the original employer page.';
    const link=document.getElementById('jobApplyLink'),url=job.apply_url||job.url;
    const safe=/^https?:\/\//i.test(url||'');
    link.hidden=!safe||job.status==='closed';link.href=safe?url:'#';
    link.textContent=job.apply_url?'Open employer application ↗':'Check original listing ↗';
    document.getElementById('jobDetailDialog').showModal();
  }catch(e){toast('Could not load role: '+e.message,'error')}
}
