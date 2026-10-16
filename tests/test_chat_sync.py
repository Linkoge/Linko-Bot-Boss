"""Run the real client synchronizer: every cached unfinished reply must be checked."""
import subprocess
from pathlib import Path


def test_client_batches_all_unfinished_reply_ids_without_dropping_older_ones():
    app = Path(__file__).resolve().parents[1] / 'ai_manager/static/app.js'
    program = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const ids=Array.from({length:205},(_,i)=>'reply-'+i),checked=[];
const context={URLSearchParams,state:{selection:1,project:'repo',history:{cursor:'tail',messages:ids.map(id=>({id,execution_id:id,finished_at:null}))}},
  api:async path=>{const query=new URL(path,'http://localhost').searchParams;assert.equal(query.get('since'),'tail');
    const watch=query.get('watch').split(',');assert(watch.length<=100);checked.push(...watch);
    return {cursor:'tail',sync_has_more:false,updates:watch.map(id=>({id,finished_at:1}))};},
  renderHistory:data=>{for(const update of data.updates)Object.assign(context.state.history.messages.find(m=>m.id===update.id),update);},
  cacheCurrentChat(){},updatesRecovered(){},updatesUnavailable(error){throw error;}};
vm.createContext(context);
const source=fs.readFileSync(process.argv[1],'utf8');
vm.runInContext(source.slice(source.indexOf('async function loadHistory('),source.indexOf('function renderHistory(')),context);
(async()=>{await vm.runInContext('loadHistory()',context);assert.deepEqual(checked,ids);
  assert(context.state.history.messages.every(m=>m.finished_at===1));})();
"""
    subprocess.run(['node', '-e', program, str(app)], check=True, timeout=30, capture_output=True, text=True)
