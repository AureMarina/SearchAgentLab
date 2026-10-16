"""Installed-wheel smoke test. Only disposable copies are opened for writing."""
from pathlib import Path
import argparse,json,shutil,sys,tempfile,threading,subprocess
from urllib.request import urlopen
from urllib.parse import urlencode
p=argparse.ArgumentParser();p.add_argument('--snapshot-data',required=True,type=Path);p.add_argument('--report',required=True,type=Path);a=p.parse_args()
import searchagent_lab
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.server import LabServer
checks=[]
def check(name,ok):
 if not ok:raise AssertionError(name)
 checks.append(name);print('PASS',name,flush=True)
check('Installed version is 0.10.0',searchagent_lab.__version__=='0.10.0')
check('Import is from isolated environment, not working source',Path(sys.prefix).resolve() in Path(searchagent_lab.__file__).resolve().parents)
check('Installed CLI reports 0.10.0',subprocess.check_output([sys.executable,'-m','searchagent_lab','--version'],text=True).strip().endswith('0.10.0'))
static=Path(searchagent_lab.__file__).parent/'static'
manifest=json.loads((static/'assets/build-manifest.json').read_text())
check('Production assets declare actual compiler and split mode',manifest['mode']=='csp-safe-split-local-modules' and manifest['builder'].startswith('offline-typescript-'))
with tempfile.TemporaryDirectory() as tmp:
 data=Path(tmp)/'copy';shutil.copytree(a.snapshot_data,data)
 with StudioRecorder(data) as rec:
  check('Copied original has 33 trajectories and 3 evaluations',rec.store.health()['trajectories']==33 and rec.store.health()['evaluations']==3)
  rec.store.register_run({'id':'installed-sft-fixture','name':'Installed smoke only','run_type':'training','training_stage':'sft','demo':True})
  rec.log_sft_batch(run_id='installed-sft-fixture',batch_id='smoke-batch',step=1,samples=[{'id':'sample','supervision_target':'actual fixture target','input_ids':[1,2],'labels':[-100,2],'loss_mask':[0,1]}])
  with LabServer(('127.0.0.1',0),rec.store) as server:
   th=threading.Thread(target=server.serve_forever,daemon=True);th.start();base='http://127.0.0.1:'+str(server.server_address[1])
   def get(path,query=None):
    with urlopen(base+path+('?' + urlencode(query) if query else ''),timeout=10) as r:return r.read(),dict(r.headers)
   def api(path,query=None):return json.loads(get('/api/'+path,query)[0])
   try:
    html,_=get('/');check('Installed index references new built entry',manifest['main'].encode() in html)
    for item in manifest['files']:
     body,headers=get('/assets/'+item['name']);check('Installed asset '+item['name'],len(body)==item['bytes'] and bool(headers.get('ETag')))
    for module in ['execution-state.js','problem-summary.js','supervision-sample.js','clarity.css']:
     body,_=get('/studio/'+module);check('Installed source module '+module,bool(body))
    summary=api('studio/training-summary',{'run':'skillrl-qwen3-4b-150steps-20261006'})
    check('Legacy stage remains explicitly unknown',summary['training_stage']['value']=='unknown')
    check('Absent real batches remain absent',not summary['batches'])
    report=api('studio/trace-browser',{'run':'skillrl-qwen3-4b-150steps-20261006'})
    ev=report['report']['b'];check('Original B score remains 8/11',ev['stats']['answer_correct']==8 and ev['stats']['expected']==11)
    ids=[row['b']['id'] for row in report['report']['items'] if row.get('b')]
    result=api('inspection/summaries',{'evaluation':ev['id'],'ids':json.dumps(ids)})
    check('Installed summary endpoint returns 11 original records',len(result['items'])==11 and all(item['cause_status']=='unknown' for item in result['items']))
    sft=api('studio/training-summary',{'run':'installed-sft-fixture'})
    check('Installed SFT adapter records stage without fabricated reward',sft['training_stage']['value']=='sft' and sft['batch_summary']['reward'] is None)
    sample=api('studio/training-rollout',{'run':'installed-sft-fixture','batch':'smoke-batch','group':'demonstrations','sample':'sample'})
    check('SFT sample origin is supervision, not agent execution',sample['origin']=='sft_demonstration' and sample['trace_role']=='sft_demonstration' and sample['sample']['labels']==[-100,2])
    check('SFT fixture did not enter evaluation denominator',rec.store.health()['evaluations']==3 and rec.store.health()['trajectories']==33)
   finally:server.shutdown();th.join()
a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps({'version':searchagent_lab.__version__,'checks':checks,'count':len(checks),'passed':True,'module_path':searchagent_lab.__file__,'scope':'Isolated installed wheel + actual loopback HTTP. Disposable copied workspace and explicit synthetic SFT fixture; not user-server deployment or model evaluation.'},ensure_ascii=False,indent=2)+'\n')
print('COMPLETE',len(checks))
