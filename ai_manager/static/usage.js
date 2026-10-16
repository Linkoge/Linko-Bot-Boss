'use strict';
// The server resolves the saved effective manager selection for this project.
let usageData=null,usageTimer=null,usageTick=null,usageRequest=null,usageSession=0,usageOffset=0;
let usageProvider=null,usageProject;
function setUsageProvider(provider,force=false){
  if(!force&&usageProvider===provider&&usageProject===state.project)return;
  clearUsage(provider);refreshUsage(true);
}
function clearUsage(provider=null){
  usageProject=state.project;
  usageProvider=provider;usageSession++;usageRequest=null;usageData={provider,buckets:[]};renderUsage();
}
const usageNow=()=>Date.now()/1000+usageOffset;
function usageBucket(data=usageData,provider=data?.provider){return data?.buckets?.find(b=>b.id===provider);}
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
  const windows=usageWindows(),labels=[],provider=usageData?.provider==='claude'?'Claude':usageData?.provider==='codex'?'Codex':'Manager';
  for(const [id,minutes,name] of [['usage-five',300,'Five-hour'],['usage-week',10080,'Weekly']]){
    const matches=windows.filter(w=>w.duration_minutes===minutes),w=matches.length===1?matches[0]:null;
    const stale=w&&usageStale(w),known=w&&Number.isFinite(w.remaining_percent)&&!stale&&!usageData?.error;
    $(id).textContent=known?`${Math.round(w.remaining_percent)}%`:'unavailable';
    $(id).classList.toggle('stale',!!stale);$(id).classList.toggle('unavailable',!known);
    $(id).title=`${name} remaining: ${known?usagePercent(w.remaining_percent):'unavailable'}${stale?' (stale)':''}`;
    labels.push($(id).title);
  }
  $('usage-button').setAttribute('aria-label',`${provider} account usage. ${labels.join('. ')}. Open details`);
  $('usage-button').title=labels.join(' · ');
  if(!$('usage-dialog').open)return;
  const readings=['codex','claude'].map(usageReading);
  const stale=readings.some(data=>data?.error||data?.buckets?.some(b=>b.windows?.some(usageStale)));
  $('usage-status').textContent=usageRequest?'Refreshing…':stale?'Some readings are stale.':
    readings.every(data=>!data?.buckets?.length)?'Usage unavailable':'Usage updated.';
  const details=$('usage-details');
  const expanded=new Set([...details.querySelectorAll('.usage-metadata[open]')].map(n=>n.closest('.usage-provider').id));
  details.replaceChildren();
  for(const provider of ['codex','claude'])details.append(usageProviderCard(provider,usageReading(provider),expanded.has('usage-provider-'+provider)));
  $('usage-last-refresh').textContent='Last refresh '+(usageData?.last_successful_refresh_at!=null?usageClock(usageData.last_successful_refresh_at):'Never');
}
// Time of day only: the panel shows no date, month, year or zone label.
function usageClock(timestamp){
  return new Intl.DateTimeFormat(undefined,{timeZone:'Asia/Tbilisi',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(timestamp*1000));
}
function usageReading(provider){
  return usageData?.providers?.[provider]||(usageData?.provider===provider?usageData:null);
}
function usageProviderCard(provider,data,expanded=false){
  const name=provider==='codex'?'OpenAI / Codex':'Claude',bucket=usageBucket(data,provider),windows=bucket?.windows||[];
  const card=element('section',undefined,'usage-provider');
  card.id='usage-provider-'+provider;card.setAttribute('aria-label',name);
  const heading=element('div',undefined,'usage-provider-head'),logo=element('span',undefined,'usage-logo');
  // Trusted, bundled SVG only; provider metadata is always rendered as text.
  logo.innerHTML=provider==='codex'?PROVIDER_LOGOS.codex:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="m12 2 .3 6.7M17 3.4l-3 6M21 7l-6 3.4M22 12l-6.8.2M20.6 17l-6-3M17 21l-3.5-6M12 22l-.2-6.8M7 20.6l3-6M3 17l6-3.5M2 12l6.8-.3M3.4 7l6 3M7 3l3.5 6"/></svg>';
  logo.setAttribute('role','img');logo.setAttribute('aria-label',name);heading.append(logo);card.append(heading);
  for(const [minutes,label] of [[300,'5 hour'],[10080,'Weekly']]){
    const matches=windows.filter(w=>w.duration_minutes===minutes),w=matches.length===1?matches[0]:null;
    const known=w&&!usageStale(w)&&!data?.error&&Number.isFinite(w.remaining_percent);
    const section=element('div',undefined,'usage-window'),row=element('div',undefined,'usage-window-head');
    const remaining=element('p',known?usagePercent(w.remaining_percent):'Unavailable','usage-remaining');
    const isStale=!!w&&(usageStale(w)||!!data?.error);
    remaining.classList.toggle('stale',isStale);
    if(isStale){remaining.title='Previous reading is stale';remaining.setAttribute('aria-label',remaining.textContent+'. Previous reading is stale.');}
    row.append(element('h4',label),remaining);
    const bar=element('div',undefined,'usage-bar');bar.setAttribute('role','progressbar');
    bar.setAttribute('aria-label',`${name} ${label} remaining`);bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');
    bar.setAttribute('aria-valuetext',remaining.textContent);
    if(known){
      const value=Math.max(0,Math.min(100,w.remaining_percent));bar.setAttribute('aria-valuenow',String(value));
      const fill=element('span');fill.style.width=value+'%';bar.append(fill);
    }else bar.classList.add('unavailable');
    const reportedReset=known?w.resets_at:null;
    const reset=element('p',reportedReset!=null?usageDuration(reportedReset):'Reset time unavailable','usage-reset');
    reset.title=reportedReset!=null?'Resets '+localTime(reportedReset):'Reset time unavailable';
    reset.setAttribute('aria-label',reset.title+'. '+reset.textContent);
    section.append(row,bar,reset);card.append(section);
  }
  const metadata=element('details',undefined,'usage-metadata');metadata.open=expanded;
  metadata.append(element('summary','Credits & resets'));
  const staleMetadata=data?.metadata_stale||!!data?.error||
    (data?.observed_at!=null&&data.observed_at+180<=usageNow())||
    (usageBucket(data,provider)?.observed_at!=null&&usageBucket(data,provider).observed_at+180<=usageNow())||
    data?.reset_credits?.credits?.some(c=>c.expires_at!=null&&c.expires_at<=usageNow());
  if(staleMetadata)metadata.append(element('p','Previous metadata is stale.'));
  const resets=staleMetadata?null:data?.reset_credits;
  metadata.append(element('p','Available resets: '+(resets?.available_count??'Unavailable')));
  if(resets?.credits?.length){
    if(resets.credits.length<resets.available_count)metadata.append(element('p','Expiration details are partial.'));
    for(const credit of resets.credits){
      const expiry=!credit.expiration_known?'Unavailable':credit.expires_at===null?'Does not expire':
        localTime(credit.expires_at)+(credit.expires_at<=usageNow()?' (expired)':'');
      metadata.append(element('p','Reset credit expiration: '+expiry));
    }
  }else metadata.append(element('p','Reset credit expiration: Unavailable'));
  const credits=staleMetadata?null:usageBucket(data,provider)?.credits;
  metadata.append(element('p','Credits: '+(credits?.unlimited===true?'Unlimited':credits?.balance??
    (credits?.has_credits===false?'None reported':credits?.has_credits===true?'Available; balance unavailable':'Unavailable'))));
  metadata.append(element('p','Credit balance expiration: Unavailable'));
  card.append(metadata);
  return card;
}

async function refreshUsage(force=false){
  if(!force&&typeof modelPending!=='undefined'&&modelPending>0)return usageRequest;
  if(usageRequest||$('app').hidden||document.hidden)return usageRequest;
  const session=usageSession,project=state.project;
  usageRequest=(async()=>{try{const data=await api('/api/account-usage'+(project?'?project_id='+encodeURIComponent(project):''));if(session!==usageSession||project!==state.project)return;usageData=data;usageProvider=data.provider;usageProject=project;if(typeof setProviderIcon==='function')setProviderIcon(data.provider);usageOffset=data.server_time-Date.now()/1000;}catch(error){if(session!==usageSession)return;usageData={provider:usageProvider,buckets:[],error:'Account usage unavailable.',last_attempt_at:usageNow()};}finally{if(session===usageSession){usageRequest=null;renderUsage();}}})();
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
