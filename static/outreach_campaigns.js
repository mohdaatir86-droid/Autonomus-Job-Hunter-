/* Finite campaign approval and durable outreach controls; no secrets stored here. */
let campaignOverview=null,campaignPreview=null;
const outreachLoadBase=loadOutreach;
loadOutreach=async function(){
  if(!privateConnectionReady())return;
  try{
    const [,data]=await Promise.all([outreachLoadBase(),privateApi('/api/outreach/overview'),loadResearchProgram()]);
    if(!privateConnectionReady())return;
    campaignOverview=data;
    document.getElementById('campaignMetrics').innerHTML=Object.entries(data.counts).map(([k,v])=>'<div class="coverage-item"><strong>'+Number(v)+'</strong><span>'+esc(k==='sent'?'Accepted by Gmail':k.replaceAll('_',' '))+'</span></div>').join('')||'<p>No outreach emails yet.</p>';
    const r=data.rules;
    for(const key of ['timezone','business_start','business_end','enrichment_daily','enrichment_monthly','followups','first_followup_days','final_followup_days'])document.getElementById('rule_'+key).value=r[key];
    document.getElementById('rule_paused').checked=r.paused;
    const pdf=data.profile.resume_attachment;
    document.getElementById('campaignResume').textContent=pdf?pdf.filename+' · version '+pdf.version+' · uploaded '+new Date(pdf.uploaded_at).toLocaleString()+' · '+Math.ceil(pdf.size_bytes/1024)+' KB':'Original PDF needed. Upload your latest PDF in Profile & rules; older extracted text cannot be attached.';
    document.getElementById('campaignProfile').textContent=(data.profile.full_name||'Name missing')+' · '+(data.profile.email||'Email missing')+' · '+(data.profile.experience_summary||'Experience summary missing');
    document.getElementById('campaignTargets').textContent='Saved targeting: '+(data.search_preferences.target_titles||[]).join(', ')+' · '+(data.search_preferences.target_countries||[]).join(', ');
    document.getElementById('campaignStorage').textContent='Approved emails send only after activation and unpausing. Free hosting may delay processing while asleep. Apollo limits count lookups; check actual credits in Apollo.'+(data.storage.startsWith('sqlite')?' Persistent storage must be confirmed before sending.':'');
    const selector=document.getElementById('outreachCampaign'),prior=selector.value;
    selector.innerHTML='<option value="">Individual reviewed email</option>'+data.campaigns.map(c=>'<option value="'+Number(c.id)+'">'+esc(c.name)+' · '+esc(c.kind)+' · '+esc(c.state)+'</option>').join('');
    if([...selector.options].some(x=>x.value===prior))selector.value=prior;
    document.getElementById('campaignList').innerHTML=data.campaigns.map(c=>'<div class="conn-card"><div><strong>'+esc(c.name)+'</strong><p>'+esc(c.kind)+' · '+esc(c.state)+'</p></div><div><button class="btn btn-ghost btn-sm" onclick="previewCampaign('+Number(c.id)+')">Preview / approve</button> <button class="btn btn-ghost btn-sm" onclick="refreshCampaignResume('+Number(c.id)+',this)">Use latest PDF</button> <button class="btn btn-ghost btn-sm" onclick="pauseCampaign('+Number(c.id)+')">Pause</button></div></div>').join('')||'<p>Create a campaign, select it above, then prepare emails for 1–5 verified contacts.</p>';
    const filter=document.getElementById('queueCampaignFilter'),savedFilter=filter.value;
    filter.innerHTML='<option value="">All campaigns</option>'+data.campaigns.map(c=>'<option value="'+Number(c.id)+'">'+esc(c.name)+'</option>').join('');filter.value=savedFilter;
    renderCampaignQueue();
    if(typeof renderFocusedOutreach==='function'){renderFocusedOutreach(data);renderFocusedCampaigns(data);}
    document.getElementById('campaignReplies').innerHTML=data.replies.map(r=>'<article class="conn-card"><div><strong>'+esc(r.category.replaceAll('_',' '))+'</strong><p>'+esc(r.snippet)+'</p><p class="profile-note">Suggested reply — edit and send manually: '+esc(r.suggested_reply)+'</p></div></article>').join('')||'<div class="panel queue-empty"><span class="empty-icon" aria-hidden="true">↩</span><h3>Your conversations will appear here</h3><p>Once you send an approved email, check here for replies and next steps.</p></div>';
  }catch(e){document.getElementById('campaignStorage').textContent=e.message;}
};
function renderCampaignQueue(){if(typeof renderFocusedQueue==='function')renderFocusedQueue();}

async function createCampaign(){
  try{const c=await privateApi('/api/outreach/campaigns',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.getElementById('campaignName').value,kind:document.getElementById('campaignKind').value})});await loadOutreach();document.getElementById('outreachCampaign').value=c.id;toast('Draft campaign created. Prepare its contact emails next.','success');}catch(e){toast(e.message,'error');}
}
createOutreachDraft=async function(contactId){
  const cId=Number(document.getElementById('outreachCampaign').value)||null;
  const campaign=campaignOverview?.campaigns.find(c=>c.id===cId);
  const jobId=Number(document.getElementById('outreachJob').value)||null;
  if(campaign?.kind!=='introduction'&&!jobId){toast('Choose a discovered vacancy first.','error');return;}
  try{const d=await privateApi('/api/outreach/drafts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({contact_id:contactId,job_id:jobId,campaign_id:cId,target_domain:document.getElementById('outreachDomain').value.trim()})});await loadOutreach();openOutreachDraft(d.id);}catch(e){toast(e.message,'error');}
};
const outreachOpenBase=openOutreachDraft;
openOutreachDraft=function(id){outreachOpenBase(id);const d=outreachCurrentDraft,p=campaignOverview?.profile.resume_attachment;document.getElementById('outreachAttachment').textContent=p&&d.resume_sha===p.sha256?'Attachment: '+p.filename+' · version '+p.version:'Attachment missing or outdated. Upload the PDF, then use Attach current resume.';};
async function bindCurrentResume(){const id=outreachCurrentDraft.id;try{await privateApi('/api/outreach/drafts/'+id+'/resume',{method:'POST'});await loadOutreach();if(outreachCurrentDraft?.id===id&&document.getElementById('outreachComposeDialog').open)openOutreachDraft(id);}catch(e){toast(e.message,'error');}}
async function refreshCampaignResume(id,button){if(button.disabled)return;button.disabled=true;try{const r=await privateApi('/api/outreach/campaigns/'+id+'/resume',{method:'POST'});await loadOutreach();toast('Latest PDF attached to '+r.updated+' unsent drafts. Review email content and approve a fresh preview.','success');}catch(e){toast(e.message,'error');}finally{if(button.isConnected)button.disabled=false;}}
async function previewCampaign(id){try{campaignPreview=await privateApi('/api/outreach/campaigns/'+id+'/preview');const x=campaignPreview,s=x.snapshot;document.getElementById('campaignPreviewTitle').textContent=x.campaign.name;document.getElementById('campaignPreviewFacts').textContent=humanCampaignFacts(s);document.getElementById('campaignPreviewEmails').innerHTML=x.items.map(d=>'<article class="campaign-email"><strong>'+esc(d.sender_email)+' → '+esc(d.recipient_email)+'</strong><p>'+esc(d.subject)+' · '+(d.step_number?'Follow-up '+d.step_number:'Initial email')+'</p><pre>'+esc(d.body)+'</pre></article>').join('')||'<p>No emails prepared yet.</p>';document.getElementById('campaignConfirm').checked=false;document.getElementById('campaignPreviewDialog').showModal();}catch(e){toast(e.message,'error');}}
async function activateCampaign(){if(!document.getElementById('campaignConfirm').checked){toast('Review and confirm the profile, PDF, recipients and every email first.','error');return;}try{const r=await privateApi('/api/outreach/campaigns/'+campaignPreview.campaign.id+'/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true,profile_confirmed:true,preview_hash:campaignPreview.preview_hash})});document.getElementById('campaignPreviewDialog').close();toast('Campaign approved.'+(r.paused_globally?' Global pause is still on.':''),'success');await loadOutreach();}catch(e){toast(e.message,'error');}}
async function pauseCampaign(id){try{await privateApi('/api/outreach/campaigns/'+id+'/pause',{method:'POST'});await loadOutreach();}catch(e){toast(e.message,'error');}}
async function saveCampaignRules(){const payload={paused:document.getElementById('rule_paused').checked,timezone:document.getElementById('rule_timezone').value};for(const k of ['business_start','business_end','enrichment_daily','enrichment_monthly','followups','first_followup_days','final_followup_days'])payload[k]=Number(document.getElementById('rule_'+k).value);try{await privateApi('/api/outreach/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});toast('Rules saved. Changed campaign scope requires a fresh preview approval.','success');await loadOutreach();}catch(e){toast(e.message,'error');}}
async function stopAllOutreach(){try{await privateApi('/api/outreach/stop',{method:'POST'});toast('Outreach paused; queued approvals cleared. In-flight requests cannot be recalled.','success');await loadOutreach();}catch(e){toast(e.message,'error');}}
async function syncOutreachReplies(){try{const r=await privateApi('/api/outreach/replies/sync',{method:'POST'});toast(r.blocked||'Checked '+r.checked+' outreach threads.');await loadOutreach();}catch(e){toast(e.message,'error');}}
async function runOutreachQueue(){try{const r=await privateApi('/api/outreach/dispatch',{method:'POST'});toast(r.skipped||r.message||('Accepted by Gmail: '+r.sent));await loadOutreach();}catch(e){toast(e.message,'error');}}
const verifyBase=openContactVerification;
openContactVerification=function(id){verifyBase(id);document.getElementById('verifyTimezone').value=outreachContacts.find(c=>c.id===id)?.timezone||'';};
verifyOutreachContact=async function(){try{await privateApi('/api/outreach/contacts/'+outreachVerifyId+'/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email_confirmed:document.getElementById('verifyEmail').checked,employer_confirmed:document.getElementById('verifyEmployer').checked,role_confirmed:document.getElementById('verifyRole').checked,note:document.getElementById('verifyNote').value,timezone:document.getElementById('verifyTimezone').value})});document.getElementById('outreachVerifyDialog').close();await loadOutreach();}catch(e){toast(e.message,'error');}};
// Clear all added private content on logout, including an open campaign snapshot.
const resetCampaignBase=resetPrivateWorkspace;
resetPrivateWorkspace=function(){resetCampaignBase();campaignOverview=null;campaignPreview=null;for(const id of ['campaignProfile','campaignResume','campaignTargets','campaignList','campaignReplies','campaignMetrics','batchPreparation','campaignPreviewFacts','campaignPreviewEmails']){const el=document.getElementById(id);if(el)el.textContent='';}document.getElementById('campaignPreviewDialog')?.close();};

async function loadResearchProgram(){
  try{const p=await privateApi('/api/outreach/program');if(!privateConnectionReady())return;
    if(typeof renderResearchSignal==='function')renderResearchSignal(p);
    document.getElementById('programTargets').value=p.targets.map(t=>[t.company,t.domain,t.source_url].join(' | ')).join('\n');
    document.getElementById('programStatus').textContent=(p.job_found?'JOB FOUND · STOPPED':p.enabled?'ON · DAILY RESEARCH & DRAFTS':'PAUSED')+' · '+p.message;
    document.getElementById('programShortlists').innerHTML=(p.shortlist||[]).map(t=>'<div class="conn-card"><div><strong>'+esc(t.company)+'</strong><p>'+esc(t.domain)+' · '+Number(t.lead_count)+' hiring leads · '+esc(t.searched_at)+'</p></div><button class="btn btn-ghost btn-sm" data-domain="'+esc(t.domain)+'" onclick="openProgramShortlist(this.dataset.domain)">Review cached hiring shortlist</button></div>').join('');
  }catch(e){document.getElementById('programStatus').textContent=e.message;}
}
async function saveResearchProgram(enabled){
  try{const targets=document.getElementById('programTargets').value.trim().split('\n').filter(Boolean).map(line=>{const [company,domain,source_url]=line.split('|').map(x=>x.trim());return {company,domain,source_url};});
    await privateApi('/api/outreach/program',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled,job_found:false,targets})});await loadResearchProgram();
  }catch(e){toast(e.message,'error');}
}
async function runResearchProgram(){try{const r=await privateApi('/api/outreach/program/run',{method:'POST'});toast(r.message||r.blocked||r.skipped);await loadOutreach();}catch(e){toast(e.message,'error');}}
async function jobSearchCompleted(){try{await privateApi('/api/outreach/program',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_found:true})});await loadOutreach();toast('Job search and queued outreach stopped.');}catch(e){toast(e.message,'error');}}
async function openProgramShortlist(domain){openRecruiterSearch();document.getElementById('outreachDomain').value=domain;document.getElementById('outreachProvider').value='prospeo';await searchContactProvider(document.getElementById('providerSearchBtn'));}
const resetResearchBase=resetPrivateWorkspace;
resetPrivateWorkspace=function(){resetResearchBase();for(const id of ['programTargets','programStatus','programShortlists']){const el=document.getElementById(id);if(el){el.textContent='';if('value' in el)el.value='';}}};

async function prepareMatchedOutreach(){try{const r=await privateApi('/api/outreach/prepare-batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.getElementById('campaignName').value||'Matched vacancy applications'})});document.getElementById('batchPreparation').textContent=r.blocked||('Prepared '+r.prepared+' contacts. Review the exact batch before activation.');if(r.pending_contacts?.length)document.getElementById('batchPreparation').textContent+=' Contacts needed: '+r.pending_contacts.map(j=>j.title+' at '+j.company).join('; ');await loadOutreach();if(r.campaign_id)await previewCampaign(r.campaign_id);}catch(e){toast(e.message,'error');}}


function humanCampaignFacts(s){
  const p=s.candidate.profile,pdf=s.candidate.resume,t=s.candidate.search_preferences,r=s.rules;
  return ['YOUR PROFILE',p.full_name,p.email,p.phone||'',p.linkedin_url||'',p.experience_summary||p.headline||'',
    'Skills: '+(p.skills||[]).join(', '),'',
    'RESUME',pdf?pdf.filename+' · version '+pdf.version+' · uploaded '+new Date(pdf.uploaded_at).toLocaleString():'Original PDF missing','',
    'TARGETING','Roles: '+(t.target_titles||[]).join(', '),'Countries: '+(t.target_countries||[]).join(', '),'Locations: '+(t.preferred_locations||[]).join(', '),
    'Minimum experience: '+t.min_experience_years+' years · Minimum India salary: '+t.min_india_salary_lpa+' LPA','',
    'SENDING RULES','Shared daily cap: '+r.daily_limit+' attempts','Automatically send approved emails: '+(r.auto_send_approved?'on':'off'),
    'Daily reset timezone: '+r.timezone,'Recipient-local business hours: '+r.business_start+':00–'+r.business_end+':00, Monday–Friday',
    'One initial contact per company','Maximum follow-ups: '+r.followups,'First follow-up: '+r.first_followup_days+' working days after the initial email',
    'Final follow-up: '+r.final_followup_days+' working days after the first follow-up',
    'Apollo lookup caps: '+r.enrichment_daily+'/day; '+r.enrichment_monthly+'/month','Any reply, bounce or opt-out stops follow-ups'].filter(x=>x!==undefined).join('\n');
}
