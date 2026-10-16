"""Install the built wheel offline into a clean venv, outside the source checkout."""
import json,os,subprocess,sys,tempfile,threading,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_team import plan,seed_source
from searchagent_lab.team.hub import Hub
from searchagent_lab.team.auth import Auth
from searchagent_lab.team.server import TeamServer

def main():
 checks=[]
 def check(name,ok):
  if not ok:raise AssertionError(name)
  checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
 wheel=ROOT/'dist/searchagent_lab-0.7.0-py3-none-any.whl'
 with zipfile.ZipFile(wheel) as z:
  names=set(z.namelist())
  check('wheel includes collector receiver auth SDK and browser assets',all('searchagent_lab/'+p in names for p in ['team/cli.py','team/hub.py','team/sdk.py','team/server.py','static/studio/team.js','static/studio/team-login.js','static/studio/team.css']))
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);venv=root/'venv';env=os.environ.copy();env.pop('PYTHONPATH',None)
  subprocess.run([sys.executable,'-m','venv',str(venv)],check=True,capture_output=True)
  py=venv/'bin/python';cli=venv/'bin/searchagent-lab'
  def command(*args):
   r=subprocess.run(list(map(str,args)),cwd=root,env=env,capture_output=True,text=True,timeout=20)
   if r.returncode:raise RuntimeError(r.stderr+r.stdout)
   return r.stdout
  command(py,'-m','pip','install','--no-index','--no-deps',wheel)
  result=json.loads(command(py,'-c','import searchagent_lab,json;print(json.dumps({"version":searchagent_lab.__version__,"path":searchagent_lab.__file__}))'))
  check('imports installed 0.7.0 from venv not source checkout',result['version']=='0.7.0' and str(venv) in result['path'])
  check('installed console script exposes center and collector commands','team-watch' in command(cli,'--help') and command(cli,'--version').strip()=='0.7.0')
  source=root/'local';seed_source(source);pkg=root/'results.zip'
  command(py,'-m','searchagent_lab','team-export','--data-dir',source,'--run','run','--out',pkg)
  check('installed exporter creates validated package',json.loads(command(cli,'team-check',pkg))['transport_integrity']=='verified')
  with Hub(root/'center') as h:
   h.apply_plan(plan());token,_=Auth(h.data_dir).issue('installed-client','uploader',['e1']);env['SEARCHAGENT_LAB_TOKEN']=token
   with TeamServer(('127.0.0.1',0),h) as server:
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
     result=json.loads(command(cli,'team-sync','--data-dir',source,'--run','run','--url',server.public_origin))
     check('installed client delivers over real HTTP and receives durable acknowledgement',result['counts']['acknowledged']==1 and len(h.runs())==1)
     result=json.loads(command(cli,'team-sync','--data-dir',source,'--run','run','--url',server.public_origin))
     check('installed repeat sync is idempotent',result['counts']['acknowledged']==1 and len(h.runs())==1)
    finally:server.shutdown();thread.join()
 (ROOT/'docs/team/install-acceptance.json').write_text(json.dumps({'checks':checks,'synthetic_only':True,'installation':'offline clean venv with PYTHONPATH unset, source checkout not current directory'},ensure_ascii=False,indent=2))
 print('INSTALL_TEAM_OK',len(checks))
if __name__=='__main__':main()
