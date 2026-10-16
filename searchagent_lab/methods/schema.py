"""Explicit evidence protocol. Never infer role, training use or success from names."""
from __future__ import annotations
import re
from ..core import ValidationError
from ..inspection.repository import digest, encode
SCHEMA='method-evidence-v1'
KINDS={'selfplay_cycle','generated_question','proposal','question_review','solver_attempt',
       'trace','training_use','training_update','skill_bank','skill_retrieval','skill_evolution',
       'dataset_manifest','evaluation_binding','method_case','state_snapshot',
       'skill_archive','method_profile','entropy_definition','entropy_observation',
       'entropy_probe','entropy_intervention','entropy_comparison',
       'opd_failure','opd_teacher','opd_supervision','opd_diagnostic','opd_recovery',
       'skill_asset','skill_instance','skill_comparison',
       'selfplay_reference','selfplay_pool_event','selfplay_comparison'}
USAGES={'training','evolution','validation','test','unknown'}
MAX_OBJECTS=10000
MAX_OBJECT_BYTES=8*1024*1024

def require(value, label, maximum=20000):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:
        raise ValidationError(label+' must be a nonempty string (max '+str(maximum)+')')
    return value

def identifier(v,label='id'):
    if not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,180}',v):
        raise ValidationError(label+' must contain 1–180 ASCII letters, digits, _.:-')
    return v

def array(v,label):
    if not isinstance(v,list): raise ValidationError(label+' must be an array')
    return v

def validate_object(o):
    if not isinstance(o,dict):raise ValidationError('Each method object must be an object')
    identifier(o.get('id'))
    kind=o.get('kind')
    if kind not in KINDS and not (isinstance(kind,str) and re.fullmatch(r'extension\.[A-Za-z0-9_.-]{1,80}',kind)):
        raise ValidationError('Unknown kind: use extension.your_method for unrecognized payloads')
    if not isinstance(o.get('data'),dict):raise ValidationError('data must be an object; original contents belong here')
    try:serialized_size=len(encode(o).encode())
    except (TypeError,ValueError,RecursionError) as exc:raise ValidationError('Method data must be finite JSON values') from exc
    if serialized_size>MAX_OBJECT_BYTES:raise ValidationError('Method object exceeds 8 MiB')
    scope=o.get('scope',{})
    if not isinstance(scope,dict) or set(scope)-{'project_id','experiment_id','run_id'}:
        raise ValidationError('scope accepts project_id / experiment_id / run_id only')
    for k,v in scope.items():identifier(v,k)
    for k in ('demo','shareable'):
        if k in o and not isinstance(o[k],bool):raise ValidationError(k+' must be boolean')
    if o.get('usage','unknown') not in USAGES:raise ValidationError('Unknown data usage')
    for k in ('derived_from','evidence_refs'):
        array(o.get(k,[]),k)
    for v in o.get('derived_from',[]):identifier(v,'derived_from')
    if len(set(o.get('derived_from',[])))!=len(o.get('derived_from',[])):
        raise ValidationError('Duplicate derived_from entry')
    if 'version' in o:require(o['version'],'version',180)
    d=o['data']
    def need(*names):
        for key in names:require(d.get(key),kind+'.'+key,200000)
    if kind=='selfplay_cycle':
        ps=array(d.get('participants',[]),'participants'); ids=[]
        for p in ps:
            if not isinstance(p,dict):raise ValidationError('participant must be an object')
            ids.append(identifier(p.get('id'),'participant.id'))
        if len(ids)!=len(set(ids)):raise ValidationError('Duplicate participant id in cycle')
    elif kind=='generated_question':need('question')
    elif kind=='proposal':need('cycle_id','question_id','actor_id')
    elif kind=='solver_attempt':
        need('cycle_id','question_id','actor_id')
        if 'scores' in d:
            if not isinstance(d['scores'],dict):raise ValidationError('scores must be an object')
            em=d['scores'].get('em')
            if em is not None and (isinstance(em,bool) or em not in (0,1)):raise ValidationError('EM is 0/1/null, never reward')
    elif kind=='question_review':
        need('question_id')
        if d.get('status') not in ('accepted','rejected','needs_review'):raise ValidationError('Unknown question review status')
        if d.get('status')=='accepted' and 'verified_answer' in d and not d.get('reviewer'):
            raise ValidationError('A verified answer requires a recorded reviewer identity/version')
    elif kind=='training_use':
        array(d.get('artifact_ids',[]),'artifact_ids')
        if not d.get('artifact_ids'):raise ValidationError('training_use requires artifact_ids')
        if d.get('status') not in ('selected','sampled','excluded'):raise ValidationError('status is selected/sampled/excluded')
        if d['status']=='sampled':need('batch_id')
        if d.get('step') is not None and (type(d['step']) is not int or d['step']<0):raise ValidationError('step must be a nonnegative integer')
    elif kind=='training_update':
        array(d.get('consumption_ids',[]),'consumption_ids')
    elif kind=='skill_retrieval':
        need('bank_id')
        for k in ('candidates','selected','injections'):array(d.get(k,[]),k)
        for v in d.get('injections',[]):
            if not isinstance(v,dict):raise ValidationError('injection must be an object')
            require(v.get('member'),'injection.member',500)
            if 'rendered_text' in v and not isinstance(v['rendered_text'],str):raise ValidationError('rendered_text must be text')
        if 'injections_complete' in d and not isinstance(d['injections_complete'],bool):raise ValidationError('injections_complete must be boolean')
    elif kind=='skill_evolution':
        if sum(bool(d.get(k)) for k in ('bank_id','asset_id','instance_id'))!=1:
            raise ValidationError('skill_evolution needs one bank_id, asset_id or instance_id')
    elif kind=='evaluation_binding':
        need('evaluation_id')
        if d.get('binding_origin') not in ('recorded_execution','retrospective'):
            raise ValidationError('binding_origin must be recorded_execution or retrospective')
        if not isinstance(d.get('retrieval_config',{}),dict):raise ValidationError('retrieval_config must be object')
    from .contracts import validate_method_contract
    validate_method_contract(o)
    from .skill_tracking import validate_skill_contract
    validate_skill_contract(o)
    from .opd_tracking import validate_opd_contract
    validate_opd_contract(o)
    from .entropy_tracking import validate_entropy_contract
    validate_entropy_contract(o)
    from .selfplay_tracking import validate_selfplay_contract
    validate_selfplay_contract(o)
    return o

# Direct entity references have typed targets; evidence references are resolved separately.
REF_FIELDS={
 'skill_bank':{'asset_ids':{'skill_asset'}},
 'skill_archive':{'bank_ids':{'skill_bank'}},
 'entropy_observation':{'definition_id':{'entropy_definition'}},
 'entropy_probe':{'definition_id':{'entropy_definition'}},
 'entropy_intervention':{'definition_id':{'entropy_definition'},'observation_ids':{'entropy_observation','entropy_probe'}},
 'proposal':{'cycle_id':{'selfplay_cycle'},'question_id':{'generated_question'}},
 'solver_attempt':{'cycle_id':{'selfplay_cycle'},'question_id':{'generated_question'},'reference_id':{'selfplay_reference'}},
 'question_review':{'question_id':{'generated_question'},'supersedes':{'question_review'}},
 'generated_question':{'first_generated_cycle_id':{'selfplay_cycle'},'generation_cycle_id':{'selfplay_cycle'},'duplicate_of':{'generated_question'}},
 'selfplay_pool_event':{'question_id':{'generated_question'},'cycle_id':{'selfplay_cycle'}},
 'training_use':{'artifact_ids':None,'cycle_id':{'selfplay_cycle'},'attempt_id':{'solver_attempt'}},
 'training_update':{'consumption_ids':{'training_use'},'cycle_id':{'selfplay_cycle'}},
 'skill_retrieval':{'bank_id':{'skill_bank'}},
 'skill_asset':{'parent_asset_id':{'skill_asset'}},
 'skill_instance':{'asset_id':{'skill_asset'},'bank_id':{'skill_bank'},'comparison_ids':{'skill_comparison'}},
 'skill_evolution':{'bank_id':{'skill_bank'},'parent_bank_id':{'skill_bank'},
                    'asset_id':{'skill_asset'},'parent_asset_id':{'skill_asset'},
                    'instance_id':{'skill_instance'},'comparison_ids':{'skill_comparison'}},
 'skill_comparison':{'initial_bank_id':{'skill_bank'},'current_bank_id':{'skill_bank'},
                     'skill_asset_id':{'skill_asset'},'bank_id':{'skill_bank'}},
 'evaluation_binding':{'bank_id':{'skill_bank'}},
 'opd_teacher':{'failure_id':{'opd_failure'}},
 'opd_supervision':{'failure_id':{'opd_failure'},'teacher_ids':{'opd_teacher'}},
 'opd_diagnostic':{'failure_id':{'opd_failure'},'teacher_ids':{'opd_teacher'},
                   'supervision_id':{'opd_supervision'}},
}

def direct_refs(o):
    for field,kinds in REF_FIELDS.get(o['kind'],{}).items():
        value=o['data'].get(field)
        if value is None:continue
        for v in value if isinstance(value,list) else [value]:
            yield v,kinds,'/data/'+field
    for v in o.get('derived_from',[]):yield v,None,'/derived_from'
    if o['kind']=='opd_recovery':
        from .opd_recovery import recovery_refs
        yield from recovery_refs(o)
    if o['kind']=='entropy_comparison':
        from .entropy_evaluation import entropy_comparison_refs
        yield from entropy_comparison_refs(o)
    if o['kind']=='selfplay_comparison':
        from .selfplay_evaluation import selfplay_comparison_refs
        yield from selfplay_comparison_refs(o)
    if o['kind']=='trace' and o['data'].get('selfplay_evidence_type')=='run_accounting':
        for cid in o['data']['cycle_ids']:yield cid, {'selfplay_cycle'}, '/data/cycle_ids'

def evidence_refs(o):
    """Only documented evidence fields are treated as references, never arbitrary JSON."""
    for r in o.get('evidence_refs',[]):yield r
    for k in ('trace_ref','teacher_trace_ref','proposer_trace_ref','request_ref'):
        if o['data'].get(k) is not None:yield o['data'][k]
    for r in o['data'].get('evidence_refs',[]):yield r
    for x in o['data'].get('injections',[]):
        if isinstance(x,dict) and x.get('evidence_ref') is not None:yield x['evidence_ref']
    verification=o['data'].get('verification')
    if isinstance(verification,dict) and verification.get('evidence_ref') is not None:
        yield verification['evidence_ref']
    if o['kind']=='skill_instance':
        for key in ('selection','visibility','loading','execution'):
            value=o['data'].get(key,{})
            if isinstance(value,dict):
                for field in ('evidence_ref','request_ref'):
                    if value.get(field) is not None:yield value[field]
                for ref in value.get('evidence_refs',[]):yield ref
        for value in o['data'].get('related_actions',[]):
            if isinstance(value,dict) and value.get('evidence_ref') is not None:yield value['evidence_ref']
    if o['kind']=='skill_evolution':
        for ref in o['data'].get('source_refs',[]):yield ref
    if o['kind'] in ('opd_failure','opd_teacher','opd_supervision','opd_diagnostic'):
        for ref in o['data'].get('source_refs',[]):yield ref
        for key in ('start_ref','verification_ref'):
            if o['data'].get(key) is not None:yield o['data'][key]
    if o['kind']=='opd_recovery':
        from .opd_recovery import recovery_evidence_refs
        yield from recovery_evidence_refs(o)
    if o['kind'] in ('entropy_definition','entropy_observation','entropy_probe','entropy_intervention'):
        for ref in o['data'].get('source_refs',[]):yield ref
        for key in ('input_ref','measurement_ref','selection_ref','cost_ref'):
            if o['data'].get(key) is not None:yield o['data'][key]
        prefix=o['data'].get('frozen_prefix',{})
        if isinstance(prefix,dict) and prefix.get('input_ref') is not None:yield prefix['input_ref']
        for section in ('selection','coverage'):
            value=o['data'].get(section,{})
            if isinstance(value,dict) and value.get('evidence_ref') is not None:yield value['evidence_ref']
    if o['kind']=='entropy_comparison':
        from .entropy_evaluation import entropy_comparison_evidence_refs
        yield from entropy_comparison_evidence_refs(o)
    if o['kind']=='selfplay_comparison':
        from .selfplay_evaluation import selfplay_comparison_evidence_refs
        yield from selfplay_comparison_evidence_refs(o)
    if o['kind']=='question_review':
        for value in o['data'].get('checks', {}).values():
            if value.get('evidence_ref') is not None:yield value['evidence_ref']
