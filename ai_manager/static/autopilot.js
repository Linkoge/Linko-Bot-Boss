'use strict';
// Autopilot (ai_manager/autopilot.py, docs/AUTOPILOT.md): the on/off switch in the harness menu, a dot on the harness
// button while it is on, and the small Settings > Autopilot panel (status, the one setup command, goals).
// The switch is only the local soft switch: it is disabled until scripts/autopilot_setup.sh set both locks, and it can
// never set a lock. Hidden on restricted instances.
const autopilot={data:null};
function renderAutopilotSwitch(){
  const level=state.autopilot,setUp=level==='full',on=setUp&&!!state.autopilotOn;
  $('harness-button').dataset.autopilot=on&&!state.restricted?'on':'off';
  const group=$('autopilot-group');group.hidden=!!state.restricted||!level||!!harnessMenu.env;/* this machine only */if(group.hidden)return;
  const toggle=$('autopilot-toggle');toggle.disabled=!setUp;toggle.checked=on;toggle.setAttribute('aria-checked',String(on));
  $('autopilot-setup-hint').hidden=setUp;
}
function applyAutopilot(data){state.autopilot=data.status.level;state.autopilotOn=data.on;renderAutopilotSwitch();}
async function refreshAutopilot(){
  if(state.restricted)return;
  try{const auth=await api('/api/auth-status');state.autopilot=auth.autopilot;state.autopilotOn=auth.autopilot_on;renderAutopilotSwitch();}catch{}
}
function autopilotGoalButtons(goal){
  const box=element('div',undefined,'autopilot-goal-actions');
  const ops={active:['pause','stop'],waiting:['resume','stop'],paused:['resume','stop']}[goal.status]||[];
  for(const op of ops){
    const button=element('button',op[0].toUpperCase()+op.slice(1));button.type='button';button.dataset.op=op;
    button.onclick=action(async()=>{await api(`/api/autopilot/goals/${encodeURIComponent(goal.id)}/${op}`,{method:'POST',body:'{}'});await loadAutopilot();});
    box.append(button);
  }
  return box;
}
function renderAutopilotGoals(goals){
  const list=$('autopilot-goals');list.replaceChildren();
  if(!goals.length){list.append(element('p','No goals yet. Describe one in a chat: “Autopilot goal: …”.','muted'));return;}
  for(const goal of goals){
    const card=element('div',undefined,'autopilot-goal');card.dataset.goal=goal.id;card.dataset.status=goal.status;
    const head=element('div',undefined,'autopilot-goal-head');
    head.append(element('strong',goal.title),element('span',`${goal.status} · ${Math.round((goal.score||0)*100)}%`,'muted'),autopilotGoalButtons(goal));
    card.append(head);
    if(goal.pause_reason)card.append(element('p',goal.pause_reason,'autopilot-goal-reason'));
    list.append(card);
  }
}
function renderAutopilot(data){
  autopilot.data=data;applyAutopilot(data);
  const setUp=data.set_up,partly=!setUp&&data.status.level==='partial';
  $('autopilot-state').textContent=data.main?'Main machine: Autopilot is for a dedicated machine.':
    `Set up: ${setUp?'yes':partly?'partly':'no'} · ${setUp&&data.on?'on':'off'}`;
  $('autopilot-setup').hidden=setUp||!data.command;$('autopilot-setup-command').textContent=data.command||'';
  renderAutopilotGoals(data.goals);
}
async function loadAutopilot(){if(state.restricted)return;renderAutopilot(await api('/api/autopilot'));}
for(const button of document.querySelectorAll('#autopilot-panel [data-copy]'))button.onclick=action(async()=>{
  await copyText($(button.dataset.copy).textContent);const label=button.textContent;button.textContent='Copied';setTimeout(()=>{button.textContent=label;},1500);
});
$('autopilot-toggle').onchange=async()=>{
  const toggle=$('autopilot-toggle'),wanted=toggle.checked;toggle.disabled=true;
  try{const data=await api('/api/autopilot/switch',{method:'POST',body:JSON.stringify({on:wanted})});applyAutopilot(data);if(!$('view-settings').hidden)renderAutopilot(data);}
  catch(error){$('harness-status').textContent=error.message;await refreshAutopilot();}
  finally{renderAutopilotSwitch();}
};
const openAutopilot=()=>loadAutopilot().catch(error=>{$('autopilot-state').textContent=error.message;});
$('autopilot-setup-link').onclick=event=>{
  event.preventDefault();closeHarnessMenu();showView('settings');openAutopilot();$('autopilot-panel').scrollIntoView({block:'start'});
};
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',openAutopilot);
$('harness-environment').addEventListener('change',renderAutopilotSwitch);
$('harness-button').addEventListener('click',()=>{if(!$('harness-menu').hidden)refreshAutopilot();});
renderAutopilotSwitch();
