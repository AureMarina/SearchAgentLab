"""Compact, rebuildable observations for method assessments.

The immutable objects retain complete prompts, failed branches and artifacts.
Only small counts and declared identifiers enter the list/read index. A signal
flag describes a recorded loss term, never a per-sample causal gradient claim.
"""
from __future__ import annotations

from ..inspection.adapters import text

VERSION=9
KINDS={'skill_bank','skill_retrieval','skill_evolution','selfplay_cycle',
       'generated_question','proposal','question_review','solver_attempt',
       'training_use','training_update','evaluation_binding',
       'opd_failure','opd_teacher','opd_supervision','opd_diagnostic','opd_recovery',
       'skill_asset','skill_instance','skill_comparison','skill_archive',
       'entropy_definition','entropy_observation','entropy_probe','entropy_intervention','entropy_comparison',
       'selfplay_reference','selfplay_pool_event','selfplay_comparison'}


def active_signal(d):
    if not isinstance(d,dict):return None,'未记录监督项'
    if isinstance(d.get('loss_mask'),list) and d.get('loss_mask_tokens') is None:
        d={**d,'loss_mask_tokens':sum(x!=0 for x in d['loss_mask'])}
    objective=d.get('objective')
    if d.get('loss_active') is False:return False,'训练器记录此学习项未生效'
    if d.get('loss_mask_tokens')==0:return False,'有效 loss mask 为零'
    if d.get('loss_weight')==0:return False,'学习项权重为零'
    if objective=='advantage_reweight' and d.get('original_advantage')==0:
        return False,'纯乘法重加权后的任务 advantage 仍为零；其他损失另列'
    if objective in ('policy_gradient','advantage_reweight'):
        if d.get('effective_advantage')==0:return False,'实际任务 advantage 为零；不代表所有优化器梯度为零'
        if d.get('effective_advantage') is None:return None,'实际 effective_advantage 未记录'
    elif objective not in ('distillation','supervised_finetuning'):
        return None,'学习项目标没有可核对的信号定义'
    if d.get('loss_mask_tokens') is None:return None,'有效 loss mask 未记录'
    if d.get('loss_weight') is None:return None,'实际学习项权重未记录'
    return True,'所记录学习项的 mask、权重及必要的 advantage 非零；不推断样本的因果贡献'


def _pick(d,*keys):
    return {k:d[k] for k in keys if k in d}


def _credit_summary(credit):
    """Keep list screens bounded; complete token credit arrays stay in payload."""
    out={'instance_id':credit['instance_id']}
    for key in ('skill_decision','action_decision'):
        if key not in credit:continue
        source=credit[key];value={}
        for field in ('reward','advantage','original_advantage','effective_advantage','loss_weight','mask','loss_mask','source','objective','signal'):
            if field not in source:continue
            v=source[field]
            if isinstance(v,list) and len(v)>32 and all(type(x) in (int,float,bool) for x in v):
                value[field+'_summary']={'count':len(v),'nonzero':sum(x!=0 for x in v),'min':min(v),'max':max(v)}
            else:value[field]=v
        out[key]=value
    return out


def object_projection(repo,o):
    d=o['data'];kind=o['kind']
    p=_pick(d,'step','cycle_id','question_id','sample_id','trajectory_id','group_id')
    if kind=='skill_bank':
        # Members are already indexed separately. Never duplicate skill bodies.
        p={'asset_ids':d['asset_ids']} if 'asset_ids' in d else {}
    elif kind=='skill_asset':
        p=_pick(d,'skill_id','version','package_hash','parent_asset_id','owner')
        p['file_count']=len(d['package']['files'])
    elif kind=='skill_instance':
        from .skill_tracking import instance_projection
        p=instance_projection(repo,o)
    elif kind=='skill_comparison':
        p=_pick(d,'comparison_type','initial_checkpoint_id','current_checkpoint_id',
                'initial_bank_id','current_bank_id','cells','checkpoint_id','skill_asset_id',
                'bank_id','member','member_content_hash','with_evaluation_id','without_evaluation_id','context')
    elif kind=='skill_retrieval':
        p.update(_pick(d,'bank_id','injections_complete','trajectory_complete','evaluation_id','trace_ref'))
        p['selection_source']=d.get('selection',{}).get('source',d.get('selection_source','unknown'))
        p['recorded_steps']=[d['step']] if type(d.get('step')) is int else []
        bank=repo.conn.execute('SELECT content_hash FROM objects WHERE id=?',(d['bank_id'],)).fetchone()
        p['bank_content_hash']=bank['content_hash'] if bank else None
        identity=d.get('trajectory_id')
        if identity is None:
            r=d.get('trace_ref')
            if isinstance(r,dict):
                identity=r.get('trajectory') or ('method:'+r['object_id'] if r.get('object_id') else
                    'file:'+str(r.get('file'))+':'+str(r.get('record')) if r.get('file') else None)
        if identity is None and all(d.get(k) is not None for k in ('evaluation_id','dataset','sample_id','trial_id')):
            import json
            identity='trial:'+json.dumps([d[k] for k in ('evaluation_id','dataset','sample_id','trial_id')],ensure_ascii=False,separators=(',',':'))
        p['trajectory_id']=identity
        # Legacy collectors sometimes supplied only a formal trajectory ID.
        # Recover its evaluation only when its registered owner really matches;
        # arbitrary external strings are not promoted to formal bindings.
        if repo.store:
            from ..core import NotFoundError
            try:
                formal_id=d.get('trajectory_id') or (d.get('trace_ref') or {}).get('trajectory')
                ev_id=repo.store.trajectory(formal_id)['evaluation_id'] if formal_id else d.get('evaluation_id')
                ev=repo.store.evaluation(ev_id) if ev_id else None
                expected={'project_id':ev['project_id'],'experiment_id':ev['experiment_id'],'run_id':ev['checkpoint']['run_id']} if ev else {}
                verified=bool(ev) and all(o.get('scope',{}).get(k,'')==v for k,v in expected.items())
                if d.get('evaluation_id') and ev_id!=d['evaluation_id']:verified=False
                p['evaluation_id']=ev_id if verified else None
                p['binding_verified']=verified
            except (NotFoundError,KeyError):
                p['evaluation_id']=None;p['binding_verified']=False
        p['selected_count']=len(d.get('selected',[]));p['recorded_injections']=len(d.get('injections',[]))
        p['verified_injections']=0;p['checked_injections']=0
        from .diagnostics import visibility_evidence
        from .skill_tracking import input_contains
        direct=d.get('final_input') if 'final_input' in d else None
        complete_input=direct is not None
        if not complete_input and isinstance(d.get('request_ref'),dict) and visibility_evidence(d['request_ref']):
            complete_input=True
        for injection in d.get('injections',[]):
            rendered=injection.get('rendered_text');ref=injection.get('evidence_ref')
            if not isinstance(rendered,str) or not rendered:continue
            if direct is not None:
                p['checked_injections']+=1
                p['verified_injections']+=int(input_contains(rendered,direct))
            elif isinstance(ref,dict) and visibility_evidence(ref):
                checked=repo.resolve_ref(ref,owner=o)
                p['checked_injections']+=1
                p['verified_injections']+=int(input_contains(rendered,checked['value']))
                complete_input=True
        p['final_input_recorded']=complete_input
        p['input_status']='verified' if p['verified_injections'] else (
            'absent' if d.get('injections_complete') is True and complete_input and p['checked_injections']==p['recorded_injections'] else 'unknown')
        p['log_complete']=(d.get('trajectory_complete') is True and d.get('injections_complete') is True
            and complete_input and p['checked_injections']==p['recorded_injections'])
    elif kind=='skill_evolution':
        from .skill_tracking import evolution_projection
        p.update(evolution_projection(o))
        p.update(_pick(d,'bank_id','parent_bank_id','asset_id','instance_id'))
        if isinstance(p.get('reason'),str):p['reason']=p['reason'][:250]
    elif kind=='selfplay_cycle':
        p.update(_pick(d,'coverage','round','round_index'))
        p['participant_count']=len(d.get('participants',[]))
    elif kind=='generated_question':
        p.update(_pick(d,'first_generated_cycle_id','generation_step','duplicate_of'))
        p['question_uid']=d.get('stable_question_id') or d.get('question_uid')
        p['question_preview']=d['question'][:240]
        p['deduplication']=d.get('deduplication',{}).get('status','unknown')
    elif kind=='proposal':p.update(_pick(d,'actor_id'))
    elif kind=='question_review':
        p.update(_pick(d,'status','supersedes'))
        v=d.get('verification',{})
        p['verification_status']=v.get('status')
        p['verifier_recorded']=bool(v.get('verifier'))
        p['verification_method']=v.get('method')
        # Preserve the old verified-answer contract without making all accepted
        # selections synonymous with independent answer verification.
        p['legacy_verified_answer']=('verified_answer' in d and d['verified_answer'] is not None and bool(d.get('reviewer')))
    elif kind=='solver_attempt':
        p.update(_pick(d,'actor_id','advantage','groundtruth_visible'))
        p['em']=d.get('scores',{}).get('em')
    elif kind=='training_use':
        p.update(_pick(d,'artifact_ids','status','batch_id'))
        flag,reason=active_signal(d.get('signal'))
        p.update(signal_active=flag,signal_reason=reason)
        if 'credit_assignment' in d:
            from .skill_tracking import credit_entries
            p['credit_assignment']=[_credit_summary(c) for c in credit_entries(d['credit_assignment'])]
    elif kind=='training_update':
        p.update(_pick(d,'consumption_ids','input_checkpoint_id','output_checkpoint_id','status'))
    elif kind=='evaluation_binding':
        p.update(_pick(d,'evaluation_id','assessment_families','independent','autonomous','binding_origin'))
        if 'opd_context' in d:
            from .opd_tracking import _small
            p['opd_context']=_small(d['opd_context'])
        if 'entropy_context' in d:
            from .opd_tracking import _small
            p['entropy_context']=_small(d['entropy_context'])
    elif kind=='opd_failure':
        p.update(_pick(d,'student_checkpoint_id','eligible','attempt_budget','failure_step','original_advantage','sampling_protocol'))
        p['state_status']=d['state']['status']
    elif kind=='opd_teacher':
        p.update(_pick(d,'failure_id','teacher_id','mode','status','attempt_index','state_alignment'))
        v=d.get('verification',{})
        p.update(verification_status=v.get('status'),verification_method=v.get('method'),
                 verifier_recorded=bool(v.get('verifier')))
        p['verified_success']=(d.get('mode')=='continuation' and d.get('status')=='completed'
            and d.get('state_alignment') in ('restored','replayed') and v.get('status')=='passed'
            and v.get('method') in ('environment','scorer') and bool(v.get('verifier')))
        p['verified_failure']=(d.get('mode')=='continuation' and d.get('status') in ('completed','failed')
            and d.get('state_alignment') in ('restored','replayed') and v.get('status')=='failed'
            and v.get('method') in ('environment','scorer') and bool(v.get('verifier')))
    elif kind=='opd_supervision':
        p.update(_pick(d,'failure_id','teacher_ids','objective','status','loss_mask_tokens','loss_weight',
                       'original_advantage','effective_advantage','loss_active'))
        flag,reason=active_signal(d)
        if d['status']!='accepted':flag=False;reason='监督记录未接受'
        p.update(signal_active=flag,signal_reason=reason)
    elif kind=='opd_diagnostic':
        from .opd_tracking import diagnostic_projection
        p=diagnostic_projection(repo,o)
    elif kind=='opd_recovery':
        from .opd_recovery import recovery_summary
        p=recovery_summary(o)
    elif kind in ('entropy_definition','entropy_observation','entropy_probe','entropy_intervention','entropy_comparison'):
        from .entropy_tracking import entropy_projection
        p=entropy_projection(repo,o)
    if kind in ('opd_failure','opd_teacher','opd_supervision'):
        from .opd_tracking import opd_projection
        p=opd_projection(repo,o,p)
    if kind in ('selfplay_cycle','generated_question','question_review','solver_attempt','training_use','selfplay_reference','selfplay_pool_event','selfplay_comparison'):
        from .selfplay_tracking import selfplay_projection
        p=selfplay_projection(o,p)
    return p
