"""Compiled frontend acceptance over real HTTP, with isolated data copies.

The browser routes through a local bridge because native browser loopback is
restricted in the execution environment. Responses come from LabServer, not mocks.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.server import LabServer
from searchagent_lab.core import ValidationError, digest, dumps


MODES = ('snapshot', 'no_panel', 'provenance')


def seed_training(rec):
    rec.store.register_project({'id':'browser-p','name':'Synthetic browser fixture','demo':True})
    rec.store.register_experiment({'id':'browser-e','project_id':'browser-p','name':'Synthetic training without a panel','demo':True})
    rec.repo.register_profile({'experiment_id':'browser-e','track':'main'})
    rec.store.register_run({'id':'browser-run','experiment_id':'browser-e','name':'Synthetic RLOO','run_type':'training','base_model':'fixture','algorithm':'RLOO','demo':True})
    rec.store.log_metrics('browser-run',1,{'train/reward':0.0,'train/loss':-0.01,'val/success_rate':0.25})
    rec.store.log_metrics('browser-run',2,{'train/reward':0.5,'train/loss':-0.03,'train/entropy':0.4,'val/success_rate':0.75})
    rec.log_training_batch({'id':'browser-batch','run_id':'browser-run','step':2,'groups':[{'id':'g1','samples':[
        {'id':'s0','reward':0.0,'advantage':-0.00001,'token_ids':[10,11,12],'response_mask':[0,1,1],
         'question':'Synthetic training example only','sample_id':'fixture-q','dataset':'synthetic',
         'steps':[{'kind':'model','input':'Synthetic prompt','output':'Recorded synthetic output'}]},
        {'id':'s1','reward':1.0,'advantage':0.00001,'token_ids':[10,13,14],'response_mask':[0,1,1]},
        {'id':'s2','reward':0.0}]}]})


def seed_provenance(rec):
    """Synthetic regressions only; called against a fresh TemporaryDirectory.

    A raw execution reward deliberately differs from its referring batch reward.
    The one direct SQL insert simulates a pre-validation historical batch without
    bypassing validation for any real imported data or touching the supplied DB.
    """
    rec.store.register_project({'id':'browser-p','name':'Synthetic provenance fixture','demo':True})
    rec.store.register_experiment({'id':'browser-e','project_id':'browser-p','name':'Synthetic provenance regression','demo':True})
    rec.repo.register_profile({'experiment_id':'browser-e','track':'main'})
    rec.store.register_run({'id':'browser-run','experiment_id':'browser-e','name':'Synthetic provenance run',
                            'run_type':'training','base_model':'fixture','demo':True})
    rec.store.log_metrics('browser-run',3,{'train/reward':0.0})
    records = [
        {'sample_id':'evidence-only','dataset':'synthetic','run_id':'browser-run','demo':True,
         'question':'Synthetic evidence-only execution','reward':0.875,'scores':{'em':1},
         'raw_final_output':'Synthetic evidence answer','parsed_answer':'fixture-answer','gold_answers':['fixture-answer'],
         'runtime_status':'ok','termination_reason':'final_answer',
         'steps':[{'kind':'model','input':'Synthetic evidence prompt','output':'SYNTHETIC EVIDENCE ORIGINAL'}]},
        {'sample_id':'legacy-conflict','dataset':'synthetic','run_id':'another-synthetic-run','purpose':'agent_eval','demo':True,
         'question':'Synthetic conflicting legacy execution','reward':0.999,'scores':{'em':0},
         'raw_final_output':'Synthetic conflict answer','parsed_answer':'wrong','gold_answers':['fixture-answer'],
         'runtime_status':'ok','termination_reason':'final_answer',
         'steps':[{'kind':'model','input':'Synthetic conflict prompt','output':'SYNTHETIC CONFLICT ORIGINAL'}]},
    ]
    file = rec.inspections.import_bytes(('\n'.join(dumps(record) for record in records)+'\n').encode(),
                                        'provenance.synthetic.jsonl','jsonl')
    rec.log_training_batch({'id':'provenance-evidence','run_id':'browser-run','step':3,'groups':[
        {'id':'evidence-only','samples':[{'id':'evidence-sample','reward':0.125,
                                      'trace_ref':{'file':file['id'],'record':1}}]}]})
    legacy = {'id':'provenance-legacy','run_id':'browser-run','step':3,'groups':[
        {'id':'legacy-conflict','samples':[{'id':'conflict-sample','reward':0.625,
                                        'trace_ref':{'file':file['id'],'record':2}}]}]}
    try:
        rec.log_training_batch(legacy)
    except ValidationError:
        pass
    else:
        raise AssertionError('Conflicting synthetic source must fail current import validation')
    legacy['content_hash'] = digest(legacy)
    with rec.repo.lock, rec.repo.conn:
        rec.repo.conn.execute('INSERT INTO training_batches VALUES (?,?,?,?)',
                             (legacy['id'],legacy['run_id'],legacy['step'],dumps(legacy)))
        rec.repo._index_batch(legacy)
    rec.log_training_batch({'id':'provenance-index','run_id':'browser-run','step':3,'groups':[
        {'id':'numeric-collision','samples':[
            {'id':'1','reward':0.75,'messages':[{'role':'assistant','content':'SYNTHETIC INDEX A explicit id'}]},
            {'reward':0.25,'messages':[{'role':'assistant','content':'SYNTHETIC INDEX B anonymous sample'}]},
        ]}]})
    assert rec.repo.training_rollout('browser-run','provenance-evidence','evidence-only',sample_index=0)['trace_role']=='evidence_only'
    conflict = rec.repo.training_rollout('browser-run','provenance-legacy','legacy-conflict',sample_index=0)
    assert conflict['trace_role']=='conflicting' and conflict['trace_ref'] is None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',required=True)
    parser.add_argument('--output',default=str(ROOT/'docs/v092'))
    parser.add_argument('--mode',choices=('all',)+MODES,default='all',
                        help='Run one isolated scenario, retaining existing reports for other modes.')
    args=parser.parse_args()
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    modes = MODES if args.mode=='all' else (args.mode,)
    for mode in modes:
        with tempfile.TemporaryDirectory(prefix='searchagent-browser-') as tmp:
            if mode=='snapshot':
                for path in Path(args.data).glob('*.sqlite3'):shutil.copy2(path,Path(tmp)/path.name)
            with StudioRecorder(tmp) as rec:
                if mode=='no_panel':seed_training(rec)
                elif mode=='provenance':seed_provenance(rec)
                server=LabServer(('127.0.0.1',0),rec.store)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base='http://127.0.0.1:'+str(server.server_address[1])
                try:
                    result=subprocess.run([os.environ.get('CODEX_PRIMARY_RUNTIME_NODE','node'),str(ROOT/'tests/browser_v092.mjs'),base,str(output),mode],cwd=ROOT,timeout=120)
                    if result.returncode:raise SystemExit(result.returncode)
                finally:server.shutdown();server.server_close();thread.join()
    reports = [json.loads((output/('browser-'+mode+'.json')).read_text()) for mode in MODES
               if (output/('browser-'+mode+'.json')).exists()]
    combined={'mode':'compiled_frontend_real_HTTP_bridge','checks':sum(len(r['checks']) for r in reports),'runs':reports,
              'note':'Snapshot screenshots use the supplied real saved records. no_panel and provenance are synthetic regression fixtures, not benchmark results. Existing per-mode reports are retained when a single mode runs.'}
    (output/'browser-acceptance.json').write_text(json.dumps(combined,ensure_ascii=False,indent=2)+'\n')
    print('Browser acceptance:',combined['checks'],'checks passed',flush=True)


if __name__=='__main__':main()
