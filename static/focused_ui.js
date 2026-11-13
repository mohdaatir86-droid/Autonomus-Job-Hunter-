/* Presentation only. Provider calls, approvals and sending stay in their existing modules. */
let focusedOutreachView='drafts';
function openOutreachView(view){
  if(!['drafts','campaigns','contacts','replies','controls'].includes(view))view='drafts';
  focusedOutreachView=view;
  document.querySelectorAll('[data-outreach-panel]').forEach(panel=>panel.hidden=panel.dataset.outreachPanel!==view);
  document.querySelectorAll('[data-outreach-view]').forEach(button=>{const active=button.dataset.outreachView===view;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active||(view==='controls'&&button.dataset.outreachView==='drafts')?0:-1;});
}
function openCampaignCreator(){openOutreachView('campaigns');document.getElementById('campaignCreator').open=true;document.getElementById('campaignName').focus();}
function openRecruiterSearch(){openOutreachView('contacts');document.getElementById('recruiterSearch').open=true;document.getElementById('outreachDomain').focus();}
function filterOutreachState(state){openOutreachView('drafts');document.getElementById('queueStateFilter').value=state;document.getElementById('queueShowFollowups').checked=state!=='draft';renderCampaignQueue();}
function renderResearchSignal(program){const el=document.getElementById('focusedResearch');if(!el)return;el.textContent=program.job_found?'Job search stopped':program.enabled?'Research on':'Research paused';el.className='signal-chip '+(program.enabled&&!program.job_found?'on':'');}
function renderFocusedOutreach(data){
  const drafts=outreachDrafts||[],roots=drafts.filter(d=>!d.step_number);
  document.getElementById('focusedDraftCount').textContent=roots.filter(d=>d.state==='draft').length;
  document.getElementById('focusedFollowupCount').textContent=drafts.filter(d=>d.step_number&&d.state==='draft').length+' follow-ups also prepared';
  document.getElementById('focusedScheduledCount').textContent=drafts.filter(d=>d.state==='approved').length;
  document.getElementById('focusedSentCount').textContent=drafts.filter(d=>d.state==='sent').length;
  document.getElementById('focusedReplyCount').textContent=new Set((data.replies||[]).map(r=>r.root_id)).size;
  const paused=!!data.rules.paused,sending=document.getElementById('focusedSending');
  sending.textContent=paused?'Sending paused':'Sending enabled';sending.className='signal-chip '+(paused?'paused':'on');
  const resume=data.profile.resume_attachment;
  document.getElementById('focusedResume').textContent=resume?'Resume v'+resume.version+' ready':'Resume needed';
}
function renderFocusedCampaigns(data){
  document.getElementById('campaignList').innerHTML=data.campaigns.map(c=>{
    const messages=outreachDrafts.filter(d=>Number(d.campaign_id)===Number(c.id)),initials=messages.filter(d=>!d.step_number).length;
    return '<article class="campaign-card"><span class="company-mark" aria-hidden="true">'+(c.kind==='introduction'?'↗':'◈')+'</span><div class="campaign-copy"><span class="campaign-kind">'+(c.kind==='introduction'?'Future introductions':'Verified vacancies')+'</span><div><strong>'+esc(c.name)+'</strong></div><p>'+initials+' initial email'+(initials===1?'':'s')+' · '+messages.filter(d=>d.step_number).length+' follow-ups · <span class="status-badge">'+esc(c.state)+'</span></p></div><div class="campaign-card-actions"><button class="btn btn-primary btn-sm" aria-label="Preview / approve" onclick="previewCampaign('+Number(c.id)+')">Review batch →</button><details class="card-menu"><summary aria-label="More campaign actions">···</summary><div><button class="btn btn-ghost btn-sm" onclick="refreshCampaignResume('+Number(c.id)+',this)">Use latest PDF</button><button class="btn btn-ghost btn-sm" onclick="pauseCampaign('+Number(c.id)+')">Pause</button></div></details></div></article>';
  }).join('')||'<div class="panel queue-empty"><span class="empty-icon" aria-hidden="true">↗</span><h3>Start with one good conversation</h3><p>Create a campaign for a verified vacancy or a future introduction.</p><button class="btn btn-primary" onclick="openCampaignCreator()">Create a campaign</button></div>';
}
function renderFocusedQueue(){
  const campaign=document.getElementById('queueCampaignFilter').value,state=document.getElementById('queueStateFilter').value,includeFollowups=document.getElementById('queueShowFollowups').checked;
  const drafts=outreachDrafts.filter(d=>(!campaign||String(d.campaign_id)===campaign)&&(!state||d.state===state)&&(includeFollowups||!d.step_number));
  const labels={draft:'Needs review',approved:'Scheduled',sent:'Accepted by Gmail',delivery_unknown:'Check delivery',cancelled:'Cancelled',failed:'Needs attention'};
  document.getElementById('outreachQueue').innerHTML=drafts.map(d=>{
    const contact=outreachContacts.find(c=>c.id===d.contact_id),company=d.target_company||contact?.company||'Professional contact';
    const followups=outreachDrafts.filter(x=>x.parent_id===d.id).length;
    const excerpt=String(d.body||'').split('Regards,')[0].split('\n').filter(Boolean).slice(1).join(' ').slice(0,145);
    return '<tr><td><div class="queue-recipient"><span class="company-mark" aria-hidden="true">'+esc(company.slice(0,1).toUpperCase())+'</span><div><strong>'+esc(company)+'</strong><small>'+esc(contact?.name||'Hiring team')+'</small><small>'+esc(d.recipient_email)+'</small></div></div></td><td><div class="queue-subject">'+esc(d.subject)+'</div><div class="queue-excerpt">'+esc(excerpt)+(excerpt.length>=145?'…':'')+'</div><span class="followup-tag">'+(d.step_number?'Follow-up '+Number(d.step_number):followups?followups+' follow-ups included':'Initial email')+'</span></td><td><span class="email-state '+esc(d.state)+'">'+esc(labels[d.state]||d.state)+'</span>'+(d.due_at?'<div class="job-meta">'+esc(new Date(d.due_at).toLocaleString())+'</div>':'')+(d.error?'<div class="job-meta">'+esc(d.error)+'</div>':'')+(d.response_status?'<div class="job-meta">'+esc(d.response_status)+'</div>':'')+'</td><td><button class="btn btn-ghost btn-sm" onclick="openOutreachDraft('+Number(d.id)+')">Review</button></td></tr>';
  }).join('')||'<tr><td colspan="4"><div class="queue-empty"><span class="empty-icon" aria-hidden="true">✉</span><h3>Your next conversation is waiting</h3><p>No messages match this view. Start with a relevant job or a verified recruiter.</p><button class="btn btn-primary btn-sm" onclick="openOutreachView(\'contacts\')">Explore recruiters →</button></div></td></tr>';
  document.querySelector('#focusedQueueTable table')?.classList.add('queue-table');labelResponsiveTables();
}
const focusedSwitchBase=switchTab;
switchTab=function(name){focusedSwitchBase(name);document.body.dataset.page=pageInfo[name]?name:'jobs';};
const focusedWorkspaceBase=renderWorkspaceState;
renderWorkspaceState=function(){focusedWorkspaceBase();document.body.classList.toggle('profile-is-ready',profileReady);const b=document.getElementById('focusedNextAction');b.textContent=profileReady?'Open outreach →':'Open profile →';b.onclick=()=>switchTab(profileReady?'outreach':'settings');};
const focusedResetBase=resetPrivateWorkspace;
resetPrivateWorkspace=function(){focusedResetBase();for(const id of ['focusedDraftCount','focusedScheduledCount','focusedSentCount','focusedReplyCount'])document.getElementById(id).textContent='—';document.getElementById('focusedFollowupCount').textContent='Sign in to view your messages';for(const id of ['focusedResearch','focusedSending','focusedResume']){document.getElementById(id).textContent='Sign in required';document.getElementById(id).className='signal-chip';}document.body.classList.remove('profile-is-ready');};
document.querySelector('.outreach-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;const buttons=[...document.querySelectorAll('[data-outreach-view]')];let index=buttons.indexOf(document.activeElement);if(index<0)return;e.preventDefault();index=e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;openOutreachView(buttons[index].dataset.outreachView);buttons[index].focus();});
document.body.dataset.page=pageInfo[location.hash.slice(1)]?location.hash.slice(1):'jobs';
document.getElementById('queueStateFilter').value='draft';
openOutreachView('drafts');renderWorkspaceState();
if(privateConnectionReady()&&campaignOverview){renderFocusedOutreach(campaignOverview);renderFocusedCampaigns(campaignOverview);renderFocusedQueue();}
