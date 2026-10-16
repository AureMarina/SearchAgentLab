"""Small workspace index and explicitly qualified aggregate references.

The mutable presentation registry is independent of frozen research ownership.
Dataset aliases align table columns only; they never certify a controlled result.
This module reads saved aggregates and scalar diagnostic counts, never traces,
method bodies, or full metric histories. No inference or score recomputation runs.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from importlib.resources import files

from ..core import ValidationError, NotFoundError, ident, dumps, now
from ..read_cache import cached_read, request_scope


FAMILIES = (
    ('baseline', '原生 Qwen 基线'),
    ('self_distillation', 'Self-distillation'),
    ('skill', 'Skill'),
    ('self_play', 'Self-play'),
    ('latent', 'Latent'),
    ('entropy', 'Entropy'),
    ('other', '待归类'),
)
FAMILY_LABELS = dict(FAMILIES)
TRACKS = ('main', 'reproduction', 'unassigned')

def _load_audited_defaults():
    """Load a bounded migration asset; the engine contains no workspace IDs."""
    registry = json.loads(files(__package__).joinpath('workspace_defaults.json').read_text(encoding='utf-8'))
    if registry.get('schema') != 'searchagent_lab.workspace_defaults' or registry.get('version') != 1:
        raise ValueError('Unsupported workspace defaults registry')
    defaults = {}
    for entry in registry['entries']:
        key = (ident(entry['project_id']), ident(entry['experiment_id']))
        family, baseline = entry['family'], entry['is_baseline']
        if key in defaults or family not in FAMILY_LABELS or not isinstance(baseline, bool) or (family == 'baseline') != baseline:
            raise ValueError('Invalid or duplicate workspace defaults entry')
        defaults[key] = (family, baseline)
    return defaults, registry['registry_id']


AUDITED_DEFAULTS, AUDITED_DEFAULTS_SOURCE = _load_audited_defaults()
DATASETS = (
    ('nq', 'NQ'), ('triviaqa', 'TriviaQA'), ('popqa', 'PopQA'),
    ('hotpotqa', 'HotpotQA'), ('2wiki', '2WikiMQA'),
    ('musique', 'MuSiQue'), ('bamboogle', 'Bamboogle'),
)
DATASET_LABELS = dict(DATASETS)
DATASET_ALIASES = {'2wikimultihopqa': '2wiki', '2wikimqa': '2wiki'}
SCORES = ('em_pct', 'f1_pct', 'accuracy_pct', 'logged_success_pct', 'logged_score_pct')
AGGREGATE_FIELDS = SCORES + ('sample_count', 'expected', 'em_correct')


def _dataset(name):
    key = str(name).casefold()
    return DATASET_ALIASES.get(key, key)


def _finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _count(value):
    value = _finite(value)
    return int(value) if value is not None and value >= 0 and int(value) == value else None


def _pct(value):
    value = _finite(value)
    return value * 100 if value is not None else None


def _model(run):
    # A namespace prefix is representational. Instruct, 2507, 4B, and 8B are
    # scientifically meaningful and are deliberately never collapsed.
    return str(run.get('base_model') or '').removeprefix('Qwen/').strip().casefold()


def _thin_run(run):
    return {k: run.get(k) for k in ('id', 'name', 'base_model', 'model_revision',
            'algorithm', 'status', 'created_at', 'experiment_id', 'project_id',
            'latest_step', 'seed', 'demo', 'team_origin')}


def _thin_experiment(exp):
    return {k: exp.get(k) for k in ('id', 'name', 'project_id', 'objective', 'demo')}


def _sort_datasets(rows):
    order = {key: i for i, (key, _) in enumerate(DATASETS)}
    return sorted(rows, key=lambda d: (order.get(d['id'], len(order)), d['id']))


def _empty_aggregate(run_id):
    return {'kind': 'missing', 'label': '尚无完整评测汇总', 'run_id': run_id,
            'step': None, 'dataset_count': 0, 'sample_count': None,
            'macro_em_pct': None, 'macro_f1_pct': None, 'macro_accuracy_pct': None,
            'macro_logged_success_pct': None, 'logged_overall_batch_success_pct': None,
            'datasets': [], 'note': '未导入完整评测汇总；固定题轨迹单独统计。',
            'source': None, 'evidence': {}}


def _score_row(name):
    key = _dataset(name)
    return {'id': key, 'label': DATASET_LABELS.get(key, str(name)),
            'source_dataset': name, 'sample_count': None,
            **{k: None for k in SCORES}, 'metric_semantics': {}}


def _comparison_evidence(run, exp):
    # Optional, explicit aggregate-level evidence. Diagnostic evaluation hashes
    # must not be reused to authenticate the unrelated full test aggregates.
    config = run.get('config') or {}
    evidence = config.get('aggregate_comparison')
    origin = 'run.config.aggregate_comparison'
    if not isinstance(evidence, dict):
        evidence = (exp.get('config') or {}).get('aggregate_comparison')
        origin = 'experiment.config.aggregate_comparison'
    if not isinstance(evidence, dict):
        return {}
    keys = ('dataset_manifest_sha256', 'protocol_sha256', 'scorer_sha256', 'base_model_revision')
    return {**{k: evidence.get(k) for k in keys}, 'source': origin}


def _from_metrics(run, exp, saved):
    if saved is None:
        return _empty_aggregate(run['id'])
    step, metrics = saved
    result = _empty_aggregate(run['id'])
    result.update(kind='question_aggregate', label='完整评测汇总', step=step,
                  source={'kind': 'saved_metrics', 'run_id': run['id'], 'step': step},
                  evidence=_comparison_evidence(run, exp),
                  note='保存的数据集汇总；完整评测题数与下方已导入诊断轨迹分开统计。')
    definitions = exp.get('metric_definitions') or {}
    rows = {}
    for key, value in metrics.items():
        parts = key.split('/')
        if len(parts) != 3 or parts[0] != 'eval':
            continue
        _, source_dataset, metric = parts
        if source_dataset in ('macro', 'weighted', 'total'):
            if source_dataset == 'macro' and metric in SCORES:
                result['macro_' + metric] = _finite(value)
            continue
        if metric not in AGGREGATE_FIELDS:
            continue
        dataset = _dataset(source_dataset)
        row = rows.setdefault(dataset, _score_row(source_dataset))
        if metric in ('sample_count', 'expected'):
            row['sample_count'] = _count(value)
        elif metric == 'em_correct':
            row['em_correct'] = _count(value)
        else:
            row[metric] = _finite(value)
            definition = definitions.get(key) or {}
            row['metric_semantics'][metric] = definition.get('definition') or {
                'em_pct': '保存的逐题 EM 汇总；以源评分定义为准',
                'f1_pct': '保存的逐题 F1 汇总；以源评分定义为准',
                'accuracy_pct': '源定义 Accuracy；不自动等同于 EM',
                'logged_success_pct': '日志成功率；不自动等同于逐题 EM',
                'logged_score_pct': '日志得分；不自动等同于逐题 EM',
            }[metric]
    result['datasets'] = _sort_datasets(rows.values())
    result['dataset_count'] = len(rows)
    if not any(key.endswith(('/em_pct', '/f1_pct', '/accuracy_pct')) for key in metrics):
        result.update(kind='training_log_batch_mean', label='训练日志 · 批均值',
                      note='源记录仅包含日志聚合得分，不自动作为精确逐题 EM。')
    total = _count(metrics.get('eval/total/sample_count'))
    if total is not None:
        result['sample_count'] = total
    elif rows and all(row['sample_count'] is not None for row in rows.values()):
        result['sample_count'] = sum(row['sample_count'] for row in rows.values())
    # A missing macro value stays missing. No implicit averaging of a partial
    # dataset set, and no Accuracy-to-EM substitution.
    return result


def _from_validation_log(run, exp, saved):
    evaluation_id, full = saved
    if full.get('kind') != 'aggregate_from_training_validation_log':
        return None
    result = _empty_aggregate(run['id'])
    result.update(kind='training_log_batch_mean', label='训练日志 · 批均值',
                  step=full.get('step'), sample_count=_count(full.get('question_count')),
                  macro_logged_success_pct=_pct(full.get('dataset_macro_mean_em')),
                  logged_overall_batch_success_pct=_pct(full.get('overall_logged_batch_mean_em')),
                  source={'kind': 'evaluation_full_validation', 'evaluation_id': evaluation_id,
                          'source_sha256': full.get('source_sha256', {})},
                  note='训练器记录的验证批成功率均值；末批大小不一，不能作为精确逐题 EM。完整评测汇总与固定诊断题独立。')
    # Keep the imported trainer's "em" values under their actual aggregation
    # semantics rather than promoting the field name into exact-match evidence.
    for dataset in full.get('datasets', []):
        row = _score_row(dataset.get('name', 'unknown'))
        row.update(sample_count=_count(dataset.get('question_count')),
                   logged_success_pct=_pct(dataset.get('em')),
                   logged_score_pct=_pct(dataset.get('mean_reward')),
                   metric_semantics={'logged_success_pct': '验证批成功率的均值，非精确逐题 EM',
                                     'logged_score_pct': '训练器保存的测试平均奖励'})
        result['datasets'].append(row)
    result['datasets'] = _sort_datasets(result['datasets'])
    result['dataset_count'] = len(result['datasets'])
    result['aggregation_note'] = full.get('aggregation_note', '')
    return result


class WorkspaceIndex:
    def __init__(self, repository):
        self.repository = repository
        self.store = repository.store

    def presentation(self, exp, registered=None):
        if registered is not None:
            return registered
        audited = AUDITED_DEFAULTS.get((exp['project_id'], exp['id']))
        family, baseline = audited if audited else ('other', False)
        return {'experiment_id': exp['id'], 'family': family, 'is_baseline': baseline,
                'source': AUDITED_DEFAULTS_SOURCE if audited else 'unclassified'}

    def register_presentation(self, data):
        if not isinstance(data, dict):
            raise ValidationError('presentation must be an object')
        experiment_id = ident(data.get('experiment_id'), 'presentation.experiment_id')
        exp = self.store._get('experiments', experiment_id)
        with self.repository.lock:
            record = self.repository.conn.execute(
                'SELECT data FROM workspace_presentations WHERE experiment_id=?', (experiment_id,)).fetchone()
        current = self.presentation(exp, json.loads(record[0]) if record else None)
        family = data.get('family', current['family'])
        baseline = data.get('is_baseline', current['is_baseline'])
        if family not in FAMILY_LABELS:
            raise ValidationError('Unknown research family')
        if not isinstance(baseline, bool):
            raise ValidationError('is_baseline must be boolean')
        if (family == 'baseline') != baseline:
            raise ValidationError('baseline 方向和 is_baseline 标记必须一致')
        result = {'experiment_id': experiment_id, 'family': family,
                  'is_baseline': baseline, 'source': 'explicit', 'updated_at': now()}
        # This mutable, replaceable view setting does not edit a frozen profile,
        # run ownership, parent link, evaluation, or the main-iteration guards.
        with self.repository.lock, self.repository.conn:
            self.repository.conn.execute('''INSERT INTO workspace_presentations VALUES (?,?)
                ON CONFLICT(experiment_id) DO UPDATE SET data=excluded.data''',
                (experiment_id, dumps(result)))
        return result

    @cached_read
    def _snapshot(self):
        """Bounded SQL projections: no trajectory bodies or metric histories."""
        with self.store.lock:
            experiments = [json.loads(row[0]) for row in self.store.conn.execute(
                'SELECT data FROM experiments ORDER BY rowid')]
            runs = []
            for row in self.store.conn.execute('''SELECT r.data,e.id AS experiment_id,e.project_id,
                (SELECT max(step) FROM metrics WHERE run_id=r.id) AS latest_step
                FROM runs r JOIN run_ownership o ON o.run_id=r.id
                JOIN experiments e ON e.id=o.experiment_id ORDER BY r.rowid'''):
                run = json.loads(row['data'])
                run.update(experiment_id=row['experiment_id'], project_id=row['project_id'], latest_step=row['latest_step'])
                runs.append(run)
            # First identify each run's latest saved aggregate, then transfer
            # only recognised scalar aggregate fields out of SQLite. Training
            # series and raw JSON blobs never enter the workspace response.
            suffixes = ' OR '.join("j.key GLOB 'eval/*/" + field + "'" for field in AGGREGATE_FIELDS)
            score_suffixes = ' OR '.join("j.key GLOB 'eval/*/" + field + "'" for field in SCORES)
            metrics = {}
            for row in self.store.conn.execute('''WITH latest AS (
                SELECT m.run_id,max(m.step) AS step FROM metrics m
                WHERE EXISTS (SELECT 1 FROM json_each(m.data) j WHERE ''' + score_suffixes + ''')
                GROUP BY m.run_id)
                SELECT m.run_id,m.step,j.key,j.value
                FROM latest l JOIN metrics m ON m.run_id=l.run_id AND m.step=l.step
                JOIN json_each(m.data) j WHERE ''' + suffixes):
                metrics.setdefault(row['run_id'], (row['step'], {}))[1][row['key']] = row['value']
            diagnostic_rows = self.store.conn.execute('''WITH counts AS (
                SELECT evaluation_id,count(*) AS trajectory_count,count(em) AS scored_count,
                    sum(em=1) AS answer_correct,sum(em) AS em_sum
                FROM trajectories GROUP BY evaluation_id)
                SELECT e.id,e.checkpoint_id,e.panel_id,e.status,e.created_at,c.run_id,c.step,
                    json_extract(e.data,'$.name') AS name,
                    json_extract(e.data,'$.note') AS note,
                    json_extract(p.data,'$.name') AS panel_name,
                    json_extract(p.data,'$.size') AS panel_size,
                    coalesce(t.trajectory_count,0) AS trajectory_count,
                    coalesce(t.scored_count,0) AS scored_count,
                    coalesce(t.answer_correct,0) AS answer_correct,t.em_sum
                FROM evaluations e JOIN checkpoints c ON c.id=e.checkpoint_id
                JOIN panels p ON p.id=e.panel_id LEFT JOIN counts t ON t.evaluation_id=e.id
                ORDER BY e.created_at DESC,e.rowid DESC''').fetchall()
            logs = {}
            for row in self.store.conn.execute('''SELECT e.id,c.run_id,json_extract(e.data,'$.full_validation') AS data
                FROM evaluations e JOIN checkpoints c ON c.id=e.checkpoint_id
                WHERE json_type(e.data,'$.full_validation')='object'
                ORDER BY e.created_at DESC,e.rowid DESC'''):
                logs.setdefault(row['run_id'], (row['id'], json.loads(row['data'])))
        with self.repository.lock:
            profiles = {row[0]: json.loads(row[1]) for row in self.repository.conn.execute('SELECT experiment_id,data FROM profiles')}
            presentations = {row[0]: json.loads(row[1]) for row in self.repository.conn.execute('SELECT experiment_id,data FROM workspace_presentations')}
        diagnostics = defaultdict(list)
        for source in diagnostic_rows:
            row = dict(source)
            expected = row['panel_size']
            em_sum = row.pop('em_sum')
            row['em_pct'] = em_sum / expected * 100 if expected and row['scored_count'] == expected else None
            diagnostics[row['run_id']].append(row)
        exps = {e['id']: e for e in experiments}
        aggregates = {}
        for run in runs:
            exp = exps[run['experiment_id']]
            aggregate = _from_metrics(run, exp, metrics.get(run['id']))
            if run['id'] in logs:
                logged = _from_validation_log(run, exp, logs[run['id']])
                if logged and (aggregate['kind'] == 'missing' or (logged['step'] or 0) > (aggregate['step'] or 0)):
                    aggregate = logged
            aggregates[run['id']] = aggregate
        return {'experiments': experiments, 'runs': runs, 'profiles': profiles,
                'presentations': presentations, 'diagnostics': dict(diagnostics), 'aggregates': aggregates}

    @staticmethod
    def _diagnostic_summary(evaluations):
        return {'evaluation_count': len(evaluations),
                'trajectory_count': sum(e['trajectory_count'] for e in evaluations),
                'latest': next(iter(evaluations), None)}

    def _baseline_candidates(self, snapshot):
        experiments = {exp['id']: exp for exp in snapshot['experiments']}
        baseline_ids = {exp['id'] for exp in snapshot['experiments'] if self.presentation(
            exp, snapshot['presentations'].get(exp['id']))['is_baseline']}
        candidates = []
        for run in snapshot['runs']:
            if run['experiment_id'] in baseline_ids:
                candidates.append({'experiment': _thin_experiment(experiments[run['experiment_id']]),
                                   'run': _thin_run(run), 'aggregate': snapshot['aggregates'][run['id']]})
        return sorted(candidates, key=lambda x: (x['run'].get('created_at') or '', x['run']['id']), reverse=True)

    @staticmethod
    def _choose_reference(run, candidates, selected_run=''):
        if selected_run:
            reference = next((x for x in candidates if x['run']['id'] == selected_run), None)
            if reference is None:
                raise ValidationError('Selected baseline run is not registered as a workspace baseline')
            return reference if _model(run) and _model(run) == _model(reference['run']) else None, reference
        matching = [x for x in candidates if _model(run) and _model(run) == _model(x['run'])]
        # Select a recorded result before an empty run, by recency, never score.
        matched = next((x for x in matching if x['aggregate']['kind'] != 'missing'), next(iter(matching), None))
        reference = matched or next((x for x in candidates if x['aggregate']['kind'] != 'missing'), next(iter(candidates), None))
        return matched, reference

    @staticmethod
    def _comparison(run, current, baseline, reference):
        result = {'status': 'reference_only', 'compatible': False, 'reasons': [],
                  'included_in_main_iteration': False, 'delta_kind': 'aggregate_reference'}
        reasons = result['reasons']
        if baseline and baseline['run']['id'] == run['id']:
            result['status'] = 'baseline'
            reasons.append('当前运行是已登记的原生基线。')
            return result
        if baseline is None:
            result['status'] = 'missing_baseline'
            reasons.append('尚未导入与当前底座完全匹配的原生基线。')
            if reference:
                reasons.append('现有 ' + (reference['run'].get('base_model') or '未注明底座') + ' 仅作参考读数，不计算提升。')
            return result
        other = baseline['aggregate']
        if current['kind'] == 'missing' or other['kind'] == 'missing':
            result['status'] = 'missing_results'
            reasons.append('当前运行或所选原生基线尚未导入完整评测汇总。')
            return result
        if current['kind'] != 'question_aggregate' or other['kind'] != 'question_aggregate':
            reasons.append('训练日志批均值与逐题评测的聚合口径不同，不能计算 EM 增量。')
        a, b = current.get('evidence', {}), other.get('evidence', {})
        for key, label in (('dataset_manifest_sha256', '完整评测题单'), ('protocol_sha256', '完整评测协议'),
                           ('scorer_sha256', '完整评测评分器'), ('base_model_revision', '底座版本')):
            x, y = a.get(key), b.get(key)
            valid = bool(isinstance(x, str) and isinstance(y, str) and x and y and 'unknown' not in x.casefold() and 'unknown' not in y.casefold())
            if key.endswith('sha256'):
                valid = valid and bool(re.fullmatch('[a-fA-F0-9]{64}', x) and re.fullmatch('[a-fA-F0-9]{64}', y))
            if not valid:
                reasons.append(label + '的可比性证据未完整登记。')
            elif x != y:
                reasons.append(label + '不一致。')
        left = {r['id']: r['sample_count'] for r in current['datasets']}
        right = {r['id']: r['sample_count'] for r in other['datasets']}
        if not left or left != right or any(n is None for n in left.values()):
            reasons.append('保存的数据集覆盖或题数不一致 / 不完整。')
        if not reasons:
            result.update(status='comparable', compatible=True)
        return result

    @request_scope
    def workspace(self):
        snapshot = self._snapshot()
        by_experiment = defaultdict(list)
        for run in snapshot['runs']:
            by_experiment[run['experiment_id']].append(run)
        baseline_candidates = self._baseline_candidates(snapshot)
        entries = []
        for exp in snapshot['experiments']:
            runs = sorted(by_experiment[exp['id']], key=lambda r: (r.get('created_at') or '', r['id']), reverse=True)
            latest = next(iter(runs), None)
            profile = snapshot['profiles'].get(exp['id']) or {
                'track': 'unassigned', 'kind': None, 'classification': 'not_registered',
                'question': exp.get('objective', '')}
            presentation = self.presentation(exp, snapshot['presentations'].get(exp['id']))
            evaluations = sorted([ev for run in runs for ev in snapshot['diagnostics'].get(run['id'], [])],
                                 key=lambda e: (e['created_at'], e['id']), reverse=True)
            aggregate = snapshot['aggregates'][latest['id']] if latest else _empty_aggregate(None)
            baseline, reference = self._choose_reference(latest or {}, baseline_candidates)
            comparison = self._comparison(latest or {}, aggregate, baseline, reference)
            entries.append({'experiment': _thin_experiment(exp),
                'profile': {k: profile.get(k) for k in ('track', 'kind', 'question', 'classification')},
                'family': presentation['family'], 'family_label': FAMILY_LABELS[presentation['family']],
                'presentation': presentation, 'runs': [_thin_run(r) for r in runs],
                'latest_run': _thin_run(latest) if latest else None,
                'latest_evaluation': next(iter(evaluations), None),
                'diagnostics': self._diagnostic_summary(evaluations), 'aggregate': aggregate,
                'reference': {'status': comparison['status'], 'reasons': comparison['reasons'],
                              'baseline_run_id': baseline['run']['id'] if baseline else None,
                              'reference_run_id': reference['run']['id'] if reference else None}})
        order = {family: i for i, (family, _) in enumerate(FAMILIES)}
        entries.sort(key=lambda e: (order[e['family']], TRACKS.index(e['profile']['track']), e['experiment']['name'] or ''))
        families, groups = [], []
        for family, label in FAMILIES:
            members = [e for e in entries if e['family'] == family]
            counts = {track: sum(e['profile']['track'] == track for e in members) for track in TRACKS}
            families.append({'id': family, 'label': label, 'count': len(members), 'counts': counts})
            groups.append({'family': family, 'label': label, 'experiment_ids': [e['experiment']['id'] for e in members], 'counts': counts})
        return {'families': families, 'groups': groups,
                'baseline_runs': baseline_candidates,
                'baselines': [e for e in entries if e['presentation']['is_baseline']],
                'experiments': entries, 'counts': {'experiments': len(entries), 'runs': len(snapshot['runs']),
                    'baselines': sum(e['presentation']['is_baseline'] for e in entries),
                    **{track: sum(e['profile']['track'] == track and not e['presentation']['is_baseline'] for e in entries) for track in TRACKS},
                    'trajectories': sum(e['diagnostics']['trajectory_count'] for e in entries)},
                'notice': '研究方向用于浏览；我的实验与文献复现保持各自归属。原生基线置顶，跨项目对照不进入科研迭代增量。'}

    @request_scope
    def reference(self, experiment_id='', run_id='', baseline_run=''):
        snapshot = self._snapshot()
        exps = {exp['id']: exp for exp in snapshot['experiments']}
        if experiment_id and experiment_id not in exps:
            raise NotFoundError(experiment_id)
        if run_id:
            run = next((r for r in snapshot['runs'] if r['id'] == run_id), None)
            if run is None:
                raise NotFoundError(run_id)
            if experiment_id and run['experiment_id'] != experiment_id:
                raise ValidationError('Selected run does not belong to this experiment')
            experiment_id = run['experiment_id']
        elif experiment_id:
            runs = sorted((r for r in snapshot['runs'] if r['experiment_id'] == experiment_id),
                          key=lambda r: (r.get('created_at') or '', r['id']), reverse=True)
            run = next(iter(runs), None)
        else:
            raise ValidationError('experiment or run is required')
        exp = exps[experiment_id]
        candidates = self._baseline_candidates(snapshot)
        current = snapshot['aggregates'][run['id']] if run else _empty_aggregate(None)
        baseline, reference = self._choose_reference(run or {}, candidates, baseline_run)
        comparison = self._comparison(run or {}, current, baseline, reference)
        current_rows = {row['id']: row for row in current['datasets']}
        reference_rows = {row['id']: row for row in reference['aggregate']['datasets']} if reference else {}
        rows = []
        for dataset in set(current_rows) | set(reference_rows):
            a, b = current_rows.get(dataset), reference_rows.get(dataset)
            row = {'id': dataset, 'label': DATASET_LABELS.get(dataset, (a or b)['label']),
                   'current': a, 'baseline': b if baseline else None, 'reference': b}
            for metric in ('em', 'f1', 'accuracy'):
                av, bv = (a or {}).get(metric + '_pct'), (b or {}).get(metric + '_pct')
                row['delta_' + metric + '_pp'] = av - bv if comparison['compatible'] and av is not None and bv is not None else None
            rows.append(row)
        return {'experiment': _thin_experiment(exp), 'run': _thin_run(run) if run else None,
                'current': current, 'baseline': baseline, 'available_reference': reference,
                'rows': _sort_datasets(rows), 'comparison': comparison,
                'diagnostics': self._diagnostic_summary(snapshot['diagnostics'].get(run['id'], []) if run else []),
                'baseline_candidates': [{'experiment_id': c['experiment']['id'], 'run_id': c['run']['id'],
                    'base_model': c['run']['base_model'], 'name': c['run']['name']} for c in candidates]}
