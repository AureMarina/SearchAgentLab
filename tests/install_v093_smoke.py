"""Verify the already-built 0.9.3 wheel offline in a fresh isolated venv.

Run after the current README and compiled assets are frozen into the wheel:
    python tests/install_v093_smoke.py --wheel dist/searchagent_lab-0.9.3-py3-none-any.whl

This script never builds a wheel or edits an existing workspace. It copies only
the synthetic example/protocol, imports from site-packages, and checks real
loopback HTTP plus collector ingestion. No Node, package index, GPU or model
endpoint is available to the installed runtime.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email import message_from_bytes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import venv
import zipfile


ROOT=Path(__file__).resolve().parents[1]
VERSION='0.9.3'


def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',type=Path,default=ROOT/'dist'/f'searchagent_lab-{VERSION}-py3-none-any.whl')
    args=parser.parse_args();wheel=args.wheel.expanduser().resolve()
    checks=[];commands=[]
    report=ROOT/'docs/v093/wheel-acceptance.json'
    receipt={'version':VERSION,'started_at':stamp(),'python':sys.version,'platform':sys.platform,
        'scope':'Fresh venv, offline prebuilt wheel, installed methods SDK, synthetic fixture, real loopback HTTP, collector typed-reference roundtrip',
        'built_wheel':False,'uses_original_workspace':False,'gpu_or_model_execution':False,
        'runtime_requires_node':False,'checks':checks,'commands':commands}

    def check(name,condition,detail=None):
        row={'name':name,'passed':bool(condition)}
        if detail is not None:row['detail']=detail
        checks.append(row);print(('PASS ' if condition else 'FAIL ')+name,flush=True)
        if not condition:raise AssertionError(name)

    def run(argv,*,cwd,env,timeout=60):
        args=list(map(str,argv));result=subprocess.run(args,cwd=cwd,env=env,capture_output=True,text=True,timeout=timeout)
        commands.append({'command':args,'returncode':result.returncode,'stdout':result.stdout[-14000:],'stderr':result.stderr[-8000:]})
        if result.returncode:raise RuntimeError(result.stderr or result.stdout)
        return result.stdout

    try:
        check('existing final wheel supplied; no build performed',wheel.is_file())
        receipt['wheel']={'name':wheel.name,'bytes':wheel.stat().st_size,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()}
        with zipfile.ZipFile(wheel) as archive:
            names=set(archive.namelist());prefix='searchagent_lab/static/'
            metadata=message_from_bytes(archive.read(f'searchagent_lab-{VERSION}.dist-info/METADATA'))
            check('wheel metadata has version 0.9.3',metadata['Version']==VERSION)
            dependencies=metadata.get_all('Requires-Dist',[]);receipt['requires_dist']=dependencies
            check('wheel declares zero runtime package dependencies',dependencies==[])
            readme=(ROOT/'README_ZH.md').read_text(encoding='utf-8')
            check('wheel contains the frozen current README',metadata.get_payload(decode=True).decode('utf-8').strip()==readme.strip())
            required={'searchagent_lab/methods/recorder.py','searchagent_lab/methods/assessment.py',
                'searchagent_lab/methods/projection.py','searchagent_lab/methods/schema.py',
                'searchagent_lab/methods/contracts.py','searchagent_lab/studio/evaluation.py'}
            check('wheel packages the method logging and assessment implementation',required.issubset(names))
            manifest_raw=archive.read(prefix+'assets/build-manifest.json');manifest=json.loads(manifest_raw)
            check('wheel build manifest equals the frozen source manifest',manifest_raw==(ROOT/prefix/'assets/build-manifest.json').read_bytes())
            assets={'assets/'+entry['name']:entry['bytes'] for entry in manifest['files']}
            asset_hashes={asset:hashlib.sha256(archive.read(prefix+asset)).hexdigest() for asset in assets}
            check('all current build-manifest assets are packaged with exact bytes',
                bool(assets) and all(prefix+asset in names and len(archive.read(prefix+asset))==size and
                    archive.read(prefix+asset)==(ROOT/prefix/asset).read_bytes() for asset,size in assets.items()),
                {'asset_count':len(assets),'entry':manifest['main']})
            html=archive.read(prefix+'index.html').decode('utf-8')
            entries=sorted(set(re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']',html)))
            check('compiled HTML references packaged local assets',bool(entries) and all(url.lstrip('/') in assets for url in entries))
            check('compiled HTML has the current version label','id="s-version">0.9.3<' in html)

        clean_env=dict(os.environ)
        for key in ('PYTHONPATH','PYTHONHOME'):clean_env.pop(key,None)
        clean_env.update(PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_CONFIG_FILE=os.devnull,
                         PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
        with tempfile.TemporaryDirectory(prefix='searchagent-v093-install-') as directory:
            temporary=Path(directory);environment=temporary/'venv'
            venv.EnvBuilder(with_pip=True,system_site_packages=False).create(environment)
            binary=environment/('Scripts' if os.name=='nt' else 'bin')
            python=binary/('python.exe' if os.name=='nt' else 'python')
            runtime_env={**clean_env,'PATH':str(binary)}
            run([python,'-I','-m','pip','install','--no-index','--no-deps',wheel],cwd=temporary,env=runtime_env)
            check('clean venv installs prebuilt wheel without a package index',True)
            check('installed CLI reports 0.9.3',run([python,'-I','-m','searchagent_lab','--version'],cwd=temporary,env=runtime_env).strip()==VERSION)
            probe=json.loads(run([python,'-I','-c',"""import importlib.metadata,json,shutil,sys
import searchagent_lab
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.methods.assessment import method_assessment
from searchagent_lab.methods.projection import active_signal
from searchagent_lab.methods.schema import KINDS
print(json.dumps({'module':searchagent_lab.__file__,'prefix':sys.prefix,'node':shutil.which('node'),
 'requires':importlib.metadata.requires('searchagent-lab') or [],
 'typed_opd':{'opd_failure','opd_teacher','opd_supervision'}.issubset(KINDS),
 'recorder':all(hasattr(MethodEvidenceRecorder,k) for k in ('skill_retrieval','generated_question','opd_failure','opd_teacher','opd_supervision','export_bundle')),
 'zero_reweight':active_signal({'objective':'advantage_reweight','original_advantage':0,'effective_advantage':0,'loss_mask_tokens':2,'loss_weight':1})[0]}))
"""],cwd=temporary,env=runtime_env))
            check('installed imports come from this venv site-packages',str(environment) in probe['module'] and 'site-packages' in probe['module'] and str(ROOT) not in probe['module'])
            check('new recorder, projection and OPD schema import without Node',probe['node'] is None and probe['requires']==[] and probe['typed_opd'] and probe['recorder'])
            check('installed loss semantics retain 0 times weight equals zero',probe['zero_reweight'] is False)

            # The example prepends its parent to sys.path. Only these two files
            # are copied, so no source package can shadow installed imports.
            copied=temporary/'copied-example'/'examples';copied.mkdir(parents=True)
            for filename in ('method_evidence_example.py','protocol_v2.synthetic.json'):
                shutil.copyfile(ROOT/'examples'/filename,copied/filename)
            workspace=temporary/'synthetic-workspace'
            example=json.loads(run([python,'-I',copied/'method_evidence_example.py','--data-dir',workspace],cwd=temporary,env=runtime_env))
            check('complete method example runs from installed SDK in a new directory',example.get('synthetic_only') is True and set(example['events'])=={'skill','self_play','opd'})
            check('example exports separate collector packages for all method families',len(example['packages'])==3 and all(Path(p).is_file() for p in example['packages']))
            journal=workspace/'example-artifacts'/'opd-method-events.jsonl'
            imported=json.loads(run([python,'-I','-m','searchagent_lab','import-methods',journal,'--data-dir',workspace],cwd=temporary,env=runtime_env))
            check('installed CLI imports the native method SDK JSONL idempotently',imported['count']==example['events']['opd'])

            # Exercise collector normalization with the installed modules only.
            collector_code="""import json,sys
from pathlib import Path
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.team.hub import Hub,gid
from searchagent_lab.team.package import read_package
source=Path(sys.argv[1]);center=Path(sys.argv[2]);checks={}
with Hub(center) as hub:
 for prefix,family in [('sk','skill'),('sp','self_play'),('opd','opd')]:
  path=source/'example-artifacts'/(prefix+'-run.zip')
  manifest,payload,_=read_package(path);wid=manifest['workspace_id']
  received=hub.ingest(path,allow_definitions=True)
  run=received['run_id'];assessment=hub.methods.method_assessment(prefix+'-experiment',run,family)
  checks[prefix+'_run_scope']=(assessment['scope']['run_id']==run and all(x['scope'].get('run_id')==run for x in assessment['evidence']['items']))
  checks[prefix+'_synthetic']=(all(x.get('demo') is True for x in payload['methods']) and hub.store.run(run).get('demo') is True)
  checks[prefix+'_repeat_idempotent']=hub.ingest(path)['delivery_status']=='duplicate'
  if family=='opd':
   failure=hub.methods.object(gid(wid,'method','opd-failure-repair'))
   teacher=hub.methods.object(gid(wid,'method','opd-teacher-2'))
   supervision=hub.methods.object(gid(wid,'method','opd-supervision-active'))
   use=hub.methods.object(gid(wid,'method','opd-use-active'))
   update=hub.methods.object(gid(wid,'method','opd-update-1'))
   checks['opd_typed_references']=(teacher['data']['failure_id']==failure['id'] and teacher['id'] in supervision['data']['teacher_ids'] and supervision['id'] in use['data']['artifact_ids'] and use['id'] in update['data']['consumption_ids'])
   checks['opd_checkpoint_and_batch_references']=(failure['data']['student_checkpoint_id']==gid(wid,'checkpoint','opd-cp-0') and use['data']['batch_id']==gid(wid,'batch','opd-batch-1') and update['data']['output_checkpoint_id']==gid(wid,'checkpoint','opd-cp-1'))
   raw=(source/'example-artifacts'/'opd-method-events.jsonl').read_bytes()
   checks['opd_original_jsonl_preserved']=any(hub.methods.original(i['id'])['content']==raw for i in hub.methods.imports_for(failure['id']))
   checks['opd_assessed_after_transport']=assessment['counts']['supervised_updated_states']==1
  if family=='self_play':
   question=hub.methods.object(gid(wid,'method','sp-q-valid'))
   reused=hub.methods.object(gid(wid,'method','sp-reused-zero-signal'))
   checks['selfplay_cycle_and_reuse_references']=(question['data']['first_generated_cycle_id']==gid(wid,'method','sp-cycle-1') and reused['data']['cycle_id']==gid(wid,'method','sp-cycle-2'))
   checks['selfplay_unique_supply_after_transport']=assessment['counts']['new_verified_consumed_questions']==1
  if family=='skill':
   retrieval=hub.methods.object(gid(wid,'method','sk-retrieval-used'))
   checks['skill_bank_reference']=retrieval['data']['bank_id']==gid(wid,'method','sk-bank-v2')
print(json.dumps(checks))
"""
            transported=json.loads(run([python,'-I','-c',collector_code,workspace,temporary/'synthetic-center'],cwd=temporary,env=runtime_env))
            check('collector preserves typed OPD failure, teacher, supervision, use and update references',all(transported.get(k) is True for k in ('opd_typed_references','opd_checkpoint_and_batch_references','opd_assessed_after_transport')))
            check('collector preserves Self-play round identity and distinct new supply',transported.get('selfplay_cycle_and_reuse_references') is True and transported.get('selfplay_unique_supply_after_transport') is True)
            check('collector preserves SkillBank reference and original OPD journal bytes',transported.get('skill_bank_reference') is True and transported.get('opd_original_jsonl_preserved') is True)
            check('collector families stay scoped, synthetic and idempotent',all(value is True for value in transported.values()),transported)

            with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
            with (temporary/'server.log').open('w+') as log:
                process=subprocess.Popen([str(python),'-I','-m','searchagent_lab','serve','--data-dir',str(workspace),'--host','127.0.0.1','--port',str(port)],cwd=temporary,env=runtime_env,stdout=log,stderr=log)
                base='http://127.0.0.1:'+str(port)
                def fetch(path,params=None):
                    url=base+path+('?' + urllib.parse.urlencode(params) if params else '')
                    with urllib.request.urlopen(url,timeout=8) as response:return response.read()
                def get(path,params=None):return json.loads(fetch(path,params))
                def rejected(path,params,code=400):
                    try:get(path,params);return False
                    except urllib.error.HTTPError as exc:return exc.code==code
                try:
                    for _ in range(80):
                        if process.poll() is not None:log.seek(0);raise RuntimeError('Installed server exited: '+log.read())
                        try:health=get('/api/health');break
                        except OSError:time.sleep(.1)
                    else:raise RuntimeError('Installed server failed to become healthy')
                    check('installed HTTP starts with venv-only PATH and version 0.9.3',health.get('ok') is True and get('/api/session').get('version')==VERSION)
                    endpoint='/api/methods/assessment'
                    sk={'experiment_id':'sk-experiment','run_id':'sk-run','family':'skill'}
                    skill=get(endpoint,sk)
                    check('method assessment exposes explicit evaluation choices over HTTP',len(skill['evaluation_options'])==3 and skill['metrics'][0]['status']=='selection_required' and skill['metrics'][0]['value'] is None)
                    selected=get(endpoint,{**sk,'evaluation':'sk-eval-new-bank'})
                    check('HTTP selector returns only the selected evaluation and cost',selected['evaluation']['evaluation_id']=='sk-eval-new-bank' and selected['metrics'][0]['value']==1 and selected['metrics'][2]['value']==24)
                    check('HTTP selector rejects another method run evaluation',rejected(endpoint,{**sk,'evaluation':'sp-external-eval'}))
                    opd={'experiment_id':'opd-experiment','run_id':'opd-run','family':'opd'}
                    first=get(endpoint,{**opd,'limit':1});second=get(endpoint,{**opd,'limit':1,'offset':1})
                    check('OPD evidence pagination is real and raw-body-free',first['evidence']['total']==3 and first['evidence']['has_more'] and len(first['evidence']['items'])==1 and first['evidence']['items'][0]['id']!=second['evidence']['items'][0]['id'] and 'data' not in first['evidence']['items'][0])
                    all_opd=get(endpoint,opd)
                    check('OPD repair and learning signals retain measured and unknown states',all_opd['counts']['supervised_updated_states']==1 and all_opd['counts']['rescore_records']==1 and all_opd['counts']['verification_errors']==1 and all_opd['metrics'][0]['unknown']>0)
                    unbound=get(endpoint,{'experiment_id':'opd-experiment','family':'opd'})
                    check('omitting run does not silently union sibling or unbound evidence',unbound['scope']['run_id']=='' and unbound['evidence']['total']==0)
                    sp=get(endpoint,{'experiment_id':'sp-experiment','run_id':'sp-run','family':'self_play'})
                    check('Self-play HTTP assessment separates distinct supply and zero-signal reuse',sp['counts']['new_verified_consumed_questions']==1 and sp['counts']['reused_questions']==1 and sp['counts']['signal_active_records']==2 and sp['counts']['sampled_records']==3)
                    pair='/api/studio/evaluation-pair'
                    bank_pair=get(pair,{'a':'sk-eval-old-bank','b':'sk-eval-new-bank','axis':'method_state'})
                    off_on=get(pair,{'a':'sk-eval-bank-off','b':'sk-eval-new-bank','axis':'method_state'})
                    checkpoint_pair=get(pair,{'a':'sk-eval-old-bank','b':'sk-eval-new-bank','axis':'checkpoint'})
                    check('installed controlled comparison supports Bank versions and Off/On only on method-state axis',bank_pair['compatible'] is True and off_on['compatible'] is True and checkpoint_pair['compatible'] is False and bank_pair.get('rl_attribution') is False)
                    check('installed HTTP serves the exact frozen compiled HTML',fetch('/').decode('utf-8')==html)
                    check('all current compiled assets are readable with exact wheel hashes over HTTP',all(hashlib.sha256(fetch('/'+asset)).hexdigest()==digest for asset,digest in asset_hashes.items()),{'asset_count':len(assets)})
                finally:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        receipt['passed']=True
    except BaseException as error:
        receipt['passed']=False;receipt['error']=f'{type(error).__name__}: {error}';raise
    finally:
        receipt.update(finished_at=stamp(),check_count=len(checks),passed_count=sum(row['passed'] for row in checks))
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'PASSED {len(checks)} installed-wheel acceptance checks',flush=True)


if __name__=='__main__':main()
