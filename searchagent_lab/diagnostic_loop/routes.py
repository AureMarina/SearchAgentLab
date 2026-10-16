"""Local HTTP endpoints for case diagnostics, never external execution."""
from ..inspection.routes import document
from ..inspection.repository import digest
from ..core import ValidationError
from .evidence import compare_context


def formal_context(ev):
    values= {**ev['protocol'],'checkpoint_id':ev['checkpoint_id'],
            'weights_sha256':ev['checkpoint'].get('weights_sha256'),
            'base_model':ev['run'].get('base_model'),'model_revision':ev['run'].get('model_revision'),
            'panel_hash':ev.get('panel_hash',ev['panel'].get('fingerprint'))}
    if ev.get('method_state',{}).get('status')!='not_recorded' and ev.get('method_state'):
        values['method_state']=ev['method_state'].get('runtime')
    inputs=values.get('input',{})
    for dst,key in [('prompt','prompt_sha256'),('chat_template','chat_template_sha256'),('tokenizer','tokenizer_revision')]:
        if dst not in values and key in inputs:values[dst]=inputs[key]
    return values

def source(handler,params):
    d=document(handler.server.inspections,handler.server.store,params,handler.server.methods)
    if d['source']['kind']=='evaluation':
        ev=handler.server.store.evaluation(d['source']['evaluation_id']);d['formal_context']=formal_context(ev)
    return d

def get(handler,path,q):
    if not path.startswith('/api/loop/'):return False
    repo=handler.server.loop;doc=source(handler,q)
    if path=='/api/loop/workspace':handler._json(repo.workspace(doc))
    elif path=='/api/loop/export':handler._json(repo.export(doc),filename='case-verification-evidence.json')
    elif path=='/api/loop/object':handler._json(repo.object(q['id'],doc),filename='diagnostic-object.json')
    else:handler._json({'error':'Not found'},404)
    return True

def post(handler,path,data):
    if not path.startswith('/api/loop/'):return False
    repo=handler.server.loop;doc=source(handler,data);op=path.rsplit('/',1)[-1]
    methods={'contract':repo.bind_contract,'hypothesis':repo.add_hypothesis,'rule-hypothesis':repo.hypothesis_from_rule,
             'task':repo.add_task,'execute':repo.execute,'result':repo.import_result,
             'hypothesis-review':repo.review_hypothesis,'fix':repo.record_fix,
             'judge-packet':repo.add_judge_packet,'judge-result':repo.import_judge,
             'judge-review':repo.review_judge,'reliability':repo.add_reliability}
    if op=='judge-packet' and doc['source'].get('object_kind')=='method_case' and data.get('mode')=='decision_prefix':
        raise ValidationError('A multi-object method case is retrospective, not a role-visible prefix')
    if op in methods:result=methods[op](doc,data)
    elif op=='context-diff':
        repo.guard(doc,data)
        result=compare_context(data['baseline'],data['candidate'],data.get('allowed_changes',[]),data.get('required_paths'))
    elif op=='evaluation-result':
        repo.guard(doc,data);task=repo.object(data['task_id'],doc,'task');arms=[]
        evs=[handler.server.store.evaluation(data[k]) for k in ('baseline_evaluation','candidate_evaluation')]
        if evs[0]['project_id']!=evs[1]['project_id'] and data.get('cross_project') is not True:raise ValidationError('Cross-project comparison requires explicit confirmation')
        if evs[0]['panel_hash']!=evs[1]['panel_hash']:raise ValidationError('Different frozen panels cannot be a controlled regression')
        for ev in evs:
            exported=handler.server.store.export_evaluation(ev['id']);traces=exported['trajectories']
            wanted={(c['dataset'],c['sample_id']) for c in task['expected_cases']}
            selected=[t for t in traces if (t['dataset'],t['sample_id']) in wanted]
            arms.append({'evaluation_id':ev['id'],'trace_sha256':digest(traces),'context':formal_context(ev),
                'groundtruth_assisted':ev['protocol'].get('groundtruth_to_agent') is not False,
                'from_empty_state':all(ev['protocol'].get('state_reset',{}).get(k) is True for k in ('per_question','between_evaluations')),
                'cases':[{'dataset':t['dataset'],'sample_id':t['sample_id'],'em':t['scores'].get('em'),
                          'trajectory_id':t['id'],'parsed_answer':t.get('parsed_answer'),'runtime_status':t.get('runtime_status')} for t in selected]})
        result=repo.import_result(doc,{**data,'result':{'task_id':task['id'],'task_hash':task['task_hash'],
            'execution':'executed_externally','baseline':arms[0],'candidate':arms[1]}})
    else:handler._json({'error':'Not found'},404);return True
    handler._json(result,201);return True
