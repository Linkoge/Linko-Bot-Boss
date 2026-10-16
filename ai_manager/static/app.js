'use strict';
const $ = id => document.getElementById(id);
// Reload starts at the loaded chat bottom; browser restoration must not race it.
if('scrollRestoration' in history)history.scrollRestoration='manual';
let initialChatPosition=true,chatPositionVersion=0;
function preserveChatPosition(render){
  const messages=$('messages'),top=messages.scrollTop;
  try{return render();}finally{messages.scrollTop=top;}
}
function cancelInitialChat(){initialChatPosition=false;chatPositionVersion++;}
async function positionInitialChat(selection){
  if(!initialChatPosition||!state.history||state.view!=='chat')return;
  const version=++chatPositionVersion,messages=$('messages');
  const current=()=>initialChatPosition&&version===chatPositionVersion&&selection===state.selection&&!$('app').hidden&&state.view==='chat';
  const bottom=()=>{if(current())messages.scrollTop=messages.scrollHeight;};
  // Position through fonts, images and viewport/composer sizing, then release
  // control. Routine updates retain the reader's deliberate position.
  const observer=new ResizeObserver(bottom);
  observer.observe(messages);for(const row of messages.children)observer.observe(row);
  messages.addEventListener('load',bottom,true);bottom();
  let deadline;
  try{
    await Promise.race([
      Promise.allSettled([document.fonts.ready,...[...messages.querySelectorAll('img')].map(img=>img.decode())]),
      new Promise(resolve=>{deadline=setTimeout(resolve,3000);})
    ]);
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    bottom();
    await new Promise(resolve=>setTimeout(resolve,150));
    bottom();
    if(current())initialChatPosition=false;
  }finally{clearTimeout(deadline);observer.disconnect();messages.removeEventListener('load',bottom,true);}
}
// A gesture wins even while fonts or attachment images are still pending.
for(const type of ['wheel','touchstart','pointerdown','keydown']){
  $('messages').addEventListener(type,cancelInitialChat,{passive:true});
}
$('chat-older').addEventListener('pointerdown',cancelInitialChat,{passive:true});
const draftRecovery=JSON.parse(sessionStorage.getItem('linko-drafts-v1')||'[]');
const appliedTranscripts=new Set(draftRecovery.transcripts||[]);
const state = {csrf:null, project:null, portfolio:null, detail:null, source:null, execution:null, view:'chat', setup:false, selection:0, drafts:new Map(draftRecovery.drafts||draftRecovery), pending:new Set()};
const submissionKey = 'linko-chat-submissions-v1';
const submissions = new Map(JSON.parse(sessionStorage.getItem(submissionKey)||'[]'));
// Settings intents and owner chat acceptance share one order, including background favorite recall.
let modelIntentQueue=Promise.resolve(),modelSelectionError=null;
function orderModelIntent(run){const next=modelIntentQueue.then(run);modelIntentQueue=next.catch(()=>{});return next;}
function submitWithModelSelection(submission){
  return orderModelIntent(()=>{
    if(modelSelectionError){const error=new Error('Message not sent: '+modelSelectionError+' Choose a Manager selection again before sending.');error.modelBlocked=true;throw error;}
    return api('/api/chat',{method:'POST',body:JSON.stringify(submission)});
  });
}
function saveDrafts(){try{sessionStorage.setItem('linko-drafts-v1',JSON.stringify({drafts:[...state.drafts],transcripts:[...appliedTranscripts]}));}catch{notice('Draft recovery storage unavailable. Keep this tab open.');}}
function saveSubmissions(){sessionStorage.setItem(submissionKey,JSON.stringify([...submissions]));}
const element = (tag, text, cls) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
function notice(text){$('notice-text').textContent=text;$('notice').hidden=!text;}
$('notice-dismiss').onclick=()=>notice('');
const updateFailures=new Map();
function renderUpdateStatus(){
  $('connection-status').textContent='Updates unavailable · Retrying…';
  $('connection-status').title=[...updateFailures.values()].join(' · ');
  $('connection-status').hidden=!updateFailures.size;
}
function updatesUnavailable(error,channel='refresh'){
  if($('app').hidden)return;
  updateFailures.set(channel,error.message);renderUpdateStatus();
}
function updatesRecovered(channel='refresh'){updateFailures.delete(channel);renderUpdateStatus();}

async function api(path, options={}){
  const headers={'Content-Type':'application/json',...(options.headers||{})};
  if(options.method && options.method!=='GET')headers['X-CSRF-Token']=state.csrf||'';
  const controller=new AbortController(),read=!options.method||options.method==='GET';
  const timeout=read?setTimeout(()=>controller.abort(),15000):null;
  let response,result;
  try{
    response=await fetch(path,{...options,headers,signal:options.signal||controller.signal});
    // Preserve HTTP rejection status even when a proxy returns an HTML error page.
    try{result=await response.json();}catch{result={detail:response.ok?'Invalid server response. Please retry.':`Request failed (${response.status}). Please retry.`};if(response.ok)throw new Error(result.detail);}
  }catch(error){if(error.name==='AbortError')throw new Error('Request timed out. Please retry.');if(error instanceof TypeError)throw new Error('Connection interrupted. Please retry.');throw error;}
  finally{if(timeout)clearTimeout(timeout);}
  if(!response.ok){if(response.status===401 && !path.includes('login'))showAuth();const error=new Error(result.detail||result.message||'Request failed');error.status=response.status;error.result=result.result||(response.status<500?'rejected':'failed');throw error;}
  return result;
}
function action(fn){return async event=>{const button=event?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(event);}catch(error){notice(error.ambiguous?error.message:'Rejected or failed: '+error.message);}finally{if(button?.tagName==='BUTTON')button.disabled=false;}};}
function actionResult(result){notice(`${result.result||'accepted'}: ${result.message||result.status||'Operation recorded'}`);}
function showAuth(){cancelInitialChat();initialChatPosition=true;$('agent-list').replaceChildren();if($('history-reader').open)$('history-reader').close();if($('model-dialog')?.open)$('model-dialog').close();closeChatMenu();audioChanged(true);updateFailures.clear();renderUpdateStatus();stopUsage();state.ready=false;state.selection++;state.source?.close();state.source=null;state.execution=null;clearTimeout(state.reconcileTimer);$('app').hidden=true;$('auth-screen').hidden=false;$('chat-notice').hidden=true;clearTimeout(state.chatNoticeTimer);}
async function boot(){const auth=await api('/api/auth-status');state.csrf=auth.csrf;state.setup=auth.setup_required;$('setup-label').hidden=!state.setup;$('setup-token').required=state.setup;if(auth.authenticated)await enter();else showAuth();}
async function enter(){startUsage();state.ready=false;const selection=state.selection;$('auth-screen').hidden=true;$('app').hidden=false;const settings=await api('/api/settings');$('global-instructions').value=settings.global_instructions;if(typeof setProviderIcon==='function')setProviderIcon(settings.model_provider);await refresh(false);if(selection!==state.selection){state.ready=true;return;}const initial=[null,...state.portfolio.projects.filter(p=>p.visible!==false)].sort((a,b)=>contextRecency(b)-contextRecency(a)||compareContexts(a,b))[0];await selectProject(initial?.id||null);await positionInitialChat(state.selection);state.ready=true;}
let refreshVersion=0;
async function refresh(loadChat=true){
  const version=++refreshVersion,selection=state.selection,project=state.project;
  const portfolio=await api('/api/portfolio'+(project?'?project_id='+encodeURIComponent(project):''));
  if(version!==refreshVersion||selection!==state.selection||project!==state.project||$('app').hidden)return false;
  preserveChatPosition(()=>{state.portfolio=portfolio;renderProjects();renderTasks();renderAgents();renderQuestions();});
  if(state.project&&portfolio.projects.find(p=>p.id===state.project)?.visible===false){await selectProject(null);return false;}
  if(portfolio.queue_error)notice(portfolio.queue_error);
  if(state.view==='todo')await loadTodos(selection);
  if(loadChat&&!state.source&&!state.pending.has(project))await loadHistory(selection);
  return selection===state.selection&&project===state.project&&version===refreshVersion;
}
function localTime(timestamp){
  if(timestamp===null||timestamp===undefined)return 'Not recorded';
  return new Intl.DateTimeFormat(undefined,{timeZone:'Asia/Tbilisi',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(timestamp*1000))+' · Tbilisi';
}
function timestamp(label,value){const time=element('time',label+' '+localTime(value),'record-time');if(value!==null&&value!==undefined)time.dateTime=new Date(value*1000).toISOString();return time;}

const displayName = p => p ? p.display_name || p.name : 'All Projects';
const compareContexts = (a,b) => displayName(a).localeCompare(displayName(b),undefined,{sensitivity:'base'})||(a?.id||'').localeCompare(b?.id||'');
const contextRecency = p => p ? p.last_used_at||0 : state.portfolio.global_last_used_at||0;
function projectLabel(p){
  const duplicates=state.portfolio.projects.filter(other=>displayName(other).toLocaleLowerCase()===displayName(p).toLocaleLowerCase());
  return displayName(p)+(duplicates.length>1?` · ${p.name} (${p.id.slice(0,6)})`:'');
}
function renderProjects(){
  const scroll=$('project-tabs').scrollLeft,focused=document.activeElement.closest('#projects button')?.dataset.project,globalButton=$('all-projects');
  $('projects').replaceChildren();
  for(const p of [null,...state.portfolio.projects.filter(p=>p.visible!==false)].sort((a,b)=>contextRecency(b)-contextRecency(a)||compareContexts(a,b))){
    const id=p?.id||null,b=p?element('button',projectLabel(p),'project-button'):globalButton;
    b.classList.toggle('selected',state.project===id);b.title=p?p.path:'Global manager conversation and portfolio';
    if(p?.github_backup?.state==='needs-attention'){
      b.append(element('span',' · GitHub needs attention','muted'));
      b.title+=`\nGitHub backup: ${p.github_backup.reason}. Local commits retained; retrying automatically.`;
    }
    b.dataset.project=id||'';b.setAttribute('aria-pressed',String(state.project===id));
    b.onclick=action(()=>selectProject(id));$('projects').append(b);
  }
  if(focused!==undefined)$('projects').querySelector(`[data-project="${focused}"]`)?.focus({preventScroll:true});
  $('project-tabs').scrollLeft=scroll;
  $('all-projects').classList.toggle('selected',!state.project);$('all-projects').setAttribute('aria-pressed',String(!state.project));
  if(!$('project-menu').hidden)renderProjectMenu();
  renderProjectAvailability();
}
function closeProjectMenu(focus=false){$('project-menu').closest('.navigation').classList.remove('project-menu-open');$('project-menu').hidden=true;$('project-menu-button').setAttribute('aria-expanded','false');if(focus)$('project-menu-button').focus();}
let stashedProjectsOpen=false,renderingProjectMenu=false;
const visibilityPending=new Set();
function renderProjectAvailability(){
  for(const c of $('project-menu').querySelectorAll('input'))c.disabled=visibilityPending.has(c.dataset.visibility);
}
// Recording controls can expand the sticky navigation; keep notices below it.
if(window.ResizeObserver)new ResizeObserver(entries=>{
  document.documentElement.style.setProperty('--nav-height',`${entries[0].target.getBoundingClientRect().height}px`);
}).observe(document.querySelector('.navigation'));
async function setProjectVisibility(p,visible){
  const restoreFocus=$('project-menu').contains(document.activeElement);
  visibilityPending.add(p.id);renderProjectAvailability();renderAudio();
  try{
    const updated=await api(`/api/projects/${p.id}/visibility`,{method:'PUT',body:JSON.stringify({visible})});
    ++refreshVersion;
    const current=state.portfolio.projects.find(p=>p.id===updated.id);if(current)current.visible=updated.visible;
    if(!visible&&state.project===p.id)await selectProject(null);
    else renderProjects();
  }finally{visibilityPending.delete(p.id);renderProjectMenu();renderAudio();
    if(restoreFocus&&!$('project-menu').hidden&&document.activeElement===document.body){const target=$('project-menu').querySelector(`[data-visibility="${p.id}"]`);(target?.closest('details:not([open])')?.querySelector('summary')||target||$('project-menu-button')).focus({preventScroll:true});}
  }
}
function renderProjectMenu(){
  const menu=$('project-menu'),active=menu.contains(document.activeElement)?document.activeElement:null;
  const focusKey=active?.dataset.visibility,focused=active?.dataset.project,summaryFocused=active?.tagName==='SUMMARY',scroll=menu.scrollTop;renderingProjectMenu=true;menu.replaceChildren();
  const global=element('button','◈ All Projects · global context',!state.project?'selected':'');
  global.dataset.project='';global.setAttribute('aria-pressed',String(!state.project));
  global.onclick=action(async()=>{closeProjectMenu(true);await selectProject(null);});
  const addRow=(p,parent,stashed)=>{
    const row=element('div',undefined,'project-menu-row'),checkbox=element('input');checkbox.type='checkbox';checkbox.checked=!stashed;
    checkbox.dataset.visibility=p.id;checkbox.setAttribute('aria-label',`Show ${projectLabel(p)} in navigation`);
    checkbox.onchange=action(()=>setProjectVisibility(p,checkbox.checked));row.append(checkbox);
    if(stashed)row.append(element('span',projectLabel(p)));
    else{const button=element('button',projectLabel(p),state.project===p.id?'selected':'');button.dataset.project=p.id;button.setAttribute('aria-pressed',String(state.project===p.id));
      button.onclick=action(async()=>{closeProjectMenu(true);await selectProject(p.id);});row.append(button);}
    parent.append(row);
  };
  const sorted=[...state.portfolio.projects].sort(compareContexts);
  for(const p of [null,...sorted.filter(p=>p.visible!==false)].sort(compareContexts)){if(p)addRow(p,menu,false);else menu.append(global);}
  const stashed=sorted.filter(p=>p.visible===false);
  if(stashed.length){const details=element('details'),summary=element('summary',`Stashed projects (${stashed.length})`);details.open=stashedProjectsOpen;
    details.ontoggle=()=>{stashedProjectsOpen=details.open;};details.append(summary);for(const p of stashed)addRow(p,details,true);menu.append(details);}
  if(state.project){const rename=element('button','Rename selected project…');rename.dataset.project='rename';rename.onclick=()=>{closeProjectMenu();renameProject();};menu.append(rename);}
  renderProjectAvailability();
  if(focusKey!==undefined){const target=menu.querySelector(`[data-visibility="${focusKey}"]`);(target?.closest('details:not([open])')?.querySelector('summary')||target)?.focus({preventScroll:true});}
  else if(focused!==undefined)menu.querySelector(`[data-project="${focused}"]`)?.focus({preventScroll:true});
  else if(summaryFocused)menu.querySelector('summary')?.focus({preventScroll:true});
  menu.scrollTop=scroll;renderingProjectMenu=false;
}
$('project-menu-button').onclick=()=>{closeChatMenu();if(!$('project-menu').hidden){closeProjectMenu(true);return;}renderProjectMenu();$('project-menu').hidden=false;$('project-menu').closest('.navigation').classList.add('project-menu-open');$('project-menu-button').setAttribute('aria-expanded','true');$('project-menu').querySelector('[aria-pressed="true"]')?.focus();};
$('project-menu').onkeydown=event=>{
  const controls=[...$('project-menu').querySelectorAll('button:not(:disabled),input:not(:disabled),summary')].filter(e=>e.getClientRects().length),index=controls.indexOf(document.activeElement);
  if(event.key==='Escape'){event.preventDefault();closeProjectMenu(true);}
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();controls[event.key==='Home'?0:event.key==='End'?controls.length-1:(index+(event.key==='ArrowDown'?1:-1)+controls.length)%controls.length]?.focus();}
};
$('project-menu').addEventListener('focusout',event=>{if(!renderingProjectMenu&&event.relatedTarget&&!$('project-menu').contains(event.relatedTarget)&&event.relatedTarget!==$('project-menu-button'))closeProjectMenu();});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#project-menu')&&!event.target.closest('#project-menu-button'))closeProjectMenu();});
function closeChatMenu(focus=false){$('chat-menu').closest('.navigation').classList.remove('chat-menu-open');$('chat-menu').hidden=true;$('chat-menu-button').setAttribute('aria-expanded','false');if(focus)$('chat-menu-button').focus({preventScroll:true});}
function openChatMenu(last=false){closeProjectMenu();$('chat-menu').hidden=false;$('chat-menu').closest('.navigation').classList.add('chat-menu-open');$('chat-menu-button').setAttribute('aria-expanded','true');const buttons=chatMenuButtons();buttons[last?buttons.length-1:0]?.focus();}
function chatMenuButtons(){return [...$('chat-menu').querySelectorAll('button:not(:disabled)')];}
$('chat-menu-button').onclick=()=>{$('chat-menu').hidden?openChatMenu():closeChatMenu(true);};
$('chat-menu-button').onkeydown=event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();openChatMenu(event.key==='ArrowUp');}};
$('chat-menu').onkeydown=event=>{
  const buttons=chatMenuButtons(),index=buttons.indexOf(document.activeElement);
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeChatMenu(true);}
  if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}
  if(event.key==='Tab')closeChatMenu(true);
};
$('chat-menu').addEventListener('focusout',event=>{if(!$('chat-menu').contains(event.relatedTarget)&&event.relatedTarget!==$('chat-menu-button'))closeChatMenu();});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#chat-menu')&&!event.target.closest('#chat-menu-button'))closeChatMenu();});
async function selectProject(id){
  if(id&&state.portfolio?.projects.find(p=>p.id===id)?.visible===false)id=null;
  closeChatMenu();
  if(id!==state.project)audioChanged();
  $('agent-list').replaceChildren();
  if(state.draftContextReady){state.drafts.set(state.project,$('message-input').value);saveDrafts();}
  const selection=++state.selection;
  cancelInitialChat();initialChatPosition=true;
  state.todoSelection=new Set();state.todos=[];renderTodos(true);
  state.project=id;state.detail=null;state.brief=null;clearTimeout(state.memoryTimer);renderMemory();renderMemoryReview();state.source?.close();state.source=null;
  state.execution=null;state.history=null;$('messages').replaceChildren();updateChatActions();
  $('message-input').value=state.drafts.get(id)||submissions.get(id)?.content||'';state.draftContextReady=true;
  $('stop-chat').hidden=true;$('send').disabled=state.pending.has(id);renderAttachments();renderAudio();
  state.portfolio={...state.portfolio,tasks:[],executions:[],questions:[],active_agents:0};
  renderProjects();renderTasks();renderAgents();renderQuestions();
  await refresh(false);
  if(selection!==state.selection)return;
  if(state.view==='memory')await loadMemory(selection);
  if(state.view==='search')await searchHistory();
  await loadHistory(selection);
  if(selection===state.selection&&submissions.has(id))await sendSubmission();
}

function projectName(id){const p=state.portfolio?.projects.find(p=>p.id===id);return p?displayName(p):'All Projects';}
async function loadMemory(selection=state.selection){if(!state.project){renderMemory();renderMemoryReview();return;}const project=state.project;const [detail,brief]=await Promise.all([api(`/api/projects/${project}`),api(`/api/projects/${project}/brief`)]);if(selection!==state.selection||project!==state.project)return;state.detail=detail;state.brief=brief;renderMemory();renderMemoryReview();}
function renderMemory(){$('memory-list').replaceChildren();if(!state.detail){$('memory-list').append(element('p','Select a project to read historical owner notes.'));return;}for(const m of state.detail.memory){const d=element('details');d.append(element('summary',m.name),element('pre',m.content));$('memory-list').append(d);}if(!state.detail.memory.length)$('memory-list').append(element('p','Project context comes from its repository and instructions.'));}
function recoveryLabel(record){const reasons={connection:'Waiting for connection',interrupted:'Recovering interrupted execution'};return (reasons[record.recovery_reason]||'Waiting')+(record.retry_at?' · Next attempt '+new Date(record.retry_at*1000).toLocaleString():'');}
function taskControlButton(task,label,command){
  const selection=state.selection,button=element('button',label);
  button.onclick=action(async()=>{
    if(selection!==state.selection)return;
    button.disabled=true;
    try{await api(`/api/tasks/${task.id}`,{method:'PATCH',body:JSON.stringify({action:command,revision:task.revision})});if(selection===state.selection)await refresh();}
    finally{button.disabled=false;}
  });
  return button;
}
function appendTaskControls(controls,t){
  if(t.status==='running')controls.append(taskControlButton(t,'Cancel','cancel'));
  else if(t.status==='queued')controls.append(taskControlButton(t,'Hold','hold'),taskControlButton(t,'Cancel','cancel'));
  else if(t.status==='held')controls.append(taskControlButton(t,'Release','release'),taskControlButton(t,'Cancel','cancel'));
  else if(t.status==='failed')controls.append(taskControlButton(t,'Continue','continue'));
}
function taskStateLabel(status){return ({held:'Held · skipped until released',done:'Done',failed:'Failed'})[status]||status;}
function renderTasks(){
  const tasks=state.portfolio.tasks.filter(t=>!state.project||t.project_id===state.project);
  const count=tasks.filter(t=>['queued','running','held'].includes(t.status)).length;
  $('task-count').textContent=count;$('task-count').hidden=count===0;$('task-list').replaceChildren();
  for(const t of tasks){
    const row=element('article',undefined,'panel');row.dataset.taskId=t.id;
    row.append(element('h3',`${taskStateLabel(t.status)} · ${projectName(t.project_id)}`),element('p',t.description),element('p',t.blocker));
    if(t.rounds)row.append(element('small',`Repair rounds ${t.rounds} of ${t.repair_limit}`));
    const controls=element('div',undefined,'task-controls');controls.append(timestamp('Created',t.created_at));appendTaskControls(controls,t);
    if(t.result_summary)row.append(element('p',t.result_summary,'result-summary'));
    const records=element('button','History');records.onclick=action(()=>readHistory('task',t.id));
    controls.append(records);row.append(controls);$('task-list').append(row);
  }
  updateComposer();
}
function renderAgents(){
  const count=state.portfolio.active_agents||0,badge=$('agent-count'),scope=projectName(state.project);badge.textContent=count;badge.hidden=count===0;badge.title=`${count} working · ${scope}`;
  document.querySelector('[data-view=agents]').setAttribute('aria-label',`Agents · ${scope}${count?' · '+count+' working':''}`);
  const list=$('agent-list'),top=window.scrollY,focused=document.activeElement;
  const selection=window.getSelection(),selected=selection?.rangeCount&&list.contains(selection.anchorNode)&&list.contains(selection.focusNode)?{anchor:selection.anchorNode,start:selection.anchorOffset,focus:selection.focusNode,end:selection.focusOffset}:null;
  const reading=[...list.querySelectorAll('details[open] pre')].find(node=>{const box=node.getBoundingClientRect();return box.bottom>0&&box.top<innerHeight;});
  const readingY=reading?.getBoundingClientRect().top;
  const existing=new Map([...list.children].map(row=>[row.dataset.executionId,row]));
  const visible=state.portfolio.executions.filter(e=>!state.project||e.project_id===state.project);
  const ids=new Set(visible.map(e=>e.id));
  // Retain an expanded result even when it rolls out of the bounded server list.
  for(const [id,row] of existing)if(!ids.has(id)&&!row.querySelector('details').open)row.remove();
  for(const [index,e] of visible.entries()){
    let row=existing.get(e.id);
    if(!row){row=element('article',undefined,'panel');row.dataset.executionId=e.id;row.append(element('div',undefined,'agent-metadata'));const details=element('details');details.append(element('summary','Output and execution references'),element('pre',undefined,'agent-output'));row.append(details,element('div',undefined,'agent-controls'));const next=visible.slice(index+1).map(item=>existing.get(item.id)).find(Boolean);list.insertBefore(row,next||null);}
    const metadata=row.querySelector('.agent-metadata'),controls=row.querySelector('.agent-controls');metadata.replaceChildren(element('h3',`${e.agent_id} · ${taskStateLabel(e.status)} · ${projectName(e.project_id)}`));controls.replaceChildren();
    metadata.append(e.work_started_at!=null?timestamp('Work started',e.work_started_at):element('small',['queued','starting'].includes(e.status)?'Work has not started':'Work start not recorded'));metadata.append(timestamp('Queued',e.started_at));
    const output=row.querySelector('pre'),text=[e.output,e.error,e.thread_id,e.turn_id].filter(Boolean).join('\n');
    if(chatHasSelection(output))output._pendingAgentOutput=text;
    else if(output.textContent!==text){const scroll=output.scrollTop,left=output.scrollLeft;output.textContent=text;output.scrollTop=scroll;output.scrollLeft=left;delete output._pendingAgentOutput;}
    const history=element('button','History and full output');history.onclick=action(()=>readHistory('worker',e.id));controls.append(history);
    const task=state.portfolio.tasks.find(t=>t.id===e.task_id&&t.lease_owner===e.id);
    if(!e.finished_at&&task){const controls=element('div',undefined,'task-controls');appendTaskControls(controls,task);row.querySelector('.agent-controls').append(controls);}
    else if(!e.finished_at&&!e.task_id){const selection=state.selection,b=element('button','Stop reply');b.onclick=action(async()=>{if(selection!==state.selection)return;await api(`/api/executions/${e.id}/interrupt`,{method:'POST'});if(selection===state.selection)await refresh();});controls.append(b);}
  }
  // Keep the established active-first server order as statuses change. Native
  // moveBefore preserves interaction state; fallback restores output selection/focus.
  const rendered=new Map([...list.children].map(row=>[row.dataset.executionId,row]));
  const outputPositions=[...list.querySelectorAll('pre')].map(node=>({node,top:node.scrollTop,left:node.scrollLeft}));
  for(const [index,e] of visible.entries()){const row=rendered.get(e.id),before=list.children[index];if(row!==before){if(typeof list.moveBefore==='function')list.moveBefore(row,before||null);else list.insertBefore(row,before||null);}}
  for(const position of outputPositions){position.node.scrollTop=position.top;position.node.scrollLeft=position.left;}
  if(selected?.anchor.isConnected&&selected.focus.isConnected)selection.setBaseAndExtent(selected.anchor,selected.start,selected.focus,selected.end);
  if(focused!==document.activeElement&&list.contains(focused))focused.focus({preventScroll:true});
  if(state.view==='agents')window.scrollTo({top:reading?.isConnected?window.scrollY+reading.getBoundingClientRect().top-readingY:top,behavior:'instant'});
}
document.addEventListener('selectionchange',()=>{for(const output of document.querySelectorAll('.agent-output')){if(output._pendingAgentOutput!==undefined&&!chatHasSelection(output)){const top=output.scrollTop,left=output.scrollLeft;output.textContent=output._pendingAgentOutput;delete output._pendingAgentOutput;output.scrollTop=top;output.scrollLeft=left;}}});

function renderQuestions(){$('questions').replaceChildren();for(const q of state.portfolio.questions.filter(q=>!state.project||q.project_id===state.project)){const request=JSON.parse(q.request),form=element('form',undefined,'panel');form.append(element('h3','Codex needs your answer · '+projectName(q.project_id)));if(q.kind==='item/tool/requestUserInput'){for(const question of request.questions){const label=element('label',question.question),input=element('input');input.name=question.id;input.required=true;if(question.options)label.append(element('small',question.options.map(x=>x.label).join(' / ')));label.append(input);form.append(label);}form.append(element('button','Send answer','primary'));form.onsubmit=action(async event=>{event.preventDefault();await api(`/api/questions/${q.id}/answer`,{method:'POST',body:JSON.stringify({answers:Object.fromEntries(new FormData(form))})});await refresh();});}else{form.append(element('pre',JSON.stringify(request,null,2)));for(const [label,approve] of [['Confirm exact action',true],['Decline',false]]){const b=element('button',label);b.type='button';b.onclick=action(async()=>{await api(`/api/questions/${q.id}/answer`,{method:'POST',body:JSON.stringify({answers:{approve}})});await refresh();});form.append(b);}}$('questions').append(form);}}
async function loadHistory(selection=state.selection,before=null){
  if(selection!==state.selection)return;
  if(before)cancelInitialChat();
  const query=new URLSearchParams();if(state.project)query.set('project_id',state.project);if(before)query.set('before',before);
  if(!before){state.source?.close();state.source=null;}
  const request=state.loadHistoryRequest=(state.loadHistoryRequest||0)+1;
  const history=await api(`/api/history?${query}`);if(selection!==state.selection||request!==state.loadHistoryRequest)return;
  const messages=$('messages'),fresh=state.history?.conversation.id!==history.conversation.id;
  const previousTop=messages.scrollTop,previousHeight=messages.scrollHeight;
  history.messages=history.messages.filter(m=>!history.deleted_message_ids?.includes(m.id));
  if(!before)state.history=history;
  if(fresh||before||!state.olderLoaded)state.olderHasMore=history.has_more;
  if(fresh)state.olderLoaded=false;
  if(before)state.olderLoaded=true;
  $('chat-older').hidden=!state.olderHasMore;updateChatActions();
  if(fresh||(!before&&!history.messages.length))messages.replaceChildren();
  const olderAnchor=before?messages.firstElementChild:null;
  const rows=new Map([...messages.querySelectorAll('[data-message]')].map(row=>[row.dataset.message,row]));
  const ids=new Set(history.messages.map(m=>m.id));
  // /api/history is a rolling 50-message tail, not a deletion list. Keep
  // already loaded older rows until the conversation changes or is cleared.
  for(const [id,row] of rows)if((fresh&&!ids.has(id))||history.deleted_message_ids?.includes(id))row.remove();
  for(const m of history.messages){
    let row=rows.get(m.id);
    if(!row){row=element('article',undefined,`message ${m.role}`);row.setAttribute('aria-label',messageAuthor(m));const body=element('div'),label=element('div',undefined,'message-label');label.append(element('span',undefined,'badge'));body.append(label,element('div',messageText(m),'message-content'));row.append(body);row.dataset.message=m.id;messages.insertBefore(row,olderAnchor);}
    // Reply/stream completion does not establish requested-task acceptance or deployment.
    const badge=row.querySelector('.badge'),label=row.querySelector('.message-label');
    label.hidden=m.status==='completed';
    badge.textContent=m.status==='waiting'&&m.retry_at?recoveryLabel(m):m.status;
    badge.className=`badge ${m.status==='completed'?'':m.status}`;
    updateMessageContent(row.querySelector('.message-content'),messageText(m),m.role);renderMessageAttachments(row,m);renderMessageActions(row,m,history.conversation.id);

  }
  if(before){messages.scrollTop=previousTop+messages.scrollHeight-previousHeight;return;}
  if(!fresh)messages.scrollTop=previousTop;
  if(fresh){initialChatPosition=true;positionInitialChat(selection);}
  const active=history.messages.find(m=>m.role==='assistant'&&m.execution_id&&m.execution_status&&m.finished_at===null);
  state.execution=active?.execution_id||null;
  $('stop-chat').hidden=!active;$('send').disabled=state.pending.has(state.project);
  $('status-label').textContent=active?(active.status==='waiting'?recoveryLabel(active):active.status==='queued'?'Queued':'Thinking / responding'):'Ready to help';
  if(active)attachStream(active.execution_id,active.id,active.stream_cursor);
  updateChatActions();
}
function renderMessageActions(row,message,conversation){
  if(!['user','assistant'].includes(message.role))return;
  row._wholeMessage=message;
  let controls=row.querySelector('.message-actions');
  if(!controls){
    controls=element('details',undefined,'message-actions');
    const toggle=element('summary','⌄');toggle.setAttribute('aria-label','Message actions');toggle.title='Message actions';
    const buttons=element('div',undefined,'message-action-buttons'),copy=element('button','Copy'),remove=element('button','Delete');
    copy.type=remove.type='button';remove.className='message-delete';
    copy.onclick=action(async()=>{await copyText(row._wholeMessage.content);controls.open=false;});
    remove.onclick=()=>confirmMessageDelete(row._wholeMessage,conversation);
    buttons.append(copy,remove);controls.append(toggle,buttons);row.append(controls);
  }
  if(row.lastElementChild!==controls)row.append(controls);
  const m=row._wholeMessage;
  controls.querySelector('.message-delete').disabled=['queued','streaming','waiting','processing'].includes(m.status)||(!!m.execution_id&&m.finished_at===null);
}
function confirmMessageDelete(message,conversation){
  const project=state.project,selection=state.selection;
  $('chat-confirm-title').textContent='Delete this message?';
  $('chat-confirm-description').textContent='Remove this entire '+(message.role==='assistant'?'assistant response':'message')+' from chat and history? Internal task and recovery evidence is retained.';
  $('chat-archive-label').hidden=true;$('chat-confirm-submit').textContent='Delete message';
  $('chat-confirm-submit').className='danger';$('chat-confirm-error').textContent='';
  $('chat-confirm-form').onsubmit=async event=>{
    event.preventDefault();const button=$('chat-confirm-submit');if(button.disabled)return;button.disabled=true;
    try{
      if(selection!==state.selection)throw new Error('Context changed. Select the message and confirm again.');
      await api('/api/chat/message/delete',{method:'POST',body:JSON.stringify({project_id:project,conversation_id:conversation,message_id:message.id,confirmed:true})});
      $('chat-confirm').close();
      if(selection===state.selection){
        for(const container of [$('messages'),$('history-detail-messages')])for(const row of container.querySelectorAll('[data-message]'))if(row.dataset.message===message.id)row.remove();
        if(state.savedHistory?.conversation.id===conversation)state.savedHistory.messages=state.savedHistory.messages.filter(m=>m.id!==message.id);
        await loadHistory(selection);
      }
    }catch(error){$('chat-confirm-error').textContent=error.message;}finally{button.disabled=false;}
  };
  $('chat-confirm').showModal();
}
function messageText(m){if(m.attachments?.length&&!m.content)return '';return m.content||(m.status==='failed'?(m.execution_error||'Response failed.'):m.status==='interrupted'?'Stopped.':m.status==='completed'?'No text response.':m.status==='waiting'?recoveryLabel(m):m.status==='queued'?'Queued':'Thinking…');}
function messageAuthor(message){return message.role==='user'?'You':message.role==='worker'?'Bot result':'Bot Boss';}
function chatHasSelection(node=$('messages')){const selection=window.getSelection();return !!selection&&!selection.isCollapsed&&selection.containsNode(node,true);}
function updateMessageContent(node,text,role='assistant'){
  const markdown=role==='assistant'||role==='worker';
  if(node._messageSource===text&&node.classList.contains('markdown')===markdown){delete node._pendingMessage;return;}
  if(chatHasSelection(node)){node._pendingMessage={text,role};return;}
  delete node._pendingMessage;
  if(markdown)preserveChatPosition(()=>node.replaceChildren(renderChatMarkdown(text)));
  else if(node.firstChild?.nodeType===Node.TEXT_NODE&&text.startsWith(node.textContent))node.firstChild.appendData(text.slice(node.textContent.length));
  else node.textContent=text;
  node.classList.toggle('markdown',markdown);node._messageSource=text;
}
// Rendering waits while text is selected; apply the latest authoritative source
// after selection clears, including a final history refresh that arrived earlier.
document.addEventListener('selectionchange',()=>{
  for(const node of document.querySelectorAll('.message-content')){
    const pending=node._pendingMessage;if(pending&&!chatHasSelection(node))updateMessageContent(node,pending.text,pending.role);
  }
});
function attachStream(execution,messageId,cursor=0){
  state.source?.close();state.execution=execution;
  const selection=state.selection,conversation=state.history?.conversation.id;
  const source=new EventSource(`/api/executions/${execution}/events?after=${cursor}`);state.source=source;
  const current=()=>state.source===source&&selection===state.selection&&conversation===state.history?.conversation.id&&!$('app').hidden;
  const reconcile=()=>{if(!current())return;source.close();state.source=null;loadHistory(selection).then(()=>{if(selection===state.selection)updatesRecovered('history');}).catch(error=>{if(selection===state.selection)updatesUnavailable(error,'history');});};
  source.addEventListener('chat.delta',event=>{
    if(!current()||Number(event.lastEventId)<=cursor)return;cursor=Number(event.lastEventId);
    const data=JSON.parse(event.data),message=state.history?.messages.find(m=>m.id===messageId);
    if(!message)return;message.content=data.reset?data.delta:message.content+data.delta;
    const content=document.querySelector(`[data-message="${messageId}"] .message-content`);
    if(content)updateMessageContent(content,message.content,message.role);
  });
  source.addEventListener('chat.started',reconcile);
  source.addEventListener('recovery.scheduled',reconcile);
  source.addEventListener('chat.finished',reconcile);
  source.addEventListener('stream.end',reconcile);
  source.addEventListener('stream.auth_expired',()=>{if(current())showAuth();});
  source.onopen=()=>{if(current())clearTimeout(state.reconcileTimer);};
  source.onerror=()=>{
    if(!current())return;$('status-label').textContent='Checking saved response…';
    clearTimeout(state.reconcileTimer);state.reconcileTimer=setTimeout(()=>{if(current())reconcile();},1000);
  };
}
function updateChatActions(){
  const disabled=!state.history?.messages.length||state.pending.has(state.project)||!!state.execution||state.history.messages.some(m=>['queued','streaming','waiting'].includes(m.status));
  for(const id of ['archive-chat','clear-chat'])$(id).disabled=disabled;
  updateChatTrashAvailability();
  for(const id of ['copy-all','copy-five'])$(id).disabled=!state.history?.messages.length||!!state.copyingChat;
  renderAttachments();
}
function updateChatTrashAvailability(){
  const changing=state.pending.has(state.project)||submissions.has(state.project)||!!state.execution||!!audioState.session;
  const files=attachmentDraft(state.project).length||attachmentState.uploads.has(state.project);
  $('restore-trash').disabled=!state.history?.trash||changing||!!files;
  $('restore-trash').title=files?'Send or remove draft attachments before restoring a different chat':'Restore cleared chat; swaps with a nonempty current chat';
  $('empty-trash').disabled=!state.history?.trash||changing;
}
async function copyChat(all){
  const history=state.history;if(!history?.messages.length||state.copyingChat)return;
  const latest=history.messages.map(m=>({...m}));
  state.copyingChat=true;updateChatActions();
  try{
    let messages=latest.slice(-5);
    if(all){
      const query=new URLSearchParams();if(history.conversation.project_id)query.set('project_id',history.conversation.project_id);
      const pages=[];let page;
      do{page=await api(`/api/chat-history/${encodeURIComponent(history.conversation.id)}?${query}`);pages.push(page.messages);if(page.has_more)query.set('before',page.messages[0].id);}while(page.has_more);
      messages=pages.reverse().flat();
      const streaming=new Map(latest.filter(m=>['queued','streaming','waiting'].includes(m.status)).map(m=>[m.id,m]));
      messages=messages.map(m=>{const live=streaming.get(m.id);return live&&live.content.length>m.content.length?live:m;});
    }
    await copyText(messages.map(m=>`${messageAuthor(m)}:\n${m.content}`).join('\n\n'));
    $('chat-notice').textContent=all?'Copied the whole chat.':`Copied the latest ${messages.length} message${messages.length===1?'':'s'}.`;
    $('chat-notice').hidden=false;clearTimeout(state.chatNoticeTimer);state.chatNoticeTimer=setTimeout(()=>$('chat-notice').hidden=true,4000);
  }finally{state.copyingChat=false;updateChatActions();}
}
async function copyText(text){
  try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);return;}}catch{}
  const selection=window.getSelection(),ranges=Array.from({length:selection.rangeCount},(_,i)=>selection.getRangeAt(i).cloneRange()),focused=document.activeElement;
  const input=element('textarea');input.value=text;input.className='clipboard-fallback';input.setAttribute('aria-hidden','true');document.body.append(input);
  try{input.select();if(!document.execCommand('copy'))throw new Error('Could not copy to the clipboard. Select the chat text and copy it manually.');}
  finally{input.remove();focused?.focus({preventScroll:true});selection.removeAllRanges();for(const range of ranges)selection.addRange(range);}
}
for(const [id,all] of [['copy-all',true],['copy-five',false]])$(id).onclick=()=>{closeChatMenu(true);return copyChat(all).catch(error=>notice('Rejected or failed: '+error.message));};
function confirmChatReset(archive){
  closeChatMenu(true);
  if(!state.history?.messages.length)return;
  const project=state.project,selection=state.selection,history=state.history;
  $('chat-confirm-title').textContent=archive?'Archive this chat?':'Clear this chat?';
  $('chat-confirm-description').textContent=archive?`Save the entire ${projectName(project)} conversation in a searchable archive and start a fresh chat.`:`Move the current ${projectName(project)} chat to the single trash slot? It replaces the previous trash. Restore swaps with a nonempty current chat. Archives, tasks and recovery evidence are preserved.`;
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
function confirmTrash(restore){
  closeChatMenu(true);const history=state.history;if(!history?.trash)return;
  const project=state.project,selection=state.selection;
  $('chat-confirm-title').textContent=restore?'Restore Trash?':'Empty Trash?';
  $('chat-confirm-description').textContent=restore?'Restore the cleared chat. If this chat has messages, it moves to trash so you can swap back. Your unsent text stays in the composer.':'Remove the cleared chat from the recoverable trash slot? Conversation and task evidence remains retained internally.';
  $('chat-archive-label').hidden=true;$('chat-confirm-submit').textContent=restore?'Restore Trash':'Empty Trash';
  $('chat-confirm-submit').className=restore?'primary':'danger';$('chat-confirm-error').textContent='';
  $('chat-confirm-form').onsubmit=async event=>{
    event.preventDefault();const button=$('chat-confirm-submit');if(button.disabled)return;button.disabled=true;
    try{
      if(selection!==state.selection||audioState.session||(restore&&(attachmentDraft(project).length||attachmentState.uploads.has(project)||submissions.has(project))))throw new Error('Chat or draft attachments changed. Confirm again.');
      const result=await api('/api/chat/trash/'+(restore?'restore':'empty'),{method:'POST',body:JSON.stringify({project_id:project,conversation_id:history.conversation.id,last_message_id:history.messages.at(-1)?.id||null,trash_id:history.trash.conversation_id,confirmed:true})});
      $('chat-confirm').close();notice(result.message);if(selection===state.selection)await loadHistory(selection);
    }catch(error){$('chat-confirm-error').textContent=error.message;}finally{button.disabled=false;}
  };$('chat-confirm').showModal();
}
$('restore-trash').onclick=()=>confirmTrash(true);$('empty-trash').onclick=()=>confirmTrash(false);
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
  for(const m of state.savedHistory.messages){const row=element('article',undefined,`message ${m.role}`),body=element('div');row.setAttribute('aria-label',messageAuthor(m));body.append(element('small',new Date(m.created_at*1000).toLocaleString(),'history-time'),element('div',undefined,'message-content'));updateMessageContent(body.querySelector('.message-content'),m.content,m.role);row.append(body);row.dataset.message=m.id;renderMessageAttachments(row,m);renderMessageActions(row,m,id);$('history-detail-messages').append(row);}
  if(!state.savedHistory.messages.length)$('history-detail-messages').append(element('p','This chat is empty.','empty'));
  $('history-older').hidden=!data.has_more;$('history-detail').hidden=false;
  if(!older)$('history-detail').scrollIntoView({block:'start'});
}
$('chat-older').onclick=action(()=>loadHistory(state.selection,$('messages').querySelector('[data-message]')?.dataset.message));
let readerRequest=0;
async function readHistory(kind,id){
  const request=++readerRequest,selection=state.selection,query=historyQuery();
  const path=kind==='task'?`/api/tasks/${id}/execution-records`:kind==='worker'?`/api/executions/${id}/history`:'/api/execution-history';
  const data=await api(path+'?'+query);if(selection!==state.selection||request!==readerRequest)return;
  $('reader-title').textContent=kind==='task'?'Task history':kind==='worker'?'Worker history':'Execution history';
  $('reader-content').replaceChildren();$('reader-controls').replaceChildren();
  const append=page=>{
    const content=element('pre',JSON.stringify(page,null,2));$('reader-content').append(content);
    for(const execution of page.executions||[]){const button=element('button','Read execution '+execution.id);button.onclick=action(()=>readHistory('worker',execution.id));$('reader-content').append(button);}
  };
  append(data);
  const more=(label,key,cursor)=>{
    if(cursor===null||cursor===undefined)return;
    const button=element('button',label);button.type='button';let next=cursor;
    button.onclick=action(async()=>{query.set(key,next);const page=await api(path+'?'+query);if(request!==readerRequest||selection!==state.selection)return;append(page);next=key==='before_execution'?page.before_execution:key==='output_offset'?page.next_output_offset:page.before_event;button.hidden=next===null||next===undefined;});$('reader-controls').append(button);
  };
  more('Load older events','before_event',data.before_event);
  more('Load older executions','before_execution',data.before_execution);
  if(kind==='worker')more('Read full output','output_offset',0);
  if(!$('history-reader').open)$('history-reader').showModal();
}
$('reader-close').onclick=()=>$('history-reader').close();
$('history-reader').addEventListener('close',()=>readerRequest++);
$('execution-history').onclick=action(()=>readHistory('executions'));
$('history-more').onclick=action(()=>searchHistory(true));
$('history-older').onclick=action(()=>openSavedHistory(state.savedHistory.conversation.id,true));
$('history-full').onclick=action(()=>openSavedHistory(state.savedHistory.conversation.id));
function showView(view){if(state.view==='agents'&&view!=='agents')$('agent-list').replaceChildren();if(state.view==='chat'&&view!=='chat')cancelInitialChat();const returning=state.view!=='chat'&&view==='chat';closeChatMenu();if(view!=='chat')audioChanged();state.view=view;if(view!=='memory')clearTimeout(state.memoryTimer);$('app').classList.toggle('chat-layout',view==='chat');if(view==='todo')loadTodos().catch(error=>notice(error.message));if(view==='memory')loadMemory().catch(error=>notice(error.message));if(view==='search')searchHistory().catch(error=>notice(error.message));document.querySelectorAll('.view').forEach(node=>node.hidden=node.id!==`view-${view}`);document.querySelectorAll('.tabs button').forEach(b=>(b.classList.toggle('active',b.dataset.view===view),b.setAttribute('aria-current',b.dataset.view===view?'page':'false')));if(returning){initialChatPosition=true;positionInitialChat(state.selection);}}
function editor(title,fields,onSave,reloadContext=true){
  $('editor-title').textContent=title;$('editor-fields').replaceChildren();$('editor-error').textContent='';
  for(const f of fields){const label=element('label',f.label);let control;if(f.options){control=element('select');for(const [value,text] of f.options){const option=element('option',text);option.value=value;control.append(option);}}else{control=element(f.long?'textarea':'input');if(f.long)control.rows=f.rows||4;else control.type=f.type||'text';}control.name=f.name;control.value=f.value??'';control.required=!!f.required;control.readOnly=!!f.readonly;if(f.min!==undefined)control.min=f.min;if(f.max!==undefined)control.max=f.max;label.append(control);$('editor-fields').append(label);}
  let saving=false;
  $('editor-form').onsubmit=async event=>{event.preventDefault();if(saving)return;saving=true;const button=event.target.querySelector('.primary');button.disabled=true;const values=Object.fromEntries(new FormData(event.target));try{const result=await onSave(values);if(result)actionResult(result);$('editor').close();if(reloadContext){await refresh();await selectProject(state.project);}}catch(error){$('editor-error').textContent=error.message;}finally{saving=false;button.disabled=false;}};$('editor').showModal();
}
function editProject(p){editor('Project settings',[{name:'display_name',label:'Display name (blank uses original name)',value:p.display_name||''},{name:'objective',label:'Objective',value:p.objective,long:true},{name:'success_criteria',label:'Success criteria',value:p.success_criteria,long:true},{name:'instructions',label:'Project instructions',value:p.instructions,long:true},{name:'priority',label:'Priority',type:'number',value:p.priority,min:0,max:100},{name:'status',label:'Registry status (source files are preserved)',value:p.status,options:['active','paused','archived','removed'].map(x=>[x,x])}],async values=>{await api(`/api/projects/${p.id}`,{method:'PATCH',body:JSON.stringify({...values,priority:Number(values.priority),revision:p.revision})});if(values.status==='removed'&&state.project===p.id)state.project=null;});}
$('auth-form').onsubmit=action(async event=>{event.preventDefault();try{if(state.setup)await api('/api/setup',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value,token:$('setup-token').value})});const result=await api('/api/login',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value})});state.csrf=result.csrf;await enter();}catch(error){$('auth-error').textContent=error.message;}});
async function sendSubmission(bound=null){
  if(audioState.session||attachmentState.uploads.has(bound?bound.project_id:state.project))return;
  const project=bound?bound.project_id:state.project,selection=state.selection;
  if(state.pending.has(project))return;
  let submission=submissions.get(project);
  if(!submission){submission={request_id:crypto.randomUUID(),project_id:project,content:bound?bound.content:$('message-input').value,conversation_id:bound?bound.conversation_id:state.history?.conversation.id,attachment_ids:attachmentDraft(project).map(file=>file.id)};submissions.set(project,submission);try{saveSubmissions();}catch(error){submissions.delete(project);throw error;}}
  state.pending.add(project);$('send').disabled=true;renderAttachments();
  let accepted=false;
  try{
    await submitWithModelSelection(submission);
    accepted=true;clearSentAttachments(project,submission.attachment_ids||[]);
    submissions.delete(project);saveSubmissions();
    if(state.drafts.get(project)===submission.content)state.drafts.set(project,'');saveDrafts();
    if(selection===state.selection){if($('message-input').value===submission.content)$('message-input').value='';await loadHistory(selection);}
  }catch(error){
    if(error.modelBlocked){submissions.delete(project);saveSubmissions();}
    else if(accepted){if(selection===state.selection)updatesUnavailable(error,'history');error.ambiguous=true;error.message='Message accepted. Saved response unavailable; updates will retry.';}
    else if(error.status>=400&&error.status<500&&![401,403,408,429].includes(error.status)){submissions.delete(project);saveSubmissions();}
    else{error.ambiguous=true;error.message='Acceptance is unconfirmed. Your saved request will be checked on return or Send.';}
    throw error;
  }finally{state.pending.delete(project);if(selection===state.selection){$('send').disabled=state.pending.has(project);updateChatActions();renderAttachments();}}
}
$('chat-form').onsubmit=action(async event=>{event.preventDefault();if(!$('message-input').value.trim()&&!attachmentDraft(state.project).length&&!submissions.has(state.project))return;await sendSubmission();});
$('message-input').addEventListener('input',()=>{state.drafts.set(state.project,$('message-input').value);saveDrafts();});
// Cancel only pointer press focus transfer. Click/keyboard activation remains native;
// in particular file-picker activation must stay synchronous inside its trusted click.
function preserveDraftFocus(event){
  if(event.button!==0||!event.isPrimary||document.activeElement!==$('message-input'))return;
  if(event.target.closest('#microphone,#attach,#audio-stop,#audio-retry,#audio-cancel,#audio-download,#audio-upload'))event.preventDefault();
}
$('app').addEventListener('pointerdown',preserveDraftFocus);
// Older mouse browsers without Pointer Events still get the same focus protection.
if(!window.PointerEvent)$('app').addEventListener('mousedown',event=>{
  if(event.button===0&&document.activeElement===$('message-input')&&event.target.closest('#microphone,#attach,#audio-stop,#audio-retry,#audio-cancel,#audio-download,#audio-upload'))event.preventDefault();
});

// Textarea Enter (including IME composition) keeps the browser's native multiline editing.
$('stop-chat').onclick=action(()=>api(`/api/executions/${state.execution}/interrupt`,{method:'POST'}));
$('all-projects').onclick=action(()=>selectProject(null));
$('discover').onclick=action(async()=>{await api('/api/projects/discover',{method:'POST'});await refresh();});
$('rename-project').onclick=()=>renameProject();
$('add-project').onclick=()=>editor('Add project',[{name:'path',label:'Project directory',required:true}],values=>api('/api/projects',{method:'POST',body:JSON.stringify(values)}));
$('project-settings').onclick=()=>{const p=state.portfolio.projects.find(p=>p.id===state.project);if(p)editProject(p);else notice('Select a project first.');};
$('logout').onclick=action(async()=>{await api('/api/logout',{method:'POST'});state.csrf=null;showAuth();});
$('revoke-sessions').onclick=action(async()=>{await api('/api/sessions/revoke',{method:'POST'});showAuth();});
$('instructions-form').onsubmit=action(async event=>{event.preventDefault();await api('/api/settings/instructions',{method:'PUT',body:JSON.stringify({content:$('global-instructions').value})});notice('Instructions saved.');});

let searchTimer;
$('history-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>searchHistory().catch(error=>notice(error.message)),250);};
document.querySelectorAll('.tabs [data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view));
// Schedule after completion: slow/disconnected reads cannot pile up every three seconds.
let pollTimer,pollRunning=false,pollFailures=0;
async function pollUpdates(){
  clearTimeout(pollTimer);
  if(pollRunning)return;
  pollRunning=true;
  const selection=state.selection;
  try{
    if(!$('app').hidden&&state.ready&&!document.hidden){
      const historyChecked=!state.source&&!state.pending.has(state.project);
      const recovered=await refresh();
      if(recovered&&selection===state.selection){pollFailures=0;updatesRecovered();if(historyChecked)updatesRecovered('history');}
    }
  }catch(error){if(selection===state.selection){pollFailures=Math.min(pollFailures+1,4);updatesUnavailable(error);}}
  finally{pollRunning=false;pollTimer=setTimeout(pollUpdates,Math.min(30000,3000*2**pollFailures));}
}
pollTimer=setTimeout(pollUpdates,3000);
window.addEventListener('online',pollUpdates);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollUpdates();});
// Deferred companion scripts must initialize before authentication can render the composer.
document.addEventListener('DOMContentLoaded',()=>boot().catch(error=>notice(error.message)),{once:true});

function renameProject(){const p=state.portfolio.projects.find(p=>p.id===state.project);if(p)editor('Rename project',[{name:'display_name',label:'Display name',value:p.display_name||''}],values=>api(`/api/projects/${p.id}`,{method:'PATCH',body:JSON.stringify({...values,revision:p.revision})}));}

// Keyboard APIs are optional; viewport shrink is a best-effort mobile signal.
const composerKeyboard={baseline:window.visualViewport?.height||innerHeight,seen:false,closed:false,active:false};
function mobileComposer(){return matchMedia('(pointer:coarse)').matches||navigator.maxTouchPoints>0;}
function updateComposerViewport(){
  const viewport=window.visualViewport, height=viewport?.height||innerHeight;
  const focused=$('chat-form').contains(document.activeElement);
  if(!focused){composerKeyboard.baseline=Math.max(height,innerHeight);composerKeyboard.seen=false;composerKeyboard.closed=false;composerKeyboard.active=false;}
  if(document.activeElement===$('message-input'))composerKeyboard.active=true;
  if(focused&&(!viewport||Math.abs(viewport.scale-1)<0.01)){
    const keyboard=navigator.virtualKeyboard?.boundingRect;
    const visible=!!keyboard?.height||composerKeyboard.baseline-height>Math.max(100,composerKeyboard.baseline*.18);
    if(visible){composerKeyboard.seen=true;composerKeyboard.closed=false;}
    else if(composerKeyboard.seen)composerKeyboard.closed=true;
  }
  updateComposer();
}
function updateComposer(){
  const form=$('chat-form'),input=$('message-input');
  const focused=document.activeElement===input,scroll=input.scrollTop;
  const mobile=mobileComposer();
  const recording=typeof audioState!=='undefined'&&!!audioState.session;
  const files=typeof attachmentState!=='undefined'&&(attachmentDraft(state.project).length||attachmentState.uploads.has(state.project));
  const active=!!input.value.length||!!files||recording||(composerKeyboard.active&&form.contains(document.activeElement)&&(!mobile||!composerKeyboard.closed));
  form.classList.toggle('composer-active',active);
  form.classList.toggle('composer-audio',typeof audioState!=='undefined'&&!!audioState.session);
  form.classList.toggle('composer-recording',typeof audioState!=='undefined'&&audioState.session?.phase==='recording');
  form.classList.toggle('composer-caret',focused&&!document.hidden&&(!mobile||(composerKeyboard.seen&&!composerKeyboard.closed)));
  $('send').hidden=!active;
  // Measure at the initial active size, independent of the previous overflow size.
  form.style.setProperty('--composer-height',(active?140:92)+'px');
  form.classList.toggle('composer-overflow',false);
  // Audio reserves a bottom row inside the existing height, without expanding the composer.
  const audioRow=parseFloat(getComputedStyle(input.parentElement).paddingBottom)||0;
  if(active&&input.scrollHeight>input.clientHeight+audioRow+1){
    form.style.setProperty('--composer-height','188px');
    form.classList.add('composer-overflow');
  }
  input.scrollTop=scroll;
}
$('message-input').addEventListener('input',updateComposer);
$('chat-form').addEventListener('focusin',updateComposerViewport);
// Let native pointer focus transfer finish before hiding/moving its target.
$('chat-form').addEventListener('focusout',()=>requestAnimationFrame(updateComposerViewport));
// A fresh tap can reopen a dismissed keyboard without a new focus event.
$('message-input').addEventListener('pointerdown',()=>{composerKeyboard.active=true;if(composerKeyboard.closed)composerKeyboard.seen=false;composerKeyboard.closed=false;updateComposer();});
navigator.virtualKeyboard?.addEventListener('geometrychange',updateComposerViewport);
window.addEventListener('orientationchange',()=>{composerKeyboard.baseline=innerHeight;composerKeyboard.seen=false;composerKeyboard.closed=false;updateComposerViewport();});
document.addEventListener('visibilitychange',updateComposer);

// Use the visible viewport once: do not subtract keyboard height or safe area twice.
function updateViewport(){
  const viewport=window.visualViewport;
  // Pinch zoom should retain normal document navigation rather than resize chat.
  const unzoomed=!viewport||Math.abs(viewport.scale-1)<0.01;
  const height=unzoomed&&viewport?viewport.height:window.innerHeight;
  document.documentElement.style.setProperty('--app-height',height+'px');
  document.documentElement.style.setProperty('--viewport-top',(unzoomed&&viewport?viewport.offsetTop:0)+'px');
  updateComposerViewport();
}
window.addEventListener('resize',updateViewport);
window.visualViewport?.addEventListener('resize',updateViewport);
window.visualViewport?.addEventListener('scroll',updateViewport);
updateViewport();

// Refresh keeps text selection, focus and unsaved edits intact; context switches clear selection.
state.todoSelection=new Set();state.todos=[];
async function loadTodos(selection=state.selection){
  const request=state.todoRequest=(state.todoRequest||0)+1,project=state.project;
  const data=await api('/api/todos'+(project?'?project_id='+encodeURIComponent(project):''));
  if(selection!==state.selection||project!==state.project||request!==state.todoRequest)return;
  state.todos=data.entries;
  for(const id of state.todoSelection){if(!state.todos.some(t=>t.id===id))state.todoSelection.delete(id);}
  renderTodos();
}
function renderTodos(force=false){
  if(!state.todos)return;
  $('todo-delete').disabled=!state.todoSelection.size;
  const list=$('todo-list'),signature=JSON.stringify(state.todos);
  if(list.dataset.signature!==signature){
    const selection=window.getSelection();
    if(!force&&selection&&!selection.isCollapsed&&selection.containsNode(list,true))return;
    const focused=document.activeElement,listFocused=list.contains(focused),focusId=focused?.dataset.todo,focusKind=focused?.type;
    list.replaceChildren();list.dataset.signature=signature;
    for(const entry of state.todos){
      const row=element('article',undefined,'panel'),label=element('label'),checkbox=element('input');
      checkbox.type='checkbox';checkbox.dataset.todo=entry.id;checkbox.checked=state.todoSelection.has(entry.id);
      checkbox.setAttribute('aria-label','Select '+entry.title);
      checkbox.onchange=()=>{if(checkbox.checked)state.todoSelection.add(entry.id);else state.todoSelection.delete(entry.id);renderTodos();};
      label.append(checkbox,element('strong',entry.title));row.append(label,element('small',projectName(entry.project_id)),element('p',entry.details,'todo-details'));
      if(entry.source_task_id)row.append(element('small','Linked recording assignment: '+entry.source_task_id));
      const edit=element('button','Edit');edit.dataset.todo=entry.id;edit.type='button';edit.onclick=()=>editTodo(entry);row.append(edit);
      const start=element('button','Start');start.type='button';start.dataset.todoStart=entry.id;start.onclick=action(()=>startTodo(entry));row.append(start);list.append(row);
    }
    if(!state.todos.length)list.append(element('p','No future ideas in this context.','empty'));
    if(listFocused&&focusId)list.querySelector(`[data-todo="${focusId}"][type="${focusKind}"]`)?.focus({preventScroll:true});
  }
  for(const box of list.querySelectorAll('input[type=checkbox]'))box.checked=state.todoSelection.has(box.dataset.todo);
  for(const button of list.querySelectorAll('[data-todo-start]'))button.disabled=todoStarts.has(button.dataset.todoStart);
  $('todo-delete').disabled=!state.todoSelection.size;
}
const todoStarts=new Map();
async function startTodo(entry){
  if(todoStarts.has(entry.id))return todoStarts.get(entry.id);
  const selection=state.selection;
  const pending=orderModelIntent(async()=>{
    if(modelSelectionError)throw new Error('Choose model settings again before starting: '+modelSelectionError);
    const result=await api('/api/todos/'+encodeURIComponent(entry.id)+'/start',{method:'POST'});
    notice('To-Do started · Assignment '+result.task_id+' · '+result.status);
    if(selection===state.selection){
      state.todos=state.todos.filter(todo=>todo.id!==entry.id);state.todoSelection.delete(entry.id);renderTodos();
      await refresh().catch(error=>updatesUnavailable(error));
    }
    return result;
  });
  todoStarts.set(entry.id,pending);renderTodos();
  try{return await pending;}
  finally{todoStarts.delete(entry.id);renderTodos();}
}
function editTodo(entry=null){
  const project=state.project,selection=state.selection;
  const fields=[{name:'title',label:'Idea',required:true,value:entry?.title||''},{name:'details',label:'Details',long:true,value:entry?.details||''}];
  if(!entry&&!project)fields.unshift({name:'project_id',label:'Project',required:true,options:state.portfolio.projects.filter(p=>p.status!=='removed').map(p=>[p.id,projectLabel(p)])});
  if(!entry&&!project&&!fields[0].options.length){notice('Add a project before recording an idea.');return;}
  editor(entry?'Edit To-Do':'Add To-Do',fields,async values=>{
    if(selection!==state.selection||project!==state.project)throw new Error('Context changed. Close this editor and select the idea again.');
    await api('/api/todos'+(entry?'/'+entry.id:''),{method:entry?'PATCH':'POST',body:JSON.stringify({...values,project_id:entry?.project_id||project||values.project_id})});
    await loadTodos(selection);
  },false);
}
$('todo-add').onclick=()=>editTodo();
$('todo-delete').onclick=action(async()=>{
  const project=state.project,selection=state.selection,selected=[...state.todoSelection];
  if(!selected.length)return;
  if(!confirm(`Delete ${selected.length} selected To-Do idea${selected.length===1?'':'s'}?`))return;
  if(selection!==state.selection||project!==state.project)return;
  await api('/api/todos/archive',{method:'POST',body:JSON.stringify({project_id:project,selected})});
  if(selection===state.selection){state.todoSelection.clear();await loadTodos(selection);}
});

function deploymentText(result){
  if(result.request)return `Deploy requested: ${((result.state||{}).phase||'queued').replaceAll('_',' ')}`+(result.installed?'':' (waiting for the one-time install command; see README)');
  const last=result.last_result;return last?`Last deploy ${last.status.replaceAll('_',' ')}: ${last.reason}`:'No deploy yet.';
}
async function deploymentStatus(){const result=await api('/api/deployment');$('deployment-status').textContent=deploymentText(result);$('deployment-cancel').hidden=!result.request;}
$('deployment-check').onclick=action(deploymentStatus);
$('deployment-cancel').onclick=action(async()=>{await api('/api/deployment/cancel',{method:'POST',body:'{}'});await deploymentStatus();});
