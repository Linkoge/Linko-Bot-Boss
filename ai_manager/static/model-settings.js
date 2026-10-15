'use strict';
let modelPanel=null,modelSaving=false,modelOpener=null,modelPending=0,modelOpenVersion=0,modelOpening=false;
const modelDialog=$('model-dialog');
const PROVIDER_LOGOS={
  codex:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"/></svg>',
  claude:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path d="M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z"/></svg>'};
const PROVIDER_NAMES={codex:'Codex',claude:'Claude'};
function setProviderIcon(provider){
  if(!PROVIDER_LOGOS[provider])return;
  if(typeof setUsageProvider==='function')setUsageProvider(provider);
  const b=$('model-settings-button');b.innerHTML=PROVIDER_LOGOS[provider];b.dataset.provider=provider;
  b.setAttribute('aria-label',`Manager and worker settings, ${PROVIDER_NAMES[provider]} manager selected`);b.title=`${PROVIDER_NAMES[provider]} settings`;
}
function modelButton(text,label,selected,run,disabled=false){
  const b=element('button',text);b.type='button';b.setAttribute('aria-label',label);b.setAttribute('aria-pressed',String(selected));b.disabled=disabled||modelSaving||modelOpening;b.onclick=run;return b;
}
function modelMutation(run){
  modelPending++;modelSaving=true;$('model-save-status').textContent='Saving settings';
  modelDialog.querySelectorAll('button[data-choice]').forEach(b=>b.disabled=true);
  return orderModelIntent(async()=>{
    try{
      await run();modelSelectionError=null;$('model-error').textContent='';$('model-save-status').textContent='Settings saved';setProviderIcon(modelPanel.provider);
    }catch(error){
      modelSelectionError='Model selection could not be confirmed: '+error.message;
      $('model-error').textContent=modelSelectionError;$('model-save-status').textContent='Selection not confirmed';notice(modelSelectionError);
      try{modelPanel=await api('/api/model-settings');setProviderIcon(modelPanel.provider);}
      catch{notice(modelSelectionError+' Saved settings could not be refreshed; reopen settings when connected.');}
    }finally{modelPending--;modelSaving=modelPending>0;if(modelPanel)renderModels();}
  });
}
function recallManagerFavorite(slot){
  if(modelSaving||modelOpening||!modelPanel.favorites[slot]?.available)return;
  modelDialog.close();modelOpener?.focus({preventScroll:true}); // Close and restore focus in this click turn, before starting network work.
  modelMutation(async()=>{const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);try{modelPanel=await api(`/api/manager-favorites/${slot}/recall`,{method:'POST',signal:controller.signal});}finally{clearTimeout(timeout);}});
}
async function changeModels(change){
  return modelMutation(async()=>{
    const next=structuredClone(modelPanel.settings);change(next);
    modelPanel.settings=await api('/api/model-settings',{method:'PUT',body:JSON.stringify(next)});
    modelPanel.provider=providerOf(modelPanel.settings.manager.model);
  });
}
function providerOf(model){return /^claude-|^(Haiku|Sonnet|Opus)$/.test(model)?'claude':'codex';}
function modelRow(label){const row=element('div',undefined,'model-choice-row');row.setAttribute('role','group');row.setAttribute('aria-label',label);return row;}
function addChoice(row,b,key){b.dataset.choice=key;row.append(b);return b;}
function addModel(row,m,label,selected,run,disabled,key){
  const b=addChoice(row,modelButton(m.name,label,selected,run,disabled),key);b.classList.add('model-family');
}
function addEffort(row,e,label,selected,run,disabled,key){
  const b=addChoice(row,modelButton(titleLevel(e),label,selected,run,disabled),key);b.classList.add('effort-choice');
}
function selectModel(c,role,m){const pair=c[role]||{effort:'medium'};pair.model=m.model;if(!m.levels.includes(pair.effort))pair.effort=m.levels[0];c[role]=pair;}
const LEVEL_RANK=['minimal','low','medium','high','xhigh','max','ultra'];
function renderPair(container,role,label){
  container.replaceChildren();
  const pair=modelPanel.settings[role];
  if(role==='worker'){ // Automatic: the manager picks the model and effort for each task
    const row=modelRow(`${label} selection`);
    addChoice(row,modelButton('Automatic',`${label} Automatic`,!pair,()=>changeModels(c=>{c.worker=null;})),'worker-auto');
    container.append(row);
  }
  for(const provider of ['codex','claude']){
    const {choices}=modelPanel.providers[provider];
    if(!choices.length)continue;
    const models=modelRow(`${label} ${PROVIDER_NAMES[provider]} model`),levels=modelRow(`${label} ${PROVIDER_NAMES[provider]} effort`);
    const selected=choices.find(m=>m.model===pair?.model);
    for(const m of choices)addModel(models,m,`${label} ${m.name}`,pair?.model===m.model,()=>changeModels(c=>selectModel(c,role,m)),!m.levels.length,`${role}-${m.name}`);
    const all=[...new Set(choices.flatMap(m=>m.levels))].sort((a,b)=>(LEVEL_RANK.indexOf(a)+1||99)-(LEVEL_RANK.indexOf(b)+1||99));
    for(const e of all)addEffort(levels,e,`${label} ${PROVIDER_NAMES[provider]} ${titleLevel(e)}`,!!selected&&pair.effort===e,
      ()=>changeModels(c=>{c[role].effort=e;}),!selected?.levels.includes(e),`${role}-${provider}-${e}`);
    container.append(models,levels);
  }
}
function renderModels(){
  const focused=modelDialog.contains(document.activeElement)?document.activeElement.dataset.choice:null;
  renderPair($('manager-choices'),'manager','Manager');
  renderPair($('worker-choices'),'worker','Worker');
  const favorites=$('manager-favorites');favorites.replaceChildren();
  const managerPair=modelPanel.settings.manager,canInsert=Object.values(modelPanel.providers).some(v=>v.choices.some(m=>m.model===managerPair.model&&m.levels.includes(managerPair.effort)));
  for(let slot=0;slot<2;slot++){
    const item=modelPanel.favorites[slot],row=element('div',undefined,'favorite-row');
    const insert=addChoice(row,modelButton('Insert',`Insert current Manager into favorite ${slot+1}`,false,
      ()=>modelMutation(async()=>{modelPanel.favorites=await api(`/api/manager-favorites/${slot}`,{method:'PUT'});}),!canInsert),`favorite-insert-${slot}`);
    insert.removeAttribute('aria-pressed');
    const matching=!!item?.available&&item.pair.model===managerPair.model&&item.pair.effort===managerPair.effort;
    const entry=addChoice(row,modelButton('',item?`Recall Manager favorite ${slot+1}`:`Empty Manager favorite ${slot+1}`,matching,
      ()=>recallManagerFavorite(slot),!item?.available),`favorite-recall-${slot}`);
    entry.classList.add('favorite-entry');
    if(item){
      const label=element('span',undefined,'favorite-label');
      label.append(element('span',titleLevel(item.pair.effort),'favorite-effort'));
      const model=modelPanel.providers[item.provider]?.choices.find(m=>m.model===item.pair.model);
      label.append(element('span',model?.name||item.pair.model));
      if(!item.available)label.append(element('span','Unavailable','favorite-effort'));
      entry.append(label);
      const logo=element('span',undefined,'favorite-logo');logo.innerHTML=PROVIDER_LOGOS[item.provider]||'';entry.append(logo);
    }else entry.append(element('span','Empty','favorite-effort'));
    favorites.append(row);
  }
  if(focused)modelDialog.querySelector(`[data-choice="${focused}"]`)?.focus({preventScroll:true});
}
function titleLevel(value){return value[0].toUpperCase()+value.slice(1);}
async function openModels(event){
  const version=++modelOpenVersion;modelOpening=true;
  modelOpener=event?.currentTarget||$('model-settings-button');closeChatMenu();closeProjectMenu();
  if(modelPanel)renderModels();$('model-error').textContent=modelSelectionError||'';
  $('model-save-status').textContent=modelSaving?'Saving settings':'Loading settings';
  if(!modelDialog.open)modelDialog.showModal();modelDialog.scrollTop=0;$('model-close').focus({preventScroll:true});
  try{
    await orderModelIntent(async()=>{
      const panel=await api('/api/model-settings');
      if(version!==modelOpenVersion||!modelDialog.open)return;
      modelPanel=panel;setProviderIcon(panel.provider);modelOpening=false;renderModels();
    });
  }finally{if(version===modelOpenVersion){modelOpening=false;if(modelPanel)renderModels();}}
}
$('model-settings-button').onclick=action(openModels);
$('load-models').onclick=action(openModels);
$('model-close').onclick=()=>modelDialog.close();
modelDialog.addEventListener('close',()=>{if(modelDialog.open)return;modelOpenVersion++;modelOpening=false;modelOpener?.focus({preventScroll:true});});
modelDialog.addEventListener('click',event=>{if(event.target===modelDialog){const r=modelDialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)modelDialog.close();}});
modelDialog.addEventListener('keydown',event=>{
  if(event.key==='Tab'){
    const buttons=[...modelDialog.querySelectorAll('button:not(:disabled)')],first=buttons[0],last=buttons.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
  if(['ArrowRight','ArrowLeft','ArrowDown','ArrowUp','Home','End'].includes(event.key)&&document.activeElement.matches('[data-choice]')){
    const group=document.activeElement.parentElement,buttons=[...group.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);
    if(!buttons.length)return;event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowLeft'||event.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length;
    buttons[next].focus();
  }
});
