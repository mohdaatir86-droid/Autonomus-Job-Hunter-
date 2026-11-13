/* Provider choice never changes sending policy or falls back to a paid lookup. */
async function searchContactProvider(button){
  if(button.disabled)return;
  const provider=document.getElementById('outreachProvider').value;
  if(provider==='apollo'){await searchApollo();return;}
  if(!confirm('Search Prospeo for hiring contacts? Up to 1 credit for this results page, before revealing emails. Cached searches cost no additional lookup.'))return;
  button.disabled=true;
  const results=document.getElementById('apolloResults');
  results.textContent='Searching Prospeo hiring contacts…';
  try{
    const r=await privateApi('/api/outreach/prospeo/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({company_domain:document.getElementById('outreachDomain').value.trim(),confirm_credit_use:true})});
    results.innerHTML='<p class="profile-note">'+esc((r.cached?'Cached search from '+r.searched_at+'. ':'')+r.note)+'</p>'+(r.items||[]).map(p=>'<div class="conn-card"><div><strong>'+esc(p.name)+'</strong><div>'+esc(p.title)+' · '+esc(p.company)+'</div><div class="profile-note">Source: Prospeo · employer and hiring responsibility need review</div></div><button class="btn btn-ghost btn-sm" data-person-id="'+esc(p.person_id)+'" onclick="revealProspeoEmail(this.dataset.personId,this)">Reveal verified work email — up to 1 credit</button></div>').join('');
    if(!(r.items||[]).length)results.innerHTML+='<p>No matching current hiring contacts returned. No email address was guessed.</p>';
  }catch(e){results.textContent=e.message;}finally{button.disabled=false;}
}
async function revealProspeoEmail(id,button){
  if(button.disabled)return;
  if(!confirm('Reveal this shortlisted work email using up to 1 Prospeo credit? Phone numbers are disabled. Current employer and hiring responsibility still need review.'))return;
  button.disabled=true;
  try{
    await privateApi('/api/outreach/prospeo/enrich',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({person_id:id,confirm_credit_use:true})});
    toast('Contact saved from Prospeo. Verify current hiring responsibility before preparing an approved email.','success');
    await loadOutreach();
  }catch(e){toast(e.message,'error');}finally{button.disabled=false;}
}
