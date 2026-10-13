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
// Bind native selection and return focus to the context that actually opened the picker.
let attachmentPicker=null;
function restoreAttachmentFocus(picker,controls=[$('attach'),$('attachment-input')]){
  if(!picker?.focused||picker.project!==state.project||picker.selection!==state.selection||picker.conversation!==state.history?.conversation.id||state.view!=='chat'||$('app').hidden)return;
  // Do not steal focus if the owner selected another field/control while returning.
  if(![document.body,$('message-input'),...controls].includes(document.activeElement))return;
  if(document.activeElement===$('message-input'))return;
  $('message-input').focus({preventScroll:true});
  if($('message-input').value===picker.value)$('message-input').setSelectionRange(picker.start,picker.end,picker.direction);
}
$('attach').onclick=()=>{
  if($('attach').disabled||!state.history)return;
  const draft=$('message-input');
  attachmentPicker={project:state.project,conversation:state.history.conversation.id,selection:state.selection,focused:document.activeElement===draft,value:draft.value,start:draft.selectionStart,end:draft.selectionEnd,direction:draft.selectionDirection};
  // No await, timer, focus call or cancelled click before trusted picker activation.
  $('attachment-input').click();
};
$('attachment-input').addEventListener('cancel',()=>{
  const picker=attachmentPicker;attachmentPicker=null;restoreAttachmentFocus(picker);
});
// Some mobile browsers resume the window before dispatching change/cancel.
function attachmentPickerReturned(){
  const picker=attachmentPicker;
  if(picker&&!document.hidden)requestAnimationFrame(()=>{if(attachmentPicker===picker)restoreAttachmentFocus(picker);});
}
window.addEventListener('focus',attachmentPickerReturned);
document.addEventListener('visibilitychange',attachmentPickerReturned);
$('attachment-input').onchange=async()=>{
  const picker=attachmentPicker;attachmentPicker=null;
  const project=picker?picker.project:state.project,conversation=picker?picker.conversation:state.history?.conversation.id,files=[...$('attachment-input').files];$('attachment-input').value='';
  restoreAttachmentFocus(picker);
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
