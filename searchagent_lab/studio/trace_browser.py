"""Evaluation-first, read-only browsing of results and independent diagnostics."""
from __future__ import annotations
import json
from ..core import ValidationError, digest
from ..read_cache import request_scope
from .workspace import _from_metrics


def describe(ev):
    panel = ev.get('panel') or {}
    scope = ev.get('scope') or ev.get('protocol', {}).get('evaluation_scope')
    if scope not in ('full', 'diagnostic'):
        scope = 'full' if panel.get('split') == 'test' else 'diagnostic'
    return {**ev, 'record_type': 'evaluation', 'scope': scope,
            'split': panel.get('split') or '未记录',
            'dataset_names': list((panel.get('datasets') or {}).keys()),
            'question_count': ev['stats']['expected']}


def aggregates(repository, run):
    exp = repository.store._get('experiments', run['experiment_id'])
    with repository.store.lock:
        saved = repository.store.conn.execute(
            'SELECT step,data FROM metrics WHERE run_id=? ORDER BY step DESC', (run['id'],)).fetchall()
    values = []
    for step, raw in saved:
        metrics = {k: v for k, v in json.loads(raw).items() if k.startswith('eval/')}
        if metrics:
            aggregate = _from_metrics(run, exp, (step, metrics))
            if aggregate.get('dataset_count'): values.append(aggregate)
    if not values:
        aggregate = repository.reference(run_id=run['id']).get('current') or {}
        if aggregate.get('dataset_count'): values.append(aggregate)
    return [{'id': f"aggregate:{run['id']}:{v.get('step', 'logged')}", 'record_type': 'aggregate',
             'scope': 'full', 'name': v['label'], 'run': run, 'aggregate': v,
             'checkpoint': {'name': f"step {v.get('step', '未记录')}", 'step': v.get('step')},
             'split': (run.get('config') or {}).get('evaluation_split') or '未记录',
             'dataset_names': [d['label'] for d in v['datasets']], 'question_count': v.get('sample_count')}
            for v in values]


def group(row, comparing):
    a, b = row.get('a'), row.get('b')
    if not comparing:
        return 'missing' if not b or b.get('scores', {}).get('em') is None else 'scored'
    if not a or not b or any(x.get('scores', {}).get('em') is None for x in (a, b)):
        return 'missing'
    x, y = a['scores']['em'], b['scores']['em']
    return 'harmed' if x == 1 and y == 0 else 'rescued' if x == 0 and y == 1 else 'both_correct' if y == 1 else 'both_wrong'


def paired_report(repository, b, a, axis):
    report = repository.report(b['id'])
    if not a: return report
    other = repository.report(a['id'])
    left = {(r['sample']['dataset'], r['sample']['id']): r for r in other['items']}
    same_split = a.get('panel', {}).get('split') == b.get('panel', {}).get('split')
    consumed = set()
    for row in report['items']:
        key = row['sample']['dataset'], row['sample']['id']
        candidate = left.get(key)
        if candidate and same_split and candidate['sample']['question'] == row['sample']['question']:
            row['a'] = candidate['b']; consumed.add(key)
        elif candidate:
            row['pairing_note'] = '题目正文或数据划分不同，未配对'
    for key, row in left.items():
        if key not in consumed:
            report['items'].append({'sample': row['sample'], 'a': row['b'], 'b': None,
                                    'source_side': 'a',
                                    'pairing_note': '仅对照评测包含此题，或题目正文 / 数据划分不同'})
    if a.get('panel_hash') == b.get('panel_hash') and a.get('panel_id') == b.get('panel_id'):
        pair = repository.evaluation_pair(a['id'], b['id'], axis)
        pair = {k: v for k, v in pair.items() if k != 'items'}
    else:
        pair = {'compatible': False, 'axis': axis, 'reasons': ['评测范围不同，仅浏览同题差异，不能归因于训练提升'],
                'macro_delta': None, 'groups': {}}
    report.update(a=a, pair=pair)
    return report


@request_scope
def trace_browser(repository, run_id: str, panel_id: str = '', b_id: str = '',
                  a_id: str = '', axis: str = 'checkpoint', scope: str = '',
                  offset: int = 0, limit: int = 30, query: str = '', outcome: str = 'all',
                  sample_id: str = '', dataset: str = '', row_key: str = '', dataset_filter: str = '') -> dict:
    if axis not in ('checkpoint', 'harness', 'method_state'):
        raise ValidationError('Unknown comparison axis')
    if scope not in ('', 'all', 'full', 'diagnostic') or offset < 0 or not 1 <= limit <= 100:
        raise ValidationError('Invalid trace browser scope or page')
    filters = ('all', 'incorrect', 'error', 'missing', 'rescued', 'harmed', 'both_correct', 'both_wrong')
    if outcome not in filters: raise ValidationError('Unknown result filter')
    run = repository.store.run(run_id)
    with repository.store.lock:
        ids = [r[0] for r in repository.store.conn.execute(
            '''SELECT e.id FROM evaluations e JOIN checkpoints c ON c.id=e.checkpoint_id
               WHERE c.run_id=? ORDER BY e.created_at DESC,e.rowid DESC''', (run_id,))]
    evaluations = [describe(repository.store.evaluation(eid)) for eid in ids]
    linked_steps = {e.get('source_aggregate_step') for e in evaluations if e.get('source_aggregate_step') is not None}
    summaries = [e for e in aggregates(repository, run) if e['checkpoint'].get('step') not in linked_steps]
    catalog = evaluations + summaries
    selected = next((e for e in catalog if e['id'] == b_id), None) if b_id else None
    if b_id and selected is None: raise ValidationError('Selected evaluation does not belong to this run')
    if not selected:
        choices = [e for e in evaluations if e.get('panel_id') == panel_id] if panel_id else catalog
        if scope in ('full', 'diagnostic'): choices = [e for e in choices if e['scope'] == scope]
        if not panel_id and scope != 'diagnostic': choices = [e for e in choices if e['scope'] == 'full'] or choices
        selected = next(iter(choices), None)
    result = {'run': run, 'panel': None, 'evaluations': catalog, 'selected_evaluation': selected,
              'report': None, 'candidates': [], 'selected_a': '', 'browser_version': 2,
              'selection_rule': 'explicit' if b_id else 'full_evaluation_then_latest_record',
              'availability': 'not_connected', 'message': '尚未接入该次评测结果',
              'page': {'offset': 0, 'limit': limit, 'total': 0, 'filtered': 0, 'counts': {}},
              'notice': '固定诊断题与完整评测分别查看；已有结果用于调参后，不再属于未参与开发的最终验证。'}
    if not selected or selected['record_type'] == 'aggregate':
        if a_id: raise ValidationError('逐题结果尚未接入，无法进行同题对比')
        if selected:
            result['message'] = '已接入本次评测的汇总成绩，尚未接入逐题答案、评分和步骤日志。不能用固定诊断题替代。'
        return result
    b = selected
    if a_id == b['id']: raise ValidationError('Choose two distinct evaluations')
    with repository.store.lock:
        all_ids = [r[0] for r in repository.store.conn.execute(
            'SELECT id FROM evaluations WHERE id<>? ORDER BY created_at DESC,rowid DESC', (b['id'],))]
    candidates = []
    for eid in all_ids:
        ev = describe(repository.store.evaluation(eid))
        candidates.append({'evaluation': ev, 'same_run': ev['run']['id'] == run_id,
                           'same_project': ev['project_id'] == b['project_id']})
    candidates.sort(key=lambda row: (not row['same_run'], not row['same_project']))
    a = next((c['evaluation'] for c in candidates if c['evaluation']['id'] == a_id), None)
    if a_id and not a: raise ValidationError('Comparison evaluation does not exist')
    report = paired_report(repository, b, a, axis)
    all_rows = report['items']
    for row in all_rows:
        row['group'] = group(row, bool(a))
        row['key'] = digest([row['sample']['dataset'], row['sample']['id'], row['sample']['question'], row.get('source_side','b')])[:24]
    def match(row, key):
        if key == 'all': return True
        if key == 'incorrect': return (row.get('b') or {}).get('scores', {}).get('em') == 0
        if key == 'error': return (row.get('b') or {}).get('runtime_status', 'unknown') not in ('ok', 'unknown', None)
        return row['group'] == key
    counts = {key: sum(match(row, key) for row in all_rows) for key in filters}
    term = query.strip().casefold()
    rows = [row for row in all_rows if match(row, outcome) and (not dataset_filter or row['sample']['dataset']==dataset_filter) and (not term or term in ' '.join(
        str(x or '') for x in (row['sample']['id'], row['sample']['dataset'], row['sample']['question'],
                              (row.get('a') or {}).get('parsed_answer'), (row.get('b') or {}).get('parsed_answer'))).casefold())]
    if row_key or sample_id:
        target = next((i for i, row in enumerate(rows) if row['key'] == row_key or (not row_key and row['sample']['id'] == sample_id and row['sample']['dataset'] == dataset)), None)
        if target is not None: offset = target // limit * limit
    offset = min(offset, max(0, (len(rows) - 1) // limit * limit))
    report['items'] = rows[offset:offset + limit]
    # Paginate summaries; do not send a whole test set or raw steps in the list response.
    panel = {k: v for k, v in repository.store.panel(b['panel_id']).items() if k != 'samples'}
    saved = [row['b'] for row in all_rows if row.get('b')]
    has_steps = any(row.get('step_count', 0) for row in saved)
    result.update(panel=panel, report=report, candidates=candidates, selected_a=a_id,
                  availability='steps_available' if has_steps else 'results_only' if saved else 'not_connected',
                  message='已接入逐题结果和已保存的步骤日志' if has_steps else '可查看答案与评分，未保存逐步轨迹' if saved else '尚未接入该次评测的逐题结果',
                  page={'offset': offset, 'limit': limit, 'total': len(all_rows), 'filtered': len(rows), 'counts': counts})
    return result
