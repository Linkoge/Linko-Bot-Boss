"""Permanent UI regression using the real usage renderer and Send implementation."""
import subprocess
from pathlib import Path


def test_provider_usage_display_and_send_independence():
    static = Path(__file__).resolve().parents[1] / 'ai_manager/static'
    program = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Node {
  constructor(text=''){this.textContent=text;this.children=[];this.open=true;this.hidden=false;this.disabled=false;
    this.classList={toggle(){},add(){}};this.style={};this.value='hello';}
  querySelectorAll(){return [];}
  append(...nodes){this.children.push(...nodes);} replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;} addEventListener(){} close(){this.open=false;}
  get text(){return [this.textContent,...this.children.map(n=>n.text)].join('\n');}
}
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
const context={$,PROVIDER_LOGOS:{codex:'<svg></svg>'},element:(tag,text)=>Object.assign(new Node(text),{tag}),document:{hidden:false,addEventListener(){}},
  Date,Number,Math,setInterval,clearInterval,setTimeout,clearTimeout,localTime:t=>'date:'+t,
  api:async()=>{throw Error('offline');},closeChatMenu(){},closeProjectMenu(){},
  state:{project:null,selection:1,pending:new Set(),drafts:new Map()},
  audioState:{session:null},attachmentState:{uploads:new Map()},submissions:new Map(),
  crypto:{randomUUID:()=> 'request'},attachmentDraft:()=>[],saveSubmissions(){},renderAttachments(){},
  clearSentAttachments(){},saveDrafts(){},loadHistory:async()=>{},updateChatActions(){},
  submitWithModelSelection:async()=>{context.sent++;},sent:0};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1]+'/usage.js','utf8'),context);
const app=fs.readFileSync(process.argv[1]+'/app.js','utf8');
vm.runInContext(app.slice(app.indexOf('async function sendSubmission('),app.indexOf("$('chat-form').onsubmit=")),context);
(async()=>{
  for(const provider of ['codex','claude']){
    const data={provider,observed_at:Date.now()/1000,metadata_stale:false,buckets:[{id:provider,credits:{balance:'12.5'},
      windows:[{duration_minutes:300,remaining_percent:25,resets_at:Date.now()/1000+3600,observed_at:Date.now()/1000}]}],
      reset_credits:{available_count:2,credits:[{expiration_known:true,expires_at:2000000000}]}};
    context.data=data;vm.runInContext('usageData=data;renderUsage()',context);
    assert.equal($('usage-five').textContent,'25%');assert.equal($('usage-week').textContent,'unavailable');
    assert(!$('usage-details').text.includes(provider==='claude'?'Claude':'Codex'));
    assert($('usage-last-refresh').textContent==='Last refresh Never');
    assert($('usage-details').text.includes('Available resets: 2'));
    assert($('usage-details').text.includes('Reset credit expiration: date:2000000000'));
    assert($('usage-details').text.includes('Credits: 12.5'));
    for(const reading of [null,{...data,error:'offline',metadata_stale:true},
      {...data,buckets:[{id:provider,windows:[{duration_minutes:300,remaining_percent:0,stale:true}]}]},
      {provider,buckets:[]}]){
      context.reading=reading;vm.runInContext('usageData=reading;renderUsage()',context);
      await vm.runInContext('sendSubmission()',context);
      assert.equal($('send').disabled,false);
    }
    vm.runInContext("usageData={provider:data.provider,buckets:[]};renderUsage()",context);
    assert($('usage-details').text.includes('Available resets: Unavailable'));
    assert($('usage-details').text.includes('Reset credit expiration: Unavailable'));
    assert($('usage-details').text.includes('Credits: Unavailable'));
    assert.deepEqual($('usage-details').children.filter(n=>n.tag==='button'),[]);
  }
  // The expanded panel always renders both independent account views.
  const now=Date.now()/1000;
  const providers=Object.fromEntries(['codex','claude'].map((provider,index)=>[provider,{provider,buckets:[{id:provider,
    windows:[300,10080].map((minutes,i)=>({duration_minutes:minutes,remaining_percent:20+index*50+i,
      observed_at:now,resets_at:now+3600}))}]}]));
  for(const provider of ['codex','claude']){
    context.data={...providers[provider],provider,providers};
    vm.runInContext('usageData=data;renderUsage()',context);
    assert.equal($('usage-five').textContent,provider==='codex'?'20%':'70%');
    assert.equal($('usage-details').children.length,2);
    assert(!$('usage-details').children[0].text.includes('OpenAI / Codex'));
    assert(!$('usage-details').children[1].text.includes('Claude'));
    assert(!/resets in/i.test($('usage-details').text));
    assert($('usage-details').children[0].text.includes('20%'));
    assert($('usage-details').children[0].text.includes('21%'));
    assert($('usage-details').children[1].text.includes('70%'));
    assert($('usage-details').children[1].text.includes('71%'));
    for(const failed of ['codex','claude']){
      context.data={...providers[provider],provider,providers:{...providers,[failed]:{provider:failed,buckets:[],error:'offline'}}};
      vm.runInContext('usageData=data;renderUsage()',context);
      const cards=$('usage-details').children;
      assert(cards[failed==='codex'?0:1].text.includes('Unavailable'));
      assert(cards[failed==='codex'?1:0].text.includes(failed==='codex'?'70%':'20%'));
    }
  }
  assert.equal(context.sent,8);
  context.data={provider:'codex',buckets:[{id:'codex',windows:[{duration_minutes:300,remaining_percent:99,stale:true}]}]};
  vm.runInContext('usageData=data;renderUsage()',context);
  assert.equal($('usage-five').textContent,'unavailable');
  // A project change clears a same-provider reading before its scoped request resolves.
  context.requests=[];context.api=path=>{context.requests.push(path);return new Promise(resolve=>context.resolveUsage=resolve);};
  vm.runInContext("state.project='repo';setUsageProvider('codex',true)",context);
  assert.equal($('usage-five').textContent,'unavailable');
  assert.equal(context.requests.at(-1),'/api/account-usage?project_id=repo');
  const old=context.resolveUsage;
  vm.runInContext("state.project='locked';setUsageProvider('claude',true)",context);
  old({provider:'codex',buckets:[{id:'codex',windows:[{duration_minutes:300,remaining_percent:99}]}]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal($('usage-five').textContent,'unavailable');
  context.api=async()=>{throw Error('offline');};

  vm.runInContext("setUsageProvider('claude')",context);
  assert($('usage-button')['aria-label'].startsWith('Claude'));
  assert.equal($('usage-five').textContent,'unavailable');
  vm.runInContext("setUsageProvider('codex')",context);
  assert($('usage-button')['aria-label'].startsWith('Codex'));
  assert.equal($('usage-five').textContent,'unavailable');
  vm.runInContext("usageData=null;refreshUsage()",context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal($('send').disabled,false);
})();
"""
    subprocess.run(['node', '-e', program, str(static)], check=True, timeout=30, capture_output=True, text=True)
