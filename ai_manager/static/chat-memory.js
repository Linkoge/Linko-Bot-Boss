'use strict';
// Chat menu Compact: starts the compaction in the background at once (no dialog); the server applies it itself, the
// notice under the chat says it is running, then "Chat compacted" or why it failed. Also the Chat memory settings
// panel (model shown = saved = used).
const compaction={timers:{}};
function compactionQuery(project){const query=new URLSearchParams();if(project)query.set('project_id',project);return query;}
function showCompactionNotice(project,notice){
  if(project!==state.project)return;
  if(state.history)state.history.compaction_notice=notice;
  $('compaction-notice').hidden=!notice;$('compaction-notice-text').textContent=notice?.text||'';
}
function watchCompaction(project,selection){
  const key=project||'global';clearTimeout(compaction.timers[key]);
  compaction.timers[key]=setTimeout(async()=>{
    let status;
    try{status=await api(`/api/chat/compact/status?${compactionQuery(project)}`);}
    catch{watchCompaction(project,selection);return;}
    if(status.running){watchCompaction(project,selection);return;}
    delete compaction.timers[key];
    if(selection===state.selection)await loadHistory(selection).catch(()=>{});
    showCompactionNotice(project,status.notice);
  },1500);
}
async function startCompaction(){
  closeChatMenu(true);
  if(!state.history?.messages.length)return;
  const project=state.project,selection=state.selection;
  try{
    const started=await api('/api/chat/compact',{method:'POST',body:JSON.stringify({project_id:project})});
    showCompactionNotice(project,{text:started.message,state:'running'});
    watchCompaction(project,selection);
  }catch(error){showCompactionNotice(project,{text:`Chat compaction did not start: ${error.message}`,state:'failed'});}
}
$('compact-chat').onclick=()=>startCompaction();
function renderCompactionNotice(){
  const notice=state.history?.compaction_notice;
  $('compaction-notice').hidden=!notice;$('compaction-notice-text').textContent=notice?.text||'';
  if(notice?.state==='running'&&!compaction.timers[state.project||'global'])watchCompaction(state.project,state.selection);
}
$('compaction-notice-dismiss').onclick=action(async()=>{
  const project=state.project;$('compaction-notice').hidden=true;
  if(state.history&&project===state.project)state.history.compaction_notice=null;
  await api('/api/chat/compact/notice/dismiss',{method:'POST',body:JSON.stringify({project_id:project})});
});
// Settings panel: the select and the "Used:" line come from the same server record and route.
let chatMemorySettings=null;
function renderChatMemorySettings(data){
  chatMemorySettings=data;const saved=data.settings,select=$('chat-memory-model');
  const manager=document.createElement('option');manager.value='latest-haiku';manager.textContent='Latest Haiku (newest available)';select.replaceChildren(manager);
  for(const model of new Set([...data.models,...(saved.model&&saved.model!=='latest-haiku'?[saved.model]:[])])){const option=document.createElement('option');option.value=model;option.textContent=model;select.append(option);}
  select.value=saved.model||'latest-haiku';$('chat-memory-effort').value=saved.effort||'low';
  $('chat-memory-threshold').value=saved.threshold_tokens;
  $('chat-memory-effective').textContent=data.effective?`Used: ${data.effective.model} · ${data.effective.effort}${saved.model==='latest-haiku'?' (Latest Haiku)':''} · memory caps ${data.caps.long} long-term / ${data.caps.short} short-term (least recently used archived when over) · promoted to long-term after ${data.promote_after} reuses · timeout ${Math.round(data.timeout_seconds/60)} min`:`Compaction model unavailable: ${data.error}`;
}
async function loadChatMemorySettings(){
  const project=state.project,data=await api(`/api/chat-memory/settings?${compactionQuery(project)}`);
  if(project===state.project)renderChatMemorySettings(data);
}
$('chat-memory-form').onsubmit=action(async event=>{
  event.preventDefault();if(!chatMemorySettings)await loadChatMemorySettings();
  const body={revision:chatMemorySettings.settings.revision,model:$('chat-memory-model').value||'latest-haiku',effort:$('chat-memory-effort').value.trim()||'low',
    threshold_tokens:Number($('chat-memory-threshold').value)};
  const data=await api(`/api/chat-memory/settings?${compactionQuery(state.project)}`,{method:'PUT',body:JSON.stringify(body)});
  renderChatMemorySettings(data);$('chat-memory-status').textContent='Saved.';
});
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',()=>loadChatMemorySettings().catch(error=>{$('chat-memory-effective').textContent=error.message;}));
