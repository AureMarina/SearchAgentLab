"""Executable local examples / explicit adapter checks (synthetic input only)."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from searchagent_lab.store import Store
from searchagent_lab.io import import_file
from searchagent_lab.isolation import audit_panel


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default=str(ROOT/'docs/adapter_test_output.json'))
    args=p.parse_args()
    checks=[]
    def check(name,ok):
        checks.append({'name':name,'passed':bool(ok)})
        print(('PASS ' if ok else 'FAIL ')+name,flush=True)
        if not ok:raise AssertionError(name)
    def run(argv,code=0):
        result=subprocess.run([sys.executable,*map(str,argv)],cwd=ROOT,capture_output=True,text=True,timeout=30)
        if result.returncode!=code:raise AssertionError(f'{argv}: {result.returncode}\n{result.stdout}\n{result.stderr}')
        return result
    try:
        with tempfile.TemporaryDirectory(prefix='lab-v2-adapter-') as tmp:
            temp=Path(tmp)
            with Store(temp/'minimal') as s:
                import_file(s,ROOT/'examples/import_bundle_v2.json')
                check('v2 minimum bundle imports actual example ownership',s.run('example-run')['project_id']=='example-latent')
                check('minimal example preserves four synthetic traces',s.health()['trajectories']==4)
                import_file(s,ROOT/'examples/import_bundle_v2.json')
                check('reimport is idempotent',s.health()['trajectories']==4 and len(s.runs())==1)
                check('minimal example protocol records full v2 fields',s.evaluation('example-eval-0')['readiness']['complete'])
            out=temp/'mapped.json'
            base=['examples/convert_existing_results.py','--panel','examples/fixed_panel_v2.example.json',
                  '--results','examples/raw_results.example.jsonl','--mapping','examples/mapping.example.json',
                  '--protocol','examples/protocol_v2.synthetic.json','--project-id','adapter-demo',
                  '--experiment-id','adapter-demo-e01','--run-id','adapter-demo-s42',
                  '--checkpoint-id','adapter-demo-step100','--evaluation-id','adapter-demo-eval01',
                  '--step','100','--seed','42','--out',out,'--complete','--demo']
            run(base)
            bundle=json.loads(out.read_text())
            check('adapter writes schema2 and explicit parents',bundle['schema_version']==2 and bundle['runs'][0]['experiment_id']=='adapter-demo-e01')
            raw=[json.loads(line) for line in (ROOT/'examples/raw_results.example.jsonl').read_text().splitlines() if line.strip()]
            check('adapter keeps entire original record',all(a['source_record']==b for a,b in zip(bundle['trajectories'],raw)) and len(raw)==len(bundle['trajectories']))
            with Store(temp/'mapped') as s:
                import_file(s,out)
                check('mapped real fields import as two synthetic trajectories',s.health()['trajectories']==2)
                rows=s.export_evaluation('adapter-demo-eval01')['trajectories']
                original={(row['dataset'],row['id']):row for row in raw}
                check('scorer outputs are not recomputed',all(t['scores']==original[(t['dataset'],t['sample_id'])]['scores'] for t in rows))
            check('adapter refuses overwriting output',run(base,2).returncode==2)
            wrong=list(base);wrong[wrong.index('--out')+1]=temp/'not-demo.json';wrong.remove('--demo')
            check('synthetic panel cannot be relabelled real',run(wrong,2).returncode==2 and not (temp/'not-demo.json').exists())
            badmap=json.loads((ROOT/'examples/mapping.example.json').read_text());badmap['evaluation_id']=['attack']
            (temp/'badmap.json').write_text(json.dumps(badmap))
            wrong=list(base);wrong[wrong.index('--mapping')+1]=temp/'badmap.json';wrong[wrong.index('--out')+1]=temp/'bad.json'
            check('mapping cannot redirect evaluation parent',run(wrong,2).returncode==2 and not (temp/'bad.json').exists())
            full=list(base)
            for flag,value in [('--project-id','example-latent'),('--experiment-id','example-latent-e01'),('--run-id','example-run'),('--out',temp/'metadata-mapped.json')]:full[full.index(flag)+1]=value
            full.extend(['--workspace-metadata','examples/metadata.synthetic.json'])
            run(full)
            fullbundle=json.loads((temp/'metadata-mapped.json').read_text())
            metadata=json.loads((ROOT/'examples/metadata.synthetic.json').read_text())
            check('full metadata option reuses exact experiment and run config',fullbundle['experiments']==metadata['experiments'] and fullbundle['runs']==metadata['runs'])
            with Store(temp/'metadata-mapped') as s:
                import_file(s,temp/'metadata-mapped.json')
                check('metadata-assisted bundle imports without parent conflict',s.run('example-run')['project_id']=='example-latent')
            run(['examples/live_logger.py','--data-dir',temp/'live','--steps',2,'--interval',0])
            with Store(temp/'live') as s:
                check('stream example writes under its own project',s.run('live-demo')['project_id']=='live-demo-project' and s.run('live-demo')['status']=='completed')
                check('stream example writes three actual metric records',s.conn.execute('SELECT count(*) FROM metrics').fetchone()[0]==3)
            with Store(temp/'empty-config') as s:
                import_file(s,ROOT/'examples/workspace_metadata.template.json')
                check('project template creates no fabricated runs or scores',len(s.projects())==3 and s.health()['runs']==0 and s.health()['trajectories']==0)
                from searchagent_lab.demo import make_sample
                sample=make_sample('HotpotQA',0)
                s.register_panel({'id':'gt','name':'synthetic','demo':True,'samples':[sample]})
                (temp/'empty.jsonl').write_text('')
                inventory={'projects':['self-play'],'entries':[{'project_id':'self-play','role':role,'path':'empty.jsonl','revision':'unknown','description':'Explicit empty fixture only','declared_complete':True} for role in ['train','test']]}
                audit=audit_panel(s.panel('gt'),inventory,temp,['self-play'])
                check('unknown source revision cannot produce full supplied-scope clearance',audit['status']=='incomplete_scope')
    finally:
        Path(args.out).write_text(json.dumps({'synthetic_only':True,'checks':checks,'count':len(checks)},ensure_ascii=False,indent=2)+'\n')
    print(f'{len(checks)} adapter/example checks passed.')

if __name__=='__main__':main()
