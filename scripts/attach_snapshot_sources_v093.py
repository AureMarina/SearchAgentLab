"""Attach already supplied metadata to its explicit experiment, never a run.

This is an opt-in helper for the delivered snapshot, not a startup migration.
It never creates a run, evaluation, score, teacher response or skill-use event.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from searchagent_lab.core import digest,ValidationError
from searchagent_lab.studio.sdk import StudioRecorder

ARCHIVE_SHA256='f380850d2c7528fa283cb8b9e86921241834a5ff0f68797835c312ea18726ac5'
DECLARATIONS={
    'sdar-searchqa-4b-reproduction':('SDAR',['opd']),
    'skillrl-qwen3-4b-reproduction':('SkillRL',['skill']),
    'uniopsd-qwen3-4b-reproduction':('UniOPSD',['opd']),
}


def attach(data_dir,archive):
    raw=Path(archive).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_SHA256:
        raise ValidationError('This helper only attaches the previously supplied, audited SkillRL archive')
    receipt={'version':'0.9.3','profiles':[],'archive':None,
             'scope_rule':'Experiment-level unbound evidence only; no inference of run use or evaluation state.'}
    with StudioRecorder(data_dir) as rec:
        for eid,(method,families) in DECLARATIONS.items():
            row=rec.store.conn.execute('SELECT data FROM experiments WHERE id=?',(eid,)).fetchone()
            if not row:continue
            experiment=json.loads(row[0]);profile=rec.repo.profile(eid)
            if profile.get('track')!='reproduction' or profile.get('reproduction',{}).get('method_name')!=method:
                raise ValidationError('Existing explicit reproduction metadata does not match '+eid)
            evidence={'method_name':method,'families':families,
                'configuration':{'experiment_config':experiment.get('config',{}),'reproduction':profile['reproduction']},
                'source':{'kind':'existing_experiment_profile','experiment_id':eid,'profile_sha256':digest(profile)},
                'notes':'已有复现档案中的方法声明；这不是逐次执行、teacher 输出或训练消费记录。'}
            oid='profile-v093-'+digest([eid,evidence])[:24]
            result=rec.methods.import_bundle({'method_schema':'method-evidence-v1','objects':[
                {'id':oid,'kind':'method_profile','title':method+' · 已有方法配置','data':evidence}]},
                scope={'experiment_id':eid})
            receipt['profiles'].append({'experiment_id':eid,'object_id':oid,'method':method,'families':families})
        eid='skillrl-qwen3-4b-reproduction'
        if any(p['experiment_id']==eid for p in receipt['profiles']):
            result=rec.methods.import_bytes(raw,Path(archive).name,scope={'experiment_id':eid})
            receipt['archive']={**result,'experiment_id':eid,'run_id':None,'source_sha256':ARCHIVE_SHA256,
                'note':'用户已有技能库快照作为此复现实验的归档资料；原文件未记录对应 run，不绑定 step100/150 评测。'}
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--archive',type=Path,default=ROOT.parent/'eval-data/reference-assets/skillrl/SkillRL_skills_20261005.zip')
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    if not (args.data_dir/'searchagent_lab.sqlite3').is_file():
        parser.error('--data-dir must be the existing snapshot or an explicitly selected existing workspace')
    result=attach(args.data_dir,args.archive)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.receipt:
        args.receipt.parent.mkdir(parents=True,exist_ok=True);args.receipt.write_text(text,encoding='utf-8')
    print(text)


if __name__=='__main__':main()
