'use strict';
const $ = id => document.getElementById(id);
const state = {csrf:null, project:null, portfolio:null, detail:null, source:null, execution:null, view:'chat', setup:false, selection:0, drafts:new Map(), memorySelection:0, pending:new Set(), taskFeedback:new Map(), busyTasks:new Set()};
const element = (tag, text, cls) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
async function api(path, options={}){
  const headers={'Content-Type':'application/json',...(options.headers||{})};
  if(options.method && options.method!=='GET')headers['X-CSRF-Token']=state.csrf||'';
  const response=await fetch(path,{...options,headers});
  const result=await response.json();
  if(!response.ok){if(response.status===401 && !path.includes('login'))showAuth();const error=new Error(result.detail||result.message||'Request failed');error.result=result.result||(response.status<500?'rejected':'failed');throw error;}
  return result;
}
function action(fn){return async event=>{const button=event?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(event);}catch(error){notice('Rejected or failed: '+error.message);}finally{if(button?.tagName==='BUTTON')button.disabled=false;}};}
function taskFeedback(id,text){state.taskFeedback.set(id,text);const feedback=document.querySelector(`[data-task="${id}"] .task-feedback`);if(feedback){feedback.textContent=text;feedback.hidden=!text;if(text&&!$('editor').open)feedback.scrollIntoView({block:'nearest'});}}
function taskAction(task,fn){return async event=>{const button=event.currentTarget;if(state.busyTasks.has(task.id))return;state.busyTasks.add(task.id);button.disabled=true;taskFeedback(task.id,`${button.textContent} requested…`);try{await fn(event);}catch(error){const text=`${error.result||'failed'}: ${error.message}`;taskFeedback(task.id,text);notice(text);}finally{state.busyTasks.delete(task.id);document.querySelectorAll(`[data-task="${task.id}"] button`).forEach(b=>b.disabled=false);}};}
function actionResult(result,taskId){const text=`${result.result||'accepted'}: ${result.message||result.status||'Operation recorded'}${result.execution_id?' · Execution '+result.execution_id.slice(0,8):''}`;notice(text);if(taskId)taskFeedback(taskId,text);}
function showAuth(){state.source?.close();$('app').hidden=true;$('auth-screen').hidden=false;$('chat-notice').hidden=true;clearTimeout(state.chatNoticeTimer);}
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
  const settings=await api('/api/settings');state.upgradeAvailable=settings.autonomy_upgrade_available===true;$('owner-administration').hidden=settings.owner_administration_available!==true;for(const id of ['switch-profile','policy-history','configure-integration','discover-verification','verification-overview','propose-command','batch-approvals'])$(id).hidden=!state.upgradeAvailable;$('global-instructions').value=settings.global_instructions;$('scheduler-status').textContent=settings.scheduler_enabled?'Scheduler enabled':'Scheduler paused';
  state.historyAvailable=settings.chat_history_available===true;document.querySelector('[data-view="search"]').hidden=!state.historyAvailable;document.querySelector('.chat-actions').hidden=!state.historyAvailable;
  await refresh();await selectProject(state.project);
}
async function refresh(){
  state.portfolio=await api('/api/portfolio');const scheduler=state.portfolio.scheduler;$('scheduler-status').textContent=scheduler?`${scheduler.enabled?'Enabled':'Paused'} · loop ${scheduler.running?'running':'stopped'} · scope ${projectName(scheduler.scope)} · ${scheduler.active_workers.length} active / ${scheduler.max_workers} workers · ${scheduler.pending_count??state.portfolio.tasks.filter(t=>t.status==='pending').length} pending · ${scheduler.blocked_count??state.portfolio.tasks.filter(t=>t.status==='blocked').length} blocked · last verified ${scheduler.last_verified?new Date(scheduler.last_verified.created_at*1000).toLocaleString():state.upgradeAvailable?'none':'unavailable'}${scheduler.error?' · Error: '+scheduler.error:''}`:'Scheduler status unavailable';$('execution-status').textContent=$('scheduler-status').textContent;renderProjects();renderDashboard();renderTasks();renderAgents();renderApprovals();renderNotifications();$('autonomy-status').textContent='Global profile: '+(state.portfolio.autonomy_profile||'legacy').replaceAll('_',' ');
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
  clearTimeout(searchTimer);$('history-search').value='';state.historyRequest=(state.historyRequest||0)+1;state.detailRequest=(state.detailRequest||0)+1;state.history=null;$('history-detail').hidden=true;updateChatActions();
  renderDashboard();renderTasks();renderAgents();renderMemory();
  if(state.view==='memory')await loadMemory(selection);
  if(state.view==='search')await searchHistory();
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
  for(const t of tasks){
    const row=element('article',undefined,'task-row');row.dataset.task=t.id;row.tabIndex=-1;
    row.append(element('h3',t.description));const meta=element('div',undefined,'row-meta');
    meta.append(element('span',t.status,`badge ${t.status}`),element('span',projectName(t.project_id)),element('span',`Priority ${t.priority}`),element('span',`#${t.id.slice(0,8)}`));row.append(meta,element('p',`Acceptance: ${t.acceptance}`));
    if(t.blocker)row.append(element('p','Blocked: '+t.blocker,'task-blocker'));
    if(t.readiness?.reason&&!t.blocker&&t.status==='pending')row.append(element('p','Cannot start: '+t.readiness.reason,'task-blocker'));
    if(t.readiness?.verification_only&&t.status==='blocked')row.append(element('p','The worker has finished. Configure verification, then retry its independent checks. Retrying does not run the worker again.'));
    if(!t.readiness?.verification_configured&&!['completed','cancelled'].includes(t.status))row.append(element('p','Trusted verification is not configured; completion will be blocked.'));
    for(const dependency of t.readiness?.dependencies||[]){
      const view=element('button',`View prerequisite #${dependency.id.slice(0,8)} (${dependency.status})`);
      view.onclick=action(async()=>{await selectProject(t.project_id);const prerequisite=document.querySelector(`[data-task="${dependency.id}"]`);prerequisite?.scrollIntoView({block:'center'});prerequisite?.focus({preventScroll:true});});row.append(view);
    }
    if(t.readiness?.verification_only&&t.status==='blocked'){
      const verify=element('button','Retry verification');verify.onclick=taskAction(t,async()=>{const result=await api(`/api/tasks/${t.id}/verify`,{method:'POST',body:JSON.stringify({revision:t.revision})});await refresh();actionResult(result,t.id);});row.append(verify);
      const configure=element('button','Configure verification');configure.onclick=taskAction(t,async()=>{await selectProject(t.project_id);await configureVerification(t.project_id);taskFeedback(t.id,'Verification settings opened. After saving approved commands, click Retry verification.');});row.append(configure);
    }
    if(t.status==='pending'){const start=element('button','Start worker');start.onclick=taskAction(t,async()=>{const result=await api(`/api/tasks/${t.id}/start`,{method:'POST'});await refresh();actionResult(result,t.id);});row.append(start);}
    if(['paused','blocked','failed'].includes(t.status)&&t.readiness?.can_resume){const resume=element('button','Resume persisted worker');resume.onclick=taskAction(t,async()=>{if(t.status==='blocked'){editor('Reconcile uncertain execution',[{name:'reconciliation',label:'Inspect saved thread, workspace and operation records first. Describe evidence establishing what ran and why continuing is safe.',long:true,required:true}],async values=>{const result=await api(`/api/tasks/${t.id}/resume`,{method:'POST',body:JSON.stringify({...values,revision:t.revision,reconciled:true})});actionResult(result,t.id);});taskFeedback(t.id,'Owner reconciliation is required before this uncertain execution can resume.');return;}const result=await api(`/api/tasks/${t.id}/resume`,{method:'POST',body:JSON.stringify({revision:t.revision})});await refresh();actionResult(result,t.id);});row.append(resume);}
    if(['paused','blocked','failed'].includes(t.status)&&t.readiness?.can_retry){const retry=element('button','Retry launch');retry.onclick=taskAction(t,async()=>{const result=await api(`/api/tasks/${t.id}/retry`,{method:'POST',body:JSON.stringify({revision:t.revision})});await refresh();actionResult(result,t.id);});row.append(retry);}
    if(['pending','paused','blocked','failed'].includes(t.status)){const cancel=element('button','Cancel task');cancel.onclick=taskAction(t,async()=>{const result=await api(`/api/tasks/${t.id}`,{method:'PATCH',body:JSON.stringify({revision:t.revision,status:'cancelled'})});await refresh();actionResult(result,t.id);});row.append(cancel);}
    if(state.upgradeAvailable&&t.readiness?.verification_only){const workspaceCommands=element('button','Review workspace verification');workspaceCommands.onclick=taskAction(t,async()=>{const project=state.portfolio.projects.find(p=>p.id===t.project_id),worker=state.portfolio.executions.find(e=>e.task_id===t.id&&e.worktree);if(!worker)throw new Error('No assigned workspace available.');await discoverVerification([project],worker.id);taskFeedback(t.id,'Review commands against this completed worker workspace.');});row.append(workspaceCommands);}
    if(state.upgradeAvailable&&t.status==='blocked'&&(state.portfolio.integration||[]).some(i=>i.task_id===t.id&&['blocked','started'].includes(i.status))){const reconcile=element('button','Reconcile integration');reconcile.onclick=taskAction(t,async()=>{editor('Reconcile blocked integration',[{name:'reconciliation',label:'Inspect saved integration, source HEAD, workspace and operation records. Record evidence that retry or recognition of the existing merge is safe.',long:true,required:true}],values=>api(`/api/tasks/${t.id}/integration/reconcile`,{method:'POST',body:JSON.stringify({...values,revision:t.revision,reconciled:true})}));taskFeedback(t.id,'Owner reconciliation required; uncertain integration will not be replayed automatically.');});row.append(reconcile);}
    const inspect=element('button','Inspect execution records');inspect.onclick=taskAction(t,async()=>{const records=await api(`/api/tasks/${t.id}/execution-records`);editor('Persisted task and execution records',[{name:'records',label:'Review task, thread, turn, workspace, operations, approvals and verification evidence',long:true,rows:14,value:JSON.stringify(records,null,2)}],async()=>{});taskFeedback(t.id,'Execution records opened.');});row.append(inspect);
    const feedback=element('p',state.taskFeedback.get(t.id)||'','task-feedback');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');feedback.hidden=!feedback.textContent;row.append(feedback);
    if(state.busyTasks.has(t.id))row.querySelectorAll('button').forEach(button=>button.disabled=true);
    $('task-list').append(row);
  }
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
  const query=new URLSearchParams();if(state.project)query.set('project_id',state.project);
  const request=state.loadHistoryRequest=(state.loadHistoryRequest||0)+1;
  const history=await api(`/api/history?${query}`);if(selection!==state.selection||request!==state.loadHistoryRequest)return;
  state.history=history;updateChatActions();
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
  updateChatActions();
  const source=new EventSource(`/api/executions/${execution}/events?after=${cursor}`);state.source=source;
  source.addEventListener('chat.delta',event=>{const data=JSON.parse(event.data);const content=document.querySelector(`[data-message="${messageId}"] .message-content`);if(content){if(content.textContent==='Connecting to Codex…')content.textContent='';content.textContent+=data.delta;}$('messages').scrollTop=$('messages').scrollHeight;});
  source.addEventListener('chat.finished',event=>{const data=JSON.parse(event.data);if(data.error)notice(data.error);});
  source.addEventListener('stream.end',action(async()=>{source.close();if(state.source!==source)return;state.source=null;state.execution=null;$('stop-chat').hidden=true;$('send').disabled=false;$('status-label').textContent='Ready to help';await loadHistory();await refresh();}));
  source.addEventListener('stream.auth_expired',()=>{source.close();showAuth();});
  source.onerror=()=>{if(state.source===source)$('status-label').textContent='Reconnecting to saved stream…';};
}
function updateChatActions(){
  const disabled=!state.historyAvailable||!state.history?.messages.length||state.pending.has(state.project)||!!state.execution||state.history.messages.some(m=>['queued','streaming'].includes(m.status));
  for(const id of ['archive-chat','clear-chat'])$(id).disabled=disabled;
}
function confirmChatReset(archive){
  if(!state.history?.messages.length)return;
  const project=state.project,selection=state.selection,history=state.history;
  $('chat-confirm-title').textContent=archive?'Archive this chat?':'Clear this chat?';
  $('chat-confirm-description').textContent=archive?`Save the entire ${projectName(project)} conversation in a searchable archive and start a fresh chat.`:`Remove all messages from the current ${projectName(project)} chat? This cannot be undone. Existing archives, tasks and project memory are preserved.`;
  $('chat-archive-label').hidden=!archive;
  $('chat-archive-name').value=`${projectName(project)} · ${new Date().toLocaleString()}`.slice(0,120);
  $('chat-confirm-submit').textContent=archive?'Archive chat':'Clear chat';
  $('chat-confirm-submit').className=archive?'primary':'danger';$('chat-confirm-error').textContent='';
  $('chat-confirm-form').onsubmit=async event=>{
    event.preventDefault();const button=$('chat-confirm-submit');if(button.disabled)return;button.disabled=true;
    try{
      const result=await api(archive?'/api/chat/archive':'/api/chat/clear',{method:'POST',body:JSON.stringify({project_id:project,conversation_id:history.conversation.id,last_message_id:history.messages.at(-1).id,confirmed:true,title:archive?$('chat-archive-name').value:''})});
      $('chat-confirm').close();$('chat-notice').textContent=result.message;$('chat-notice').hidden=false;clearTimeout(state.chatNoticeTimer);state.chatNoticeTimer=setTimeout(()=>$('chat-notice').hidden=true,8000);
      if(selection===state.selection){state.source?.close();state.source=null;state.execution=null;$('stop-chat').hidden=true;$('send').disabled=false;await loadHistory(selection);}
    }catch(error){$('chat-confirm-error').textContent=error.message;}finally{button.disabled=false;}
  };
  $('chat-confirm').showModal();
}
$('archive-chat').onclick=()=>confirmChatReset(true);$('clear-chat').onclick=()=>confirmChatReset(false);
$('chat-confirm-cancel').onclick=()=>$('chat-confirm').close();
function historyQuery(){const query=new URLSearchParams();if(state.project)query.set('project_id',state.project);return query;}
async function searchHistory(append=false){
  const request=state.historyRequest=(state.historyRequest||0)+1,query=historyQuery(),offset=append?state.historyOffset||0:0;
  query.set('search',$('history-search').value);query.set('offset',offset);
  $('history-scope').textContent=projectName(state.project);$('history-search-status').textContent='Searching…';
  if(!append){$('history-results').replaceChildren();$('history-more').hidden=true;$('history-detail').hidden=true;state.detailRequest=(state.detailRequest||0)+1;}
  const data=await api('/api/chat-history?'+query);if(request!==state.historyRequest)return;
  for(const chat of data.conversations){
    const row=element('article',undefined,'history-result'),button=element('button',chat.archived_at?chat.title:'Current chat');
    button.onclick=action(()=>openSavedHistory(chat.id,false,chat.match_count?$('history-search').value:''));row.append(button,element('small',`${chat.archived_at?'Archived '+new Date(chat.archived_at*1000).toLocaleString():'Active conversation'} · ${chat.message_count} messages${$('history-search').value?' · '+chat.match_count+' matching messages':''}`));$('history-results').append(row);
  }
  state.historyOffset=offset+data.conversations.length;$('history-more').hidden=!data.has_more;
  $('history-search-status').textContent=state.historyOffset?`${state.historyOffset} chat${state.historyOffset===1?'':'s'}${data.has_more?' shown':''}. Select a chat to read its history.`:'No matching chats in this context.';
}
async function openSavedHistory(id,older=false,search=''){
  const request=state.detailRequest=(state.detailRequest||0)+1,query=historyQuery();
  if(older&&state.savedHistory?.messages.length)query.set('before',state.savedHistory.messages[0].id);
  query.set('search',older?state.savedHistory.search:search);
  const data=await api(`/api/chat-history/${encodeURIComponent(id)}?${query}`);if(request!==state.detailRequest)return;
  state.savedHistory=older?{...data,messages:[...data.messages,...state.savedHistory.messages]}:data;
  $('history-detail-title').textContent=data.conversation.title||'Current chat';
  $('history-detail-meta').textContent=`${projectName(state.project)} · ${data.conversation.archived_at?'Archived '+new Date(data.conversation.archived_at*1000).toLocaleString():'Active conversation'}${data.search?' · Messages matching “'+data.search+'”':''}`;
  $('history-full').hidden=!data.search;
  $('history-detail-messages').replaceChildren();
  for(const m of state.savedHistory.messages){const row=element('article',undefined,`message ${m.role}`),body=element('div');body.append(element('strong',m.role==='user'?'You':'AI Manager'),element('small',new Date(m.created_at*1000).toLocaleString(),'history-time'),element('div',m.content,'message-content'));row.append(element('div',m.role==='user'?'YOU':'Ai','avatar'),body);$('history-detail-messages').append(row);}
  if(!state.savedHistory.messages.length)$('history-detail-messages').append(element('p','This chat is empty.','empty'));
  $('history-older').hidden=!data.has_more;$('history-detail').hidden=false;
  if(!older)$('history-detail').scrollIntoView({block:'start'});
}
$('history-more').onclick=action(()=>searchHistory(true));
$('history-older').onclick=action(()=>openSavedHistory(state.savedHistory.conversation.id,true));
$('history-full').onclick=action(()=>openSavedHistory(state.savedHistory.conversation.id));
function showView(view){state.view=view;if(view==='memory')loadMemory().catch(error=>notice(error.message));if(view==='search')searchHistory().catch(error=>notice(error.message));document.querySelectorAll('.view').forEach(node=>node.hidden=node.id!==`view-${view}`);document.querySelectorAll('.tabs button').forEach(b=>(b.classList.toggle('active',b.dataset.view===view),b.setAttribute('aria-current',b.dataset.view===view?'page':'false')));}
function editor(title,fields,onSave){
  $('editor-title').textContent=title;$('editor-fields').replaceChildren();$('editor-error').textContent='';
  for(const f of fields){const label=element('label',f.label);let control;if(f.options){control=element('select');for(const [value,text] of f.options){const option=element('option',text);option.value=value;control.append(option);}}else{control=element(f.long?'textarea':'input');if(f.long)control.rows=f.rows||4;else control.type=f.type||'text';}control.name=f.name;control.value=f.value??'';control.required=!!f.required;control.readOnly=!!f.readonly;if(f.min!==undefined)control.min=f.min;if(f.max!==undefined)control.max=f.max;label.append(control);$('editor-fields').append(label);}
  $('editor-form').onsubmit=async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));try{const result=await onSave(values);if(result)actionResult(result);$('editor').close();await refresh();await selectProject(state.project);}catch(error){$('editor-error').textContent=error.message;}};$('editor').showModal();
}
function editProject(p){editor('Project settings',[{name:'display_name',label:'Display name (blank uses original name)',value:p.display_name||''},{name:'objective',label:'Objective',value:p.objective,long:true},{name:'success_criteria',label:'Success criteria',value:p.success_criteria,long:true},{name:'instructions',label:'Project instructions',value:p.instructions,long:true},{name:'priority',label:'Priority',type:'number',value:p.priority,min:0,max:100},{name:'status',label:'Registry status (source files are preserved)',value:p.status,options:['active','paused','archived','removed'].map(x=>[x,x])}],async values=>{await api(`/api/projects/${p.id}`,{method:'PATCH',body:JSON.stringify({...values,priority:Number(values.priority),revision:p.revision})});if(values.status==='removed'&&state.project===p.id)state.project=null;});}
function editMemory(m){const project=state.project;editor(`Edit ${m.name}`,[{name:'content',label:'Human-readable project memory',value:m.content,long:true,rows:12}],values=>api(`/api/projects/${project}/memory/${m.name}`,{method:'PUT',body:JSON.stringify({...values,revision:m.revision})}));}
$('auth-form').onsubmit=async event=>{event.preventDefault();$('auth-error').textContent='';$('login-button').disabled=true;try{const credentials={username:$('username').value,password:$('password').value};if(state.setup){await api('/api/setup',{method:'POST',body:JSON.stringify({...credentials,token:$('setup-token').value})});state.setup=false;$('setup-token').value='';}const login=await api('/api/login',{method:'POST',body:JSON.stringify(credentials)});state.csrf=login.csrf;$('password').value='';await enter();}catch(error){$('auth-error').textContent=error.message;}finally{$('login-button').disabled=false;}};
$('chat-form').onsubmit=action(async event=>{
  event.preventDefault();const content=$('message-input').value.trim(),project=state.project,selection=state.selection;
  if(!content||$('send').disabled||state.pending.has(project))return;
  state.pending.add(project);$('send').disabled=true;
  updateChatActions();
  try{
    await api('/api/chat',{method:'POST',body:JSON.stringify({project_id:project,content,mode:$('chat-mode').value})});
    state.drafts.set(project,'');
    if(selection===state.selection){$('message-input').value='';await loadHistory(selection);}
    await refresh();
  }finally{state.pending.delete(project);if(selection===state.selection&&!state.execution)$('send').disabled=false;updateChatActions();}
});
$('message-input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send').disabled)$('chat-form').requestSubmit();}};
$('all-projects').onclick=action(()=>selectProject(null));$('discover').onclick=action(async()=>{await api('/api/projects/discover',{method:'POST'});await refresh();notice('Project discovery complete.');});
$('logout').onclick=action(async()=>{await api('/api/logout',{method:'POST'});state.csrf=null;showAuth();});
$('revoke-sessions').onclick=action(async()=>{await api('/api/sessions/revoke',{method:'POST'});showAuth();});
$('stop-chat').onclick=action(async()=>{if(state.execution)await api(`/api/executions/${state.execution}/interrupt`,{method:'POST'});notice('Interruption requested.');});
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>showView(b.dataset.view));
let searchTimer;$('history-search').oninput=()=>{clearTimeout(searchTimer);state.historyRequest=(state.historyRequest||0)+1;state.detailRequest=(state.detailRequest||0)+1;$('history-detail').hidden=true;searchTimer=setTimeout(action(()=>searchHistory()),250);};
$('editor-cancel').onclick=()=>$('editor').close();
$('add-project').onclick=()=>editor('Add an existing project',[{name:'path',label:'Directory inside a configured project root',required:true},{name:'name',label:'Name (optional)'}],values=>api('/api/projects',{method:'POST',body:JSON.stringify({...values,name:values.name||null})}));
$('add-task').onclick=()=>editor('Add a persistent task',[{name:'project_id',label:'Project',value:state.project,options:state.portfolio.projects.filter(p=>p.status!=='removed').map(p=>[p.id,projectLabel(p)]),required:true},{name:'description',label:'What should be done?',long:true,required:true},{name:'acceptance',label:'How will we know it succeeded?',long:true,required:true},{name:'verification',label:'Independent verification requirements',long:true},{name:'kind',label:'Task type',value:'code',options:['code','documentation','research','infrastructure','investigation'].map(x=>[x,x])},{name:'verification_spec',label:'Task evidence specification (JSON: files, sections, allowed_paths, evidence_file)',long:true,value:'{}'},{name:'priority',label:'Priority',type:'number',value:50,min:0,max:100},{name:'dependencies',label:'Dependency task IDs, separated by commas'}].filter(f=>state.upgradeAvailable||!['kind','verification_spec'].includes(f.name)),values=>api('/api/tasks',{method:'POST',body:JSON.stringify({...values,verification_spec:values.verification_spec?JSON.parse(values.verification_spec):{},priority:Number(values.priority),dependencies:values.dependencies.split(',').map(s=>s.trim()).filter(Boolean)})}));
$('instructions-form').onsubmit=action(async event=>{event.preventDefault();await api('/api/settings/instructions',{method:'PUT',body:JSON.stringify({content:$('global-instructions').value})});notice('Global instructions saved.');});
$('load-models').onclick=action(async()=>{const catalog=await api('/api/models');$('model-routes').replaceChildren();for(const [role,route] of Object.entries(catalog.routes)){const row=element('form',undefined,'route-row'),model=element('select'),effort=element('select');for(const m of catalog.models){const option=element('option',m.displayName);option.value=m.model;model.append(option);}model.value=route.model;function efforts(){effort.replaceChildren();const m=catalog.models.find(m=>m.model===model.value);for(const e of m.supportedReasoningEfforts.filter(e=>e.reasoningEffort!=='ultra')){const option=element('option',e.reasoningEffort);option.value=e.reasoningEffort;effort.append(option);}effort.value=route.effort;}efforts();model.onchange=efforts;model.setAttribute('aria-label',`${role} model`);effort.setAttribute('aria-label',`${role} reasoning effort`);const save=element('button','Save','primary');row.append(element('label',role),model,effort,save);row.onsubmit=action(async event=>{event.preventDefault();await api(`/api/models/${role}`,{method:'PUT',body:JSON.stringify({model:model.value,effort:effort.value})});notice(`${role} model settings saved.`);});$('model-routes').append(row);}});
setInterval(()=>{if(!$('app').hidden && true)refresh().catch(error=>notice(error.message));},10000);
boot().catch(error=>notice(error.message));


$('plan-goal').onclick=()=>{if(!state.project){notice('Select a project to plan a goal.');return;}const project=state.project;editor('Plan a delegated goal',[{name:'goal',label:'What should the manager accomplish?',long:true,required:true}],values=>api('/api/goals',{method:'POST',body:JSON.stringify({...values,project_id:project,request_key:crypto.randomUUID()})}));};
$('configure-scheduler').onclick=()=>editor('Autonomous scheduling',[{name:'enabled',label:'Scheduler',value:state.portfolio.automation_enabled?'true':'false',options:[['true','Enabled'],['false','Paused']]},{name:'project_id',label:'Project scope',value:state.project||'',options:[['','All Projects'],...state.portfolio.projects.map(p=>[p.id,projectLabel(p)])]},{name:'limit',label:'Number of successful completions (blank means continuous)',type:'number',min:1,max:100},{name:'max_workers',label:'Maximum concurrent workers',type:'number',value:state.portfolio.scheduler?.max_workers||2,min:1,max:8}],values=>api('/api/scheduler',{method:'PUT',body:JSON.stringify({enabled:values.enabled==='true',project_id:values.project_id||null,limit:values.limit?Number(values.limit):null,max_workers:Number(values.max_workers)})}));
async function configureVerification(project){
  const settings=await api(`/api/projects/${project}/verification`);
  editor('Owner-approved verification commands',[{name:'commands',label:'Explicitly approve the project’s documented test commands as JSON argument arrays. Tests run with system runtimes and /workspace only, without host home, installed home environments or network. Saving permits safe verification retry only for confirmed completed work blocked by missing commands; workers are never replayed.',value:JSON.stringify(settings.commands,null,2),long:true,rows:8}],values=>api(`/api/projects/${project}/verification`,{method:'PUT',body:JSON.stringify({commands:JSON.parse(values.commands)})}));
}
$('configure-verification').onclick=action(async()=>{if(!state.project){notice('Select a project first.');return;}await configureVerification(state.project);});

function renderApprovals(){
  const approvals=(state.portfolio.approvals||[]).filter(a=>!state.project||a.project_id===state.project);$('approval-count').textContent=approvals.length;$('approval-list').replaceChildren();
  if(!approvals.length)$('approval-list').append(element('p','Nothing waiting for your approval.','empty'));
  for(const a of approvals){const args=JSON.parse(a.arguments),card=element('article',undefined,'panel');card.append(element('h2',`${a.action.replaceAll('_',' ')} · ${projectName(a.project_id)}`),element('p',a.reason),element('p',a.consequences),element('small',`Expires ${new Date(a.expires_at*1000).toLocaleString()}`));const details=element('details');details.open=true;details.append(element('summary',`Exact operation${args.path?' — '+args.path:''}`),element('pre',JSON.stringify(args,null,2)));card.append(details);for(const [label,approve] of [['Approve',true],['Reject',false]]){const b=element('button',label,approve?'primary':undefined);b.onclick=action(async()=>{b.disabled=true;await api(`/api/approvals/${a.id}/decision`,{method:'POST',body:JSON.stringify({approve})});await refresh();});card.append(b);}$('approval-list').append(card);}
}
function renderNotifications(){$('notification-list').replaceChildren();for(const n of (state.portfolio.notifications||[]).filter(n=>!n.read_at)){const row=element('article',undefined,'task-row');row.append(element('h3',n.title),element('p',n.body));const read=element('button','Dismiss');read.onclick=action(async()=>{await api(`/api/notifications/${n.id}/read`,{method:'POST'});await refresh();});row.append(read);$('notification-list').append(row);}}
$('load-policies').onclick=action(async()=>{const query=state.project?'?project_id='+state.project:'',policies=await api('/api/policies'+query);$('policy-list').replaceChildren();for(const p of policies){const row=element('form',undefined,'route-row'),mode=element('select');for(const value of ['ALLOW','ASK','DENY','INHERIT']){const option=element('option',value);option.value=value;mode.append(option);}mode.value=p.mode;const save=element('button','Save','primary');row.append(element('label',p.action.replaceAll('_',' ')),mode,element('small',p.explanation||`${p.source} · ${p.available?'Available':'Unavailable'}`),save);row.onsubmit=action(async event=>{event.preventDefault();const body={mode:mode.value==='INHERIT'?null:mode.value};if(mode.value==='ALLOW'&&!p.available){editor('Accept policy risk',[{name:'risk',label:'This action remains unavailable until a controlled capability is implemented. Accept the policy risk for future enablement?',options:[['false','No'],['true','Yes, accept explicitly']]}],values=>api('/api/policies/'+p.action+query,{method:'PUT',body:JSON.stringify({...body,risk_accepted:values.risk==='true'})}));return;}await api('/api/policies/'+p.action+query,{method:'PUT',body:JSON.stringify(body)});notice('Policy saved. Pending approvals were invalidated to prevent stale permission grants.');});$('policy-list').append(row);}});
$('daily-summary-toggle').onclick=()=>editor('Daily summaries',[{name:'enabled',label:'One daily summary in your notification center',options:[['false','Disabled'],['true','Enabled']]}],values=>api('/api/settings/notifications',{method:'PUT',body:JSON.stringify({daily_summaries:values.enabled==='true'})}));

function fitViewport(){document.documentElement.style.setProperty('--app-height',`${window.visualViewport?.height||window.innerHeight}px`);}
window.visualViewport?.addEventListener('resize',fitViewport);window.addEventListener('resize',fitViewport);fitViewport();

// Profile activation is an explicit authenticated decision bound to a current preview.
$('switch-profile').onclick=action(async()=>{
  const project=state.project, query=project?'&project_id='+encodeURIComponent(project):'';
  const current=await api('/api/autonomy'+(project?'?project_id='+encodeURIComponent(project):''));
  editor('Choose autonomy profile',[{name:'profile',label:`Scope: ${project?projectName(project):'Global defaults'} · Active: ${current.active_profile}`,value:'high_autonomy',options:current.profiles.map(p=>[p,p.replaceAll('_',' ')+(p==='high_autonomy'?' (recommended)':'')])}],async values=>{
    const proposal=await api('/api/autonomy/proposal?profile='+encodeURIComponent(values.profile)+query);
    // Open the review after the selection dialog closes.
    setTimeout(()=>editor('Owner approval: consolidated policy changes',[
      {name:'summary',label:`${proposal.scope==='global'?'Global defaults and inheriting projects':projectName(project)} · ${proposal.previous} → ${proposal.profile}`,long:true,rows:12,readonly:true,value:proposal.changes.map(c=>`${c.scope==='global'?'Global':projectName(c.scope)}: ${c.action} ${c.old} → ${c.mode}${c.available?'':' (capability unavailable)'}`).join('\n')||'No effective permission changes.'},
      {name:'boundaries',label:'Scope, duration and consequences',long:true,rows:5,readonly:true,value:proposal.boundaries+'\n'+proposal.scheduler+'\n'+proposal.duration},
      {name:'decision',label:'Activate this exact reviewed policy?',value:'no',options:[['no','Keep current profile'],['yes','Approve and activate']]}
    ],async decision=>{
      if(decision.decision!=='yes')return {message:'Current autonomy policy retained.'};
      const result=await api('/api/autonomy/activate',{method:'POST',body:JSON.stringify({profile:proposal.profile,project_id:project,proposal_hash:proposal.proposal_hash})});
      $('autonomy-status').textContent=`Active: ${proposal.profile.replaceAll('_',' ')} · ${project?projectName(project):'Global defaults'}`;
      return result;
    }),0);
  });
});
$('policy-history').onclick=action(async()=>{
  const data=await api('/api/autonomy'+(state.project?'?project_id='+state.project:''));
  $('autonomy-status').textContent='Active: '+data.active_profile.replaceAll('_',' ');
  editor('Policy change history',[{name:'history',label:'Owner-authenticated decisions; switch profiles or use INHERIT to reverse overrides',long:true,rows:14,readonly:true,value:JSON.stringify(data.history,null,2)}],async()=>{});
});
$('configure-integration').onclick=action(async()=>{
  if(!state.project){notice('Select the project whose branch you authorize for integration.');return;}
  const project=state.project, data=await api(`/api/projects/${project}/integration`);
  editor('Verified local integration',[{name:'auto_commit',label:'Automatically commit independently verified content',value:String(data.auto_commit),options:[['false','Disabled'],['true','Authorized for this project']]},{name:'auto_merge',label:'Automatically fast-forward verified commits into the selected clean source branch',value:String(data.auto_merge),options:[['false','Disabled'],['true','Authorize verified merges']]},{name:'target_branch',label:'Exact target branch (no push or deployment)',value:data.target_branch}],values=>api(`/api/projects/${project}/integration`,{method:'PUT',body:JSON.stringify({...values,auto_commit:values.auto_commit==='true',auto_merge:values.auto_merge==='true'})}));
});
async function discoverVerification(projects,executionId=null){
  const discovered=[];
  for(const project of projects)discovered.push({project,...await api(`/api/projects/${project.id}/verification/discover${executionId?'?execution_id='+encodeURIComponent(executionId):''}`,{method:'POST'})});
  const choices=discovered.flatMap(data=>data.candidates.map(c=>({project:data.project,command:c})));
  if(!choices.length){notice('No safe command candidates found. Configure documented argv manually.');return;}
  const fields=choices.map(({project,command:c},i)=>({name:'command_'+i,label:`${projectLabel(project)} · ${c.status} · ${JSON.stringify(c.argv)} · cwd ${c.cwd}\n${c.explanation}\nRuntime: ${c.requirements.runtime}. ${c.requirements.dependencies}\nEnvironment: ${c.environment.id}; no host home/network; configuration bound. Approval persists until revoked or materially changed.`,value:'keep',options:[['keep','Keep current decision'],['approved','Approve exact command'],['rejected','Reject'],['revoked','Revoke']]}));
  fields.unshift({name:'batch',label:'Optional batch approval: all commands shown below (verification and offline provisioning)',value:'individual',options:[['individual','Use individual decisions'],['all','Approve all listed discovered commands']]});
  editor('Reusable verification command approvals',fields,async values=>{
    const grouped=new Map();
    choices.forEach(({project,command},i)=>{const status=values['command_'+i]!=='keep'?values['command_'+i]:values.batch==='all'&&command.status==='discovered'?'approved':'keep';if(status==='keep')return;if(!grouped.has(project.id))grouped.set(project.id,[]);grouped.get(project.id).push({id:command.id,status});});
    let changed=0;
    for(const [project,decisions] of grouped){await api(`/api/projects/${project}/verification/decisions`,{method:'POST',body:JSON.stringify({decisions})});changed+=decisions.length;}
    return {message:`${changed} scoped command decisions saved. Approvals persist; safe blocked verification will be reevaluated.`};
  });
}
$('verification-overview').onclick=action(async()=>{
  const data=(await api('/api/verification')).filter(p=>!state.project||p.project_id===state.project);
  editor('Verification configurations and failures',[{name:'status',label:'Discovered, approved, revoked or stale configurations and recent failed verification',long:true,rows:16,readonly:true,value:JSON.stringify(data,null,2)}],async()=>{});
});
$('batch-approvals').onclick=action(async()=>{
  const approvals=(state.portfolio.approvals||[]).filter(a=>!state.project||a.project_id===state.project);
  if(!approvals.length){notice('No pending operations.');return;}
  editor('Review exact pending operations together',approvals.map((a,i)=>({name:'approval_'+i,label:`${projectName(a.project_id)} · ${a.action} · ${a.consequences}\n${a.arguments}\nExpires ${new Date(a.expires_at*1000).toLocaleString()}; one use, exact scope.`,value:'keep',options:[['keep','Leave pending'],['approve','Approve exact operation'],['reject','Reject']]})),async values=>{
    let count=0;for(let i=0;i<approvals.length;i++){const decision=values['approval_'+i];if(decision==='keep')continue;await api(`/api/approvals/${approvals[i].id}/decision`,{method:'POST',body:JSON.stringify({approve:decision==='approve'})});count++;}
    return {message:`${count} exact operation decisions saved.`};
  });
});

$('discover-verification').onclick=action(()=>discoverVerification(state.portfolio.projects.filter(p=>p.status!=='removed'&&(!state.project||p.id===state.project))));

$('propose-command').onclick=action(async()=>{
  if(!state.project){notice('Select a project for a scoped command definition.');return;}
  const project=state.project;
  editor('Propose isolated verification or provisioning command',[{name:'argv',label:'Exact JSON argument array',long:true,required:true,value:'["python3", "-m", "unittest", "discover", "-v"]'},{name:'cwd',label:'Project-relative working directory',value:'.',required:true},{name:'purpose',label:'Purpose',value:'verification',options:[['verification','Independent verification'],['dependency','Offline project-local provisioning']]},{name:'execution_id',label:'Optional assigned worker execution ID (for changed workspace configuration)'}],async values=>{
    await api(`/api/projects/${project}/verification/commands`,{method:'POST',body:JSON.stringify({argv:JSON.parse(values.argv),cwd:values.cwd,purpose:values.purpose,execution_id:values.execution_id||null})});
    return {message:'Command proposed. Use Discover and review commands to approve its exact scope.'};
  });
});

$('owner-administration').onclick=action(async()=>{
  const data=await api('/api/administration');
  const grants=data.grants.filter(g=>g.active && (!state.project || g.project_id===state.project));
  const fields=[{name:'receipts',label:'Persistent receipts: actual result and original scoped authorization',value:JSON.stringify(data,null,2),long:true,readonly:true,rows:10}];
  grants.forEach((g,i)=>fields.push({name:`grant_${i}`,label:`${g.id}: ${g.actions.join(', ')}; ${g.project_id||'global'}; until revoked`,options:[['keep','Keep authorization'],['revoke','Revoke authorization']]}));
  editor('Owner-authorized chat administration',fields,async values=>{const receipts=[];for(let i=0;i<grants.length;i++)if(values[`grant_${i}`]==='revoke')receipts.push(await api(`/api/administration/grants/${encodeURIComponent(grants[i].id)}`,{method:'DELETE'}));return {result:'accepted',message:'Administration grants reviewed',receipts};});
});
