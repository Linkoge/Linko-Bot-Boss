'use strict';
// Harness picker (☼, left of the chat menu): the chat scope's manager harness and autonomy, and per project the
// default worker harness. Shown = saved = used: every change is saved at once and the next manager turn uses it.
// Main only: an Environment picker on top lists Main plus every registered instance (the deploy registry). For another
// instance the panel shows the same controls (manager harness, autonomy, default worker harness) and saves them in that
// instance's own database. Models are chosen only in the model picker (⚙).
// Access switch (another instance only, never Main): "Disable access" asks for confirmation, then the instance answers
// every request with only "Error 69" and everyone is signed out; "Enable access" works at once (ai_manager/access.py).
const harnessMenu={project:undefined,env:'',envs:null,data:null,access:null,error:'',saving:false,loading:0};
function harnessEnvQuery(){return 'name='+encodeURIComponent(harnessMenu.env);}
function renderHarnessEnvironments(){
  const group=$('harness-environment-group'),select=$('harness-environment');
  group.hidden=!!state.restricted||!(harnessMenu.envs?.length>1);if(group.hidden)return;
  select.replaceChildren();
  for(const env of harnessMenu.envs){const option=element('option',env.name);option.value=env.main?'':env.name;select.append(option);}
  select.value=harnessMenu.env;
}
async function loadHarnessEnvironments(){
  if(state.restricted)return;
  try{const result=await api('/api/environments');harnessMenu.envs=result.environments;if(result.error)$('harness-status').textContent=result.error;}
  catch{harnessMenu.envs=[{name:'Main',main:true}];}
  if(!harnessMenu.envs.some(env=>(env.main?'':env.name)===harnessMenu.env))harnessMenu.env='';
  renderHarnessEnvironments();
}
function harnessQuery(project){const query=new URLSearchParams();if(project)query.set('project_id',project);return query;}
function harnessControls(){return [...$('harness-menu').querySelectorAll('select:not(:disabled),button:not(:disabled)')].filter(e=>e.getClientRects().length);}
function harnessOptions(select,items,current,label=item=>item.name){
  select.replaceChildren();
  for(const item of items){const option=element('option',label(item));option.value=item.id;if(item.available===false){option.disabled=true;}select.append(option);}
  if(current&&!items.some(item=>item.id===current.id)){const option=element('option',`${current.name||current.id} (unavailable)`);option.value=current.id;option.disabled=true;select.append(option);}
  select.value=current?.id||'';
}
function renderHarnessAccess(){
  const group=$('harness-access'),button=$('harness-access-toggle'),env=harnessMenu.env;
  group.hidden=!!state.restricted||!env;if(group.hidden)return;
  const disabled=!!harnessMenu.access?.disabled;
  button.textContent=disabled?'Enable access':'Disable access';button.classList.toggle('danger',!disabled);
  button.disabled=!!harnessMenu.error||!harnessMenu.access||harnessMenu.saving;
  $('harness-access-state').textContent=harnessMenu.error?`${env} is unreachable; access cannot be changed.`
    :!harnessMenu.access?'':disabled?`Access to ${env} is disabled: every request gets Error 69.`:`Access to ${env} is enabled.`;
}
async function setHarnessAccess(env,disabled){
  if(env!==harnessMenu.env)return;
  harnessMenu.saving=true;renderHarnessAccess();$('harness-status').textContent=`Saving ${env}…`;
  try{
    const result=await api('/api/instance-access?'+harnessEnvQuery(),{method:'PUT',body:JSON.stringify({disabled})});
    if(env!==harnessMenu.env)return;
    harnessMenu.error='';harnessMenu.data=result.harness;harnessMenu.access=result.access;
    $('harness-status').textContent=disabled?`Access to ${env} disabled; everyone was signed out.`:`Access to ${env} enabled.`;
  }catch(error){$('harness-status').textContent=`Not saved for ${env}: ${error.message}`;throw error;}
  finally{harnessMenu.saving=false;if(env===harnessMenu.env)renderHarnessMenu();}
}
function confirmDisableAccess(env){
  const dialog=$('access-confirm');if(dialog.open)return;
  $('access-confirm-title').textContent=`Disable access to ${env}? Everyone signed in will be signed out.`;
  $('access-confirm-error').textContent='';$('access-confirm-submit').disabled=false;
  $('access-confirm-cancel').onclick=()=>dialog.close();
  $('access-confirm-form').onsubmit=async event=>{
    event.preventDefault();$('access-confirm-submit').disabled=true;
    try{await setHarnessAccess(env,true);dialog.close();}
    catch(error){$('access-confirm-error').textContent=error.message;$('access-confirm-submit').disabled=false;}
  };
  dialog.showModal();
}
function renderHarnessMenu(){
  renderHarnessEnvironments();renderHarnessAccess();
  const error=harnessMenu.env&&harnessMenu.error;
  for(const id of ['harness-manager','harness-autonomy'])$(id).disabled=!!error;
  if(error){$('harness-scope').textContent=`${harnessMenu.env} is unreachable`;$('harness-worker-group').hidden=true;$('harness-invalid').hidden=true;
    for(const id of ['harness-manager','harness-autonomy'])$(id).replaceChildren();$('harness-manager-description').textContent=error;$('harness-autonomy-description').textContent='';return;}
  const data=harnessMenu.data;if(!data)return;
  const manager=data.manager,worker=data.worker,levels=data.autonomy_levels;
  $('harness-scope').textContent=harnessMenu.env?`${harnessMenu.env} · applies from its next manager turn`:`${projectName(data.project_id)} · applies from the next manager turn`;
  harnessOptions($('harness-manager'),data.options.manager,manager);
  $('harness-manager-description').textContent=manager.error||manager.description||'';
  const autonomy=levels.find(level=>level.id===data.autonomy);
  harnessOptions($('harness-autonomy'),levels,autonomy||null,level=>level.available?level.label:`${level.label} (not yet available)`);
  $('harness-autonomy').disabled=!!manager.error;
  $('harness-autonomy-description').textContent=autonomy?`${autonomy.description}${data.autonomy===manager.autonomy?' Harness default.':''}`:'';
  const showWorker=!!worker&&(!!data.project_id||!!harnessMenu.env);
  $('harness-worker-group').hidden=!showWorker;
  if(showWorker){harnessOptions($('harness-worker'),data.options.worker,worker);$('harness-worker-description').textContent=worker.error||worker.description||'';}
  const invalid=$('harness-invalid');invalid.replaceChildren();invalid.hidden=!data.invalid.length;
  if(data.invalid.length){
    invalid.append(element('p',`Invalid harnesses in ${data.directory} (not used):`,'harness-invalid-title'));
    const list=element('ul');for(const item of data.invalid)list.append(element('li',`${item.folder}: ${item.error}`));invalid.append(list);
  }
}
async function loadHarnessMenu(){
  const project=state.project,env=harnessMenu.env,load=++harnessMenu.loading;
  $('harness-status').textContent='Loading…';
  try{
    if(env){
      let result;
      try{result=await api('/api/instance-harness?'+harnessEnvQuery());}
      catch(error){if(load!==harnessMenu.loading)return;harnessMenu.error=error.message;harnessMenu.data=null;harnessMenu.access=null;harnessMenu.project=project;renderHarnessMenu();$('harness-status').textContent=`${env} is unreachable.`;return;}
      if(load!==harnessMenu.loading||env!==harnessMenu.env)return;
      harnessMenu.error='';harnessMenu.data=result.harness;harnessMenu.access=result.access;
    }else{
      const data=await api(`/api/harnesses?${harnessQuery(project)}`);
      if(load!==harnessMenu.loading||project!==state.project||env!==harnessMenu.env)return;
      harnessMenu.error='';harnessMenu.data=data;
    }
    harnessMenu.project=project;renderHarnessMenu();$('harness-status').textContent='';
  }catch(error){if(load===harnessMenu.loading)$('harness-status').textContent='Cannot load harnesses: '+error.message;}
}
// Changes save one after another in the order made; none is dropped while an earlier one is in flight.
let harnessSaves=Promise.resolve();
function saveHarness(change){harnessSaves=harnessSaves.then(()=>saveHarnessNow(change));return harnessSaves;}
async function saveHarnessNow(change){
  if(harnessMenu.env)return saveInstanceHarness(harnessMenu.env,change);
  const project=harnessMenu.project,selection=state.selection;
  if(project!==state.project)return;
  harnessMenu.saving=true;$('harness-status').textContent='Saving…';
  try{
    const result=await api(`/api/harnesses?${harnessQuery(project)}`,{method:'PUT',body:JSON.stringify(change)});
    if(project!==state.project)return;
    harnessMenu.data=result;renderHarnessMenu();
    $('harness-status').textContent=result.notice||'Saved.';
    if(result.notice&&selection===state.selection&&!state.source)loadHistory(selection).catch(()=>{});
  }catch(error){renderHarnessMenu();$('harness-status').textContent='Not saved: '+error.message;}
  finally{harnessMenu.saving=false;}
}
async function saveInstanceHarness(env,change){
  if(env!==harnessMenu.env)return;
  harnessMenu.saving=true;$('harness-status').textContent=`Saving ${env}…`;
  try{
    const result=await api('/api/instance-harness?'+harnessEnvQuery(),{method:'PUT',body:JSON.stringify(change)});
    if(env!==harnessMenu.env)return;
    harnessMenu.error='';harnessMenu.data=result.harness;harnessMenu.access=result.access;renderHarnessMenu();
    $('harness-status').textContent=`Saved for ${env}; it applies from its next manager turn.`;
  }catch(error){renderHarnessMenu();$('harness-status').textContent=`Not saved for ${env}: ${error.message}`;loadHarnessMenu();}
  finally{harnessMenu.saving=false;}
}
function closeHarnessMenu(focus=false){
  const menu=$('harness-menu');if(menu.hidden){if(focus)$('harness-button').focus({preventScroll:true});return;}
  menu.closest('.navigation')?.classList.remove('harness-menu-open');menu.hidden=true;
  $('harness-button').setAttribute('aria-expanded','false');if(focus)$('harness-button').focus({preventScroll:true});
}
async function openHarnessMenu(last=false){
  closeChatMenu();closeProjectMenu();
  const menu=$('harness-menu');menu.hidden=false;menu.closest('.navigation')?.classList.add('harness-menu-open');
  $('harness-button').setAttribute('aria-expanded','true');
  if(harnessMenu.project!==state.project&&!harnessMenu.env){harnessMenu.data=null;$('harness-manager').replaceChildren();}
  else renderHarnessMenu();
  const focusFirst=()=>{const controls=harnessControls();controls[last?controls.length-1:0]?.focus({preventScroll:true});};
  focusFirst();
  await loadHarnessEnvironments();
  await loadHarnessMenu();
  if(!menu.hidden&&!menu.contains(document.activeElement))focusFirst();
}
$('harness-button').onclick=()=>{$('harness-menu').hidden?openHarnessMenu():closeHarnessMenu(true);};
$('harness-button').onkeydown=event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();openHarnessMenu(event.key==='ArrowUp');}};
$('harness-manager').onchange=()=>saveHarness({manager:$('harness-manager').value});
$('harness-autonomy').onchange=()=>saveHarness({autonomy:$('harness-autonomy').value});
$('harness-worker').onchange=()=>saveHarness({worker:$('harness-worker').value});
$('harness-environment').onchange=()=>{harnessMenu.env=$('harness-environment').value;harnessMenu.data=null;harnessMenu.access=null;harnessMenu.error='';$('harness-manager').replaceChildren();loadHarnessMenu();};
$('harness-access-toggle').onclick=()=>{
  const env=harnessMenu.env;if(!env||harnessMenu.error||!harnessMenu.access)return;
  if(harnessMenu.access.disabled)setHarnessAccess(env,false).catch(()=>{});else confirmDisableAccess(env);
};
$('harness-menu').onkeydown=event=>{
  const controls=harnessControls(),index=controls.indexOf(document.activeElement);
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeHarnessMenu(true);}
  if(event.key==='Tab'&&controls.length){
    if(event.shiftKey&&index<=0){event.preventDefault();controls.at(-1).focus();}
    else if(!event.shiftKey&&index===controls.length-1){event.preventDefault();controls[0].focus();}
  }
};
$('harness-menu').addEventListener('focusout',event=>{if(event.relatedTarget&&!$('harness-menu').contains(event.relatedTarget)&&event.relatedTarget!==$('harness-button')&&!event.relatedTarget.closest('#access-confirm'))closeHarnessMenu();});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#harness-menu')&&!event.target.closest('#harness-button')&&!event.target.closest('#access-confirm'))closeHarnessMenu();});
