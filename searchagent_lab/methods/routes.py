"""Local-only method evidence API. No arbitrary execution or file-system reads."""
from .schema import SCHEMA
from . import views
from ..core import ValidationError
from ..inspection.adapters import resolve
from ..inspection.repository import digest

def scope_from(q):
    scope = {k:q[k] for k in ('project_id','experiment_id','run_id') if k in q}
    if q.get('unbound') == '1':
        if scope.get('run_id'): raise ValidationError('unbound cannot be combined with a run_id')
        scope['run_id'] = ''
    return scope

def get(handler,path,q):
    if not path.startswith('/api/methods/'):return False
    r=handler.server.methods
    if path=='/api/methods/catalog':handler._json(r.catalog(scope_from(q) or None,q.get('kind',''),int(q.get('offset',0)),int(q['limit']) if q.get('limit') else None))
    elif path=='/api/methods/summary':handler._json(views.summary(r,q['id']))
    elif path=='/api/methods/experiment-overview':handler._json(views.experiment_overview(r,q.get('experiment_id',''),q.get('run_id','')))
    elif path=='/api/methods/assessment':handler._json(r.method_assessment(q.get('experiment_id',''),q.get('run_id',''),q.get('family',''),q.get('offset',0),q.get('limit',25),q.get('evaluation','')))
    elif path=='/api/methods/selfplay-training':handler._json(r.selfplay_training(q.get('experiment_id',''),q.get('run_id',''),q.get('cycle_id',''),q.get('reference_id',''),q.get('evaluation_id',q.get('evaluation','')),q.get('offset',0),q.get('limit',25)))
    elif path=='/api/methods/selfplay-runtime':handler._json(r.selfplay_runtime(q.get('experiment_id',''),q.get('run_id',''),q.get('evaluation_id',q.get('evaluation',''))))
    elif path=='/api/methods/selfplay-question':handler._json(r.selfplay_question(q['id'],q.get('cycle_id','')))
    elif path=='/api/methods/selfplay-comparison':handler._json(r.selfplay_comparison(q['id']))
    elif path=='/api/methods/skill-runtime':handler._json(r.skill_runtime(q.get('experiment_id',''),q.get('run_id',''),q.get('evaluation_id',q.get('evaluation','')),q.get('trajectory_id',q.get('trajectory','')),q.get('offset',0),q.get('limit',25),q.get('step')))
    elif path=='/api/methods/skill-assets':handler._json(r.skill_assets(q.get('experiment_id',''),q.get('run_id',''),q.get('offset',0),q.get('limit',25)))
    elif path=='/api/methods/skill-instance':handler._json(r.skill_instance(q['id']))
    elif path=='/api/methods/skill-comparison':handler._json(r.skill_comparison(q['id']))
    elif path=='/api/methods/opd-training':handler._json(r.opd_training(q.get('experiment_id',''),q.get('run_id',''),q.get('offset',0),q.get('limit',25)))
    elif path=='/api/methods/opd-runtime':handler._json(r.opd_runtime(q.get('experiment_id',''),q.get('run_id',''),q.get('evaluation_id',q.get('evaluation','')),q.get('trajectory_id',q.get('trajectory','')),q.get('offset',0),q.get('limit',25),q.get('step')))
    elif path=='/api/methods/opd-record':handler._json(r.opd_record(q['id']))
    elif path=='/api/methods/opd-tokens':handler._json(r.opd_tokens(q['id'],q.get('offset',0),q.get('limit',50)))
    elif path=='/api/methods/opd-recovery':handler._json(r.opd_recovery(q['id']))
    elif path=='/api/methods/entropy-training':handler._json(r.entropy_training(q.get('experiment_id',''),q.get('run_id',''),q.get('offset',0),q.get('limit',25)))
    elif path=='/api/methods/entropy-runtime':handler._json(r.entropy_runtime(q.get('experiment_id',''),q.get('run_id',''),q.get('evaluation_id',q.get('evaluation','')),q.get('trajectory_id',q.get('trajectory','')),q.get('offset',0),q.get('limit',25),q.get('step')))
    elif path=='/api/methods/entropy-record':handler._json(r.entropy_record(q['id']))
    elif path=='/api/methods/entropy-tokens':handler._json(r.entropy_tokens(q['id'],q.get('offset',0),q.get('limit',50)))
    elif path=='/api/methods/entropy-comparison':handler._json(r.entropy_comparison(q['id']))
    elif path=='/api/methods/overview':handler._json(views.overview(r,scope_from(q) or None))
    elif path=='/api/methods/skill-archive':handler._json(views.archive(r,q['id']))
    elif path=='/api/methods/skill-members':handler._json(views.members(r,q['bank'],q.get('q',''),q.get('group',''),q.get('offset',0),q.get('limit',25)))
    elif path=='/api/methods/skill-detail':handler._json(views.skill_detail(r,q['bank'],q['member']))
    elif path=='/api/methods/skill-lifecycle':
        if q.get('archive'):handler._json(views.lifecycle(r,q['archive'],q['member']))
        else:handler._json(r.skill_lifecycle(q.get('experiment_id',''),q.get('run_id',''),q.get('offset',0),q.get('limit',25)))
    elif path=='/api/methods/skill-member-diff':handler._json(views.member_diff(r,q['a'],q['b'],q['member']))
    elif path=='/api/methods/entropy':handler._json(views.entropy_observations(r,q['definition'],q.get('context',''),q.get('offset',0),q.get('limit',50),scope_from(q) or None))
    elif path=='/api/methods/object':handler._json(r.details(q['id']))
    elif path=='/api/methods/cycle':handler._json(r.cycle(q['id']))
    elif path=='/api/methods/bank':handler._json(r.skill_bank(q['id']))
    elif path=='/api/methods/evaluation-matrix':handler._json(r.evaluation_matrix(q['bank']))
    elif path=='/api/methods/usage':handler._json(r.skill_usage(q['bank'],q['member']))
    elif path=='/api/methods/diff':handler._json(r.diff(q['a'],q['b'],q.get('cross_project')=='1'))
    elif path=='/api/methods/related':handler._json(r.evidence_for(q))
    elif path=='/api/methods/resolve':
        import json
        result=r.resolve_ref(json.loads(q['ref']));handler._json(result)
    elif path=='/api/methods/export':handler._json(r.export(q['id']),filename='method-evidence.json')
    elif path=='/api/methods/original':
        f=r.original(q['id']);iszip=f['content'][:4]==b'PK\x03\x04';handler._send(f['content'],'application/zip' if iszip else 'application/octet-stream',filename=f['id']+('.zip' if iszip else '.json'))
    elif path=='/api/methods/evaluation-state':handler._json(r.evaluation_state(q['evaluation']))
    else:handler._json({'error':'Not found'},404)
    return True

def post(handler,path,data):
    if not path.startswith('/api/methods/'):return False
    r=handler.server.methods
    if path=='/api/methods/import':result=r.import_bundle(data['bundle'],scope=data.get('scope'))
    elif path=='/api/methods/review':
        result=r.append_review(data['id'],data['source_hash'],data['status'],data['note'],data.get('pointer',''),data.get('evidence',[]))
    elif path=='/api/methods/diagnostic-case':
        # Explicit multi-object case assembly. Only local resolved references.
        refs=data.get('evidence',[])
        if not isinstance(refs,list) or not 1<=len(refs)<=50:raise ValidationError('Select 1–50 explicit evidence fields')
        scope=r._scope(data.get('scope',{}));owner={'scope':scope}
        checked=[r.resolve_ref(x,owner=owner) for x in refs]
        oid=data.get('id') or 'case-'+digest([scope,[x['ref'] for x in checked]])[:24]
        case={'id':oid,'kind':'method_case','scope':scope,'title':data.get('title') or '跨产物诊断证据包','usage':'unknown',
              'data':{'question':data.get('title') or 'Method evidence case','mode':'retrospective',
                      'evidence':[{'ref':x['ref'],'value':x['value']} for x in checked],
                      'notes':'显式选取的事后证据；不是某个角色的原始请求，不能作为前缀判断或自主轨迹。'},
              'evidence_refs':[x['ref'] for x in checked], 'demo':any(r.object(x['ref']['object_id']).get('demo') is True for x in checked if 'object_id' in x['ref'])}
        result=r.import_bundle({'method_schema':SCHEMA,'objects':[case]});result['case_id']=oid
    else:handler._json({'error':'Not found'},404);return True
    handler._json(result,201);return True
