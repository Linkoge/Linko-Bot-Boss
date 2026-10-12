'use strict';
// Metadata shares the authenticated API client. Project selection never scopes it.
let usageData=null,usageTimer=null,usageTick=null,usageRequest=null,usageSession=0,usageOffset=0;
const usageNow=()=>Date.now()/1000+usageOffset;
function usageWindows(){return usageData?.buckets.find(b=>b.id==='codex')?.windows||[];}
function usageStale(window){return window.stale||(window.resets_at!==null&&window.resets_at<=usageNow())||window.observed_at+180<=usageNow();}
function usagePercent(value){return value===null?'Unavailable':`${value}%`;}
function usageDuration(seconds){if(seconds===null)return 'Unavailable';if(seconds<=0)return 'Reset passed; awaiting refresh';const m=Math.ceil(seconds/60),d=Math.floor(m/1440),h=Math.floor(m%1440/60);return [d?`${d}d`:'',h?`${h}h`:'',`${m%60}m`].filter(Boolean).join(' ');}
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
  const stale=usageData?.buckets.some(b=>b.windows.some(usageStale));
  $('usage-status').textContent=usageRequest?'Refreshing…':!usageData?'Usage unavailable':
    `${usageData.error||(!usageData.buckets.some(b=>b.windows.some(w=>w.used_percent!==null))?'Usage unavailable: no quota windows reported.':stale?'Stale readings — awaiting refresh.':'Available readings.')} Last refresh attempt: ${localTime(usageData.last_attempt_at)}. Refreshes at most once per minute.`;
  const details=$('usage-details');details.replaceChildren();
  for(const bucket of usageData?.buckets||[]){
    const section=element('section',undefined,'usage-bucket');section.append(element('h3',`${bucket.name}${bucket.plan?' · '+bucket.plan:''}`),element('p','Last reading: '+localTime(bucket.observed_at),'muted'));
    if(!bucket.windows.length)section.append(element('p','No quota windows reported.'));
    for(const w of bucket.windows){
      const card=element('article',undefined,'usage-window');card.append(element('h4',w.label+(usageStale(w)?' · stale':'')));
      const list=element('dl');
      for(const [label,value] of [['Remaining',usagePercent(w.remaining_percent)],['Used',usagePercent(w.used_percent)],['Window',w.duration_minutes===null?'Duration unavailable':`${w.duration_minutes} minutes`],['Resets',w.resets_at===null?'Unavailable':localTime(w.resets_at)+' · '+new Date(w.resets_at*1000).toISOString()],['Time remaining',usageDuration(w.resets_at===null?null:w.resets_at-usageNow())]])list.append(element('dt',label),element('dd',value));
      card.append(list);section.append(card);
    }
    details.append(section);
  }
  if(!usageData?.buckets.length)details.append(element('p','No account allowance reading is available.'));
}
async function refreshUsage(){
  if(usageRequest||$('app').hidden||document.hidden)return usageRequest;
  const session=usageSession;
  usageRequest=(async()=>{try{const data=await api('/api/account-usage');if(session!==usageSession)return;usageData=data;usageOffset=data.server_time-Date.now()/1000;}catch(error){if(session!==usageSession)return;usageData={...(usageData||{buckets:[],server_time:usageNow()}),error:'Account usage unavailable. Refresh failed; previous readings are stale.',last_attempt_at:usageNow()};for(const b of usageData.buckets)for(const w of b.windows)w.stale=true;}finally{if(session===usageSession){usageRequest=null;renderUsage();}}})();
  renderUsage();return usageRequest;
}
function startUsage(){stopUsage();usageTimer=setInterval(refreshUsage,60000);usageTick=setInterval(renderUsage,15000);setTimeout(refreshUsage,0);}
function stopUsage(){usageSession++;clearInterval(usageTimer);clearInterval(usageTick);usageData=null;usageRequest=null;$('usage-dialog')?.close();}
$('usage-button').onclick=()=>{closeProjectMenu();$('usage-dialog').showModal();renderUsage();refreshUsage();};
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
