'use strict';
// Poll only an owner-started running review while its project Memory view is visible.
function memoryText(title,value,parent){const details=element('details');details.append(element('summary',title),element('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));parent.append(details);return details;}
function renderMemoryReview(){
  clearTimeout(state.memoryTimer);
  $('memory-review').disabled=!state.project;
  for(const id of ['memory-current','memory-reviews','memory-versions'])$(id).replaceChildren();
  const snapshot=state.brief,project=state.project,selection=state.selection;
  if(!project||!snapshot)return;
  memoryText('Active brief',snapshot.brief,$('memory-current')).open=true;
  $('memory-current').append(element('small',`Conservative token upper bound ${snapshot.token_upper_bound}; target ${snapshot.target}. Authoritative docs remain retrievable below.`));
  for(const review of snapshot.review_records){
    const panel=element('article',undefined,'panel');
    panel.append(element('h3',`Review · ${review.status}`),element('p',review.progress));
    panel.append(element('p',review.usage?`Actual reported usage: ${JSON.stringify(review.usage.turn||review.usage)}`:'Actual usage unavailable; no estimate.'));
    if(review.error)panel.append(element('p',review.error));
    if(review.sources.length)memoryText('Source references',review.sources,panel);
    if(review.status==='running'){
      const cancel=element('button','Cancel review');cancel.onclick=action(async()=>{if(selection!==state.selection)return;await api(`/api/projects/${project}/brief/cancel`,{method:'POST',body:JSON.stringify({review:review.reference})});await loadMemory(selection);});panel.append(cancel);
    }
    if(review.candidate){
      memoryText('Candidate brief',review.candidate,panel);
      memoryText('Changed facts · old/new comparison',review.diff||'No changes',panel).open=true;
      if(review.status==='candidate'){
        const resolutions=[];
        for(const [index,mismatch] of review.mismatches.entries()){
          const label=element('label',mismatch),input=element('textarea'),choice=element('select');input.rows=2;input.placeholder='Owner resolution (required before Apply)';
          if(review.protected_changes?.[index]){for(const [value,text] of [['keep','Keep existing requirement'],['accept','Accept proposed change']]){const option=element('option',text);option.value=value;choice.append(option);}}
          else{const option=element('option','Confirm conflict resolved with candidate as shown');option.value='accept';choice.append(option);}
          label.append(choice,input);panel.append(label);resolutions.push({input,choice});
        }
        const apply=element('button','Apply candidate','primary');apply.disabled=review.mismatches.length>0||review.base!==snapshot.active;if(review.base!==snapshot.active)panel.append(element('p','Candidate is stale because active memory changed. Start a new review when appropriate.'));
        for(const {input} of resolutions)input.oninput=()=>apply.disabled=review.base!==snapshot.active||resolutions.some(node=>!node.input.value.trim());
        apply.onclick=action(async()=>{if(selection!==state.selection)throw new Error('Project changed; reopen Memory');await api(`/api/projects/${project}/brief/apply`,{method:'POST',body:JSON.stringify({review:review.reference,revision:snapshot.revision,resolutions:resolutions.map(node=>({action:node.choice.value,reason:node.input.value}))})});await loadMemory(selection);});panel.append(apply);
      }
    }
    $('memory-reviews').append(panel);
  }
  const versions=element('details');versions.append(element('summary','Previous versions · inspect or roll back'));
  for(const version of [...snapshot.versions].reverse()){
    const row=element('div',undefined,'task-controls'),inspect=element('button',version===snapshot.active?'Inspect active version':'Inspect previous version');
    inspect.onclick=action(async()=>{const value=await api(`/api/projects/${project}/brief/version?reference=${encodeURIComponent(version)}`);if(selection!==state.selection)return;memoryText(version,value,row).open=true;});row.append(inspect);
    if(version!==snapshot.active){const rollback=element('button','Roll back');rollback.onclick=action(async()=>{if(selection!==state.selection)return;if(!confirm('Activate this prior brief? Current version and all evidence remain retrievable.'))return;await api(`/api/projects/${project}/brief/rollback`,{method:'POST',body:JSON.stringify({version,revision:snapshot.revision})});await loadMemory(selection);});row.append(rollback);}
    versions.append(row);
  }
  $('memory-versions').append(versions);
  if(state.view==='memory'&&snapshot.review_records.some(review=>review.status==='running'))state.memoryTimer=setTimeout(()=>{if(selection===state.selection&&state.view==='memory'&&!document.hidden)loadMemory(selection).catch(error=>notice(error.message));},2000);
}
$('memory-review').onclick=action(async()=>{
  const project=state.project,selection=state.selection;
  if(!project)return;
  if(!confirm(`Start full review for ${projectName(project)}? It will inspect project code, descriptions, scoped state and selected historical evidence, using time and Codex allowance. Cost is unknown. Active memory stays unchanged until Apply.`))return;
  await api(`/api/projects/${project}/brief/review`,{method:'POST',body:JSON.stringify({confirmed:true})});
  if(selection===state.selection)await loadMemory(selection);
});
