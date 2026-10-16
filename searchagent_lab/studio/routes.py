"""Narrow local HTTP adapter. All writes pass the server's CSRF checks."""
import json
from ..core import ValidationError
from ..io import import_bundle


def get(h,p,q):
    if not p.startswith('/api/studio/'):return False
    r=h.server.studio
    if p=='/api/studio/workspace':result=r.workspace()
    elif p=='/api/studio/reference':result=r.reference(q.get('experiment',''),q.get('run',''),q.get('baseline_run',''))
    elif p=='/api/studio/catalog':result=r.catalog(q.get('project',''),q.get('track','main'))
    elif p=='/api/studio/results':result=r.results(q['run'],q.get('panel',''),q.get('b',''),q.get('a',''),scope=q.get('scope',''))
    elif p=='/api/studio/trace-browser':
        from .trace_browser import trace_browser
        result=trace_browser(r,q['run'],q.get('panel',''),q.get('b',''),q.get('a',''),q.get('axis','checkpoint'),
            scope=q.get('scope',''),offset=int(q.get('offset','0')),limit=int(q.get('limit','30')),
            query=q.get('q',''),outcome=q.get('filter','all'),sample_id=q.get('sample',''),dataset=q.get('dataset',''),row_key=q.get('key',''),dataset_filter=q.get('dataset_filter',''))
    elif p=='/api/studio/evaluation-summary':result=r.evaluation_summary(q.get('run',''),q.get('evaluation',''))
    elif p=='/api/studio/evaluation-pair':result=r.evaluation_pair(q['a'],q['b'],q.get('axis','checkpoint'))
    elif p=='/api/studio/training-summary':result=r.training_summary(q['run'])
    elif p=='/api/studio/training-rollout':result=r.training_rollout(q['run'],q['batch'],q['group'],q.get('sample',''),q.get('sample_index'))
    elif p=='/api/studio/profile':result=r.profile(q['experiment'])
    elif p=='/api/studio/position':
        tr=r.store.trajectory(q['trajectory'])
        if tr['evaluation_id']!=q['evaluation']:raise ValidationError('Trajectory is outside selected evaluation')
        with r.store.lock:
            n=r.store.conn.execute('SELECT count(*) FROM trajectories WHERE evaluation_id=? AND (dataset<? OR (dataset=? AND sample_id<?))',(q['evaluation'],tr['dataset'],tr['dataset'],tr['sample_id'])).fetchone()[0]
        result={'offset':n//50*50,'position':n}
    elif p=='/api/studio/dynamics':result=r.dynamics(q['run'],q['panel'],json.loads(q.get('selections','{}')),q.get('baseline','previous'))
    elif p=='/api/studio/reports':result=r.report_catalog(q['project'],q.get('track','main'))
    elif p=='/api/studio/report':result=r.report(q['b'],q.get('a',''))
    elif p=='/api/studio/saved-report':result=r.saved_report(q['id'])
    elif p=='/api/studio/batches':result=r.batches(q['run'],int(q['step']))
    elif p=='/api/studio/pair':result=r.pair(q['a'],q['b'])
    else:h._json({'error':'Not found'},404);return True
    h._json(result);return True


def post(h,p,d):
    if not p.startswith('/api/studio/'):return False
    r=h.server.studio
    if not isinstance(d,dict):raise ValidationError('Request body must be an object')
    if p=='/api/studio/presentation':result=r.register_presentation(d)
    elif p=='/api/studio/profile':result=r.register_profile(d)
    elif p=='/api/studio/experiment':result=r.create_experiment(d)
    elif p=='/api/studio/project':result={'id':r.store.register_project(d)}
    elif p=='/api/studio/report':result=r.save_report(d)
    elif p=='/api/studio/batch':result=r.record_batch(d)
    elif p=='/api/studio/agent-evaluation':result=r.register_agent_evaluation(d)
    elif p=='/api/studio/harness-check':result=r.record_harness_check(d)
    elif p=='/api/studio/import':result=import_bundle(r.store,d)
    else:h._json({'error':'Not found'},404);return True
    h._json(result,201);return True
