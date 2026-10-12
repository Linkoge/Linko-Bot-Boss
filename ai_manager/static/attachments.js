'use strict';
const attachmentState={drafts:new Map(JSON.parse(sessionStorage.getItem('linko-attachments-v1')||'[]')),uploads:new Set()};
function attachmentDraft(project){return attachmentState.drafts.get(project)||[];}
function saveAttachments(){sessionStorage.setItem('linko-attachments-v1',JSON.stringify([...attachmentState.drafts]));}
function clearSentAttachments(project,ids){attachmentState.drafts.set(project,attachmentDraft(project).filter(file=>!ids.includes(file.id)));saveAttachments();renderAttachments();}
function renderAttachments(){
  const files=attachmentDraft(state.project),list=$('attachment-list');list.replaceChildren();list.hidden=!files.length;
  for(const file of files){const row=element('div'),link=element('a',file.name);link.href='/api/attachments/'+file.id;link.download=file.name;const remove=element('button','×');remove.type='button';remove.setAttribute('aria-label','Remove '+file.name+' from draft');remove.disabled=state.pending.has(state.project)||submissions.has(state.project);remove.onclick=()=>clearSentAttachments(state.project,[file.id]);row.append(link,remove);list.append(row);}
  const uploading=attachmentState.uploads.has(state.project);
  $('attach').disabled=uploading||state.pending.has(state.project)||submissions.has(state.project)||!state.history;
  $('attachment-status').hidden=!uploading;$('attachment-status').textContent=uploading?'Uploading…':'';
  if(typeof renderAudio==='function')renderAudio();
}
$('attach').onclick=()=>{$('attachment-input').click();};
$('attachment-input').onchange=async()=>{
  const project=state.project,conversation=state.history?.conversation.id,files=[...$('attachment-input').files];$('attachment-input').value='';
  if(!files.length||!conversation||attachmentState.uploads.has(project))return;
  if(files.length+attachmentDraft(project).length>4){notice('Use at most four attachments per message.');return;}
  attachmentState.uploads.add(project);renderAttachments();
  try{
    for(const file of files){
      if(!file.size||file.size>8*1024*1024)throw new Error('Each file must contain data and be at most 8 MiB.');
      const query=new URLSearchParams({name:file.name,conversation_id:conversation});if(project)query.set('project_id',project);
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),130000);
      let uploaded;try{uploaded=await api('/api/attachments?'+query,{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file,signal:controller.signal});}finally{clearTimeout(timeout);}
      // Do not add to a replaced chat; switching projects keeps the original draft.
      if(project===state.project&&conversation!==state.history?.conversation.id){notice('Chat changed. Uploaded file was not added to the new draft.');continue;}
      attachmentState.drafts.set(project,[...attachmentDraft(project),uploaded]);saveAttachments();renderAttachments();
    }
  }catch(error){notice(error.message+' Successfully uploaded files remain in the original draft.');}
  finally{attachmentState.uploads.delete(project);renderAttachments();}
};
function renderMessageAttachments(row,message){
  if(row.dataset.attachments===JSON.stringify(message.attachments||[]))return;
  row.dataset.attachments=JSON.stringify(message.attachments||[]);row.querySelector('.message-attachments')?.remove();
  if(!message.attachments?.length)return;
  const links=element('div',undefined,'message-attachments');
  for(const file of message.attachments){const link=element('a',file.name);link.href='/api/attachments/'+file.id;link.download=file.name;links.append(link);}
  row.append(links);
}
renderAttachments();
