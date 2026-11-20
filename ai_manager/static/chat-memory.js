'use strict';
// Chat menu Compact: one model call proposes memory items, the owner reviews them, Apply archives the full chat and
// continues from a short chat with the compaction summary. Also the automatic-compaction notice and the Chat memory
// settings panel (model shown = saved = used).
const compaction={id:null,project:null,selection:null};
function compactionQuery(project){const query=new URLSearchParams();if(project)query.set('project_id',project);return query;}
function compactionList(title,items,parent){
  const section=element('section',undefined,'compact-section');section.append(element('h3',`${title} · ${items.length}`));
  if(items.length){const list=element('ul');for(const text of items)list.append(element('li',text));section.append(list);}
  else section.append(element('p','None','muted'));
  parent.append(section);
}
function renderCompactionProposal(result){
  const box=$('chat-compact-proposal');box.replaceChildren();
  const proposal=result.proposal,counts=['new','updated','reused','promoted','dropped','expired'].map(k=>`${proposal[k].length} ${k}`).join(' · ');
  const summary=element('section',undefined,'compact-section');summary.append(element('h3','Summary that starts the short chat'),element('p',result.summary));box.append(summary);
  compactionList('Long-term memory (kept)',proposal.long_term,box);
  compactionList('Short-term memory (kept, expires when unused)',proposal.short_term,box);
  if(proposal.dropped.length||proposal.expired.length)compactionList('Moved to the memory archive',[...proposal.dropped,...proposal.expired],box);
  box.append(element('p',`${proposal.kept} items kept · ${counts}. The full chat (${result.message_count} messages) will be archived and stays searchable.`,'muted'));
}
async function startCompaction(){
  closeChatMenu(true);
  if(!state.history?.messages.length)return;
  const project=state.project,selection=state.selection;
  Object.assign(compaction,{id:null,project,selection});
  $('chat-compact-proposal').replaceChildren();$('chat-compact-error').textContent='';
  $('chat-compact-submit').disabled=true;$('chat-compact-name-label').hidden=true;
  $('chat-compact-name').value=`${projectName(project)} · ${new Date().toLocaleString()} · before compaction`.slice(0,120);
  $('chat-compact-status').textContent='Preparing a proposal with one model call. Nothing changes until you apply it.';
  $('chat-compact').showModal();
  try{
    let result=await api('/api/chat/compact',{method:'POST',body:JSON.stringify({project_id:project})});
    compaction.id=result.id;
    while(result.status==='running'){
      await new Promise(resolve=>setTimeout(resolve,1500));
      if(compaction.id!==result.id||!$('chat-compact').open)return;
      result=await api(`/api/chat/compact/${encodeURIComponent(result.id)}?${compactionQuery(project)}`);
    }
    if(compaction.id!==result.id||!$('chat-compact').open)return;
    if(result.status!=='ready'){$('chat-compact-status').textContent='No proposal; the chat is unchanged.';$('chat-compact-error').textContent=result.error||'Compaction failed';return;}
    compaction.result=result;
    $('chat-compact-status').textContent='Review what will be kept. Apply archives the full chat and continues from a short chat with this summary.';
    renderCompactionProposal(result);$('chat-compact-name-label').hidden=false;$('chat-compact-submit').disabled=false;
  }catch(error){$('chat-compact-status').textContent='No proposal; the chat is unchanged.';$('chat-compact-error').textContent=error.message;}
}
function discardCompaction(){
  const id=compaction.id,project=compaction.project;compaction.id=null;
  if(id)api('/api/chat/compact/discard',{method:'POST',body:JSON.stringify({project_id:project,proposal_id:id})}).catch(()=>{});
}
$('compact-chat').onclick=()=>startCompaction();
$('chat-compact-cancel').onclick=()=>$('chat-compact').close();
$('chat-compact').addEventListener('close',()=>{if(compaction.id)discardCompaction();});
$('chat-compact-form').onsubmit=async event=>{
  event.preventDefault();const button=$('chat-compact-submit'),result=compaction.result;
  if(button.disabled||!compaction.id||!result)return;button.disabled=true;
  try{
    const receipt=await api('/api/chat/compact/apply',{method:'POST',body:JSON.stringify({project_id:compaction.project,proposal_id:compaction.id,conversation_id:result.conversation_id,last_message_id:result.last_message_id,confirmed:true,title:$('chat-compact-name').value})});
    compaction.id=null;$('chat-compact').close();
    $('chat-notice').textContent=receipt.message;$('chat-notice').hidden=false;clearTimeout(state.chatNoticeTimer);state.chatNoticeTimer=setTimeout(()=>$('chat-notice').hidden=true,8000);
    if(compaction.selection===state.selection)await loadHistory(compaction.selection);
  }catch(error){$('chat-compact-error').textContent=error.message;button.disabled=false;}
};
function renderCompactionNotice(){
  const notice=state.history?.compaction_notice;
  $('compaction-notice').hidden=!notice;$('compaction-notice-text').textContent=notice?.text||'';
}
$('compaction-notice-dismiss').onclick=action(async()=>{
  const project=state.project;$('compaction-notice').hidden=true;
  await api('/api/chat/compact/notice/dismiss',{method:'POST',body:JSON.stringify({project_id:project})});
  if(state.history&&project===state.project)state.history.compaction_notice=null;
});
// Settings panel: the select and the "Used:" line come from the same server record and route.
let chatMemorySettings=null;
function renderChatMemorySettings(data){
  chatMemorySettings=data;const saved=data.settings,select=$('chat-memory-model');
  const manager=document.createElement('option');manager.value='';manager.textContent='Same as the manager';select.replaceChildren(manager);
  for(const model of new Set([...data.models,...(saved.model?[saved.model]:[])])){const option=document.createElement('option');option.value=model;option.textContent=model;select.append(option);}
  select.value=saved.model||'';$('chat-memory-effort').value=saved.effort||'';$('chat-memory-effort').disabled=!saved.model;
  $('chat-memory-threshold').value=saved.threshold_chars;$('chat-memory-days').value=saved.short_term_days;
  $('chat-memory-effective').textContent=data.effective?`Used: ${data.effective.model} · ${data.effective.effort}${saved.model?'':' (the manager\'s choice)'} · promoted to long-term after ${data.promote_after} reuses · timeout ${Math.round(data.timeout_seconds/60)} min`:`Compaction model unavailable: ${data.error}`;
}
async function loadChatMemorySettings(){
  const project=state.project,data=await api(`/api/chat-memory/settings?${compactionQuery(project)}`);
  if(project===state.project)renderChatMemorySettings(data);
}
$('chat-memory-model').onchange=()=>{$('chat-memory-effort').disabled=!$('chat-memory-model').value;if(!$('chat-memory-model').value)$('chat-memory-effort').value='';};
$('chat-memory-form').onsubmit=action(async event=>{
  event.preventDefault();if(!chatMemorySettings)await loadChatMemorySettings();
  const body={revision:chatMemorySettings.settings.revision,model:$('chat-memory-model').value||null,effort:$('chat-memory-effort').value.trim()||null,
    threshold_chars:Number($('chat-memory-threshold').value),short_term_days:Number($('chat-memory-days').value)};
  const data=await api(`/api/chat-memory/settings?${compactionQuery(state.project)}`,{method:'PUT',body:JSON.stringify(body)});
  renderChatMemorySettings(data);$('chat-memory-status').textContent='Saved.';
});
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',()=>loadChatMemorySettings().catch(error=>{$('chat-memory-effective').textContent=error.message;}));
