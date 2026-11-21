'use strict';
// Office mode only (body.office, every instance but Main): a chat list in a left sidebar and + New. The server keeps
// one open chat per project (ai_manager/office_chats.py); switching asks the server, then reloads the chat as usual.
const officeChats={list:[],openId:null,search:'',timer:null,poll:null,loading:0,switching:false};
const officeWide=()=>matchMedia('(min-width:901px)').matches;
function isOffice(){return document.body.classList.contains('office');}
function officeSidebarOpen(){return !$('chat-sidebar').hidden;}
function setOfficeSidebar(open,remember=true){
  if(!isOffice())return;
  $('chat-sidebar').hidden=!open;$('chat-sidebar-backdrop').hidden=!open||officeWide();
  document.body.classList.toggle('chats-open',open);$('chat-list-button').setAttribute('aria-expanded',String(open));
  if(remember&&officeWide())localStorage.setItem('linko-office-sidebar',open?'open':'closed');
  clearInterval(officeChats.poll);officeChats.poll=null;
  if(open){loadOfficeChats().catch(()=>{});officeChats.poll=setInterval(()=>{if(!document.hidden)loadOfficeChats().catch(()=>{});},15000);}
  if(typeof updateViewport==='function')updateViewport();
}
async function loadOfficeChats(){
  if(!isOffice()||$('app').hidden)return;
  const request=++officeChats.loading,query=new URLSearchParams();if(officeChats.search)query.set('search',officeChats.search);
  const data=await api('/api/office/chats?'+query);
  if(request!==officeChats.loading)return;
  officeChats.list=data.chats;officeChats.openId=data.open_id;renderOfficeChats();
}
function renderOfficeChats(){
  const list=$('chat-list'),current=state.history?.conversation.id||officeChats.openId;list.replaceChildren();
  for(const chat of officeChats.list){
    const item=element('li',undefined,'chat-item'+(chat.id===current?' current':''));item.dataset.chat=chat.id;
    const open=element('button',undefined,'chat-open');open.type='button';open.title=chat.title;
    if(chat.id===current)open.setAttribute('aria-current','true');
    if(chat.pinned)open.append(element('span','📌','chat-pin-mark'));
    open.append(element('span',chat.title,chat.titled?'chat-title':'chat-title chat-untitled'));
    open.onclick=()=>openOfficeChat(chat.id);
    const actions=element('span',undefined,'chat-item-actions');
    const pin=element('button',chat.pinned?'★':'☆','chat-pin');pin.type='button';pin.setAttribute('aria-label',(chat.pinned?'Unpin ':'Pin ')+chat.title);pin.title=chat.pinned?'Unpin':'Pin';
    pin.onclick=action(async()=>{await api(`/api/office/chats/${chat.id}`,{method:'PATCH',body:JSON.stringify({pinned:!chat.pinned})});await loadOfficeChats();});
    const rename=element('button','✎','chat-rename');rename.type='button';rename.setAttribute('aria-label','Rename '+chat.title);rename.title='Rename';
    rename.onclick=()=>editor('Rename chat',[{name:'title',label:'Chat name',value:chat.titled?chat.title:'',required:true}],async values=>{
      await api(`/api/office/chats/${chat.id}`,{method:'PATCH',body:JSON.stringify({title:values.title})});await loadOfficeChats();return null;},false);
    const remove=element('button','×','chat-delete');remove.type='button';remove.setAttribute('aria-label','Delete '+chat.title);remove.title='Delete';
    remove.onclick=()=>confirmOfficeDelete(chat);
    actions.append(pin,rename,remove);item.append(open,actions);list.append(item);
  }
  $('chat-list-status').textContent=officeChats.list.length?'':officeChats.search?'No chats match.':'No chats yet.';
}
function officeBusy(){
  if(state.pending.has(state.project)||submissions.has(state.project)){notice('Wait until your message is sent, then switch chats.');return true;}
  if(attachmentState.uploads.has(state.project)){notice('Wait until the file finishes uploading, then switch chats.');return true;}
  return false;
}
// The server changed which chat is open: drop this project's cached chat and load the open one fresh.
async function showOpenedChat(){
  state.source?.close();state.source=null;state.history=null;$('messages').replaceChildren();
  chatCache.delete(state.project);persistChatCache();
  attachmentState.drafts.set(state.project,[]);saveAttachments();  // draft files belong to the chat they were sent to
  if(state.view!=='chat')showView('chat');
  await selectProject(state.project);
  if(!officeWide())setOfficeSidebar(false,false);
  loadOfficeChats().catch(()=>{});
}
async function officeSwitch(request){
  if(officeChats.switching||officeBusy())return;
  officeChats.switching=true;
  try{await request();await showOpenedChat();}
  catch(error){notice(error.message);}
  finally{officeChats.switching=false;}
}
function openOfficeChat(id){
  if(id===state.history?.conversation.id){if(state.view!=='chat')showView('chat');if(!officeWide())setOfficeSidebar(false,false);return;}
  return officeSwitch(()=>api(`/api/office/chats/${id}/open`,{method:'POST'}));
}
function newOfficeChat(){return officeSwitch(()=>api('/api/office/chats',{method:'POST'}));}
function confirmOfficeDelete(chat){
  const dialog=$('office-confirm');if(dialog.open)return;
  $('office-confirm-description').textContent=`“${chat.title}” moves to the chat archive. It is not deleted permanently.`;
  $('office-confirm-error').textContent='';$('office-confirm-submit').disabled=false;
  $('office-confirm-cancel').onclick=()=>dialog.close();
  $('office-confirm-form').onsubmit=async event=>{
    event.preventDefault();$('office-confirm-submit').disabled=true;
    try{
      const result=await api(`/api/office/chats/${chat.id}/delete`,{method:'POST',body:JSON.stringify({confirmed:true})});
      dialog.close();
      if(result.opened_conversation_id)await showOpenedChat();else await loadOfficeChats();
    }catch(error){$('office-confirm-error').textContent=error.message;$('office-confirm-submit').disabled=false;}
  };
  dialog.showModal();
}
$('chat-list-button').onclick=()=>{officeChats.started=true;setOfficeSidebar(!officeSidebarOpen());};
$('chat-sidebar-backdrop').onclick=()=>setOfficeSidebar(false,false);
$('new-chat').onclick=()=>newOfficeChat();
$('office-settings').onclick=()=>{showView('settings');if(typeof loadChatMemorySettings==='function')loadChatMemorySettings().catch(error=>notice(error.message));if(!officeWide())setOfficeSidebar(false,false);};
$('chat-search').oninput=()=>{clearTimeout(officeChats.timer);officeChats.timer=setTimeout(()=>{officeChats.search=$('chat-search').value.trim();loadOfficeChats().catch(error=>notice(error.message));},250);};
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&isOffice()&&officeSidebarOpen()&&!officeWide()&&!document.querySelector('dialog[open]'))setOfficeSidebar(false,false);});
// Start: wide screens keep the owner's last choice (open by default); narrow screens start closed.
function startOfficeChats(){
  if(!isOffice()||$('app').hidden||officeChats.started)return;
  officeChats.started=true;
  setOfficeSidebar(officeWide()&&localStorage.getItem('linko-office-sidebar')!=='closed',false);
}
const officeStart=setInterval(()=>{if(state.ready){clearInterval(officeStart);startOfficeChats();}},100);
// A sent message can change the list order and, a moment later, the chat title.
document.addEventListener('submit',event=>{if(event.target.id==='chat-form'&&isOffice()){for(const delay of [400,1500,6000,15000])setTimeout(()=>{if(officeSidebarOpen())loadOfficeChats().catch(()=>{});},delay);}},true);
