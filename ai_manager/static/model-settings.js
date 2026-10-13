'use strict';
let modelPanel=null,modelSaving=false,modelOpener=null;
const modelDialog=$('model-dialog');
function modelButton(text,label,selected,run,disabled=false){
  const b=element('button',text);b.type='button';b.setAttribute('aria-label',label);b.setAttribute('aria-pressed',String(selected));b.disabled=disabled||modelSaving;b.onclick=run;return b;
}
async function changeModels(change){
  if(modelSaving)return;
  const next=structuredClone(modelPanel.settings);change(next);modelSaving=true;$('model-save-status').textContent='Saving settings';
  modelDialog.querySelectorAll('button[data-choice]').forEach(b=>b.disabled=true);
  try{modelPanel.settings=await api('/api/model-settings',{method:'PUT',body:JSON.stringify(next)});$('model-error').textContent='';$('model-save-status').textContent='Settings saved';}
  catch(error){$('model-error').textContent=error.message;$('model-save-status').textContent='Settings unchanged';}
  finally{modelSaving=false;renderModels();}
}
function modelRow(label){const row=element('div',undefined,'model-choice-row');row.setAttribute('role','group');row.setAttribute('aria-label',label);return row;}
function addChoice(row,b,key){b.dataset.choice=key;row.append(b);}
function renderModels(){
  const focused=modelDialog.contains(document.activeElement)?document.activeElement.dataset.choice:null;
  const {settings,choices}=modelPanel,bot=settings.bot,automatic=bot.mode==='automatic';
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
  modelPanel=await api('/api/model-settings');renderModels();$('model-error').textContent='';modelDialog.showModal();$('model-close').focus({preventScroll:true});
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
