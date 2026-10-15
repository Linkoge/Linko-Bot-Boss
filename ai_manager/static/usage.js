'use strict';
// Metadata shares the authenticated API client. Project selection never scopes it.
let usageData=null,usageTimer=null,usageTick=null,usageRequest=null,usageSession=0,usageOffset=0;
let usageProvider=null;
function setUsageProvider(provider){
  if(usageProvider===provider)return;
  usageProvider=provider;usageSession++;usageRequest=null;usageData={provider,buckets:[]};renderUsage();
  refreshUsage();
}
const usageNow=()=>Date.now()/1000+usageOffset;
function usageBucket(){return usageData?.buckets.find(b=>b.id===(usageData.provider||'codex'));}
function usageWindows(){return usageBucket()?.windows||[];}
function usageStale(window){return window.stale||(window.resets_at!==null&&window.resets_at<=usageNow())||window.observed_at+180<=usageNow();}
function usagePercent(value){return value===null?'Unavailable':`${value}%`;}

function usageDuration(timestamp){
  if(!Number.isFinite(timestamp))return 'Unavailable';
  const minutes=Math.max(0,Math.ceil((timestamp-usageNow())/60));
  const days=Math.floor(minutes/1440),hours=Math.floor(minutes%1440/60),rest=minutes%60;
  const parts=[];
  if(days)parts.push(`${days}d`);
  if(hours)parts.push(`${hours}h`);
  if(rest||!parts.length)parts.push(`${rest}m`);
  return parts.join(' ');
}
function renderUsage(){
  const windows=usageWindows(),labels=[],provider=usageData?.provider==='claude'?'Claude':'Codex';
  for(const [id,minutes,name] of [['usage-five',300,'Five-hour'],['usage-week',10080,'Weekly']]){
    const matches=windows.filter(w=>w.duration_minutes===minutes),w=matches.length===1?matches[0]:null;
    const stale=w&&usageStale(w),known=w&&w.remaining_percent!==null;
    $(id).textContent=known?`${Math.round(w.remaining_percent)}%`:'—';
    $(id).classList.toggle('stale',!!stale);
    $(id).title=`${name} remaining: ${known?usagePercent(w.remaining_percent):'unavailable'}${stale?' (stale)':''}`;
    labels.push($(id).title);
  }
  $('usage-button').setAttribute('aria-label',`${provider} account usage. ${labels.join('. ')}. Open details`);
  $('usage-button').title=labels.join(' · ');
  if(!$('usage-dialog').open)return;
  const stale=windows.some(usageStale)||!!usageData?.error;
  $('usage-status').textContent=usageRequest?'Refreshing…':!usageData||!windows.length?'Usage unavailable':stale?'Previous readings are stale.':'Usage updated.';
  const details=$('usage-details');details.replaceChildren();
  details.append(element('h3',provider));
  for(const [minutes,label] of [[300,'5 hour'],[10080,'Weekly']]){
    const matches=windows.filter(w=>w.duration_minutes===minutes),w=matches.length===1?matches[0]:null;
    const section=element('section',undefined,'usage-window');
    section.append(element('h3',label));
    const remaining=element('p',w?.remaining_percent!=null?usagePercent(w.remaining_percent):'Unavailable','usage-remaining');
    const isStale=!!w&&(usageStale(w)||!!usageData?.error);
    remaining.classList.toggle('stale',isStale);
    if(isStale){remaining.title='Previous reading is stale';remaining.setAttribute('aria-label',remaining.textContent+'. Previous reading is stale.');}
    const reset=element('p',usageDuration(w?.resets_at),'usage-reset');
    reset.title=w?.resets_at!=null?'Resets '+localTime(w.resets_at):'Reset time unavailable';
    reset.setAttribute('aria-label',reset.title+'. '+reset.textContent);
    section.append(remaining,reset);
    details.append(section);
  }
  const metadata=element('section',undefined,'usage-window');
  const staleMetadata=usageData?.metadata_stale||!!usageData?.error||
    (usageData?.observed_at!=null&&usageData.observed_at+180<=usageNow())||
    (usageBucket()?.observed_at!=null&&usageBucket().observed_at+180<=usageNow())||
    usageData?.reset_credits?.credits?.some(c=>c.expires_at!=null&&c.expires_at<=usageNow());
  if(staleMetadata)metadata.append(element('p','Previous metadata is stale.'));
  const resets=usageData?.reset_credits;
  metadata.append(element('p','Available resets: '+(resets?.available_count??'Unavailable')));
  if(resets?.credits?.length){
    if(resets.credits.length<resets.available_count)metadata.append(element('p','Expiration details are partial.'));
    for(const credit of resets.credits){
      const expiry=!credit.expiration_known?'Unavailable':credit.expires_at===null?'Does not expire':
        localTime(credit.expires_at)+(credit.expires_at<=usageNow()?' (expired)':'');
      metadata.append(element('p','Reset credit expiration: '+expiry));
    }
  }else metadata.append(element('p','Reset credit expiration: Unavailable'));
  const credits=usageBucket()?.credits;
  metadata.append(element('p','Credits: '+(credits?.unlimited===true?'Unlimited':credits?.balance??
    (credits?.has_credits===false?'None reported':credits?.has_credits===true?'Available; balance unavailable':'Unavailable'))));
  metadata.append(element('p','Credit balance expiration: Unavailable'));
  details.append(metadata);
  $('usage-last-refresh').textContent='Last refresh '+(usageData?.last_successful_refresh_at!=null?localTime(usageData.last_successful_refresh_at):'Never');
}
async function refreshUsage(){
  if(usageRequest||$('app').hidden||document.hidden)return usageRequest;
  const session=usageSession;
  usageRequest=(async()=>{try{const data=await api('/api/account-usage');if(session!==usageSession)return;usageData=data;usageOffset=data.server_time-Date.now()/1000;}catch(error){if(session!==usageSession)return;usageData={...(usageData||{buckets:[],server_time:usageNow()}),error:'Account usage unavailable. Refresh failed; previous readings are stale.',last_attempt_at:usageNow()};for(const b of usageData.buckets)for(const w of b.windows)w.stale=true;}finally{if(session===usageSession){usageRequest=null;renderUsage();}}})();
  renderUsage();return usageRequest;
}
function startUsage(){stopUsage();usageTimer=setInterval(refreshUsage,60000);usageTick=setInterval(renderUsage,15000);setTimeout(refreshUsage,0);}
function stopUsage(){usageSession++;clearInterval(usageTimer);clearInterval(usageTick);usageData=null;usageRequest=null;usageProvider=null;$('usage-dialog')?.close();}
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
