'use strict';
let modelPanel=null,modelSaving=false,modelOpener=null;
const modelDialog=$('model-dialog');
const PROVIDER_LOGOS={
  codex:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"/></svg>',
  claude:'<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path d="M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z"/></svg>'};
const PROVIDER_NAMES={codex:'Codex',claude:'Claude'};
function setProviderIcon(provider){
  if(!PROVIDER_LOGOS[provider])return;
  const b=$('model-settings-button');b.innerHTML=PROVIDER_LOGOS[provider];b.dataset.provider=provider;
  b.setAttribute('aria-label',`Manager and bot settings, ${PROVIDER_NAMES[provider]} selected`);b.title=`${PROVIDER_NAMES[provider]} settings`;
}
function providerView(){return modelPanel.providers[modelPanel.provider];}
async function changeProvider(provider){
  if(modelSaving||provider===modelPanel.provider)return;
  modelSaving=true;$('model-save-status').textContent='Switching provider';
  try{await api('/api/model-provider',{method:'PUT',body:JSON.stringify({provider})});modelPanel.provider=provider;setProviderIcon(provider);$('model-error').textContent='';$('model-save-status').textContent=`${PROVIDER_NAMES[provider]} selected`;}
  catch(error){$('model-error').textContent=error.message;$('model-save-status').textContent='Provider unchanged';}
  finally{modelSaving=false;renderModels();}
}
function modelButton(text,label,selected,run,disabled=false){
  const b=element('button',text);b.type='button';b.setAttribute('aria-label',label);b.setAttribute('aria-pressed',String(selected));b.disabled=disabled||modelSaving;b.onclick=run;return b;
}
async function changeModels(change){
  if(modelSaving)return;
  const view=providerView(),next=structuredClone(view.settings);change(next);modelSaving=true;$('model-save-status').textContent='Saving settings';
  modelDialog.querySelectorAll('button[data-choice]').forEach(b=>b.disabled=true);
  try{view.settings=await api('/api/model-settings',{method:'PUT',body:JSON.stringify({...next,provider:modelPanel.provider})});$('model-error').textContent='';$('model-save-status').textContent='Settings saved';}
  catch(error){$('model-error').textContent=error.message;$('model-save-status').textContent='Settings unchanged';}
  finally{modelSaving=false;renderModels();}
}
function modelRow(label){const row=element('div',undefined,'model-choice-row');row.setAttribute('role','group');row.setAttribute('aria-label',label);return row;}
function addChoice(row,b,key){b.dataset.choice=key;if(/^(manager|manual)-(Luna|Sol|Astra|Haiku|Sonnet|Opus)$/.test(key))b.classList.add('model-family');row.append(b);}
function renderModels(){
  const focused=modelDialog.contains(document.activeElement)?document.activeElement.dataset.choice:null;
  const {settings,choices}=providerView(),bot=settings.bot,automatic=bot.mode==='automatic';
  const providers=$('provider-choices');providers.replaceChildren();
  for(const p of ['codex','claude']){
    const b=modelButton('',`Use ${PROVIDER_NAMES[p]}`,modelPanel.provider===p,()=>changeProvider(p));
    b.innerHTML=PROVIDER_LOGOS[p];b.append(element('span',PROVIDER_NAMES[p]));b.classList.add('provider-choice');addChoice(providers,b,`provider-${p}`);
  }
  const manager=$('manager-choices');manager.replaceChildren();
  const models=modelRow('Manager model'),levels=modelRow('Manager reasoning');
  for(const m of choices)addChoice(models,modelButton(m.name,`Manager ${m.name}`,settings.manager.model===m.model,
    ()=>changeModels(c=>{c.manager.model=m.model;}),!m.levels.includes(settings.manager.effort)),`manager-${m.name}`);
  const managerModel=choices.find(m=>m.model===settings.manager.model);
  for(const e of ['low','medium','high'])addChoice(levels,modelButton(titleLevel(e),`Manager ${titleLevel(e)}`,settings.manager.effort===e,
    ()=>changeModels(c=>{c.manager.effort=e;}),!managerModel?.levels.includes(e)),`manager-${e}`);
  manager.append(models,levels);
  const mode=$('bot-mode');mode.replaceChildren();
  for(const value of ['manual','automatic'])addChoice(mode,modelButton(titleLevel(value),`Bot ${titleLevel(value)}`,bot.mode===value,
    ()=>changeModels(c=>{c.bot.mode=value;})),`bot-${value}`);
  const sections=$('bot-choices');sections.replaceChildren();
  for(const m of choices){
    const section=element('div',undefined,'bot-model-section'),manual=modelRow(`Bot manual selection ${m.name}`);
    manual.classList.toggle('manual-disabled',automatic);
    const supported=m.levels.includes(bot.manual.effort);
    addChoice(manual,modelButton(m.name,`Bot manual ${m.name}`,bot.manual.model===m.model,
      ()=>changeModels(c=>{c.bot.manual.model=m.model;}),automatic||!supported),`manual-${m.name}`);
    for(const e of ['low','medium','high'])addChoice(manual,modelButton(titleLevel(e),`Bot manual ${m.name} ${titleLevel(e)}`,
      bot.manual.model===m.model&&bot.manual.effort===e,()=>changeModels(c=>{c.bot.manual={model:m.model,effort:e};}),automatic||!m.levels.includes(e)),`manual-${m.name}-${e}`);
    const policy=modelRow(`Automatic policy ${m.name}`);policy.classList.add('policy-row');policy.classList.toggle('policy-disabled',!automatic);
    for(const e of [null,'low','medium','high']){
      const cell=element('div',undefined,'policy-cell');cell.setAttribute('role','group');cell.setAttribute('aria-label',`Automatic ${m.name} ${e?titleLevel(e):'model'} policy`);
      const allowed=e?bot.policy[m.model].levels[e]:bot.policy[m.model].allowed;
      for(const [value,glyph,word] of [[true,'✓','Allow'],[false,'×','Deny']])addChoice(cell,modelButton(glyph,`${word} automatic ${m.name}${e?' '+titleLevel(e):''}`,allowed===value,
        ()=>changeModels(c=>{if(e)c.bot.policy[m.model].levels[e]=value;else c.bot.policy[m.model].allowed=value;}),!automatic||(!!e&&!m.levels.includes(e))),`policy-${m.name}-${e||'model'}-${word}`);
      policy.append(cell);
    }
    section.append(manual,policy);sections.append(section);
  }
  if(focused)modelDialog.querySelector(`[data-choice="${focused}"]`)?.focus({preventScroll:true});
}
function titleLevel(value){return value[0].toUpperCase()+value.slice(1);}
async function openModels(event){
  modelOpener=event?.currentTarget||$('model-settings-button');closeChatMenu();closeProjectMenu();
  modelPanel=await api('/api/model-settings');setProviderIcon(modelPanel.provider);renderModels();$('model-error').textContent='';modelDialog.showModal();$('model-close').focus({preventScroll:true});
}
$('model-settings-button').onclick=action(openModels);
$('load-models').onclick=action(openModels);
$('model-close').onclick=()=>modelDialog.close();
modelDialog.addEventListener('close',()=>modelOpener?.focus({preventScroll:true}));
modelDialog.addEventListener('click',event=>{if(event.target===modelDialog){const r=modelDialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)modelDialog.close();}});
modelDialog.addEventListener('keydown',event=>{
  if(event.key==='Tab'){
    const buttons=[...modelDialog.querySelectorAll('button:not(:disabled)')],first=buttons[0],last=buttons.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus({preventScroll:true});}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus({preventScroll:true});}
  }
  if(['ArrowRight','ArrowLeft','ArrowDown','ArrowUp','Home','End'].includes(event.key)&&document.activeElement.matches('[data-choice]')){
    const group=document.activeElement.parentElement,buttons=[...group.querySelectorAll('button:not(:disabled)')],index=buttons.indexOf(document.activeElement);
    if(!buttons.length)return;event.preventDefault();
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowLeft'||event.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length;
    buttons[next].focus({preventScroll:true});
  }
});
