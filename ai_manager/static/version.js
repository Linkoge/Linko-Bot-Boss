'use strict';
// Bot Boss's own version (ai_manager/self_update.py): the first line of the project menu, Settings > Version with the
// auto-update switch and Update now, and each saved machine's version (latest / behind by N / offline).
const versionState={data:null,peers:null,peersAt:0,peersLoading:false,updating:false};
function versionWhen(t){return t?new Date(t*1000).toLocaleString([],{dateStyle:'short',timeStyle:'short'}):'never';}
function versionLabel(){return versionState.data?.label||state.version||'';}
function versionTimes(){const d=versionState.data;return d?`Last checked ${versionWhen(d.checked_at)} · last updated ${versionWhen(d.updated_at)}`:'';}
function versionRefreshViews(){renderVersionPanel();if(!$('project-menu').hidden&&!renderingProjectMenu)renderProjectMenu();}
async function loadVersion(){
  let data;try{data=await api('/api/self-update');}catch{return;}
  // Re-render only on a change: an extra rebuild of the open menu can race a click on one of its entries.
  if(JSON.stringify(data)===JSON.stringify(versionState.data))return;
  versionState.data=data;versionRefreshViews();
}
async function updateNow(){
  if(versionState.updating)return;versionState.updating=true;versionRefreshViews();
  try{versionState.data=await api('/api/self-update/check',{method:'POST',body:'{}'});notice(versionState.data.reason||'Checked');}
  catch(error){notice(error.message);}
  finally{versionState.updating=false;versionRefreshViews();}
}
function updateButton(){
  const button=element('button',versionState.updating?'Checking…':'Update now');button.type='button';button.dataset.project='self-update';
  button.disabled=versionState.updating;button.title='Fetch GitHub now; pulls and deploys when main moved ahead and nothing runs';button.onclick=updateNow;return button;
}
// The very first entry of the project menu, shown with or without a selected project.
function renderVersionHead(content){
  const box=element('div',undefined,'project-menu-version');box.setAttribute('role','group');box.setAttribute('aria-label','Bot Boss version');
  box.append(element('span','Bot Boss '+(versionLabel()||'…'),'project-menu-version-label'));
  if(versionState.data&&versionState.data.available!==false&&!state.restricted){
    box.append(element('span',versionTimes(),'muted project-menu-version-times'),updateButton());
  }
  content.append(box);
}
async function loadMachineVersions(force){
  if(state.restricted||versionState.peersLoading||(!force&&Date.now()-versionState.peersAt<60000))return;
  versionState.peersLoading=true;let changed=false;
  try{const r=await api('/api/machines/versions'),peers=Object.fromEntries(r.machines.map(m=>[m.id,m]));
    changed=JSON.stringify(peers)!==JSON.stringify(versionState.peers||{});versionState.peers=peers;versionState.peersAt=Date.now();}
  catch{}
  finally{versionState.peersLoading=false;}
  if(changed&&!$('project-menu').hidden&&!renderingProjectMenu)renderProjectMenu();
}
function machineVersion(m){
  const peer=versionState.peers?.[m.id];
  if(!peer)return element('span','…','machine-version muted');
  const text=peer.state==='offline'?'offline':`${peer.version} · ${peer.state==='behind'?'behind by '+peer.behind:'latest'}`;
  const badge=element('span',text,'machine-version '+peer.state);badge.dataset.versionState=peer.state;
  if(peer.commit)badge.title=peer.commit;return badge;
}
function renderVersionPanel(){
  const d=versionState.data;
  $('version-label').textContent=versionLabel()||'…';
  const own=!!d&&d.available!==false&&!state.restricted;
  $('version-auto').hidden=!own;if(!own)return;
  const toggle=$('version-auto-toggle');toggle.checked=!!d.enabled;
  $('version-auto-note').textContent=d.main?'Main machine: task merges already deploy here; off by default.':'On by default on machines other than Main.';
  $('version-times').textContent=versionTimes();
  $('version-reason').textContent=d.reason||'';
  $('version-update').disabled=versionState.updating;$('version-update').textContent=versionState.updating?'Checking…':'Update now';
}
$('version-auto-toggle').onchange=action(async()=>{
  const toggle=$('version-auto-toggle');toggle.disabled=true;
  try{versionState.data=await api('/api/self-update',{method:'PUT',body:JSON.stringify({enabled:toggle.checked})});}
  finally{toggle.disabled=false;versionRefreshViews();}
});
$('version-update').onclick=updateNow;
$('project-menu-button').addEventListener('click',()=>{if(!$('project-menu').hidden){loadVersion();loadMachineVersions();}});
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',loadVersion);
renderVersionPanel();
// Settings > Flaky tests (Main only; the server rejects instances and style.css hides the panel there).
async function loadFlaky(){
  if(state.restricted)return;
  const list=$('flaky-list');
  try{
    const top=(await api('/api/flaky')).top;list.replaceChildren();
    $('flaky-status').textContent=top.length?'':'No flaky tests recorded.';
    for(const item of top)list.append(element('li',`${item.test} · ${item.count}× · last ${new Date(item.last_seen*1000).toISOString().slice(0,10)}`));
  }catch(error){$('flaky-status').textContent=error.message;}
}
document.querySelector('.tabs [data-view=settings]')?.addEventListener('click',loadFlaky);
