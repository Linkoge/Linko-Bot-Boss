'use strict';
const $ = id => document.getElementById(id);
const state = {csrf:null, project:null, portfolio:null, detail:null, source:null, execution:null, view:'chat', setup:false, selection:0, drafts:new Map(), memorySelection:0, pending:new Set()};
const element = (tag, text, cls) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
async function api(path, options={}){
  const headers={'Content-Type':'application/json',...(options.headers||{})};
  if(options.method && options.method!=='GET')headers['X-CSRF-Token']=state.csrf||'';
  const response=await fetch(path,{...options,headers});
  const result=await response.json();
  if(!response.ok){if(response.status===401 && !path.includes('login'))showAuth();throw new Error(result.detail||'Request failed');}
  return result;
}
function action(fn){return async event=>{const button=event?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(event);}catch(error){notice('Rejected or failed: '+error.message);}finally{if(button?.tagName==='BUTTON')button.disabled=false;}};}
function actionResult(result){notice(`${result.result||'accepted'}: ${result.message||result.status||'Operation recorded'}${result.execution_id?' · Execution '+result.execution_id.slice(0,8):''}`);}
function showAuth(){state.source?.close();$('app').hidden=true;$('auth-screen').hidden=false;}
async function boot(){
  const auth=await api('/api/auth-status');state.csrf=auth.csrf;state.setup=auth.setup_required;
  $('setup-label').hidden=!state.setup;$('setup-token').required=state.setup;
  $('auth-title').textContent=state.setup?'Set up your workspace':'Welcome back';
  $('auth-description').textContent=state.setup?'Create your private owner account.':'One conversation. Every project in view.';
  $('login-button').textContent=state.setup?'Create owner account':'Sign in';
  if(auth.authenticated)await enter();else showAuth();
}
async function enter(){
  $('auth-screen').hidden=true;$('app').hidden=false;notice('');
  const settings=await api('/api/settings');$('global-instructions').value=settings.global_instructions;$('scheduler-status').textContent=settings.scheduler_enabled?'Scheduler enabled':'Scheduler paused';
  await refresh();await selectProject(state.project);
}
async function refresh(){
  state.portfolio=await api('/api/portfolio');const scheduler=state.portfolio.scheduler;$('scheduler-status').textContent=scheduler?`${scheduler.enabled?'Enabled':'Paused'} · loop ${scheduler.running?'running':'stopped'} · scope ${projectName(scheduler.scope)} · maximum ${scheduler.max_workers} workers${scheduler.error?' · Error: '+scheduler.error:''}`:'Scheduler status unavailable';$('execution-status').textContent=$('scheduler-status').textContent;renderProjects();renderDashboard();renderTasks();renderAgents();renderApprovals();renderNotifications();
}
const displayName = p => p.display_name || p.name;
function projectLabel(p){
  const duplicates=state.portfolio.projects.filter(other=>displayName(other).toLocaleLowerCase()===displayName(p).toLocaleLowerCase());
  return displayName(p)+(duplicates.length>1?` · ${p.name} (${p.id.slice(0,6)})`:'');
}
function renderProjects(){
  const scroll=$('project-tabs').scrollLeft,focused=document.activeElement.closest('#projects button')?.dataset.project;
  $('projects').replaceChildren();
  for(const p of [...state.portfolio.projects].sort((a,b)=>(b.last_used_at||0)-(a.last_used_at||0)||displayName(a).localeCompare(displayName(b),undefined,{sensitivity:'base'})||a.id.localeCompare(b.id))){
    const b=element('button',projectLabel(p),'project-button'+(state.project===p.id?' selected':''));
    b.title=p.path;b.dataset.project=p.id;b.setAttribute('aria-pressed',String(state.project===p.id));
    b.onclick=action(()=>selectProject(p.id));$('projects').append(b);
  }
  if(focused)$('projects').querySelector(`[data-project="${focused}"]`)?.focus({preventScroll:true});
  $('project-tabs').scrollLeft=scroll;
  $('all-projects').classList.toggle('selected',!state.project);$('all-projects').setAttribute('aria-pressed',String(!state.project));
  if(!$('project-menu').hidden)renderProjectMenu();
  $('context-hint').textContent=state.project?`${projectName(state.project)} in context`:'All projects in context';
}
function closeProjectMenu(focus=false){$('project-menu').hidden=true;$('project-menu-button').setAttribute('aria-expanded','false');if(focus)$('project-menu-button').focus();}
function renderProjectMenu(){
  const menu=$('project-menu'),focused=menu.contains(document.activeElement)?document.activeElement.dataset.project:undefined;menu.replaceChildren();
  for(const p of [null,...[...state.portfolio.projects].sort((a,b)=>displayName(a).localeCompare(displayName(b),undefined,{sensitivity:'base'})||a.id.localeCompare(b.id))]){
    const selected=state.project===(p?.id||null),button=element('button',p?projectLabel(p):'◈ All Projects · global context',selected?'selected':'');
    button.setAttribute('role','menuitemradio');button.setAttribute('aria-checked',String(selected));button.dataset.project=p?.id||'';
    button.onclick=action(async()=>{closeProjectMenu(true);await selectProject(p?.id||null);});menu.append(button);
  }
  if(state.project){const rename=element('button','Rename selected project…');rename.setAttribute('role','menuitem');rename.dataset.project='rename';rename.onclick=()=>{closeProjectMenu();renameProject();};menu.append(rename);}
  if(focused!==undefined)menu.querySelector(`[data-project="${focused}"]`)?.focus({preventScroll:true});
}
$('project-menu-button').onclick=()=>{if(!$('project-menu').hidden){closeProjectMenu(true);return;}renderProjectMenu();$('project-menu').hidden=false;$('project-menu-button').setAttribute('aria-expanded','true');$('project-menu').querySelector('[aria-checked="true"]').focus();};
$('project-menu').onkeydown=event=>{
  const buttons=[...$('project-menu').querySelectorAll('button')],index=buttons.indexOf(document.activeElement);
  if(event.key==='Escape'){event.preventDefault();closeProjectMenu(true);}
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length].focus();}
  if(event.key==='Tab')closeProjectMenu();
};
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#project-menu')&&!event.target.closest('#project-menu-button'))closeProjectMenu();});
async function selectProject(id){
  state.drafts.set(state.project,$('message-input').value);
  const selection=++state.selection;state.project=id;state.detail=null;state.source?.close();state.source=null;state.execution=null;
  $('messages').replaceChildren();$('message-input').value=state.drafts.get(id)||'';
  $('stop-chat').hidden=true;$('send').disabled=state.pending.has(id);renderProjects();
  (id?$('projects').querySelector(`[data-project="${id}"]`):$('all-projects'))?.scrollIntoView({block:'nearest',inline:'nearest'});
  clearTimeout(searchTimer);$('history-search').value='';
  renderDashboard();renderTasks();renderAgents();renderMemory();
  if(state.view==='memory')await loadMemory(selection);
  await loadHistory(selection);
}
async function loadMemory(selection=state.selection){
  if(!state.project||state.detail)return;
  const detail=await api(`/api/projects/${state.project}`);
  if(selection!==state.selection)return;
  state.detail=detail;renderMemory();
}
function renameProject(){
  const p=state.portfolio.projects.find(p=>p.id===state.project);
  if(!p){notice('Select a project to rename.');return;}
  editor('Rename project',[{name:'display_name',label:`Display name (original: ${p.name}; blank restores it)`,value:p.display_name||''}],values=>api(`/api/projects/${p.id}`,{method:'PATCH',body:JSON.stringify({...values,revision:p.revision})}));
}
$('rename-project').onclick=renameProject;
function filteredTasks(){return state.portfolio.tasks.filter(t=>!state.project||t.project_id===state.project);}
function filteredExecutions(){return state.portfolio.executions.filter(e=>!state.project||e.project_id===state.project);}
function projectName(id){const p=state.portfolio.projects.find(p=>p.id===id);return p?displayName(p):'All Projects';}
function renderDashboard(){
  const projects=state.portfolio.projects.filter(p=>!state.project||p.id===state.project),tasks=filteredTasks(),executions=filteredExecutions();
  $('stats').replaceChildren();
  const values=[[projects.length,'Projects in view'],[executions.filter(e=>!e.finished_at).length,'Active responses / workers'],[tasks.filter(t=>t.status==='pending').length,'Pending tasks'],[tasks.filter(t=>['blocked','awaiting_approval'].includes(t.status)).length,'Need your attention']];
  for(const [value,label] of values){const card=element('article',undefined,'stat');card.append(element('strong',String(value)),element('span',label));$('stats').append(card);}
  $('project-cards').replaceChildren();
  for(const p of projects){
    const card=element('article',undefined,'project-card');card.append(element('h3',displayName(p)),element('p',p.objective||p.description||'Set an objective to give this project a clear direction.'));
    const footer=element('div',undefined,'card-footer');footer.append(element('span',p.status,'badge'),element('span',`Priority ${p.priority}`));
    const controls=element('div');const open=element('button','Open');open.onclick=action(()=>selectProject(p.id));const edit=element('button','Edit');edit.onclick=()=>editProject(p);controls.append(open,edit);card.append(footer,controls);$('project-cards').append(card);
  }
  $('recommendation').textContent=state.portfolio.storage?.low_space?`Storage needs attention: ${Math.round(state.portfolio.storage.free_bytes/1024/1024)} MB free. Workspace and backup guards may block work until storage is expanded or freed.`:state.portfolio.recommendation;
  $('accomplishments').replaceChildren();
  const checkpoints=state.portfolio.checkpoints.filter(c=>!state.project||c.project_id===state.project);
  if(!checkpoints.length)$('accomplishments').append(element('p','Verified checkpoints will appear here.','empty'));
  for(const c of checkpoints)$('accomplishments').append(element('p',`${projectName(c.project_id)} — ${c.summary}`));
}
function renderTasks(){
  const tasks=filteredTasks();$('task-count').textContent=tasks.filter(t=>!['completed','cancelled'].includes(t.status)).length;$('task-list').replaceChildren();
  if(!tasks.length)$('task-list').append(element('p','No queued tasks. Add a concrete goal and acceptance criteria.','empty'));
  for(const t of tasks){const row=element('article',undefined,'task-row');row.append(element('h3',t.description));const meta=element('div',undefined,'row-meta');meta.append(element('span',t.status,`badge ${t.status}`),element('span',projectName(t.project_id)),element('span',`Priority ${t.priority}`),element('span',`#${t.id.slice(0,8)}`));row.append(meta,element('p',`Acceptance: ${t.acceptance}`));if(t.blocker)row.append(element('p',t.blocker));if(t.readiness?.reason&&!t.blocker)row.append(element('p',t.readiness.reason));if(!t.readiness?.verification_configured)row.append(element('small','Trusted verification is not configured; completion will be blocked.'));
    if(t.readiness?.verification_only&&t.status==='blocked'){const verify=element('button','Retry verification');verify.onclick=action(async()=>{const result=await api(`/api/tasks/${t.id}/verify`,{method:'POST',body:JSON.stringify({revision:t.revision})});await refresh();actionResult(result);});row.append(verify);const configure=element('button','Configure verification');configure.onclick=action(async()=>{await selectProject(t.project_id);await configureVerification(t.project_id);});row.append(configure);}
    if(t.status==='pending'){const start=element('button','Start worker');start.onclick=action(async()=>{notice('Requesting worker launch…');const result=await api(`/api/tasks/${t.id}/start`,{method:'POST'});await refresh();actionResult(result);});row.append(start);}
    if(['paused','blocked','failed'].includes(t.status)&&t.readiness?.can_resume){const resume=element('button','Resume persisted worker');resume.onclick=action(async()=>{if(t.status==='blocked'){editor('Reconcile uncertain execution',[{name:'reconciliation',label:'Inspect saved thread, workspace and operation records first. Describe evidence establishing what ran and why continuing is safe.',long:true,required:true}],async values=>{const result=await api(`/api/tasks/${t.id}/resume`,{method:'POST',body:JSON.stringify({...values,revision:t.revision,reconciled:true})});actionResult(result);});return;}const result=await api(`/api/tasks/${t.id}/resume`,{method:'POST',body:JSON.stringify({revision:t.revision})});await refresh();actionResult(result);});row.append(resume);}
    if(['paused','blocked','failed'].includes(t.status)&&t.readiness?.can_retry){const retry=element('button','Retry launch');retry.onclick=action(async()=>{const result=await api(`/api/tasks/${t.id}/retry`,{method:'POST',body:JSON.stringify({revision:t.revision})});await refresh();actionResult(result);});row.append(retry);}
    if(['pending','paused','blocked','failed'].includes(t.status)){const cancel=element('button','Cancel task');cancel.onclick=action(async()=>{const result=await api(`/api/tasks/${t.id}`,{method:'PATCH',body:JSON.stringify({revision:t.revision,status:'cancelled'})});await refresh();actionResult(result);});row.append(cancel);}
    const inspect=element('button','Inspect execution records');inspect.onclick=action(async()=>{const records=await api(`/api/tasks/${t.id}/execution-records`);editor('Persisted task and execution records',[{name:'records',label:'Review task, thread, turn, workspace, operations, approvals and verification evidence',long:true,rows:14,value:JSON.stringify(records,null,2)}],async()=>{});});row.append(inspect);$('task-list').append(row);}
}
function renderAgents(){
  $('agent-list').replaceChildren();const executions=filteredExecutions();
  if(!executions.length)$('agent-list').append(element('p','No execution history yet. Start a conversation to connect Codex.','empty'));
  for(const e of executions){const row=element('article',undefined,'agent-row');row.append(element('h3',`${e.agent_id} · ${projectName(e.project_id)}`));const meta=element('div',undefined,'row-meta');meta.append(element('span',e.status,`badge ${e.status}`),element('span',`${e.model||'Unassigned'} / ${e.effort||'default'}`),element('span',new Date(e.started_at*1000).toLocaleString()),element('span',e.finished_at?`${Math.round((e.finished_at-e.started_at))} seconds`:'Running'));row.append(meta);
    const details=element('details');details.append(element('summary','Output and execution details'),element('pre',e.output||'Waiting for output…'),element('small',`Thread: ${e.thread_id||'pending'} · Turn: ${e.turn_id||'pending'}`));row.append(details);if(e.error)row.append(element('p','Execution error: '+e.error));row.append(element('small',`Execution ${e.id} · PID ${e.pid||'launch pending'} · process start ${e.process_start||'pending'}`));
    if(e.worktree){row.append(element('small',`Workspace: ${e.worktree} · Branch: ${e.branch||'pending'}`));const task=state.portfolio.tasks.find(t=>t.id===e.task_id);if(task?.status==='completed'){const download=element('a','Download verified patch');download.href=`/api/executions/${e.id}/patch`;download.className='result-link';row.append(download);const evidence=element('button','Verification evidence');evidence.onclick=action(async()=>{const records=await api(`/api/tasks/${e.task_id}/evidence`);editor('Verification evidence',[{name:'evidence',label:'Recorded independent checks',long:true,rows:12,value:records.map(r=>r.evidence).join('\n\n')}],async()=>{});});row.append(evidence);}}
    if(!e.finished_at){const stop=element('button','Interrupt');stop.onclick=action(async()=>{await api(`/api/executions/${e.id}/interrupt`,{method:'POST'});notice('Interruption requested. Waiting for confirmation.');});row.append(stop);}$('agent-list').append(row);
  }
}
function renderMemory(){
  $('memory-list').replaceChildren();
  if(!state.detail){$('memory-list').append(element('p','Select a project to read or edit its persistent memory.','empty'));return;}
  for(const m of state.detail.memory){const panel=element('article',undefined,'panel');const details=element('details');details.open=m.name==='STATE.md';details.append(element('summary',`${m.name} · revision ${m.revision}`),element('pre',m.content));const edit=element('button','Edit memory');edit.onclick=()=>editMemory(m);panel.append(details,edit);$('memory-list').append(panel);}
  const observation=element('article',undefined,'panel');observation.append(element('h2','Current Git observation'),element('pre',(state.detail.observation.commits||[]).join('\n')||'No Git commits observed.'));$('memory-list').append(observation);
}
async function loadHistory(selection=state.selection){
  const query=new URLSearchParams();if(state.project)query.set('project_id',state.project);if($('history-search').value)query.set('search',$('history-search').value);
  const history=await api(`/api/history?${query}`);if(selection!==state.selection)return;
  $('messages').replaceChildren();
  if(!history.messages.length){const welcome=element('div',undefined,'welcome');welcome.append(element('div','Ai','mark'),element('h2','What should we move forward?'),element('p','Talk through a goal, review project state, or plan the next useful step. Your context stays with each project.'));const suggestions=element('div',undefined,'suggestions');for(const text of ['What needs my attention?','Summarize current project state','Help me prioritize the next tasks','Brainstorm a plan for this project']){const b=element('button',text);b.onclick=()=>{$('message-input').value=text;$('message-input').focus();};suggestions.append(b);}welcome.append(suggestions);$('messages').append(welcome);}
  for(const m of history.messages){
    const row=element('article',undefined,`message ${m.role}`),body=element('div'),label=element('div',undefined,'message-label');label.append(element('strong',m.role==='user'?'You':'AI Manager'),element('span',m.status,`badge ${m.status}`));body.append(label,element('div',m.content||'Connecting to Codex…','message-content'));row.append(element('div',m.role==='user'?'YOU':'Ai','avatar'),body);row.dataset.message=m.id;$('messages').append(row);
    if(m.role==='assistant'&&['queued','streaming'].includes(m.status))attachStream(m.execution_id,m.id,m.stream_cursor);
  }
  $('messages').scrollTop=$('messages').scrollHeight;
}
function attachStream(execution,messageId,cursor=0){
  state.source?.close();state.execution=execution;$('stop-chat').hidden=false;$('send').disabled=true;$('status-label').textContent='Thinking / responding';
  const source=new EventSource(`/api/executions/${execution}/events?after=${cursor}`);state.source=source;
  source.addEventListener('chat.delta',event=>{const data=JSON.parse(event.data);const content=document.querySelector(`[data-message="${messageId}"] .message-content`);if(content){if(content.textContent==='Connecting to Codex…')content.textContent='';content.textContent+=data.delta;}$('messages').scrollTop=$('messages').scrollHeight;});
  source.addEventListener('chat.finished',event=>{const data=JSON.parse(event.data);if(data.error)notice(data.error);});
  source.addEventListener('stream.end',action(async()=>{source.close();if(state.source!==source)return;state.source=null;state.execution=null;$('stop-chat').hidden=true;$('send').disabled=false;$('status-label').textContent='Ready to help';await loadHistory();await refresh();}));
  source.addEventListener('stream.auth_expired',()=>{source.close();showAuth();});
  source.onerror=()=>{if(state.source===source)$('status-label').textContent='Reconnecting to saved stream…';};
}
function showView(view){state.view=view;if(view==='memory')loadMemory().catch(error=>notice(error.message));document.querySelectorAll('.view').forEach(node=>node.hidden=node.id!==`view-${view}`);document.querySelectorAll('.tabs button').forEach(b=>(b.classList.toggle('active',b.dataset.view===view),b.setAttribute('aria-current',b.dataset.view===view?'page':'false')));}
function editor(title,fields,onSave){
  $('editor-title').textContent=title;$('editor-fields').replaceChildren();$('editor-error').textContent='';
  for(const f of fields){const label=element('label',f.label);let control;if(f.options){control=element('select');for(const [value,text] of f.options){const option=element('option',text);option.value=value;control.append(option);}}else{control=element(f.long?'textarea':'input');if(f.long)control.rows=f.rows||4;else control.type=f.type||'text';}control.name=f.name;control.value=f.value??'';control.required=!!f.required;if(f.min!==undefined)control.min=f.min;if(f.max!==undefined)control.max=f.max;label.append(control);$('editor-fields').append(label);}
  $('editor-form').onsubmit=async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));try{const result=await onSave(values);if(result)actionResult(result);$('editor').close();await refresh();await selectProject(state.project);}catch(error){$('editor-error').textContent=error.message;}};$('editor').showModal();
}
function editProject(p){editor('Project settings',[{name:'display_name',label:'Display name (blank uses original name)',value:p.display_name||''},{name:'objective',label:'Objective',value:p.objective,long:true},{name:'success_criteria',label:'Success criteria',value:p.success_criteria,long:true},{name:'instructions',label:'Project instructions',value:p.instructions,long:true},{name:'priority',label:'Priority',type:'number',value:p.priority,min:0,max:100},{name:'status',label:'Registry status (source files are preserved)',value:p.status,options:['active','paused','archived','removed'].map(x=>[x,x])}],async values=>{await api(`/api/projects/${p.id}`,{method:'PATCH',body:JSON.stringify({...values,priority:Number(values.priority),revision:p.revision})});if(values.status==='removed'&&state.project===p.id)state.project=null;});}
function editMemory(m){const project=state.project;editor(`Edit ${m.name}`,[{name:'content',label:'Human-readable project memory',value:m.content,long:true,rows:12}],values=>api(`/api/projects/${project}/memory/${m.name}`,{method:'PUT',body:JSON.stringify({...values,revision:m.revision})}));}
$('auth-form').onsubmit=async event=>{event.preventDefault();$('auth-error').textContent='';$('login-button').disabled=true;try{const credentials={username:$('username').value,password:$('password').value};if(state.setup){await api('/api/setup',{method:'POST',body:JSON.stringify({...credentials,token:$('setup-token').value})});state.setup=false;$('setup-token').value='';}const login=await api('/api/login',{method:'POST',body:JSON.stringify(credentials)});state.csrf=login.csrf;$('password').value='';await enter();}catch(error){$('auth-error').textContent=error.message;}finally{$('login-button').disabled=false;}};
$('chat-form').onsubmit=action(async event=>{
  event.preventDefault();const content=$('message-input').value.trim(),project=state.project,selection=state.selection;
  if(!content||$('send').disabled||state.pending.has(project))return;
  state.pending.add(project);$('send').disabled=true;
  try{
    await api('/api/chat',{method:'POST',body:JSON.stringify({project_id:project,content,mode:$('chat-mode').value})});
    state.drafts.set(project,'');
    if(selection===state.selection){$('message-input').value='';await loadHistory(selection);}
    await refresh();
  }finally{state.pending.delete(project);if(selection===state.selection&&!state.execution)$('send').disabled=false;}
});
$('message-input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send').disabled)$('chat-form').requestSubmit();}};
$('all-projects').onclick=action(()=>selectProject(null));$('discover').onclick=action(async()=>{await api('/api/projects/discover',{method:'POST'});await refresh();notice('Project discovery complete.');});
$('logout').onclick=action(async()=>{await api('/api/logout',{method:'POST'});state.csrf=null;showAuth();});
$('revoke-sessions').onclick=action(async()=>{await api('/api/sessions/revoke',{method:'POST'});showAuth();});
$('stop-chat').onclick=action(async()=>{if(state.execution)await api(`/api/executions/${state.execution}/interrupt`,{method:'POST'});notice('Interruption requested.');});
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>showView(b.dataset.view));
let searchTimer;$('history-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(action(()=>loadHistory()),250);};
$('editor-cancel').onclick=()=>$('editor').close();
$('add-project').onclick=()=>editor('Add an existing project',[{name:'path',label:'Directory inside a configured project root',required:true},{name:'name',label:'Name (optional)'}],values=>api('/api/projects',{method:'POST',body:JSON.stringify({...values,name:values.name||null})}));
$('add-task').onclick=()=>editor('Add a persistent task',[{name:'project_id',label:'Project',value:state.project,options:state.portfolio.projects.filter(p=>p.status!=='removed').map(p=>[p.id,projectLabel(p)]),required:true},{name:'description',label:'What should be done?',long:true,required:true},{name:'acceptance',label:'How will we know it succeeded?',long:true,required:true},{name:'verification',label:'Independent verification requirements',long:true},{name:'priority',label:'Priority',type:'number',value:50,min:0,max:100},{name:'dependencies',label:'Dependency task IDs, separated by commas'}],values=>api('/api/tasks',{method:'POST',body:JSON.stringify({...values,priority:Number(values.priority),dependencies:values.dependencies.split(',').map(s=>s.trim()).filter(Boolean)})}));
$('instructions-form').onsubmit=action(async event=>{event.preventDefault();await api('/api/settings/instructions',{method:'PUT',body:JSON.stringify({content:$('global-instructions').value})});notice('Global instructions saved.');});
$('load-models').onclick=action(async()=>{const catalog=await api('/api/models');$('model-routes').replaceChildren();for(const [role,route] of Object.entries(catalog.routes)){const row=element('form',undefined,'route-row'),model=element('select'),effort=element('select');for(const m of catalog.models){const option=element('option',m.displayName);option.value=m.model;model.append(option);}model.value=route.model;function efforts(){effort.replaceChildren();const m=catalog.models.find(m=>m.model===model.value);for(const e of m.supportedReasoningEfforts.filter(e=>e.reasoningEffort!=='ultra')){const option=element('option',e.reasoningEffort);option.value=e.reasoningEffort;effort.append(option);}effort.value=route.effort;}efforts();model.onchange=efforts;model.setAttribute('aria-label',`${role} model`);effort.setAttribute('aria-label',`${role} reasoning effort`);const save=element('button','Save','primary');row.append(element('label',role),model,effort,save);row.onsubmit=action(async event=>{event.preventDefault();await api(`/api/models/${role}`,{method:'PUT',body:JSON.stringify({model:model.value,effort:effort.value})});notice(`${role} model settings saved.`);});$('model-routes').append(row);}});
setInterval(()=>{if(!$('app').hidden && true)refresh().catch(error=>notice(error.message));},10000);
boot().catch(error=>notice(error.message));


$('plan-goal').onclick=()=>{if(!state.project){notice('Select a project to plan a goal.');return;}const project=state.project;editor('Plan a delegated goal',[{name:'goal',label:'What should the manager accomplish?',long:true,required:true}],values=>api('/api/goals',{method:'POST',body:JSON.stringify({...values,project_id:project,request_key:crypto.randomUUID()})}));};
$('configure-scheduler').onclick=()=>editor('Autonomous scheduling',[{name:'enabled',label:'Scheduler',value:state.portfolio.automation_enabled?'true':'false',options:[['true','Enabled'],['false','Paused']]},{name:'project_id',label:'Project scope',value:state.project||'',options:[['','All Projects'],...state.portfolio.projects.map(p=>[p.id,projectLabel(p)])]},{name:'limit',label:'Number of successful completions (blank means continuous)',type:'number',min:1,max:100},{name:'max_workers',label:'Maximum concurrent workers',type:'number',value:state.portfolio.scheduler?.max_workers||2,min:1,max:8}],values=>api('/api/scheduler',{method:'PUT',body:JSON.stringify({enabled:values.enabled==='true',project_id:values.project_id||null,limit:values.limit?Number(values.limit):null,max_workers:Number(values.max_workers)})}));
async function configureVerification(project){
  const settings=await api(`/api/projects/${project}/verification`);
  editor('Owner-approved verification commands',[{name:'commands',label:'Explicitly approve the project’s documented test commands as JSON argument arrays. Tests run with system runtimes and /workspace only, without host home, installed home environments or network. Saving does not replay workers or unblock tasks automatically.',value:JSON.stringify(settings.commands,null,2),long:true,rows:8}],values=>api(`/api/projects/${project}/verification`,{method:'PUT',body:JSON.stringify({commands:JSON.parse(values.commands)})}));
}
$('configure-verification').onclick=action(async()=>{if(!state.project){notice('Select a project first.');return;}await configureVerification(state.project);});

function renderApprovals(){
  const approvals=(state.portfolio.approvals||[]).filter(a=>!state.project||a.project_id===state.project);$('approval-count').textContent=approvals.length;$('approval-list').replaceChildren();
  if(!approvals.length)$('approval-list').append(element('p','Nothing waiting for your approval.','empty'));
  for(const a of approvals){const args=JSON.parse(a.arguments),card=element('article',undefined,'panel');card.append(element('h2',`${a.action.replaceAll('_',' ')} · ${projectName(a.project_id)}`),element('p',a.reason),element('p',a.consequences),element('small',`Expires ${new Date(a.expires_at*1000).toLocaleString()}`));const details=element('details');details.open=true;details.append(element('summary',`Exact operation${args.path?' — '+args.path:''}`),element('pre',JSON.stringify(args,null,2)));card.append(details);for(const [label,approve] of [['Approve',true],['Reject',false]]){const b=element('button',label,approve?'primary':undefined);b.onclick=action(async()=>{b.disabled=true;await api(`/api/approvals/${a.id}/decision`,{method:'POST',body:JSON.stringify({approve})});await refresh();});card.append(b);}$('approval-list').append(card);}
}
function renderNotifications(){$('notification-list').replaceChildren();for(const n of (state.portfolio.notifications||[]).filter(n=>!n.read_at)){const row=element('article',undefined,'task-row');row.append(element('h3',n.title),element('p',n.body));const read=element('button','Dismiss');read.onclick=action(async()=>{await api(`/api/notifications/${n.id}/read`,{method:'POST'});await refresh();});row.append(read);$('notification-list').append(row);}}
$('load-policies').onclick=action(async()=>{const query=state.project?'?project_id='+state.project:'',policies=await api('/api/policies'+query);$('policy-list').replaceChildren();for(const p of policies){const row=element('form',undefined,'route-row'),mode=element('select');for(const value of ['ALLOW','ASK','DENY','INHERIT']){const option=element('option',value);option.value=value;mode.append(option);}mode.value=p.mode;const save=element('button','Save','primary');row.append(element('label',p.action.replaceAll('_',' ')),mode,element('small',`${p.source} · ${p.available?'Available':'Unavailable'}`),save);row.onsubmit=action(async event=>{event.preventDefault();const body={mode:mode.value==='INHERIT'?null:mode.value};if(mode.value==='ALLOW'&&!p.available){editor('Accept policy risk',[{name:'risk',label:'This action remains unavailable until a controlled capability is implemented. Accept the policy risk for future enablement?',options:[['false','No'],['true','Yes, accept explicitly']]}],values=>api('/api/policies/'+p.action+query,{method:'PUT',body:JSON.stringify({...body,risk_accepted:values.risk==='true'})}));return;}await api('/api/policies/'+p.action+query,{method:'PUT',body:JSON.stringify(body)});notice('Policy saved. Pending approvals were invalidated to prevent stale permission grants.');});$('policy-list').append(row);}});
$('daily-summary-toggle').onclick=()=>editor('Daily summaries',[{name:'enabled',label:'One daily summary in your notification center',options:[['false','Disabled'],['true','Enabled']]}],values=>api('/api/settings/notifications',{method:'PUT',body:JSON.stringify({daily_summaries:values.enabled==='true'})}));

function fitViewport(){document.documentElement.style.setProperty('--app-height',`${window.visualViewport?.height||window.innerHeight}px`);}
window.visualViewport?.addEventListener('resize',fitViewport);window.addEventListener('resize',fitViewport);fitViewport();
