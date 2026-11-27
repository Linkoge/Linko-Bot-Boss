'use strict';
// Settings > Autopilot (ai_manager/autopilot.py, docs/AUTOPILOT.md) and the header badge "Autopilot: full/partial/off".
// Read-only about the two locks: the panel shows the exact lines and commands to copy; nothing here can set a lock.
// Goals: create (with reference images), pause/resume/stop; the global Pause button. Hidden on restricted instances.
const autopilot={data:null};
function renderAutopilotBadge(level=state.autopilot){
  const badge=$('autopilot-badge');
  badge.hidden=!!state.restricted||!level;if(badge.hidden)return;
  badge.dataset.level=level;badge.textContent=`Autopilot: ${level}`;
}
async function refreshAutopilotBadge(){
  if(state.restricted)return;
  try{const auth=await api('/api/auth-status');state.autopilot=auth.autopilot;renderAutopilotBadge();}catch{}
}
function lockText(on,yes,no){return on?`Detected: yes. ${yes}`:`Detected: no. ${no}`;}
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
  if(!goals.length){list.append(element('p','No goals yet.','muted'));return;}
  for(const goal of goals){
    const card=element('div',undefined,'autopilot-goal');card.dataset.goal=goal.id;card.dataset.status=goal.status;
    card.append(element('h4',goal.title));
    const facts=[`Status: ${goal.status}`,`Score: ${Math.round((goal.score||0)*100)}%`,`Rounds: ${goal.rounds}`,`Approach ${goal.approach}`];
    if(goal.daily_budget)facts.push(`Today: ${goal.used_today}/${goal.daily_budget} tokens`);
    if(goal.deadline)facts.push(`Deadline: ${new Date(goal.deadline*1000).toLocaleDateString()}`);
    if(goal.images.length)facts.push(`${goal.images.length} reference image${goal.images.length>1?'s':''}`);
    card.append(element('p',facts.join(' · '),'muted autopilot-goal-facts'));
    if(goal.pause_reason)card.append(element('p',goal.pause_reason,'autopilot-goal-reason'));
    const items=element('ul');for(const item of goal.checklist)items.append(element('li',item));card.append(items);
    const last=goal.last_journal;
    if(last)card.append(element('p',`Last journal (${last.kind}): ${[last.done,last.result,last.next_idea&&'Next: '+last.next_idea].filter(Boolean).join(' — ')}`,'autopilot-goal-journal'));
    card.append(autopilotGoalButtons(goal));list.append(card);
  }
}
function renderAutopilot(data){
  autopilot.data=data;const status=data.status,guide=data.guide;
  state.autopilot=status.level;renderAutopilotBadge();
  $('autopilot-state').textContent=`Autopilot on this machine: ${status.level}${data.paused?' (paused)':''}.`+
    (status.level==='full'?` Goals run; a check every ${data.check_minutes} minutes while a step works; done at ${data.pass_percent}% plus a final check.`:status.config?' Goals can be saved but do not run until the OS lock is detected too.':' Nothing runs.');
  $('autopilot-config-lock').textContent=lockText(status.config,'The env line is loaded.','Add the env line below and restart.');
  $('autopilot-os-lock').textContent=lockText(status.os,'sudo -n true works.','Install the drop-in below (and the systemd override), then restart.');
  for(const [id,key] of [['autopilot-env-line','env_line'],['autopilot-env-command','env_command'],['autopilot-sudoers-content','sudoers_content'],['autopilot-sudoers-install','sudoers_install'],['autopilot-sudoers-validate','sudoers_validate'],['autopilot-override-install','override_install'],['autopilot-restart','restart'],['autopilot-etckeeper-install','etckeeper_install'],['autopilot-snapshot-line','snapshot_line'],['autopilot-screenshot-line','screenshot_line'],['autopilot-off','off']])$(id).textContent=guide[key];
  $('autopilot-env-file').textContent=guide.env_file;$('autopilot-sudoers-path').textContent=guide.sudoers_path;$('autopilot-override-path').textContent=guide.override_path;
  $('autopilot-env-present').textContent=!guide.env_file_exists?'the file does not exist yet; create it with mode 600':guide.env_line_present?'the line is in the file':'the line is not in the file yet';
  $('autopilot-nnp').textContent=status.no_new_privileges?'the service runs with NoNewPrivileges now':'NoNewPrivileges is off for this service';
  $('autopilot-etckeeper').textContent=guide.etckeeper_installed?'etckeeper is installed':'etckeeper is not installed';
  $('autopilot-snapshot').textContent=guide.snapshot_configured?'a snapshot hook is configured':'no snapshot hook configured';
  const approvals=$('autopilot-approvals');approvals.replaceChildren(...guide.approvals.map(item=>element('li',item[0].toUpperCase()+item.slice(1))));
  $('autopilot-pause').textContent=data.paused?'Resume Autopilot':'Pause Autopilot';$('autopilot-pause').dataset.paused=String(!!data.paused);
  $('autopilot-goal-form').hidden=!status.config;
  const select=$('autopilot-goal-project'),current=select.value||state.project||'';select.replaceChildren();
  for(const project of state.portfolio?.projects||[]){const option=element('option',displayName(project));option.value=project.id;select.append(option);}
  if([...select.options].some(option=>option.value===current))select.value=current;
  renderAutopilotGoals(data.goals);
}
async function loadAutopilot(){if(state.restricted)return;renderAutopilot(await api('/api/autopilot'));}
for(const button of document.querySelectorAll('#autopilot-panel [data-copy]'))button.onclick=action(async()=>{
  await copyText($(button.dataset.copy).textContent);const label=button.textContent;button.textContent='Copied';setTimeout(()=>{button.textContent=label;},1500);
});
$('autopilot-pause').onclick=action(async()=>{renderAutopilot(await api('/api/autopilot/pause',{method:'POST',body:JSON.stringify({paused:$('autopilot-pause').dataset.paused!=='true'})}));});
$('autopilot-goal-form').onsubmit=action(async event=>{
  event.preventDefault();const status=$('autopilot-goal-status');status.textContent='';
  const budget=$('autopilot-goal-budget').value,deadline=$('autopilot-goal-deadline').value;
  const body={project_id:$('autopilot-goal-project').value,title:$('autopilot-goal-title').value,description:$('autopilot-goal-description').value,
    done_when:$('autopilot-goal-checklist').value.split('\n').map(line=>line.trim()).filter(Boolean),daily_budget:budget?Number(budget):null,deadline:deadline||null};
  const goal=await api('/api/autopilot/goals',{method:'POST',body:JSON.stringify(body)});
  for(const file of $('autopilot-goal-images').files){
    const response=await fetch(`/api/autopilot/goals/${encodeURIComponent(goal.id)}/images?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'X-CSRF-Token':state.csrf||''},body:file});
    if(!response.ok){const result=await response.json().catch(()=>({}));status.textContent=`Goal saved; image ${file.name} rejected: ${result.detail||response.status}`;}
  }
  event.target.reset();if(!status.textContent)status.textContent='Goal saved.';await loadAutopilot();
});
const openAutopilot=()=>loadAutopilot().catch(error=>{$('autopilot-state').textContent=error.message;});
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',openAutopilot);
$('autopilot-badge').onclick=()=>{showView('settings');openAutopilot();$('autopilot-panel').scrollIntoView({block:'start'});};
renderAutopilotBadge();
