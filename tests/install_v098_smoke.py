"""Verify the already-built 0.9.8 wheel offline in a fresh isolated venv.

Run after the current README and compiled assets are frozen into the wheel:
    python tests/install_v098_smoke.py --wheel dist/searchagent_lab-0.9.8-py3-none-any.whl

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
VERSION='0.9.8'


def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel',type=Path,default=ROOT/'dist'/f'searchagent_lab-{VERSION}-py3-none-any.whl')
    args=parser.parse_args();wheel=args.wheel.expanduser().resolve()
    checks=[];commands=[]
    report=ROOT/'docs/v098/wheel-acceptance.json'
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
            check('wheel metadata has version 0.9.8',metadata['Version']==VERSION)
            dependencies=metadata.get_all('Requires-Dist',[]);receipt['requires_dist']=dependencies
            check('wheel declares zero runtime package dependencies',dependencies==[])
            readme=(ROOT/'README_ZH.md').read_text(encoding='utf-8')
            check('wheel contains the frozen current README',metadata.get_payload(decode=True).decode('utf-8').strip()==readme.strip())
            check('wheel does not redistribute the optional font binary',not any(name.lower().endswith(('.ttf','.otf','.woff','.woff2')) for name in names))
            required={'searchagent_lab/ui_fonts.py','searchagent_lab/methods/recorder.py','searchagent_lab/methods/assessment.py',
                'searchagent_lab/methods/projection.py','searchagent_lab/methods/schema.py',
                'searchagent_lab/methods/contracts.py','searchagent_lab/studio/evaluation.py',
                'searchagent_lab/methods/skill_tracking.py','searchagent_lab/methods/skill_comparison.py',
                'searchagent_lab/methods/skill_adapters.py', 'searchagent_lab/methods/opd_tracking.py',
                'searchagent_lab/methods/opd_recovery.py', 'searchagent_lab/methods/opd_adapters.py',
                'searchagent_lab/methods/entropy_tracking.py', 'searchagent_lab/methods/entropy_evaluation.py',
                'searchagent_lab/methods/entropy_adapters.py', 'searchagent_lab/methods/selfplay_tracking.py',
                'searchagent_lab/methods/selfplay_evaluation.py'}
            check('wheel packages the method logging and assessment implementation',required.issubset(names))
            runtime_files=[p for p in (ROOT/'searchagent_lab').rglob('*.py') if '__pycache__' not in p.parts]
            check('wheel runtime Python matches every frozen source module',all(
                p.relative_to(ROOT).as_posix() in names and archive.read(p.relative_to(ROOT).as_posix())==p.read_bytes()
                for p in runtime_files),{'module_count':len(runtime_files)})
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
            check('compiled HTML has the current version label','id="s-version">0.9.8<' in html)

        clean_env=dict(os.environ)
        for key in ('PYTHONPATH','PYTHONHOME'):clean_env.pop(key,None)
        clean_env.update(PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_CONFIG_FILE=os.devnull,
                         PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
        with tempfile.TemporaryDirectory(prefix='searchagent-v098-install-') as directory:
            temporary=Path(directory);environment=temporary/'venv'
            venv.EnvBuilder(with_pip=True,system_site_packages=False).create(environment)
            binary=environment/('Scripts' if os.name=='nt' else 'bin')
            python=binary/('python.exe' if os.name=='nt' else 'python')
            runtime_env={**clean_env,'PATH':str(binary)}
            run([python,'-I','-m','pip','install','--no-index','--no-deps',wheel],cwd=temporary,env=runtime_env)
            check('clean venv installs prebuilt wheel without a package index',True)
            check('installed CLI reports 0.9.8',run([python,'-I','-m','searchagent_lab','--version'],cwd=temporary,env=runtime_env).strip()==VERSION)
            check('installed CLI exposes optional font setup without installing it','setup-font' in run([python,'-I','-m','searchagent_lab','--help'],cwd=temporary,env=runtime_env))
            probe=json.loads(run([python,'-I','-c',"""import importlib.metadata,json,shutil,sys
import searchagent_lab
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.methods.assessment import method_assessment
from searchagent_lab.methods.projection import active_signal
from searchagent_lab.methods.schema import KINDS
from searchagent_lab.methods.skill_adapters import adapt_slim_lifecycle,adapt_skill_creator_results
from searchagent_lab.methods.entropy_adapters import adapt_entropy_metrics,adapt_entropy_rows,adapt_seal_scores
print(json.dumps({'module':searchagent_lab.__file__,'prefix':sys.prefix,'node':shutil.which('node'),
 'requires':importlib.metadata.requires('searchagent-lab') or [],
 'typed_opd':{'opd_failure','opd_teacher','opd_supervision'}.issubset(KINDS),
 'recorder':all(hasattr(MethodEvidenceRecorder,k) for k in ('skill_retrieval','generated_question','opd_failure','opd_teacher','opd_supervision','export_bundle')),
 'typed_skills':{'skill_asset','skill_instance','skill_comparison'}.issubset(KINDS),
 'skill_helpers':all(hasattr(MethodEvidenceRecorder,k) for k in ('skill_asset','skill_instance','skill_evolution','skill_comparison')),
 'skill_adapters':callable(adapt_slim_lifecycle) and callable(adapt_skill_creator_results),
 'typed_entropy':{'entropy_definition','entropy_observation','entropy_probe','entropy_intervention','entropy_comparison'}.issubset(KINDS),
 'entropy_helpers':all(hasattr(MethodEvidenceRecorder,k) for k in ('entropy_definition','entropy_observation','entropy_probe','entropy_intervention','entropy_comparison')),
 'entropy_adapters':all(callable(v) for v in (adapt_entropy_metrics,adapt_entropy_rows,adapt_seal_scores)),
 'zero_reweight':active_signal({'objective':'advantage_reweight','original_advantage':0,'effective_advantage':0,'loss_mask_tokens':2,'loss_weight':1})[0]}))
"""],cwd=temporary,env=runtime_env))
            check('installed imports come from this venv site-packages',str(environment) in probe['module'] and 'site-packages' in probe['module'] and str(ROOT) not in probe['module'])
            check('new recorder, projection and OPD schema import without Node',probe['node'] is None and probe['requires']==[] and probe['typed_opd'] and probe['recorder'])
            check('installed frozen Skill objects, logging helpers and native report adapters import',probe['typed_skills'] and probe['skill_helpers'] and probe['skill_adapters'])
            check('installed Entropy definitions, probes, interventions, comparisons and native adapters import',probe['typed_entropy'] and probe['entropy_helpers'] and probe['entropy_adapters'])
            check('installed loss semantics retain 0 times weight equals zero',probe['zero_reweight'] is False)

            # Examples prepend their parent to sys.path. Only example files
            # and a protocol are copied, so no source package shadows imports.
            copied=temporary/'copied-example'/'examples';copied.mkdir(parents=True)
            for filename in ('method_evidence_example.py','skill_tracking_example.py','opd_tracking_example.py','entropy_tracking_example.py','selfplay_tracking_example.py','protocol_v2.synthetic.json'):
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

            skill_workspace=temporary/'synthetic-skill-workspace'
            skill_example=json.loads(run([python,'-I',copied/'skill_tracking_example.py','--data-dir',skill_workspace],cwd=temporary,env=runtime_env))
            check('installed Skill example covers three modes and an inline no-bank run',
                skill_example.get('synthetic_only') is True and set(skill_example['modes'])=={'fixed','iteration','joint','inline'} and len(skill_example['packages'])==4)
            skill_code="""import json,sys
from pathlib import Path
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.methods.skill_comparison import skill_comparison
from searchagent_lab.team.hub import Hub,gid
from searchagent_lab.team.package import read_package
workspace=Path(sys.argv[1]);checks={}
with StudioRecorder(workspace) as studio:
 frozen=studio.methods.skill_instance('joint-training-instance')
 checks['frozen_version']=(frozen['asset_ref']['version']=='v1' and '先核对问题中的人物指代' in frozen['frozen_content']['content']['files']['SKILL.md'])
 runtime=studio.methods.skill_runtime('joint-skill-experiment','joint-skill-run',evaluation_id='joint-eval-current-current-bank')['items'][0]
 checks['runtime_four_levels']=all(runtime['observations'][key]['value'] is True for key in ('visible','loaded','execution','gain'))
 inline=studio.methods.object('inline-training-instance')['data']
 checks['inline_without_bank']=('inline_skill' in inline and 'bank_id' not in inline and 'asset_id' not in inline)
 ic=studio.methods.skill_lifecycle('inline-skill-experiment','inline-skill-run')['credits']['items'][0]
 rc=next(c for c in studio.methods.skill_lifecycle('fixed-skill-experiment','fixed-skill-run')['credits']['items'] if c['instance_id']=='fixed-retrieved-instance')
 checks['credit_separate']=(ic['model_selection_credit']['value'] is True and ic['skill_decision']['advantage']==0.25 and ic['action_decision']['advantage']==0.5 and rc['model_selection_credit']['value'] is False and rc['skill_decision']['advantage']==0.2)
 matrix=skill_comparison(studio.methods,'joint-matrix')
 checks['matrix_all_six']=(len(matrix['matrix']['cells'])==6 and all(c['status']=='measured' for c in matrix['matrix']['cells']))
 partial=skill_comparison(studio.methods,'joint-matrix-partial')
 missing=[c for c in partial['matrix']['cells'] if c['status']=='unmeasured']
 checks['missing_is_unknown']=(len(missing)==4 and all(c['value'] is None for c in missing))
 ablation=skill_comparison(studio.methods,'joint-ablation')
 checks['ablation_conditions']=(ablation['status']=='measured' and ablation['utility']==0.5 and ablation['causal_instance_credit'] is False)
 checks['feedback_not_final']=(matrix['feedback_state']['used_for_skill_iteration'] is True and matrix['feedback_state']['final_generalization_claim'] is False)
 audit=next(e for e in studio.methods.skill_lifecycle('joint-skill-experiment','joint-skill-run')['events']['items'] if e['id']=='joint-slim-audit-0')
 checks['native_report_only']=(audit['gain_verified'] is False and audit['reported_utility']['utility']==0.25 and studio.methods.object('iteration-creator-report')['data']['comparison_status']=='reported_only')
 raw=(workspace/'example-artifacts'/'slim_lifecycle_step4.json').read_bytes()
 checks['native_original_bytes']=any(studio.methods.original(i['id'])['content']==raw for i in studio.methods.imports_for('joint-slim-source'))
with Hub(Path(sys.argv[2])) as hub:
 all_scoped=True
 for prefix in ('fixed','iteration','joint','inline'):
  package=workspace/'example-artifacts'/(prefix+'-skill-run.zip')
  manifest,payload,_=read_package(package);wid=manifest['workspace_id']
  received=hub.ingest(package,allow_definitions=True)
  all_scoped=all_scoped and all(o['demo'] is True for o in payload['methods']) and hub.ingest(package)['delivery_status']=='duplicate'
  if prefix=='joint':
   item=hub.methods.object(gid(wid,'method','joint-evaluation-instance'))['data']
   retire=hub.methods.object(gid(wid,'method','joint-lifecycle-retired'))
   cross=hub.methods.object(gid(wid,'method','joint-matrix'))['data']
   checks['skill_transport']=(item['asset_id']==gid(wid,'method','joint-asset-v2') and item['comparison_ids']==[gid(wid,'method','joint-ablation')] and retire['data']['effective_scope']['run_id']==received['run_id'] and cross['initial_checkpoint_id']==gid(wid,'checkpoint','joint-cp-0'))
  if prefix=='inline':
   use=hub.methods.object(gid(wid,'method','inline-use'))['data']
   checks['credit_transport']=use['credit_assignment'][0]['instance_id']==gid(wid,'method','inline-training-instance')
 checks['transport_scoped_demo_idempotent']=all_scoped
print(json.dumps(checks))
"""
            installed_skills=json.loads(run([python,'-I','-c',skill_code,skill_workspace,temporary/'synthetic-skill-center'],cwd=temporary,env=runtime_env))
            check('installed Skill runtime preserves historical text and four evidence levels',installed_skills['frozen_version'] and installed_skills['runtime_four_levels'])
            check('installed inline SkillCredit needs no bank and separates retriever from model selection',installed_skills['inline_without_bank'] and installed_skills['credit_separate'])
            check('installed Skill matrix evaluates explicit six combinations and keeps four missing cells unknown',installed_skills['matrix_all_six'] and installed_skills['missing_is_unknown'])
            check('installed single-skill ablation checks controls and retains development-feedback limits',installed_skills['ablation_conditions'] and installed_skills['feedback_not_final'])
            check('installed native adapters retain reported-only utility and original source bytes',installed_skills['native_report_only'] and installed_skills['native_original_bytes'])
            check('installed collector preserves Skill assets, instances, retirement scope, comparison and credit links',all(installed_skills.values()),installed_skills)

            opd_workspace=temporary/'synthetic-opd-workspace'
            opd_example=json.loads(run([python,'-I',copied/'opd_tracking_example.py','--data-dir',opd_workspace],cwd=temporary,env=runtime_env))
            check('installed OPD example logs diagnostics, native formats and explicit recovery cohorts',
                opd_example.get('synthetic_only') is True and len(opd_example['native_sources'])==5 and len(opd_example['packages'])==1)
            opd_code="""import json,sys
from pathlib import Path
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub,gid
from searchagent_lab.team.package import read_package
workspace=Path(sys.argv[1]);checks={};prefix='opd-v095'
with StudioRecorder(workspace) as r:
 full=r.methods.opd_recovery(prefix+'-recovery-full')
 missing=r.methods.opd_recovery(prefix+'-recovery-missing')
 mismatch=r.methods.opd_recovery(prefix+'-recovery-mismatch')
 checks['same_state_three_arms']=(full['status']=='measured' and [m['value'] for m in full['metrics']]==[0,1,1,1])
 checks['full_task_separate']=(full['full_task']['rescued']==2 and full['full_task']['harmed']==1 and full['full_task']['macro_delta']<full['metrics'][3]['value'])
 checks['rescore_not_continuation']=(missing['metrics'][1]['value'] is None)
 checks['mismatch_not_gain']=(mismatch['cases'][0]['arms']['student_after']['value'] is None and mismatch['metrics'][3]['value'] is None)
 tokens=r.methods.opd_tokens(prefix+'-token-diagnostic',50,50)
 checks['tokens_paged']=(tokens['total']==63 and len(tokens['items'])==13 and tokens['items'][0]['index']==50)
 runtime=r.methods.opd_runtime(prefix+'-experiment',prefix+'-run',evaluation_id=prefix+'-eval-after')
 checks['independent_runtime_condition']=(runtime['teacher_condition']['autonomous'] is True and all(i['assessment'].get('evaluation_id')==prefix+'-eval-after' for i in runtime['items']))
 train=r.methods.opd_training(prefix+'-experiment',prefix+'-run')
 checks['separate_training_config']=(train['configuration']['values']['use_task_rewards'] is True and train['configuration']['values']['use_policy_gradient'] is False)
 checks['group_classification']=(train['coverage']['group_classification']['all_wrong']==1 and train['coverage']['group_classification']['mixed']==1)
 raw=(workspace/'example-artifacts/agentopsd_step_1.jsonl').read_bytes()
 checks['native_bytes']=any(bytes(row[0])==raw for row in r.methods.conn.execute('SELECT content FROM imports'))
 checks['bounded_initial_detail']=r.methods.opd_record(prefix+'-token-diagnostic')['object']['data']['tokens'].get('detail_only') is True
 package=workspace/'example-artifacts/opd-run.zip'
 manifest,_,_=read_package(package)
with Hub(Path(sys.argv[2])) as hub:
 result=hub.ingest(package,allow_definitions=True)
 normalized=hub.methods.opd_recovery(gid(manifest['workspace_id'],'method',prefix+'-recovery-full'))
 checks['collector_recovery']=(normalized['status']=='measured' and normalized['full_task']['rescued']==2 and normalized['scope']['run_id']==result['run_id'])
 checks['collector_repeat']=hub.ingest(package)['delivery_status']=='duplicate'
print(json.dumps(checks))
"""
            installed_opd=json.loads(run([python,'-I','-c',opd_code,opd_workspace,temporary/'synthetic-opd-center'],cwd=temporary,env=runtime_env))
            check('installed OPD distinguishes same-state recovery from full-task rescued and harmed outcomes',installed_opd['same_state_three_arms'] and installed_opd['full_task_separate'])
            check('installed OPD excludes rescore and budget mismatch from continuation gain',installed_opd['rescore_not_continuation'] and installed_opd['mismatch_not_gain'])
            check('installed OPD lazily pages tokens and retains original native bytes',installed_opd['tokens_paged'] and installed_opd['bounded_initial_detail'] and installed_opd['native_bytes'])
            check('installed OPD runtime conditions and training recipe/coverage stay separate',installed_opd['independent_runtime_condition'] and installed_opd['separate_training_config'] and installed_opd['group_classification'])
            check('installed OPD collector roundtrip preserves recovery and is idempotent',installed_opd['collector_recovery'] and installed_opd['collector_repeat'],installed_opd)

            entropy_workspace=temporary/'synthetic-entropy-workspace'
            entropy_example=json.loads(run([python,'-I',copied/'entropy_tracking_example.py','--data-dir',entropy_workspace],cwd=temporary,env=runtime_env))
            check('installed Entropy example keeps three synthetic runs and five native sources separate',
                entropy_example.get('synthetic_only') is True and len(set(entropy_example['runs'].values()))==3 and
                len(entropy_example['packages'])==3 and len(entropy_example['native_sources'])==5)
            entropy_code="""import importlib.util,json,sys,threading,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from searchagent_lab.inspection.repository import digest
from searchagent_lab.server import LabServer
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub,gid
from searchagent_lab.team.package import read_package,source
workspace=Path(sys.argv[1]);center=Path(sys.argv[2]);example_path=Path(sys.argv[3]);checks={};prefix='entropy-v096'
result=json.loads((workspace/'example-verification.json').read_text())
experiment=result['experiment_id'];run_id=result['run_id'];after=result['evaluations']['after'];before=result['evaluations']['before']
with StudioRecorder(workspace) as studio:
 methods=studio.methods
 fixed=methods.entropy_comparison(prefix+'-compare-prefix')
 temperature=methods.entropy_comparison(prefix+'-compare-temp-mismatch')
 tokenizer=methods.entropy_comparison(prefix+'-compare-tokenizer-mismatch')
 missing=methods.entropy_comparison(prefix+'-compare-missing')
 checks['fixed_prefix_controlled']=(fixed['status']=='measured' and fixed['pairs'][0]['token_pairing']['count']==63 and fixed['pairs'][0]['delta'] is not None)
 checks['incompatible_prefixes_unpooled']=(temperature['status']=='incomparable' and tokenizer['status']=='incomparable' and
  all(item['delta'] is None for comparison in (temperature,tokenizer,missing) for item in comparison['pairs']))
 validity=methods.entropy_comparison(prefix+'-compare-estimators')
 partial=methods.entropy_comparison(prefix+'-compare-estimators-missing')
 checks['qualified_common_support']=(validity['status']=='measured' and validity['comparison_qualified'] is True and validity['common_count']==6 and
  all(item['calibration']['status']=='not_calibrated' for item in validity['estimators']))
 checks['missing_scores_not_zero']=(partial['comparison_qualified'] is False and partial['common_count']==5 and partial['status']=='partial')
 budget=methods.entropy_comparison(prefix+'-compare-budget')
 mismatch=methods.entropy_comparison(prefix+'-compare-budget-mismatch')
 checks['budget_random_control']=(budget['status']=='measured' and budget['budget']['matched'] is True and len(budget['arms'])==3 and len(budget['effects'])==2)
 checks['mismatched_budget_no_gain']=(mismatch['status']=='incomparable' and mismatch['budget']['matched'] is False and all(item['status']!='measured' for item in mismatch['effects']))
 records={key:methods.entropy_record(prefix+'-intervention-'+key) for key in ('active','masked','pending')}
 checks['signals_and_updates_separate']=(records['active']['diagnostic']['signals']['entropy']['active'] is True and
  records['masked']['diagnostic']['signals']['entropy']['active'] is False and records['pending']['diagnostic']['signals']['entropy']['active'] is None and
  records['active']['training']['update_completed'] is True and records['masked']['training']['update_completed'] is True and records['pending']['training']['update_completed'] is None)
 token_id=result['token_observation_id'];detail=methods.entropy_record(token_id);tokens=methods.entropy_tokens(token_id,50,50)
 checks['bounded_detail_and_paging']=(detail['object']['data']['tokens'].get('detail_only') is True and tokens['total']==63 and len(tokens['items'])==13 and tokens['items'][0]['index']==50 and tokens['has_more'] is False)
 train=methods.entropy_training(experiment,run_id,limit=200)
 contexts={item['context'] for item in train['series']}
 checks['training_and_fixed_states_separate']=({'training_on_policy','fixed_prefix_probe'}.issubset(contexts) and all(item['aggregation']=='not_pooled' for item in train['series']))
 checks['signed_mi_retained']=any(item['kind']=='entropy_probe' and item['assessment'].get('value') is not None and
  item['assessment']['value']<0 and item['assessment'].get('interpretation')=='signed_diagnostic_proxy_not_entropy' for item in train['items'])
 originals=[bytes(row[0]) for row in methods.conn.execute('SELECT content FROM imports')]
 checks['all_native_bytes_retained']=all(Path(path).read_bytes() in originals for path in result['native_sources'].values())
 server=LabServer(('127.0.0.1',0),studio.store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try:
  base='http://127.0.0.1:'+str(server.server_port)
  def get(path,params):
   with urllib.request.urlopen(base+path+'?'+urllib.parse.urlencode(params),timeout=8) as response:return json.load(response)
  scope={'experiment_id':experiment,'run_id':run_id}
  data=get('/api/methods/entropy-training',scope)
  checks['http_training_saved_only']=(data['scope']['run_id']==run_id and all('data' not in item for item in data['items']))
  current=get('/api/methods/entropy-runtime',{**scope,'evaluation_id':after,'trajectory_id':after+'-fixture-q1','step':2})
  old=get('/api/methods/entropy-runtime',{**scope,'evaluation_id':before,'trajectory_id':before+'-fixture-q1','step':2})
  checks['http_exact_runtime_binding']=(result['runtime_observation_id'] in [item['id'] for item in current['items']] and old['items']==[] and
   current['runtime_condition']['context']['runtime_intervention']=='none')
  page=get('/api/methods/entropy-tokens',{'id':token_id,'offset':50,'limit':50})
  checks['http_token_paging']=(page['total']==63 and len(page['items'])==13 and page['has_more'] is False)
  comparison=get('/api/methods/entropy-comparison',{'id':prefix+'-compare-estimators'})
  checks['http_qualified_comparison']=comparison['comparison_qualified'] is True
  try:
   get('/api/methods/entropy-runtime',{**scope,'evaluation_id':before,'trajectory_id':after+'-fixture-q1'})
   checks['http_rejects_wrong_evaluation']=False
  except urllib.error.HTTPError as error:checks['http_rejects_wrong_evaluation']=error.code==400
 finally:
  server.shutdown();thread.join(timeout=10);server.server_close()
origin=source(workspace)['workspace_id'];method=lambda value:gid(origin,'method',value)
with Hub(center) as hub:
 all_synthetic=True;all_idempotent=True;all_original=True
 for package in result['packages']:
  manifest,payload,blobs=read_package(package)
  received=hub.ingest(package,allow_definitions=True)
  all_synthetic=all_synthetic and hub.store.run(received['run_id']).get('demo') is True and all(item.get('demo') is True for item in payload['methods'])
  all_idempotent=all_idempotent and hub.ingest(package)['delivery_status']=='duplicate'
  originals=[bytes(row[0]) for row in hub.methods.conn.execute('SELECT content FROM imports')]
  all_original=all_original and all(blobs[item['path']] in originals for item in payload['method_sources'])
 checks['collector_scoped_synthetic_idempotent']=(all_synthetic and all_idempotent)
 checks['collector_all_native_bytes']=all_original
 score=hub.methods.object(method(prefix+'-score-new-0'))['data']
 checks['collector_typed_score_links']=(score['checkpoint_id']==gid(origin,'checkpoint',prefix+'-cp-1') and score['evaluation_id']==gid(origin,'evaluation',after) and score['trajectory_id']==gid(origin,'trajectory',after+'-fixture-q1'))
 center_fixed=hub.methods.entropy_comparison(method(prefix+'-compare-prefix'))
 center_comparison=hub.methods.entropy_comparison(method(prefix+'-compare-estimators'))
 checks['collector_comparison_qualification']=(center_fixed['status']=='measured' and center_comparison['comparison_qualified'] is True and center_comparison['common_count']==6)
 data=hub.methods.object(method(prefix+'-compare-estimators'))['data']
 center_eval=hub.store.evaluation(gid(origin,'evaluation',after))
 source_hash=digest(sorted(after+'-'+row['id'] for row in hub.store.panel(center_eval['panel_id'])['samples']))
 checks['collector_sample_fingerprint']=(all(item['probe_protocol']['sample_set_hash']==digest(sorted(data['trajectory_ids'])) and item.get('source_sample_set_hash')==source_hash for item in data['estimators']))
 spec=importlib.util.spec_from_file_location('installed_entropy_example',example_path)
 example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)
 statuses=example.register_center_comparisons(hub,result['experiment_comparison_file'],origin)
 full_id=method(prefix+'-compare-budget');bad_id=method(prefix+'-compare-budget-mismatch')
 checks['collector_cross_run_comparison']=(statuses[full_id]=='measured' and statuses[bad_id]=='incomparable' and example.register_center_comparisons(hub,result['experiment_comparison_file'],origin)==statuses)
 arms=hub.methods.object(full_id)['data']['arms']
 checks['collector_cost_fingerprints']=all(arm['cost_ref']['sha256']==digest(hub.methods.object(arm['cost_ref']['object_id'])['data']) for arm in arms)
 raw=Path(result['experiment_comparison_file']).read_bytes()
 checks['collector_original_comparison_bytes']=any(bytes(row[0])==raw for row in hub.methods.conn.execute('SELECT content FROM imports'))
print(json.dumps(checks))
"""
            installed_entropy=json.loads(run([python,'-I','-c',entropy_code,entropy_workspace,temporary/'synthetic-entropy-center',copied/'entropy_tracking_example.py'],cwd=temporary,env=runtime_env))
            check('installed Entropy fixed-prefix comparison validates real token support and rejects changed conditions',installed_entropy['fixed_prefix_controlled'] and installed_entropy['incompatible_prefixes_unpooled'])
            check('installed Entropy error-ranking qualification keeps missing scores and probability calibration separate',installed_entropy['qualified_common_support'] and installed_entropy['missing_scores_not_zero'])
            check('installed Entropy three-arm comparison rejects mismatched recorded budgets',installed_entropy['budget_random_control'] and installed_entropy['mismatched_budget_no_gain'])
            check('installed Entropy actual signals, masked terms and pending updates remain distinct',installed_entropy['signals_and_updates_separate'])
            check('installed Entropy tokens are lazy and signed MI/native evidence keep their source meanings',installed_entropy['bounded_detail_and_paging'] and installed_entropy['training_and_fixed_states_separate'] and installed_entropy['signed_mi_retained'] and installed_entropy['all_native_bytes_retained'])
            check('installed Entropy HTTP serves scoped records and exact evaluation/step associations',installed_entropy['http_training_saved_only'] and installed_entropy['http_exact_runtime_binding'] and installed_entropy['http_rejects_wrong_evaluation'])
            check('installed Entropy HTTP token pages and qualified comparison use saved evidence',installed_entropy['http_token_paging'] and installed_entropy['http_qualified_comparison'])
            check('installed Entropy collector preserves typed identities, sample fingerprints and comparison qualification',installed_entropy['collector_typed_score_links'] and installed_entropy['collector_sample_fingerprint'] and installed_entropy['collector_comparison_qualification'])
            check('installed Entropy collector preserves cross-run comparisons, evidence hashes and source bytes',installed_entropy['collector_cross_run_comparison'] and installed_entropy['collector_cost_fingerprints'] and installed_entropy['collector_original_comparison_bytes'] and installed_entropy['collector_all_native_bytes'])
            check('installed Entropy evidence remains synthetic and collector replay is idempotent',all(installed_entropy.values()),installed_entropy)

            selfplay_workspace=temporary/'synthetic-selfplay-workspace'
            installed_selfplay_fixture=json.loads(run([python,'-I',copied/'selfplay_tracking_example.py','--data-dir',selfplay_workspace],cwd=temporary,env=runtime_env))
            check('installed Self-play example records two synthetic runs without trainer dependencies',installed_selfplay_fixture['synthetic_only'] and len(installed_selfplay_fixture['packages'])==2)
            selfplay_code="""import importlib.util,json,sys
from pathlib import Path
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub,gid
from searchagent_lab.team.package import source
root=Path(sys.argv[1]);result=json.loads((root/'example-verification.json').read_text());checks={}
fresh=result['runs']['fresh'];exp=result['experiment_id']
with StudioRecorder(root) as studio:
 a=studio.methods.selfplay_training(exp,fresh['run_id'],cycle_id=fresh['cycle_ids']['1'],evaluation_id=fresh['evaluation_ids']['2'])
 b=studio.methods.selfplay_training(exp,fresh['run_id'],cycle_id=fresh['cycle_ids']['2'])
 checks['supply_signal_separate']=a['counts']['new_verified_consumed_questions']==2 and a['task_signal']['active']==2 and a['task_signal']['total']==4 and a['opd_signal']['active']==1
 checks['missing_not_zero']=b['task_signal']['value'] is None and b['task_signal']['unknown']==1 and b['task_signal']['total']==4
 checks['old_first_vs_reuse']=b['counts']['old_first_consumed_questions']==1 and b['counts']['reused_questions']==1
 checks['fixed_reference']=a['difficulty']['groups']=={'all_failed':1,'partial_success':1,'all_succeeded':1,'incomplete':4} and b['difficulty']['groups']['incomplete']==1
 checks['external_selection']=b['metrics'][2]['status']=='selection_required' and a['metrics'][2]['value']==1
 spec=importlib.util.spec_from_file_location('installed_selfplay',sys.argv[3]);example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)
 wid=source(root)['workspace_id']
with Hub(sys.argv[2]) as hub:
 for package in result['packages']:hub.ingest(package,allow_definitions=True)
 comparisons=example.register_center_comparisons(hub,result['experiment_comparison_file'],wid)
 good=comparisons[gid(wid,'method','selfplay-v098-compare-supply')];bad=comparisons[gid(wid,'method','selfplay-v098-compare-budget-missing')]
 checks['comparison_budget']=good['comparison_qualified'] and not bad['comparison_qualified'] and bad['delta']['value'] is None
 accounting=hub.methods.object(gid(wid,'method','selfplay-v098-fresh-total-cost'))['data']
 checks['typed_accounting']=accounting['run_id']==gid(wid,'run',fresh['run_id']) and accounting['cycle_ids']==[gid(wid,'method',fresh['cycle_ids'][str(i)]) for i in (1,2)]
 raw=Path(result['experiment_comparison_file']).read_bytes()
 checks['source_bytes']=any(bytes(row[0])==raw for row in hub.methods.conn.execute('SELECT content FROM imports'))
 checks['idempotent']=all(hub.ingest(package)['delivery_status']=='duplicate' for package in result['packages'])
print(json.dumps(checks))
"""
            installed_selfplay=json.loads(run([python,'-I','-c',selfplay_code,selfplay_workspace,temporary/'synthetic-selfplay-center',copied/'selfplay_tracking_example.py'],cwd=temporary,env=runtime_env))
            check('installed Self-play separates new supply, actual RL and OPD support',installed_selfplay['supply_signal_separate'])
            check('installed Self-play retains missing signals and old-first-use versus reuse',installed_selfplay['missing_not_zero'] and installed_selfplay['old_first_vs_reuse'])
            check('installed Self-play freezes reference attempts and keeps external evaluations explicit',installed_selfplay['fixed_reference'] and installed_selfplay['external_selection'])
            check('installed Self-play collector preserves accounting and budget comparison qualification',installed_selfplay['comparison_budget'] and installed_selfplay['typed_accounting'])
            check('installed Self-play source bytes and collector idempotence survive transport',installed_selfplay['source_bytes'] and installed_selfplay['idempotent'])

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
                    check('installed HTTP starts with venv-only PATH and version 0.9.8',health.get('ok') is True and get('/api/session').get('version')==VERSION)
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
