"""HTTP adapter, isolated from the formal experiment/evaluation APIs."""
from urllib.parse import parse_qs, urlparse
from ..core import ValidationError
from .diagnostics import RULES, RULESET_VERSION
from .summary import problem_summary
import json


def document(repo, store, params, methods=None):
    if params.get("method"):
        if methods is None: raise ValidationError("Method repository unavailable")
        doc=methods.document(params["method"])
    elif params.get('trajectory'):
        doc=repo.evaluation_record(store.trajectory(params['trajectory']))
        ev=store.evaluation(doc['source']['evaluation_id'])
        doc['demo']=bool(ev.get('run',{}).get('demo') or ev.get('panel',{}).get('demo'))
        doc['source']['ownership']={k:ev.get(k) for k in ('project_id','experiment_id','checkpoint_id')}
    else:
        doc=repo.record(params['file'], int(params['record']))
    doc['problem_summary']=problem_summary(doc)
    return doc


def summaries(repo, store, evaluation_id, ids):
    if not isinstance(ids, list) or len(ids)>50 or any(not isinstance(i,str) for i in ids):
        raise ValidationError('Provide at most 50 explicit trajectory IDs')
    store.evaluation(evaluation_id)
    # Validate the complete page before projecting any evidence.
    records=[store.trajectory(i) for i in dict.fromkeys(ids)]
    if any(r['evaluation_id']!=evaluation_id for r in records):
        raise ValidationError('Trajectory summary is outside the selected evaluation')
    return {'evaluation_id':evaluation_id, 'items':[
        {'trajectory_id':r['id'], **problem_summary(repo.evaluation_record(r))} for r in records]}


def get(handler, path, q) -> bool:
    if not path.startswith('/api/inspection/'): return False
    repo=handler.server.inspections
    if path=='/api/inspection/summaries':
        try: ids=json.loads(q.get('ids','[]'))
        except (ValueError,TypeError) as exc: raise ValidationError('ids must be a JSON array') from exc
        handler._json(summaries(repo,handler.server.store,q['evaluation'],ids))
    elif path=='/api/inspection/files': handler._json({'files':repo.files()})
    elif path=='/api/inspection/records':
        handler._json(repo.list_records(q['file'],q.get('q',''),q.get('outcome',''),q.get('rule',''),int(q.get('offset','0')),int(q.get('limit','50'))))
    elif path=='/api/inspection/evaluation-position':
        store=handler.server.store
        trace=store.trajectory(q['trajectory'])
        if trace['evaluation_id']!=q['evaluation']:
            raise ValidationError('Trajectory does not belong to the selected evaluation')
        dataset=q.get('dataset') or trace['dataset']
        if dataset!=trace['dataset']:
            raise ValidationError('Selected trajectory is outside the dataset filter')
        outcome=q.get('outcome','')
        if outcome and outcome!=trace['outcome']:
            raise ValidationError('Selected trajectory is outside the outcome filter')
        with store.lock:
            sql='SELECT count(*) FROM trajectories WHERE evaluation_id=? AND dataset=? AND sample_id<?'
            args=[q['evaluation'],dataset,trace['sample_id']]
            if outcome: sql+=' AND outcome=?';args.append(outcome)
            position=store.conn.execute(sql,args).fetchone()[0]
        handler._json({'dataset':dataset,'offset':(position//50)*50,'position':position})
    elif path=='/api/inspection/record': handler._json(document(repo,handler.server.store,q,handler.server.methods))
    elif path=='/api/inspection/search': handler._json(repo.search_record(document(repo,handler.server.store,q,handler.server.methods),q.get('q','')))
    elif path=='/api/inspection/export': handler._json(repo.report(document(repo,handler.server.store,q,handler.server.methods)),filename='trace-diagnostic-evidence.json')
    elif path=='/api/inspection/original':
        f=repo.file(q['file']);handler._send(repo.original(q['file']),'application/octet-stream',filename='original-'+f['id']+'.'+f['format'])
    elif path=='/api/inspection/rules':
        handler._json({'version':RULESET_VERSION,'rules':[{'id':k,'title':v[0],'level':v[1],'basis':v[2]} for k,v in RULES.items()]})
    else: handler._json({'error':'Not found'},404)
    return True


def post(handler, path, data) -> bool:
    if path!='/api/inspection/reviews': return False
    repo=handler.server.inspections
    doc=document(repo,handler.server.store,data,handler.server.methods)
    handler._json(repo.append_review(doc,data['review']),201)
    return True
