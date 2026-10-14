import asyncio,json,sqlite3,subprocess,hashlib
from pathlib import Path
from ai_manager.config import Settings
from ai_manager.workers import Workers
from ai_manager.codex import AppServer
OUT=Path('.data/auto-deploy-bootstrap')
BASE='bee8b088fa0eb2c20d963b95f109cbf3a87caaeb'; CAND='0223c3493a6edf9b54b280fa5993fb6404e2d8ca'
class ReadOnly:
 def __init__(self):
  self.conn=sqlite3.connect('file:/home/codex/.local/share/linko-bot-manager/manager.sqlite3?mode=ro',uri=True); self.conn.row_factory=sqlite3.Row
 def setting(self,key,default=None):
  row=self.conn.execute('select value from settings where key=?',(key,)).fetchone()
  return json.loads(row[0]) if row else default
async def main():
 db=ReadOnly(); task=dict(db.conn.execute('select * from tasks where id=?',('44097fd1a52c4f9096fcd901bd04d344',)).fetchone())
 ex=dict(db.conn.execute('select model,effort from executions where id=?',('8a6e0afe948d4f04922419121954070c',)).fetchone())
 diff=subprocess.check_output(['git','diff',BASE,CAND],text=True)
 paths=subprocess.check_output(['git','diff','--name-only',BASE,CAND],text=True).splitlines()
 (OUT/'review.diff').write_text(diff)
 # Reuse the production read-only reviewer and owner model policy. No Database initializer or writes.
 worker=Workers(db,None,Settings(data_dir=OUT/'review-runtime'),server_factory=AppServer)
 result=await worker.review(task,Path.cwd(),ex,paths,diff,False)
 result.update(base=BASE,candidate=CAND,execution_id='8a6e0afe948d4f04922419121954070c',diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),diff_characters=len(diff),diff_truncated=False,changed_files=paths,record_kind='external_read_only_review_not_native_task_verification')
 (OUT/'review.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result,indent=2))
asyncio.run(main())
