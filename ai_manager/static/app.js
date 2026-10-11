'use strict';
const $ = id => document.getElementById(id);
const state = {csrf:null, project:null, portfolio:null, detail:null, source:null, execution:null, view:'chat', setup:false, selection:0, drafts:new Map(), pending:new Set()};
const submissionKey = 'linko-chat-submissions-v1';
const submissions = new Map(JSON.parse(sessionStorage.getItem(submissionKey)||'[]'));
function saveSubmissions(){sessionStorage.setItem(submissionKey,JSON.stringify([...submissions]));}
const element = (tag, text, cls) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;};
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
async function api(path, options={}){
  const headers={'Content-Type':'application/json',...(options.headers||{})};
  if(options.method && options.method!=='GET')headers['X-CSRF-Token']=state.csrf||'';
  const response=await fetch(path,{...options,headers});
  const result=await response.json();
  if(!response.ok){if(response.status===401 && !path.includes('login'))showAuth();const error=new Error(result.detail||result.message||'Request failed');error.status=response.status;error.result=result.result||(response.status<500?'rejected':'failed');throw error;}
  return result;
}
function action(fn){return async event=>{const button=event?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(event);}catch(error){notice(error.ambiguous?error.message:'Rejected or failed: '+error.message);}finally{if(button?.tagName==='BUTTON')button.disabled=false;}};}
function actionResult(result){notice(`${result.result||'accepted'}: ${result.message||result.status||'Operation recorded'}`);}
function showAuth(){state.selection++;state.source?.close();state.source=null;state.execution=null;clearTimeout(state.reconcileTimer);$('app').hidden=true;$('auth-screen').hidden=false;$('chat-notice').hidden=true;clearTimeout(state.chatNoticeTimer);}
async function boot(){const auth=await api('/api/auth-status');state.csrf=auth.csrf;state.setup=auth.setup_required;$('setup-label').hidden=!state.setup;$('setup-token').required=state.setup;if(auth.authenticated)await enter();else showAuth();}
async function enter(){$('auth-screen').hidden=true;$('app').hidden=false;const settings=await api('/api/settings');$('global-instructions').value=settings.global_instructions;await refresh();await selectProject(state.project);}
async function refresh(){state.portfolio=await api('/api/portfolio');renderProjects();renderTasks();renderAgents();renderQuestions();$('task-count').textContent=state.portfolio.tasks.filter(t=>['queued','running','waiting'].includes(t.status)).length;if(state.portfolio.queue_error)notice(state.portfolio.queue_error);if(!state.source&&!state.pending.has(state.project))await loadHistory();}
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
async function selectProject(id){state.drafts.set(state.project,$('message-input').value);const selection=++state.selection;state.project=id;state.detail=null;state.source?.close();state.source=null;state.execution=null;state.history=null;$('messages').replaceChildren();$('message-input').value=state.drafts.get(id)||submissions.get(id)?.content||'';$('stop-chat').hidden=true;$('send').disabled=state.pending.has(id);renderProjects();renderTasks();renderAgents();renderQuestions();if(state.view==='memory')await loadMemory(selection);if(state.view==='search')await searchHistory();await loadHistory(selection);if(selection===state.selection&&submissions.has(id))await sendSubmission();}
function projectName(id){const p=state.portfolio?.projects.find(p=>p.id===id);return p?displayName(p):'All Projects';}
async function loadMemory(selection=state.selection){if(!state.project)return;const detail=await api(`/api/projects/${state.project}`);if(selection!==state.selection)return;state.detail=detail;renderMemory();}
function renderMemory(){$('memory-list').replaceChildren();if(!state.detail){$('memory-list').append(element('p','Select a project to read historical owner notes.'));return;}for(const m of state.detail.memory){const d=element('details');d.append(element('summary',m.name),element('pre',m.content));$('memory-list').append(d);}if(!state.detail.memory.length)$('memory-list').append(element('p','Project context comes from its repository and instructions.'));}
function recoveryLabel(record){const reasons={connection:'Waiting for connection',usage:'Waiting for usage limit',interrupted:'Recovering interrupted execution'};return (reasons[record.recovery_reason]||'Waiting')+(record.retry_at?' · Next attempt '+new Date(record.retry_at*1000).toLocaleString():'');}
function renderTasks(){$('task-list').replaceChildren();for(const t of state.portfolio.tasks.filter(t=>!state.project||t.project_id===state.project)){const row=element('article',undefined,'panel');row.append(element('h3',`${t.status} · ${projectName(t.project_id)}`),element('p',t.description),element('p',t.retry_at?recoveryLabel(t):t.blocker));if(t.legacy&&!['completed','cancelled','failed'].includes(t.status))row.append(element('small','Historical work: continuation inspects existing source and saved results.'));if(!t.lease_owner&&t.status!=='cancelled'){for(const a of ['continue','pause','cancel']){if(a==='continue'&&t.status==='queued')continue;const b=element('button',a);b.onclick=action(async()=>{await api(`/api/tasks/${t.id}`,{method:'PATCH',body:JSON.stringify({action:a,revision:t.revision})});await refresh();});row.append(b);}}const records=element('button','History');records.onclick=action(async()=>{const result=await api(`/api/tasks/${t.id}/execution-records`);editor('Execution history',[{name:'history',label:'Recorded results',long:true,readonly:true,value:JSON.stringify(result,null,2)}],async()=>{});});row.append(records);$('task-list').append(row);}}
function renderAgents(){$('agent-list').replaceChildren();for(const e of state.portfolio.executions.filter(e=>!state.project||e.project_id===state.project)){const row=element('article',undefined,'panel');row.append(element('h3',`${e.agent_id} · ${e.status} · ${projectName(e.project_id)}`));const d=element('details');d.append(element('summary','Output and execution references'),element('pre',[e.output,e.error,e.thread_id,e.turn_id].filter(Boolean).join('\n')));row.append(d);if(!e.finished_at){const b=element('button','Stop');b.onclick=action(async()=>{await api(`/api/executions/${e.id}/interrupt`,{method:'POST'});await refresh();});row.append(b);}$('agent-list').append(row);}}
function renderQuestions(){$('questions').replaceChildren();for(const q of state.portfolio.questions.filter(q=>!state.project||q.project_id===state.project)){const request=JSON.parse(q.request),form=element('form',undefined,'panel');form.append(element('h3','Codex needs your answer · '+projectName(q.project_id)));if(q.kind==='item/tool/requestUserInput'){for(const question of request.questions){const label=element('label',question.question),input=element('input');input.name=question.id;input.required=true;if(question.options)label.append(element('small',question.options.map(x=>x.label).join(' / ')));label.append(input);form.append(label);}form.append(element('button','Send answer','primary'));form.onsubmit=action(async event=>{event.preventDefault();await api(`/api/questions/${q.id}/answer`,{method:'POST',body:JSON.stringify({answers:Object.fromEntries(new FormData(form))})});await refresh();});}else{form.append(element('pre',JSON.stringify(request,null,2)));for(const [label,approve] of [['Confirm exact action',true],['Decline',false]]){const b=element('button',label);b.type='button';b.onclick=action(async()=>{await api(`/api/questions/${q.id}/answer`,{method:'POST',body:JSON.stringify({answers:{approve}})});await refresh();});form.append(b);}}$('questions').append(form);}}
async function loadHistory(selection=state.selection){
  const query=new URLSearchParams();if(state.project)query.set('project_id',state.project);
  state.source?.close();state.source=null;
  const request=state.loadHistoryRequest=(state.loadHistoryRequest||0)+1;
  const history=await api(`/api/history?${query}`);if(selection!==state.selection||request!==state.loadHistoryRequest)return;
  const messages=$('messages'),follow=shouldFollowChat(),fresh=state.history?.conversation.id!==history.conversation.id;
  state.history=history;updateChatActions();
  if(fresh||!history.messages.length)messages.replaceChildren();
  if(history.messages.length)messages.querySelector('.welcome')?.remove();
  if(!history.messages.length){const welcome=element('div',undefined,'welcome');welcome.append(element('div','Ai','mark'),element('h2','What should we move forward?'),element('p','Talk through a goal, review project state, or plan the next useful step. Your context stays with each project.'));const suggestions=element('div',undefined,'suggestions');for(const text of ['What needs my attention?','Summarize current project state','Help me prioritize the next tasks','Brainstorm a plan for this project']){const b=element('button',text);b.onclick=()=>{$('message-input').value=text;$('message-input').focus();};suggestions.append(b);}welcome.append(suggestions);$('messages').append(welcome);}
  const rows=new Map([...messages.querySelectorAll('[data-message]')].map(row=>[row.dataset.message,row]));
  const ids=new Set(history.messages.map(m=>m.id));
  for(const [id,row] of rows)if(!ids.has(id))row.remove();
  for(const m of history.messages){
    let row=rows.get(m.id);
    if(!row){row=element('article',undefined,`message ${m.role}`);const body=element('div'),label=element('div',undefined,'message-label');label.append(element('strong',messageAuthor(m)),element('span',m.status==='waiting'&&m.retry_at?recoveryLabel(m):m.status,`badge ${m.status}`));body.append(label,element('div',messageText(m),'message-content'));row.append(element('div',m.role==='user'?'YOU':'Ai','avatar'),body);row.dataset.message=m.id;messages.append(row);}
    else{const badge=row.querySelector('.badge');if(badge.textContent!==(m.status==='waiting'&&m.retry_at?recoveryLabel(m):m.status)){badge.textContent=m.status==='waiting'&&m.retry_at?recoveryLabel(m):m.status;badge.className=`badge ${m.status}`;}updateMessageContent(row.querySelector('.message-content'),messageText(m));}

  }
  const active=history.messages.find(m=>m.role==='assistant'&&m.execution_id&&m.execution_status&&m.finished_at===null);
  state.execution=active?.execution_id||null;
  $('stop-chat').hidden=!active;$('send').disabled=!!active||state.pending.has(state.project);
  $('status-label').textContent=active?(active.status==='waiting'?recoveryLabel(active):active.status==='queued'?'Queued / connecting':'Thinking / responding'):'Ready to help';
  if(active)attachStream(active.execution_id,active.id,active.stream_cursor);
  updateChatActions();
  if(fresh||follow)messages.scrollTop=messages.scrollHeight;
}
function messageText(m){return m.content||(m.status==='failed'?(m.execution_error||'Response failed.'):m.status==='interrupted'?'Stopped.':m.status==='completed'?'No text response.':m.status==='waiting'?recoveryLabel(m):m.status==='queued'?'Connecting to Codex…':'Thinking…');}
function messageAuthor(message){return message.role==='user'?'You':message.role==='worker'?'Codex result':'AI Manager';}
function chatHasSelection(node=$('messages')){const selection=window.getSelection();return !!selection&&!selection.isCollapsed&&selection.containsNode(node,true);}
function shouldFollowChat(){const messages=$('messages');return !state.selectingChat&&!chatHasSelection()&&messages.scrollHeight-messages.clientHeight-messages.scrollTop<48;}
function updateMessageContent(node,text){
  if(node.textContent===text)return;
  if(node.firstChild?.nodeType===Node.TEXT_NODE&&text.startsWith(node.textContent))node.firstChild.appendData(text.slice(node.textContent.length));
  else if(!chatHasSelection(node))node.textContent=text;
}
$('messages').addEventListener('pointerdown',()=>{state.selectingChat=true;});
for(const event of ['pointerup','pointercancel'])document.addEventListener(event,()=>{state.selectingChat=false;});
function attachStream(execution,messageId,cursor=0){
  state.source?.close();state.execution=execution;
  const selection=state.selection,conversation=state.history?.conversation.id;
  const source=new EventSource(`/api/executions/${execution}/events?after=${cursor}`);state.source=source;
  const current=()=>state.source===source&&selection===state.selection&&conversation===state.history?.conversation.id&&!$('app').hidden;
  const reconcile=()=>{if(!current())return;source.close();state.source=null;loadHistory(selection).catch(error=>notice(error.message));};
  source.addEventListener('chat.delta',event=>{
    if(!current()||Number(event.lastEventId)<=cursor)return;cursor=Number(event.lastEventId);
    const data=JSON.parse(event.data),follow=shouldFollowChat(),message=state.history?.messages.find(m=>m.id===messageId);
    if(!message)return;message.content=data.reset?data.delta:message.content+data.delta;
    const content=document.querySelector(`[data-message="${messageId}"] .message-content`);
    if(content)updateMessageContent(content,message.content);if(follow)$('messages').scrollTop=$('messages').scrollHeight;
  });
  source.addEventListener('chat.started',reconcile);
  source.addEventListener('recovery.scheduled',reconcile);
  source.addEventListener('chat.finished',reconcile);
  source.addEventListener('stream.end',reconcile);
  source.addEventListener('stream.auth_expired',()=>{if(current())showAuth();});
  source.onerror=()=>{
    if(!current())return;$('status-label').textContent='Checking saved response…';
    clearTimeout(state.reconcileTimer);state.reconcileTimer=setTimeout(()=>{if(current())reconcile();},1000);
  };
}
function updateChatActions(){
  const disabled=!state.history?.messages.length||state.pending.has(state.project)||!!state.execution||state.history.messages.some(m=>['queued','streaming','waiting'].includes(m.status));
  for(const id of ['archive-chat','clear-chat'])$(id).disabled=disabled;
  for(const id of ['copy-all','copy-five'])$(id).disabled=!state.history?.messages.length||!!state.copyingChat;
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
for(const [id,all] of [['copy-all',true],['copy-five',false]])$(id).onclick=()=>copyChat(all).catch(error=>notice('Rejected or failed: '+error.message));
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
  for(const m of state.savedHistory.messages){const row=element('article',undefined,`message ${m.role}`),body=element('div');body.append(element('strong',m.role==='user'?'You':m.role==='worker'?'Codex result':'AI Manager'),element('small',new Date(m.created_at*1000).toLocaleString(),'history-time'),element('div',m.content,'message-content'));row.append(element('div',m.role==='user'?'YOU':'Ai','avatar'),body);$('history-detail-messages').append(row);}
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
$('auth-form').onsubmit=action(async event=>{event.preventDefault();try{if(state.setup)await api('/api/setup',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value,token:$('setup-token').value})});const result=await api('/api/login',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value})});state.csrf=result.csrf;await enter();}catch(error){$('auth-error').textContent=error.message;}});
async function sendSubmission(){
  const project=state.project,selection=state.selection;
  if(state.pending.has(project))return;
  let submission=submissions.get(project);
  if(!submission){submission={request_id:crypto.randomUUID(),project_id:project,content:$('message-input').value,conversation_id:state.history?.conversation.id,mode:$('chat-mode').value};submissions.set(project,submission);try{saveSubmissions();}catch(error){submissions.delete(project);throw error;}}
  state.pending.add(project);$('send').disabled=true;
  try{
    await api('/api/chat',{method:'POST',body:JSON.stringify(submission)});
    submissions.delete(project);saveSubmissions();
    if(state.drafts.get(project)===submission.content)state.drafts.set(project,'');
    if(selection===state.selection){if($('message-input').value===submission.content)$('message-input').value='';await loadHistory(selection);}
  }catch(error){
    if(error.status>=400&&error.status<500&&![401,403,408,429].includes(error.status)){submissions.delete(project);saveSubmissions();}
    else{error.ambiguous=true;error.message='Acceptance is unconfirmed. Your saved request will be checked on return or Send.';}
    throw error;
  }finally{state.pending.delete(project);if(selection===state.selection){$('send').disabled=!!state.execution;updateChatActions();}}
}
$('chat-form').onsubmit=action(async event=>{event.preventDefault();await sendSubmission();});
$('message-input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('send').disabled)$('chat-form').requestSubmit();}};
$('stop-chat').onclick=action(()=>api(`/api/executions/${state.execution}/interrupt`,{method:'POST'}));
$('all-projects').onclick=action(()=>selectProject(null));
$('discover').onclick=action(async()=>{await api('/api/projects/discover',{method:'POST'});await refresh();});
$('rename-project').onclick=()=>renameProject();
$('add-project').onclick=()=>editor('Add project',[{name:'path',label:'Project directory',required:true}],values=>api('/api/projects',{method:'POST',body:JSON.stringify(values)}));
$('project-settings').onclick=()=>{const p=state.portfolio.projects.find(p=>p.id===state.project);if(p)editProject(p);else notice('Select a project first.');};
$('logout').onclick=action(async()=>{await api('/api/logout',{method:'POST'});state.csrf=null;showAuth();});
$('revoke-sessions').onclick=action(async()=>{await api('/api/sessions/revoke',{method:'POST'});showAuth();});
$('instructions-form').onsubmit=action(async event=>{event.preventDefault();await api('/api/settings/instructions',{method:'PUT',body:JSON.stringify({content:$('global-instructions').value})});notice('Instructions saved.');});
$('load-models').onclick=action(async()=>{const catalog=await api('/api/models');$('model-routes').replaceChildren();for(const [role,route] of Object.entries(catalog.routes)){const row=element('form',undefined,'route-row'),model=element('select'),effort=element('select');for(const m of catalog.models){const option=element('option',m.displayName);option.value=m.model;model.append(option);}model.value=route.model;function efforts(){effort.replaceChildren();const m=catalog.models.find(m=>m.model===model.value);for(const e of m.supportedReasoningEfforts.filter(e=>e.reasoningEffort!=='ultra')){const option=element('option',e.reasoningEffort);option.value=e.reasoningEffort;effort.append(option);}effort.value=route.effort;}efforts();model.onchange=efforts;model.setAttribute('aria-label',`${role} model`);effort.setAttribute('aria-label',`${role} reasoning effort`);const save=element('button','Save','primary');row.append(element('label',role),model,effort,save);row.onsubmit=action(async event=>{event.preventDefault();await api(`/api/models/${role}`,{method:'PUT',body:JSON.stringify({model:model.value,effort:effort.value})});notice(`${role} model settings saved.`);});$('model-routes').append(row);}});
let searchTimer;
$('history-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>searchHistory().catch(error=>notice(error.message)),250);};
document.querySelectorAll('.tabs [data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view));
setInterval(()=>{if(!$('app').hidden)refresh().catch(error=>notice(error.message));},3000);
boot().catch(error=>notice(error.message));

function renameProject(){const p=state.portfolio.projects.find(p=>p.id===state.project);if(p)editor('Rename project',[{name:'display_name',label:'Display name',value:p.display_name||''}],values=>api(`/api/projects/${p.id}`,{method:'PATCH',body:JSON.stringify({...values,revision:p.revision})}));}
