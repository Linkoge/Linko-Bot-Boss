'use strict';
// Metadata shares the authenticated API client. Project selection never scopes it.
let usageData=null,usageTimer=null,usageTick=null,usageRequest=null,usageSession=0,usageOffset=0;
const usageNow=()=>Date.now()/1000+usageOffset;
function usageWindows(){return usageData?.buckets.find(b=>b.id==='codex')?.windows||[];}
function usageStale(window){return window.stale||(window.resets_at!==null&&window.resets_at<=usageNow())||window.observed_at+180<=usageNow();}
function usagePercent(value){return value===null?'Unavailable':`${value}%`;}

function renderUsage(){
  const windows=usageWindows(),labels=[];
  for(const [id,minutes,name] of [['usage-five',300,'Five-hour'],['usage-week',10080,'Weekly']]){
    const matches=windows.filter(w=>w.duration_minutes===minutes),w=matches.length===1?matches[0]:null;
    const stale=w&&usageStale(w),known=w&&w.remaining_percent!==null;
    $(id).textContent=known?`${Math.round(w.remaining_percent)}%`:'—';
    $(id).classList.toggle('stale',!!stale);
    $(id).title=`${name} remaining: ${known?usagePercent(w.remaining_percent):'unavailable'}${stale?' (stale)':''}`;
    labels.push($(id).title);
  }
  $('usage-button').setAttribute('aria-label',`Codex account usage. ${labels.join('. ')}. Open details`);
  $('usage-button').title=labels.join(' · ');
  if(!$('usage-dialog').open)return;
  const stale=windows.some(usageStale)||!!usageData?.error;
  $('usage-status').textContent=usageRequest?'Refreshing…':!usageData||!windows.length?'Usage unavailable':stale?'Previous readings are stale.':'Usage updated.';
  const details=$('usage-details');details.replaceChildren();
  for(const [minutes,label] of [[300,'5 hour'],[10080,'Weekly']]){
    const matches=windows.filter(w=>w.duration_minutes===minutes),w=matches.length===1?matches[0]:null;
    const section=element('section',undefined,'usage-window');
    section.append(element('h3',label));
    const remaining=element('p',w?.remaining_percent!=null?`${usagePercent(w.remaining_percent)} remaining`:'Unavailable','usage-remaining');
    const isStale=!!w&&(usageStale(w)||!!usageData?.error);
    remaining.classList.toggle('stale',isStale);
    if(isStale){remaining.title='Previous reading is stale';remaining.setAttribute('aria-label',remaining.textContent+'. Previous reading is stale.');}
    section.append(remaining,element('p',w?.resets_at!=null?localTime(w.resets_at):'Unavailable','usage-reset'));
    details.append(section);
  }
  $('usage-last-refresh').textContent='Last refresh '+(usageData?.last_successful_refresh_at!=null?localTime(usageData.last_successful_refresh_at):'Never');
}
async function refreshUsage(){
  if(usageRequest||$('app').hidden||document.hidden)return usageRequest;
  const session=usageSession;
  usageRequest=(async()=>{try{const data=await api('/api/account-usage');if(session!==usageSession)return;usageData=data;usageOffset=data.server_time-Date.now()/1000;}catch(error){if(session!==usageSession)return;usageData={...(usageData||{buckets:[],server_time:usageNow()}),error:'Account usage unavailable. Refresh failed; previous readings are stale.',last_attempt_at:usageNow()};for(const b of usageData.buckets)for(const w of b.windows)w.stale=true;}finally{if(session===usageSession){usageRequest=null;renderUsage();}}})();
  renderUsage();return usageRequest;
}
function startUsage(){stopUsage();usageTimer=setInterval(refreshUsage,60000);usageTick=setInterval(renderUsage,15000);setTimeout(refreshUsage,0);}
function stopUsage(){usageSession++;clearInterval(usageTimer);clearInterval(usageTick);usageData=null;usageRequest=null;$('usage-dialog')?.close();}
$('usage-button').onclick=()=>{closeChatMenu();closeProjectMenu();$('usage-dialog').showModal();renderUsage();refreshUsage();};
$('usage-close').onclick=()=>$('usage-dialog').close();
$('usage-dialog').addEventListener('close',()=>$('usage-button').focus({preventScroll:true}));
$('usage-dialog').addEventListener('keydown',event=>{
  if(event.key!=='Tab')return;
  const buttons=[...$('usage-dialog').querySelectorAll('button:not(:disabled)')];
  const first=buttons[0],last=buttons[buttons.length-1];
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
});
$('usage-dialog').addEventListener('click',event=>{if(event.target===$('usage-dialog')){const r=$('usage-dialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('usage-dialog').close();}});
$('usage-refresh').onclick=async()=>{const b=$('usage-refresh');b.disabled=true;try{await refreshUsage();}finally{b.disabled=false;}};
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!$('app').hidden)refreshUsage();});
