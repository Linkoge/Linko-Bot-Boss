'use strict';
const $ = id => document.getElementById(id);
const state = {csrf:null, project:null, portfolio:null, detail:null, source:null, execution:null, view:'chat', setup:false, selection:0};
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
function action(fn){return async event=>{try{await fn(event);}catch(error){notice(error.message);}};}
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
  const settings=await api('/api/settings');$('global-instructions').value=settings.global_instructions;
  await refresh();await selectProject(state.project);
}
async function refresh(){
  state.portfolio=await api('/api/portfolio');renderProjects();renderDashboard();renderTasks();renderAgents();
}
function renderProjects(){
  $('projects').replaceChildren();
  for(const p of state.portfolio.projects){
    if(p.status==='archived')continue;
    const b=element('button',undefined,'project-button'+(state.project===p.id?' selected':''));
    b.append(element('span',p.status==='paused'?'Ⅱ':'◇'),element('span',p.name));
    b.onclick=action(()=>selectProject(p.id));$('projects').append(b);
  }
  $('all-projects').classList.toggle('selected',!state.project);
}
async function selectProject(id){
  const selection=++state.selection;state.project=id;state.source?.close();state.source=null;state.execution=null;
  $('stop-chat').hidden=true;$('send').disabled=false;renderProjects();
  const p=state.portfolio.projects.find(p=>p.id===id);
  $('context-title').textContent=p?.name||'All Projects';$('breadcrumb').textContent=p?.name.toUpperCase()||'OVERVIEW';
  $('context-hint').textContent=p?`${p.name} in context`:'All projects in context';
  $('history-search').value='';
  state.detail=id?await api(`/api/projects/${id}`):null;
  if(selection!==state.selection)return;
  renderDashboard();renderTasks();renderAgents();renderMemory();await loadHistory(selection);
}
function filteredTasks(){return state.portfolio.tasks.filter(t=>!state.project||t.project_id===state.project);}
function filteredExecutions(){return state.portfolio.executions.filter(e=>!state.project||e.project_id===state.project);}
function projectName(id){return state.portfolio.projects.find(p=>p.id===id)?.name||'All Projects';}
function renderDashboard(){
  const projects=state.portfolio.projects.filter(p=>!state.project||p.id===state.project),tasks=filteredTasks(),executions=filteredExecutions();
  $('stats').replaceChildren();
  const values=[[projects.length,'Projects in view'],[executions.filter(e=>!e.finished_at).length,'Active responses / workers'],[tasks.filter(t=>t.status==='pending').length,'Tasks ready to plan'],[tasks.filter(t=>['blocked','awaiting_approval'].includes(t.status)).length,'Need your attention']];
  for(const [value,label] of values){const card=element('article',undefined,'stat');card.append(element('strong',String(value)),element('span',label));$('stats').append(card);}
  $('project-cards').replaceChildren();
  for(const p of projects){
    const card=element('article',undefined,'project-card');card.append(element('h3',p.name),element('p',p.objective||p.description||'Set an objective to give this project a clear direction.'));
    const footer=element('div',undefined,'card-footer');footer.append(element('span',p.status,'badge'),element('span',`Priority ${p.priority}`));
    const controls=element('div');const open=element('button','Open');open.onclick=action(()=>selectProject(p.id));const edit=element('button','Edit');edit.onclick=()=>editProject(p);controls.append(open,edit);card.append(footer,controls);$('project-cards').append(card);
  }
  $('recommendation').textContent=state.portfolio.recommendation;
  $('accomplishments').replaceChildren();
  const checkpoints=state.portfolio.checkpoints.filter(c=>!state.project||c.project_id===state.project);
  if(!checkpoints.length)$('accomplishments').append(element('p','Verified checkpoints will appear here.','empty'));
  for(const c of checkpoints)$('accomplishments').append(element('p',`${projectName(c.project_id)} — ${c.summary}`));
}
function renderTasks(){
  const tasks=filteredTasks();$('task-count').textContent=tasks.filter(t=>!['completed','cancelled'].includes(t.status)).length;$('task-list').replaceChildren();
  if(!tasks.length)$('task-list').append(element('p','No queued tasks. Add a concrete goal and acceptance criteria.','empty'));
  for(const t of tasks){const row=element('article',undefined,'task-row');row.append(element('h3',t.description));const meta=element('div',undefined,'row-meta');meta.append(element('span',t.status,`badge ${t.status}`),element('span',projectName(t.project_id)),element('span',`Priority ${t.priority}`),element('span',`#${t.id.slice(0,8)}`));row.append(meta,element('p',`Acceptance: ${t.acceptance}`));if(t.blocker)row.append(element('p',t.blocker));$('task-list').append(row);}
}
function renderAgents(){
  $('agent-list').replaceChildren();const executions=filteredExecutions();
  if(!executions.length)$('agent-list').append(element('p','No execution history yet. Start a conversation to connect Codex.','empty'));
  for(const e of executions){const row=element('article',undefined,'agent-row');row.append(element('h3',`${e.agent_id} · ${projectName(e.project_id)}`));const meta=element('div',undefined,'row-meta');meta.append(element('span',e.status,`badge ${e.status}`),element('span',`${e.model||'Unassigned'} / ${e.effort||'default'}`),element('span',new Date(e.started_at*1000).toLocaleString()),element('span',e.finished_at?`${Math.round((e.finished_at-e.started_at))} seconds`:'Running'));row.append(meta);
    const details=element('details');details.append(element('summary','Output and execution details'),element('pre',e.output||e.error||'Waiting for output…'),element('small',`Thread: ${e.thread_id||'pending'} · Turn: ${e.turn_id||'pending'}`));row.append(details);
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
  source.onerror=()=>{if(state.source===source)$('status-label').textContent='Reconnecting to saved stream…';};
}
function showView(view){state.view=view;document.querySelectorAll('.view').forEach(node=>node.hidden=node.id!==`view-${view}`);document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.view===view));}
function editor(title,fields,onSave){
  $('editor-title').textContent=title;$('editor-fields').replaceChildren();$('editor-error').textContent='';
  for(const f of fields){const label=element('label',f.label);let control;if(f.options){control=element('select');for(const [value,text] of f.options){const option=element('option',text);option.value=value;control.append(option);}}else{control=element(f.long?'textarea':'input');if(f.long)control.rows=f.rows||4;else control.type=f.type||'text';}control.name=f.name;control.value=f.value??'';control.required=!!f.required;if(f.min!==undefined)control.min=f.min;if(f.max!==undefined)control.max=f.max;label.append(control);$('editor-fields').append(label);}
  $('editor-form').onsubmit=async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));try{await onSave(values);$('editor').close();await refresh();await selectProject(state.project);}catch(error){$('editor-error').textContent=error.message;}};$('editor').showModal();
}
function editProject(p){editor('Project settings',[{name:'name',label:'Name',value:p.name,required:true},{name:'objective',label:'Objective',value:p.objective,long:true},{name:'success_criteria',label:'Success criteria',value:p.success_criteria,long:true},{name:'instructions',label:'Project instructions',value:p.instructions,long:true},{name:'priority',label:'Priority',type:'number',value:p.priority,min:0,max:100},{name:'status',label:'Registry status (source files are preserved)',value:p.status,options:['active','paused','archived','removed'].map(x=>[x,x])}],async values=>{await api(`/api/projects/${p.id}`,{method:'PATCH',body:JSON.stringify({...values,priority:Number(values.priority),revision:p.revision})});if(values.status==='removed'&&state.project===p.id)state.project=null;});}
function editMemory(m){const project=state.project;editor(`Edit ${m.name}`,[{name:'content',label:'Human-readable project memory',value:m.content,long:true,rows:12}],values=>api(`/api/projects/${project}/memory/${m.name}`,{method:'PUT',body:JSON.stringify({...values,revision:m.revision})}));}
$('auth-form').onsubmit=async event=>{event.preventDefault();$('auth-error').textContent='';$('login-button').disabled=true;try{const credentials={username:$('username').value,password:$('password').value};if(state.setup){await api('/api/setup',{method:'POST',body:JSON.stringify({...credentials,token:$('setup-token').value})});state.setup=false;$('setup-token').value='';}const login=await api('/api/login',{method:'POST',body:JSON.stringify(credentials)});state.csrf=login.csrf;$('password').value='';await enter();}catch(error){$('auth-error').textContent=error.message;}finally{$('login-button').disabled=false;}};
$('chat-form').onsubmit=action(async event=>{event.preventDefault();const content=$('message-input').value.trim();if(!content)return;$('send').disabled=true;try{await api('/api/chat',{method:'POST',body:JSON.stringify({project_id:state.project,content})});$('message-input').value='';await loadHistory();}catch(error){$('send').disabled=false;throw error;}});
$('message-input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send').disabled)$('chat-form').requestSubmit();}};
$('all-projects').onclick=action(()=>selectProject(null));$('discover').onclick=action(async()=>{await api('/api/projects/discover',{method:'POST'});await refresh();notice('Project discovery complete.');});
$('logout').onclick=action(async()=>{await api('/api/logout',{method:'POST'});state.csrf=null;showAuth();});
$('revoke-sessions').onclick=action(async()=>{await api('/api/sessions/revoke',{method:'POST'});showAuth();});
$('stop-chat').onclick=action(async()=>{if(state.execution)await api(`/api/executions/${state.execution}/interrupt`,{method:'POST'});notice('Interruption requested.');});
$('settings-button').onclick=()=>showView('settings');document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>showView(b.dataset.view));
let searchTimer;$('history-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(action(()=>loadHistory()),250);};
$('editor-cancel').onclick=()=>$('editor').close();
$('add-project').onclick=()=>editor('Add an existing project',[{name:'path',label:'Directory inside a configured project root',required:true},{name:'name',label:'Name (optional)'}],values=>api('/api/projects',{method:'POST',body:JSON.stringify({...values,name:values.name||null})}));
$('add-task').onclick=()=>editor('Add a persistent task',[{name:'project_id',label:'Project',value:state.project,options:state.portfolio.projects.filter(p=>p.status!=='removed').map(p=>[p.id,p.name]),required:true},{name:'description',label:'What should be done?',long:true,required:true},{name:'acceptance',label:'How will we know it succeeded?',long:true,required:true},{name:'verification',label:'Independent verification requirements',long:true},{name:'priority',label:'Priority',type:'number',value:50,min:0,max:100},{name:'dependencies',label:'Dependency task IDs, separated by commas'}],values=>api('/api/tasks',{method:'POST',body:JSON.stringify({...values,priority:Number(values.priority),dependencies:values.dependencies.split(',').map(s=>s.trim()).filter(Boolean)})}));
$('instructions-form').onsubmit=action(async event=>{event.preventDefault();await api('/api/settings/instructions',{method:'PUT',body:JSON.stringify({content:$('global-instructions').value})});notice('Global instructions saved.');});
$('load-models').onclick=action(async()=>{const catalog=await api('/api/models');$('model-routes').replaceChildren();for(const [role,route] of Object.entries(catalog.routes)){const row=element('form',undefined,'route-row'),model=element('select'),effort=element('select');for(const m of catalog.models){const option=element('option',m.displayName);option.value=m.model;model.append(option);}model.value=route.model;function efforts(){effort.replaceChildren();const m=catalog.models.find(m=>m.model===model.value);for(const e of m.supportedReasoningEfforts.filter(e=>e.reasoningEffort!=='ultra')){const option=element('option',e.reasoningEffort);option.value=e.reasoningEffort;effort.append(option);}effort.value=route.effort;}efforts();model.onchange=efforts;model.setAttribute('aria-label',`${role} model`);effort.setAttribute('aria-label',`${role} reasoning effort`);const save=element('button','Save','primary');row.append(element('label',role),model,effort,save);row.onsubmit=action(async event=>{event.preventDefault();await api(`/api/models/${role}`,{method:'PUT',body:JSON.stringify({model:model.value,effort:effort.value})});notice(`${role} model settings saved.`);});$('model-routes').append(row);}});
setInterval(()=>{if(!$('app').hidden && ['dashboard','tasks','agents'].includes(state.view))refresh().catch(error=>notice(error.message));},10000);
boot().catch(error=>notice(error.message));
