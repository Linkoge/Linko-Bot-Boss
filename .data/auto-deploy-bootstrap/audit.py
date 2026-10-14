import json,sqlite3,subprocess,hashlib,datetime
from pathlib import Path
from ai_manager.release_publication import inventory
from ai_manager.deployment_intent import assignment_evidence
from ai_manager.deployment import Handoff,sha
OUT=Path('.data/auto-deploy-bootstrap');DATA=Path('/home/codex/.local/share/linko-bot-manager');SOURCE=DATA/'assignment-workspaces/44097fd1a52c4f9096fcd901bd04d344'
REL='auto-deploy-standing-policy-v1-20261014';ROOT=DATA/'deployment-helper/releases'/REL
M='5c31263dea6bfd2c19d3a1b9151c20db76a5ed33169467d7ad64324825fc39cd';I='8a8c313e9db83c2817297556bd6b73b3d6a45073a805e63172dd3929e57898db'
manifest,files=inventory(ROOT,M,I); original,original_files=inventory(SOURCE/'.data/auto-deploy-stage2/runtime/deployment-helper/releases'/REL,M,I)
assert files==original_files
c=sqlite3.connect(f'file:{DATA}/manager.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
intent=json.loads(c.execute('select value from settings where key=?',('deployment_intent:ba8ce92a3d406a98cdfc9fa81851c251',)).fetchone()[0])
task=dict(c.execute('select * from tasks where id=?',('44097fd1a52c4f9096fcd901bd04d344',)).fetchone())
scope=assignment_evidence(c,task,2);assert manifest['assignments']==intent['assignment_hashes']=={task['id']:scope}
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
live=Path('/home/codex/projects/Linko-Bot-Manager')
assert git(live,'rev-parse','HEAD')==manifest['base'];assert git(SOURCE,'rev-parse','HEAD')==manifest['commit'];assert not git(SOURCE,'status','--porcelain')
git(Path.cwd(),'merge-base','--is-ancestor',manifest['base'],manifest['commit'])
heads=git(Path.cwd(),'bundle','list-heads',str(ROOT/'release.bundle'));assert manifest['commit'] in heads
asset_checks={}
for commit,key in [(manifest['base'],'base_assets'),(manifest['commit'],'assets')]:
 for name,digest in manifest[key].items():
  data=subprocess.check_output(['git','show',commit+':ai_manager/static/'+name]);assert hashlib.sha256(data).hexdigest()==digest
  if key=='base_assets':assert sha(live/'ai_manager/static'/name)==digest
  asset_checks[key+':'+name]=digest
assert sha(ROOT/'helper-v3.py')==manifest['required_helper_sha256']
assert hashlib.sha256(subprocess.check_output(['git','show',manifest['commit']+':ai_manager/deployment.py'])).hexdigest()==manifest['required_helper_sha256']
assert not git(Path.cwd(),'diff','--name-only',manifest['base'],manifest['commit'],'--','ai_manager/db.py','pyproject.toml','uv.lock')
unit=Path.home()/'.config/systemd/user/linko-deployment-helper.service';current=unit.read_text();assert current.count(str(DATA/'deployment-helper/helper-v2.py'))==1
receipt=json.loads((DATA/'deployment-helper/helper-v2-upgrade.json').read_text());assert receipt['phase']=='succeeded';assert receipt['helper_sha256']==sha(DATA/'deployment-helper/helper-v2.py')
active=json.loads((DATA/'deployment-helper/active.json').read_text());assert active['phase']=='succeeded';assert active['health']['commit']==manifest['base']
result=json.loads((DATA/'deployment-helper/results'/ (active['nonce']+'.json')).read_text());assert result['phase']=='succeeded'
source=c.execute("select content from messages where id=? and role='user'",(intent['source_message_id'],)).fetchone();assert hashlib.sha256(source[0].encode()).hexdigest()==intent['source_sha256']
review_tasks=[dict(c.execute('select id,lifecycle,status,lease_owner from tasks where id=?',(t,)).fetchone()) for t in intent.get('review_task_ids',[])]
expected={t:assignment_evidence(c,c.execute('select * from tasks where id=?',(t,)).fetchone(),2) for t in intent['task_ids']+intent.get('review_task_ids',[])}
exec_id='8a6e0afe948d4f04922419121954070c'
native=[dict(r) for r in c.execute("select seq,kind,payload from events where execution_id=? and kind in ('task.verification','worker.finished')",(exec_id,))]
report={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'release_id':REL,'registered_path':str(ROOT),'manifest_sha256':M,'index_sha256':I,'inventory_files':len(files),'inventory_indexed_files':len(files)-1,'source_registered_byte_identity':True,'manifest':manifest,'bundle_heads':heads,'asset_checks':asset_checks,'live_head':git(live,'rev-parse','HEAD'),'live_status':git(live,'status','--porcelain'),'source_head':git(SOURCE,'rev-parse','HEAD'),'source_git_status':git(SOURCE,'status','--porcelain'),'cgroup':Path('/proc/self/cgroup').read_text(),'installed_helper_version':'v2','installed_helper_sha256':sha(DATA/'deployment-helper/helper-v2.py'),'unit_sha256':sha(unit),'v2_install_receipt':str(DATA/'deployment-helper/helper-v2-upgrade.json'),'v3_receipt_exists':(DATA/'deployment-helper/helper-v3-upgrade.json').exists(),'v3_installed_exists':(DATA/'deployment-helper/helper-v3.py').exists(),'handoff_gated':Handoff(DATA).gated(),'live_succeeded_receipt':str(DATA/'deployment-helper/results'/ (active['nonce']+'.json')),'source_lifecycle':task['lifecycle'],'source_status':task['status'],'source_lease':task['lease_owner'],'native_verification_events':native,'intent_status':intent['status'],'intent_assignment_hashes':intent['assignment_hashes'],'manifest_scope_matches_recorded_intent':True,'review_tasks':review_tasks,'current_review_expected_assignments':expected,'review_expected_matches_intent':expected==intent['assignment_hashes'],'review_expected_matches_manifest':expected==manifest['assignments'],'runtime_database_access':'mode=ro; no initialization/migration or writes'}
(OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ['manifest','asset_checks']},indent=2))
