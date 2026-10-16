"""Controlled entropy diagnostics over saved, explicitly bound evidence.

Estimator discrimination, same-state policy measurements and end-to-end
intervention utility are separate results. No model or training is executed.
"""
from __future__ import annotations

from collections import Counter
import copy
import json
import math

from ..core import ValidationError
from ..inspection.repository import digest
from ..studio.evaluation import _fingerprint
from .opd_tracking import evidence_demo
from .schema import identifier
from .skill_comparison import _cell, _pair

TYPES = ('fixed_prefix', 'uncertainty', 'intervention')
ARMS = ('baseline', 'random', 'entropy')
MAX_COHORT = 10000


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _dict(value, name):
    if not isinstance(value, dict):raise ValidationError(name + ' must be an object')
    return value


def _array(value, name, maximum=MAX_COHORT):
    if not isinstance(value, list) or len(value)>maximum:
        raise ValidationError(name + ' must be an array of at most ' + str(maximum) + ' entries')
    return value


def _unique(values, name):
    if len(values)!=len(set(values)):raise ValidationError('Duplicate ' + name)


def validate_entropy_comparison_contract(data):
    kind = data.get('comparison_type')
    if kind not in TYPES:raise ValidationError('entropy_comparison.comparison_type is fixed_prefix, uncertainty or intervention')
    cohort = _dict(data.get('cohort', {}), 'cohort')
    if 'id' in cohort:identifier(cohort['id'], 'cohort.id')
    if cohort.get('usage') not in (None, 'development', 'held_out', 'test', 'unknown'):
        raise ValidationError('Unknown entropy cohort usage')
    for field in ('frozen', 'feedback_used'):
        if field in cohort and type(cohort[field]) is not bool:raise ValidationError('cohort.' + field + ' must be boolean')
    if kind=='fixed_prefix':
        pairs = _array(data.get('pairs', []), 'pairs', 200)
        keys = [];seen = set();observations = []
        for pair in pairs:
            _dict(pair, 'pair');keys.append(identifier(pair.get('id'), 'pair.id'))
            for key in ('before_observation_id', 'after_observation_id'):
                if pair.get(key) is not None:
                    identifier(pair[key], key);observations.append(pair[key])
            identity = (pair.get('before_observation_id'), pair.get('after_observation_id'))
            if any(identity) and identity in seen:raise ValidationError('Repeated fixed-prefix evidence pair')
            seen.add(identity)
        _unique(keys, 'pair.id')
        _unique(observations, 'fixed-prefix observation slot')
    elif kind=='uncertainty':
        identifier(data.get('evaluation_id'), 'evaluation_id')
        ids = _array(data.get('trajectory_ids'), 'trajectory_ids')
        for tid in ids:identifier(tid, 'trajectory_id')
        _unique(ids, 'trajectory_ids');wanted = set(ids)
        estimators = _array(data.get('estimators', []), 'estimators', 16);keys = []
        for estimator in estimators:
            _dict(estimator, 'estimator');keys.append(identifier(estimator.get('id'), 'estimator.id'))
            identifier(estimator.get('definition_id'), 'estimator.definition_id')
            if type(estimator.get('higher_is_uncertain')) is not bool:
                raise ValidationError('Explicit higher_is_uncertain boolean required')
            if 'probe_protocol' in estimator:
                protocol = _dict(estimator['probe_protocol'], 'probe_protocol')
                _dict(protocol.get('sampling_budget', {}), 'probe_protocol.sampling_budget')
                if protocol.get('sample_set_hash') is not None and _fingerprint(protocol['sample_set_hash']) is None:
                    raise ValidationError('probe_protocol.sample_set_hash must be a SHA256 fingerprint')
            scores = _array(estimator.get('scores', []), 'scores');seen = []
            for score in scores:
                _dict(score, 'score');tid = identifier(score.get('trajectory_id'), 'score.trajectory_id');seen.append(tid)
                if tid not in wanted:raise ValidationError('Score outside the explicit uncertainty cohort')
                if 'value' not in score or score['value'] is not None and not _number(score['value']):
                    raise ValidationError('Score value must be finite or null')
                if score.get('probability_error') is not None and (not _number(score['probability_error']) or not 0<=score['probability_error']<=1):
                    raise ValidationError('probability_error must be in [0, 1] or null')
            _unique(seen, 'estimator trajectory score')
            calibration = estimator.get('calibration')
            if calibration is not None:
                _dict(calibration, 'calibration')
                if calibration.get('fit_evaluation_id') is not None:identifier(calibration['fit_evaluation_id'], 'fit_evaluation_id')
                if 'frozen' in calibration and type(calibration['frozen']) is not bool:
                    raise ValidationError('calibration.frozen must be boolean')
        _unique(keys, 'estimator.id')
    else:
        if data.get('mode') not in ('training', 'runtime'):raise ValidationError('Intervention mode is training or runtime')
        expected_axis = 'checkpoint' if data['mode']=='training' else 'harness'
        if data.get('evaluation_axis', expected_axis)!=expected_axis:
            raise ValidationError('Training comparisons use checkpoint axis; runtime comparisons use harness axis')
        budget_keys = _array(data.get('budget_keys', []), 'budget_keys', 32)
        for key in budget_keys:identifier(key, 'budget key')
        _unique(budget_keys, 'budget key')
        arms = _array(data.get('arms', []), 'arms', 3);keys = [];eids = []
        for arm in arms:
            _dict(arm, 'arm')
            if arm.get('id') not in ARMS:raise ValidationError('Arm id is baseline, random or entropy')
            keys.append(arm['id'])
            for key in ('checkpoint_id', 'evaluation_id'):
                if arm.get(key) is not None:identifier(arm[key], key)
            if arm.get('evaluation_id'):
                if not arm.get('checkpoint_id'):raise ValidationError('Measured arm requires an explicit checkpoint')
                eids.append(arm['evaluation_id'])
            selection = _dict(arm.get('selection', {}), 'selection')
            if selection.get('policy') not in (None, 'none', 'random', 'entropy'):raise ValidationError('Unknown selection policy')
            if selection.get('selected_count') is not None and (type(selection['selected_count']) is not int or selection['selected_count']<0):
                raise ValidationError('selected_count must be a nonnegative integer')
            if selection.get('definition_id') is not None:identifier(selection['definition_id'], 'selection.definition_id')
            budget = _dict(arm.get('budget', {}), 'budget')
            for value in budget.values():
                if value is not None and (not _number(value) or value<0):raise ValidationError('Budget values must be finite nonnegative numbers or null')
            interventions = _array(arm.get('intervention_ids', []), 'intervention_ids', 1000)
            for oid in interventions:identifier(oid, 'intervention_id')
            _unique(interventions, 'intervention_id')
        _unique(keys, 'intervention arm');_unique(eids, 'intervention evaluation')


def entropy_comparison_refs(obj):
    data = obj['data']
    for pair in data.get('pairs', []):
        for key in ('before_observation_id', 'after_observation_id'):
            if pair.get(key):yield pair[key], {'entropy_observation'}, '/data/pairs/' + key
    for estimator in data.get('estimators', []):
        yield estimator['definition_id'], {'entropy_definition'}, '/data/estimators/definition_id'
    for arm in data.get('arms', []):
        if arm.get('selection', {}).get('definition_id'):
            yield arm['selection']['definition_id'], {'entropy_definition'}, '/data/arms/selection/definition_id'
        for oid in arm.get('intervention_ids', []):yield oid, {'entropy_intervention'}, '/data/arms/intervention_ids'


def entropy_comparison_evidence_refs(obj):
    for estimator in obj['data'].get('estimators', []):
        if estimator.get('probe_protocol_ref') is not None:yield estimator['probe_protocol_ref']
        for score in estimator.get('scores', []):
            if score.get('evidence_ref') is not None:yield score['evidence_ref']
        if estimator.get('calibration', {}).get('fit_ref') is not None:yield estimator['calibration']['fit_ref']
    for arm in obj['data'].get('arms', []):
        if arm.get('cost_ref') is not None:yield arm['cost_ref']


def _same_scope(repo, obj, target, *, definition=False):
    a, b = obj.get('scope', {}), target.get('scope', {})
    if evidence_demo(repo, obj)!=evidence_demo(repo, target):
        raise ValidationError('Synthetic and real entropy comparison evidence cannot be mixed')
    if definition and target.get('shareable') is True:return
    for key in ('project_id', 'experiment_id'):
        if a.get(key)!=b.get(key):raise ValidationError('Entropy comparison evidence belongs to another ' + key)
    if a.get('run_id') and b.get('run_id')!=a['run_id'] and not (definition and not b.get('run_id')):
        raise ValidationError('Entropy comparison evidence belongs to another run')


def _evaluation_scope(repo, obj, eid):
    ev = repo.store.evaluation(eid);scope = obj['scope']
    if ev['project_id']!=scope['project_id'] or ev['experiment_id']!=scope['experiment_id']:
        raise ValidationError('Entropy comparison evaluation belongs to another experiment')
    if scope.get('run_id') and ev['run']['id']!=scope['run_id']:
        raise ValidationError('Entropy comparison evaluation belongs to another run')
    demo = bool(ev.get('demo') or ev['run'].get('demo') or ev['panel'].get('demo'))
    if evidence_demo(repo, obj)!=demo:raise ValidationError('Synthetic and real entropy evaluations cannot be mixed')
    return ev


def validate_entropy_comparison_links(repo, obj, pending):
    data, scope = obj['data'], obj.get('scope', {})
    if repo.store is None or not scope.get('experiment_id'):
        raise ValidationError('Entropy comparison requires a registered experiment')
    if not scope.get('run_id') and not (data['comparison_type']=='intervention' and data['mode']=='training'):
        raise ValidationError('Only cross-run training interventions use experiment-only comparison scope')
    for oid, _, _ in entropy_comparison_refs(obj):
        target = repo._raw_object(oid, pending)
        _same_scope(repo, obj, target, definition=target['kind']=='entropy_definition')
    for ref in entropy_comparison_evidence_refs(obj):
        if ref.get('object_id'):_same_scope(repo, obj, repo._raw_object(ref['object_id'], pending))
        elif ref.get('trajectory'):
            t = repo.store.trajectory(ref['trajectory']);_evaluation_scope(repo, obj, t['evaluation_id'])
        repo.resolve_ref(ref, pending, obj)
    if data['comparison_type']=='uncertainty':
        _evaluation_scope(repo, obj, data['evaluation_id'])
        for tid in data['trajectory_ids']:
            if repo.store.trajectory(tid)['evaluation_id']!=data['evaluation_id']:
                raise ValidationError('Uncertainty trajectory belongs to another evaluation')
        for estimator in data['estimators']:
            eid = estimator.get('calibration', {}).get('fit_evaluation_id')
            if eid:_evaluation_scope(repo, obj, eid)
            for score in estimator.get('scores', []):
                ref = score.get('evidence_ref', {})
                if ref.get('trajectory') and ref['trajectory']!=score['trajectory_id']:
                    raise ValidationError('Uncertainty score reference points to another trajectory')
                if ref.get('object_id'):
                    target = repo._raw_object(ref['object_id'], pending)
                    if target['kind']=='entropy_probe':
                        d = target['data']
                        if d.get('trajectory_id')!=score['trajectory_id'] or d.get('evaluation_id')!=data['evaluation_id']:
                            raise ValidationError('Uncertainty probe is bound to another trajectory/evaluation')
                        if d.get('definition_id')!=estimator['definition_id']:
                            raise ValidationError('Uncertainty probe uses another estimator definition')
    elif data['comparison_type']=='intervention':
        for arm in data.get('arms', []):
            cp = repo.store._get('checkpoints', arm['checkpoint_id']) if arm.get('checkpoint_id') else None
            if cp:
                run = repo.store._get('runs', cp['run_id'])
                if run['experiment_id']!=scope['experiment_id'] or scope.get('run_id') and scope['run_id']!=run['id']:
                    raise ValidationError('Entropy intervention checkpoint belongs to another experiment/run')
            if arm.get('evaluation_id'):
                ev = _evaluation_scope(repo, obj, arm['evaluation_id'])
                if ev['checkpoint']['id']!=arm.get('checkpoint_id'):
                    raise ValidationError('Entropy intervention evaluation uses another checkpoint')
            for oid in arm.get('intervention_ids', []):
                target = repo._raw_object(oid, pending)
                if cp and target.get('scope', {}).get('run_id')!=cp['run_id']:
                    raise ValidationError('Entropy intervention record belongs to another arm run')
                if target['data']['mode']!=data['mode']:raise ValidationError('Training and runtime interventions cannot be mixed')
            ref = arm.get('cost_ref', {})
            if cp and ref.get('object_id'):
                target = repo._raw_object(ref['object_id'], pending)
                if target.get('scope', {}).get('run_id')!=cp['run_id']:
                    raise ValidationError('Entropy cost evidence belongs to another arm run')


def entropy_comparison_summary(obj):
    data = obj['data']
    return {'comparison_type':data['comparison_type'], 'mode':data.get('mode'),
            'label':data.get('label', obj.get('title')), 'cohort':copy.deepcopy(data.get('cohort', {})),
            'pair_count':len(data.get('pairs', [])), 'estimator_count':len(data.get('estimators', [])),
            'arm_count':len(data.get('arms', [])), 'evaluation_id':data.get('evaluation_id'),
            'status':'registered', 'computed':False, 'body_loaded':False,
            'note':'只登记显式对照；打开时核对原始证据与条件，列表不预读 token 或分数向量。'}


def _support_missing(data):
    measurement = data.get('measurement_support', {})
    coverage = data.get('coverage', {})
    return (measurement.get('available') is False or measurement.get('count')==0
            or coverage.get('support_count')==0)


def _observation(repo, obj, oid):
    result = {'observation_id':oid, 'value':None, 'reported_value':None, 'status':'unmeasured', 'reasons':[]}
    if not oid:
        result['reasons'].append('未指定观测记录');return result
    raw = repo.object(oid);data = raw['data'];reasons = result['reasons']
    from .entropy_tracking import definition_semantics
    definition = repo.object(data['definition_id']);semantic = definition_semantics(definition)
    result.update(checkpoint_id=data.get('checkpoint_id'), definition_id=data['definition_id'],
        reported_value=data.get('value'), value=data.get('value'), definition_complete=semantic['complete'],
        compatibility_key=semantic['compatibility_key'], unit=semantic['unit'], context=data.get('context'))
    if not _number(data.get('value')):
        reasons.append(data.get('missing_reason') or '未保存有限的观测值')
    if _support_missing(data):reasons.append('该记录明确没有有效测量支持')
    if data.get('context')!='fixed_prefix_probe':reasons.append('不是固定前缀观测；训练访问状态均值不可配对')
    if not semantic['complete']:reasons.append('熵定义口径不完整或冲突：'+', '.join(semantic['missing_fields']+semantic['conflicts']))
    prefix = data.get('frozen_prefix', {})
    frozen_value = None
    if not prefix.get('id') or not prefix.get('input_ref') or not _fingerprint(prefix.get('sha256')):
        reasons.append('缺少可核验的冻结输入引用及指纹')
    else:
        frozen_value = repo.resolve_ref(prefix['input_ref'], owner=raw)['value']
        if digest(frozen_value)!=prefix['sha256'] or data.get('prefix_hash')!=prefix['sha256']:
            reasons.append('冻结前缀引用内容与记录哈希不一致')
    expected = semantic['semantics']
    if not prefix.get('tokenizer') or prefix.get('tokenizer')!=expected.get('tokenizer'):
        reasons.append('冻结前缀与定义的 Tokenizer 不一致或未记录')
    if not _number(prefix.get('scoring_temperature')) or prefix['scoring_temperature']<=0 or prefix['scoring_temperature']!=expected.get('scoring_temperature'):
        reasons.append('冻结前缀与定义的评分温度不一致或未记录')
    result['prefix_hash'] = prefix.get('sha256');result['prefix_id'] = prefix.get('id')
    if not data.get('checkpoint_id'):reasons.append('没有指定 checkpoint')
    else:
        cp = repo.store._get('checkpoints', data['checkpoint_id'])
        result['weights_sha256'] = cp.get('weights_sha256')
        if _fingerprint(cp.get('weights_sha256')) is None:reasons.append('Checkpoint 权重 SHA256 未记录')
    tokens = data.get('tokens', {})
    ids = tokens.get('token_ids', tokens.get('ids'))
    positions = tokens.get('positions');mask = tokens.get('mask', tokens.get('response_mask'))
    segments = tokens.get('segment_types', tokens.get('segment_type'))
    values = tokens.get('entropy', tokens.get('token_entropy', tokens.get('full_vocab_entropy')))
    if isinstance(frozen_value, dict):
        for key, actual in (('teacher_forced_token_ids', ids), ('prediction_positions', positions)):
            if key in frozen_value and frozen_value[key]!=actual:
                reasons.append('实际 token 支持与冻结输入的 '+key+' 不一致')
    columns = (ids, positions, mask, segments, values)
    pairing = None
    if not all(isinstance(v, list) and v for v in columns) or len({len(v) for v in columns if isinstance(v, list)})!=1:
        reasons.append('需要等长且非空的实际 token IDs、位置、mask、片段类型和 token 熵')
    elif any(type(v) is not int for v in ids+positions) or any(type(m) not in (int, float, bool) or m not in (0, 1) for m in mask) or any(not isinstance(s, str) or not s for s in segments):
        reasons.append('Token 对齐字段存在未知 ID、位置、片段或非二元 mask')
    elif not any(mask):reasons.append('全部 token 被 mask，没有有效测量支持')
    elif any(not _number(v) or v<0 for v, m in zip(values, mask) if m):reasons.append('有效 token 的熵缺失或非法')
    else:
        pairing = {'count':len(ids), 'selected_count':sum(mask),
                   'signature':digest({'ids':ids, 'positions':positions, 'mask':mask, 'segments':segments}),
                   'id_column':'token_ids' if 'token_ids' in tokens else 'ids',
                   'position_column':'positions', 'mask_column':'mask' if 'mask' in tokens else 'response_mask',
                   'segment_column':'segment_types' if 'segment_types' in tokens else 'segment_type',
                   'value_column':next(key for key in ('entropy', 'token_entropy', 'full_vocab_entropy') if key in tokens),
                   'masked_token_mean':sum(v for v, m in zip(values, mask) if m)/sum(mask)}
        agg = definition['data'].get('aggregation', {})
        if agg.get('name') in ('masked_token_mean', 'masked_mean', 'token_mean') and _number(data.get('value')):
            if not math.isclose(data['value'], pairing['masked_token_mean'], rel_tol=1e-6, abs_tol=1e-9):
                reasons.append('记录的 masked-token mean 与实际选中 token 值不一致')
    result['token_pairing'] = pairing
    result['reasons'] = list(dict.fromkeys(reasons))
    result.update(status='incomparable' if reasons else 'measured', value=None if reasons else data['value'])
    if data.get('value') is None or _support_missing(data):result['status']='unmeasured'
    result['reason'] = '；'.join(result['reasons']) if result['reasons'] else '固定输入与实际 token 支持已核验'
    return result


def _fixed_prefix(repo, obj):
    pairs = []
    for declaration in obj['data'].get('pairs', []):
        a = _observation(repo, obj, declaration.get('before_observation_id'))
        b = _observation(repo, obj, declaration.get('after_observation_id'))
        reasons = list(dict.fromkeys(a['reasons']+b['reasons']))
        for field, label in (('compatibility_key', '熵定义、评分温度或 Tokenizer'), ('prefix_hash', '冻结输入')):
            if a.get(field) and b.get(field) and a[field]!=b[field]:reasons.append(label+'不一致')
        if a.get('token_pairing') and b.get('token_pairing') and a['token_pairing']['signature']!=b['token_pairing']['signature']:
            reasons.append('实际 token IDs、位置、mask 或片段类型不一致；相同长度不等于相同支持')
        measured = a['status']==b['status']=='measured' and not reasons
        delta = b['value']-a['value'] if measured else None
        status = 'measured' if measured else 'unmeasured' if 'unmeasured' in (a['status'], b['status']) else 'incomparable'
        pairs.append({'id':declaration['id'], 'before':a, 'after':b, 'status':status,
                      'delta':delta, 'value':delta, 'reasons':reasons,
                      'reason':'同一冻结输入、定义和实际 token 支持的 ΔH（after − before）' if measured else '；'.join(reasons),
                      'token_pairing':{**a['token_pairing'], 'before_value_column':a['token_pairing']['value_column'],
                          'after_value_column':b['token_pairing']['value_column'], 'before_columns':a['token_pairing'],
                          'after_columns':b['token_pairing']} if measured else None})
    measured = [p for p in pairs if p['status']=='measured']
    keys = {p['before']['compatibility_key'] for p in measured}
    frozen = obj['data'].get('cohort', {}).get('frozen') is True
    complete = bool(pairs) and len(measured)==len(pairs) and len(keys)==1 and frozen
    status = 'measured' if complete else 'partial' if measured else 'incomparable' if any(p['status']=='incomparable' for p in pairs) else 'unmeasured'
    return {'pairs':pairs, 'status':status,
            'summary':{'known':len(measured), 'expected':len(pairs), 'definition_groups':len(keys), 'cohort_frozen':frozen,
                'mean_delta':sum(p['delta'] for p in measured)/len(measured) if complete else None,
                'aggregation':'equal_weight_explicit_pairs' if complete else 'not_pooled'},
            'reason':'固定前缀观测的描述性变化；高低熵不直接代表任务成败。' if complete else '题单未冻结、缺测或口径不一致的配对保留原值，不合成平均改善。'}


def error_auroc(uncertainty, errors):
    """Error-positive pairwise probability, exact ties worth one half."""
    positives = sum(errors);negatives = len(errors)-positives
    if not positives or not negatives:return None
    ordered = sorted(zip(uncertainty, errors));rank_sum = 0.;offset = 0
    while offset<len(ordered):
        end = offset+1
        while end<len(ordered) and ordered[end][0]==ordered[offset][0]:end+=1
        mean_rank = (offset+1+end)/2
        rank_sum += mean_rank*sum(v[1] for v in ordered[offset:end]);offset=end
    return (rank_sum-positives*(positives+1)/2)/(positives*negatives)


def risk_coverage(uncertainty, errors, max_points=200):
    """Discrete AURC with expected random ordering inside exact-score ties."""
    n = len(errors)
    if not n:return None, []
    ordered = sorted(zip(uncertainty, errors));offset = 0;prior_errors = 0;area = 0.;curve = []
    shown = {max(1, math.ceil(i*n/min(n, max_points))) for i in range(1, min(n, max_points)+1)}
    while offset<n:
        end = offset+1
        while end<n and ordered[end][0]==ordered[offset][0]:end+=1
        count = end-offset;error_count = sum(v[1] for v in ordered[offset:end])
        for j in range(1, count+1):
            retained = offset+j;risk = (prior_errors+j*error_count/count)/retained;area += risk
            if retained in shown:curve.append({'coverage':retained/n, 'risk':risk, 'retained':retained})
        prior_errors += error_count;offset=end
    return area/n, curve


def _trajectories(repo, eid):
    with repo.store.lock:
        rows = repo.store.conn.execute('SELECT data FROM trajectory_summaries WHERE evaluation_id=?', (eid,)).fetchall()
    return {r['id']:r for r in (json.loads(row[0]) for row in rows)}


def _score_valid(repo, obj, estimator, score):
    if not score:return False, 'score_missing'
    if not _number(score.get('value')):return False, 'score_unmeasured'
    ref = score.get('evidence_ref')
    if not ref:return False, 'score_evidence_missing'
    evidence = repo.resolve_ref(ref, owner=obj)['value']
    if not isinstance(evidence, dict) or evidence.get('trajectory_id')!=score['trajectory_id'] or evidence.get('value')!=score['value']:
        return False, 'score_evidence_mismatch'
    if evidence.get('evaluation_id')!=obj['data']['evaluation_id'] or evidence.get('definition_id')!=estimator['definition_id']:
        return False, 'score_identity_mismatch'
    if evidence.get('probe_type')=='budget_record':return False, 'metadata_is_not_uncertainty'
    if evidence.get('context') not in ('evaluation', 'fixed_prefix_probe'):
        return False, 'not_an_evaluation_probe'
    if _support_missing(evidence):return False, 'measurement_support_unavailable'
    if score.get('probability_error') is not None and evidence.get('probability_error')!=score['probability_error']:
        return False, 'probability_evidence_mismatch'
    return True, None


def _sample_keys(rows):
    identities = {(v.get('dataset'), v.get('sample_id')) for v in rows.values()}
    questions = {digest(str(v['question']).strip()) for v in rows.values() if v.get('question')}
    return identities, questions


def _calibration(repo, obj, estimator, ids, errors, scores, evaluation_rows):
    result = {'status':'not_calibrated', 'ece':None, 'brier':None, 'bins':[],
              'reason':'熵分数不是错误概率；未提供冻结、独立开发集拟合的概率映射。'}
    calibration = estimator.get('calibration')
    if not calibration:return result
    reasons = [];proof = None
    if calibration.get('frozen') is not True or not calibration.get('kind'):
        reasons.append('概率映射未明确冻结或未说明映射方法')
    eid = calibration.get('fit_evaluation_id');fit_ref = calibration.get('fit_ref')
    if not eid or not fit_ref:reasons.append('缺少概率映射的拟合评测及实际拟合证据')
    else:
        ev = repo.store.evaluation(eid)
        if ev['panel'].get('split') not in ('probe', 'dev', 'validation', 'development'):
            reasons.append('概率映射拟合集不是独立开发用途')
        fitting = _trajectories(repo, eid)
        a, aq = _sample_keys(fitting);b, bq = _sample_keys(evaluation_rows)
        if not fitting or not aq or len(aq)!=len(fitting) or len(bq)!=len(evaluation_rows):
            reasons.append('缺少完整题目身份，无法核验拟合与评测隔离')
        if eid==obj['data']['evaluation_id'] or a&b or aq&bq:
            reasons.append('概率映射的拟合题目与评测重叠')
        proof = repo.resolve_ref(fit_ref, owner=obj)['value']
        if not isinstance(proof, dict) or proof.get('fit_evaluation_id')!=eid or proof.get('kind')!=calibration.get('kind') or proof.get('frozen') is not True or not (proof.get('parameters') or proof.get('mapping')):
            reasons.append('拟合证据没有对应冻结映射及参数')
        elif proof.get('definition_id')!=estimator['definition_id']:
            reasons.append('冻结概率映射没有绑定当前估计器定义')
    probabilities = [scores[tid].get('probability_error') for tid in ids]
    if not ids or any(not _number(p) or not 0<=p<=1 for p in probabilities):
        reasons.append('共同样本缺少显式且有证据的错误概率')
    # A label saying "calibrated" is insufficient. This lightweight runtime
    # validates one explicit mapping without importing a fitting library.
    parameters = proof.get('parameters', {}) if isinstance(proof, dict) else {}
    if calibration.get('kind')!='logistic' or not all(_number(parameters.get(k)) for k in ('intercept', 'coefficient')):
        reasons.append('当前自动校验仅支持 logistic 冻结映射及有限 intercept/coefficient；其他映射保留原件')
    elif all(_number(p) and 0<=p<=1 for p in probabilities):
        for tid, probability in zip(ids, probabilities):
            z = parameters['intercept']+parameters['coefficient']*scores[tid]['value']
            if not math.isfinite(z):
                reasons.append('概率映射数值溢出');break
            expected = 1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
            if not math.isclose(expected, probability, rel_tol=1e-7, abs_tol=1e-9):
                reasons.append('记录错误概率与冻结映射的实际输出不一致');break
    if reasons:return {**result, 'reason':'；'.join(dict.fromkeys(reasons)), 'reasons':list(dict.fromkeys(reasons))}
    bins = [];ece = 0.
    for i in range(10):
        indexes = [j for j, p in enumerate(probabilities) if min(int(p*10), 9)==i]
        if not indexes:continue
        predicted = sum(probabilities[j] for j in indexes)/len(indexes);actual = sum(errors[j] for j in indexes)/len(indexes)
        ece += len(indexes)/len(ids)*abs(predicted-actual)
        bins.append({'lower':i/10, 'upper':(i+1)/10, 'count':len(indexes), 'probability_error':predicted, 'error_rate':actual})
    return {'status':'measured', 'ece':ece, 'brier':sum((p-y)**2 for p, y in zip(probabilities, errors))/len(ids),
            'bins':bins, 'binning':'10_equal_width_probability_bins', 'fit_evaluation_id':eid,
            'reason':'仅对显式冻结概率映射及共同样本报告 ECE / Brier；不将原始熵当概率。'}


def _uncertainty(repo, obj):
    data = obj['data'];rows = _trajectories(repo, data['evaluation_id']);declarations = data['estimators']
    maps = [{s['trajectory_id']:s for s in e.get('scores', [])} for e in declarations]
    common = [];excluded = Counter();score_counts = [0]*len(declarations)
    for tid in data['trajectory_ids']:
        em = rows.get(tid, {}).get('scores', {}).get('em');reasons = []
        if type(em) not in (int, float) or em not in (0, 1):reasons.append('formal_em_unscored')
        for index, (estimator, scores) in enumerate(zip(declarations, maps)):
            valid, reason = _score_valid(repo, obj, estimator, scores.get(tid))
            if valid:score_counts[index]+=1
            else:reasons.append(reason)
        if not reasons and declarations:common.append(tid)
        else:excluded.update(set(reasons or ['no_estimators']))
    errors = [1-int(rows[tid]['scores']['em']) for tid in common];results = []
    for index, (estimator, scores) in enumerate(zip(declarations, maps)):
        uncertainty = [scores[tid]['value']*(1 if estimator['higher_is_uncertain'] else -1) for tid in common]
        auc = error_auroc(uncertainty, errors);aurc, curve = risk_coverage(uncertainty, errors)
        from .entropy_tracking import definition_semantics
        semantics = definition_semantics(repo.object(estimator['definition_id']))
        results.append({'id':estimator['id'], 'label':estimator.get('label', estimator['id']),
            'definition_id':estimator['definition_id'], 'definition_complete':semantics['complete'],
            'definition_missing_fields':semantics['missing_fields'], 'status':'measured' if common else 'unmeasured',
            'score_count':score_counts[index], 'common_count':len(common), 'higher_is_uncertain':estimator['higher_is_uncertain'],
            'auroc':{'value':auc, 'status':'measured' if auc is not None else 'undefined', 'positive_class':'formal_em_error',
                     'reason':'错误样本的不确定性高于正确样本的概率；并列计 0.5。' if auc is not None else 'AUROC 需要至少一个正确与一个错误样本。'},
            'aurc':{'value':aurc, 'status':'measured' if aurc is not None else 'unmeasured',
                    'definition':'mean risk at k/n, k=1..n; expected random order within exact ties', 'lower_is_better':True},
            'curve':curve, 'calibration':_calibration(repo, obj, estimator, common, errors, scores, rows)})
    complete = bool(declarations) and bool(common) and len(common)==len(data['trajectory_ids'])
    ev = repo.store.evaluation(data['evaluation_id'])
    cell = _cell(repo, ev['id'], ev['checkpoint']['id'], None, bank_expected=False)
    context, context_reasons = _entropy_context(repo, ev['id'], 'training', 'baseline')
    reasons = list(cell['reasons'])+context_reasons
    if data.get('cohort', {}).get('frozen') is not True:reasons.append('指标比较题单未明确冻结')
    if set(data['trajectory_ids'])!=set(rows):reasons.append('显式题单不是该正式评测的全部样本；只报告子集描述')
    if not complete:reasons.append('估计器在共同样本上存在缺测，完整题单比较不成立')
    if not errors or not 0<sum(errors)<len(errors):reasons.append('错误识别比较需要正确和错误两类标签')
    if any(not result['definition_complete'] for result in results):reasons.append('存在未完整定义的估计器')
    protocol_reasons = [];protocols = []
    expected_hash = digest(sorted(data['trajectory_ids']))
    for estimator in declarations:
        protocol = estimator.get('probe_protocol', {});protocols.append(protocol)
        if not _recorded_known(protocol.get('sampling_budget')):
            protocol_reasons.append(estimator['id']+': 未完整登记指标采样预算')
        if protocol.get('sample_set_hash')!=expected_hash:
            protocol_reasons.append(estimator['id']+': 采样题单指纹未与显式轨迹集合一致')
        ref = estimator.get('probe_protocol_ref')
        if ref and repo.resolve_ref(ref, owner=obj)['value']!=protocol:
            protocol_reasons.append(estimator['id']+': 采样协议与引用原件不一致')
    if len({digest(p) for p in protocols})>1:protocol_reasons.append('估计器使用的采样协议或预算不同')
    reasons += protocol_reasons;reasons = list(dict.fromkeys(reasons))
    return {'status':'measured' if complete else 'partial' if common else 'unmeasured',
            'reason':'同一批正式输出、同一错误标签、所有指标共同有效样本；这评估识错能力，不证明训练收益。',
            'evaluation_id':data['evaluation_id'], 'cohort_count':len(data['trajectory_ids']), 'common_count':len(common),
            'excluded_count':len(data['trajectory_ids'])-len(common), 'exclusion_reasons':dict(excluded),
            'common_cohort_hash':digest(common), 'label_target':'error = 1 - formal scores.em',
            'errors':sum(errors), 'correct':len(errors)-sum(errors), 'estimators':results,
            'comparison_qualified':not reasons, 'qualification_reasons':reasons,
            'measurement_budget':{'matched':bool(protocols) and not protocol_reasons,
                'status':'matched_declared_protocols' if protocols and not protocol_reasons else 'unverified_or_mismatched',
                'sample_set_hash':expected_hash, 'reasons':protocol_reasons,
                'note':'核对登记的采样协议；实际额外评分计算量应另记成本。'},
            'evaluation_condition':{'status':cell['status'], 'entropy_context':context},
            'statistical_scope':'descriptive_on_explicit_common_cohort', 'ood_status':data.get('ood_status', 'not_measured')}


def _recorded_known(value):
    if value is None:return False
    if isinstance(value, str):return bool(value.strip()) and value.strip().lower() not in ('unknown', 'unmeasured', 'not_recorded', 'not_provided')
    if isinstance(value, dict):return bool(value) and all(_recorded_known(v) for v in value.values())
    if isinstance(value, list):return bool(value) and all(_recorded_known(v) for v in value)
    return type(value) is bool or _number(value)


def _update_evidence(repo, declaration):
    from .assessment import _read
    cpid = declaration.get('checkpoint_id');oids = declaration.get('intervention_ids', [])
    result = {'intervention_ids':list(oids), 'sampled_use_ids':[], 'completed_update_ids':[],
              'completed':False, 'missing_intervention_ids':list(oids), 'causal_contribution':False}
    if not cpid or not oids:return result
    cp = repo.store._get('checkpoints', cpid)
    rows = _read(repo, {'run_id':cp['run_id']}, ('training_use', 'training_update'))
    matched = []
    for oid in oids:
        uses = [r['id'] for r in rows if r['kind']=='training_use' and r['assessment'].get('status')=='sampled'
                and oid in r['assessment'].get('artifact_ids', [])]
        updates = [r['id'] for r in rows if r['kind']=='training_update' and r['assessment'].get('status')=='completed'
                   and r['assessment'].get('output_checkpoint_id')==cpid
                   and set(uses)&set(r['assessment'].get('consumption_ids', []))]
        result['sampled_use_ids'] += uses;result['completed_update_ids'] += updates
        if updates:matched.append(oid)
    result['sampled_use_ids'] = sorted(set(result['sampled_use_ids']))
    result['completed_update_ids'] = sorted(set(result['completed_update_ids']))
    result.update(completed=len(matched)==len(oids), missing_intervention_ids=[oid for oid in oids if oid not in matched])
    return result


def _entropy_context(repo, eid, mode, role):
    binding = (repo.evaluation_state(eid).get('binding') or {}).get('data', {})
    context = binding.get('entropy_context', {});reasons = []
    expected = 'none' if mode=='training' or role=='baseline' else role
    if context.get('runtime_intervention')!=expected:reasons.append('评测时熵策略未明确为 '+expected)
    resources = context.get('extra_resources', {})
    if not all(type(resources.get(k)) is bool for k in ('branching', 'summary_model', 'teacher')):
        reasons.append('额外分支、摘要模型与 Teacher 条件未完整登记')
    elif mode=='training' and any(resources.values()):reasons.append('训练效果评测仍使用额外运行资源')
    if context.get('training_only_guidance_removed') is not True:reasons.append('未明确撤去训练专用指导')
    return context, reasons


def _intervention(repo, obj):
    data = obj['data'];mode = data['mode'];declarations = {a['id']:a for a in data.get('arms', [])}
    keys = data.get('budget_keys', []);arms = [];cost_reasons = []
    for role in ARMS:
        declaration = declarations.get(role, {});eid = declaration.get('evaluation_id');cpid = declaration.get('checkpoint_id')
        arm = _cell(repo, eid, cpid, None, bank_expected=False,
                    allowed_purposes=('checkpoint_eval',) if mode=='training' else ('checkpoint_eval', 'agent_eval'))
        arm.update(id=role, label={'baseline':'基础训练 / 无熵干预', 'random':'随机选择对照', 'entropy':'Entropy 方法'}[role],
                   selection=copy.deepcopy(declaration.get('selection', {})), budget=copy.deepcopy(declaration.get('budget', {})))
        if eid:
            arm['run_id'] = repo.store.evaluation(eid)['run']['id']
            context, reasons = _entropy_context(repo, eid, mode, role);arm['entropy_context'] = context
            arm['reasons'] += reasons
        if arm['reasons']:
            arm.update(status='incomparable', value=None, reason='；'.join(dict.fromkeys(arm['reasons'])))
        ref = declaration.get('cost_ref');proof = repo.resolve_ref(ref, owner=obj)['value'] if ref else None
        proof_reasons = []
        if not isinstance(proof, dict):proof_reasons.append('缺少实际成本记录')
        elif proof.get('evaluation_id')!=eid or proof.get('checkpoint_id')!=cpid or proof.get('budget')!=arm['budget'] or proof.get('selection')!=arm['selection']:
            proof_reasons.append('预算/选择与该组正式评测及成本原件不一致')
        if isinstance(proof, dict) and proof.get('run_id') and cpid:
            cp = repo.store._get('checkpoints', cpid)
            if proof['run_id']!=cp['run_id']:proof_reasons.append('成本原件来自另一训练 run')
        expected_policy = 'none' if role=='baseline' else role
        if arm['selection'].get('policy')!=expected_policy:proof_reasons.append('选择策略未明确为 '+expected_policy)
        count = arm['selection'].get('selected_count')
        if type(count) is not int or count<0 or role!='baseline' and count==0:proof_reasons.append('缺少有效的实际选择数量')
        if role=='random' and (arm['selection'].get('seed') is None or arm['selection'].get('seed') in ('', 'unknown')):
            proof_reasons.append('随机对照未保存明确随机种子')
        if not keys:proof_reasons.append('没有指定实际比较的预算维度')
        for key in keys:
            if not _number(arm['budget'].get(key)):proof_reasons.append('预算 '+key+' 未测')
        if mode=='training':
            controls = proof.get('training_controls', {}) if isinstance(proof, dict) else {}
            arm['training_controls'] = copy.deepcopy(controls)
            for key in ('initial_weights_sha256', 'training_data_sha256', 'trainer_revision', 'optimizer', 'seed'):
                if not _recorded_known(controls.get(key)):proof_reasons.append('共同训练条件 '+key+' 未完整登记')
            for key in ('initial_weights_sha256', 'training_data_sha256'):
                if _fingerprint(controls.get(key)) is None:proof_reasons.append('共同训练条件 '+key+' 缺少有效指纹')
            if role!='baseline':
                arm['update_evidence'] = _update_evidence(repo, declaration)
                if not arm['update_evidence']['completed']:
                    proof_reasons.append('没有完整的干预 → sampled use → completed update → 本组输出 checkpoint 证据')
        arm['cost_status'] = 'verified' if not proof_reasons else 'unverified';arm['cost_reasons'] = proof_reasons
        cost_reasons.extend(role+': '+reason for reason in proof_reasons);arms.append(arm)
    indexed = {a['id']:a for a in arms}
    for key in keys:
        values = [a['budget'].get(key) for a in arms]
        if all(_number(v) for v in values) and len(set(values))!=1:cost_reasons.append('实际预算 '+key+' 不一致')
    if mode=='training' and len({digest(a.get('training_controls', {})) for a in arms})!=1:
        cost_reasons.append('初始权重、训练数据、优化器、训练器或种子条件不一致')
    counts = [indexed[role]['selection'].get('selected_count') for role in ('random', 'entropy')]
    if all(type(c) is int and c>0 for c in counts) and counts[0]!=counts[1]:cost_reasons.append('Entropy 与随机对照的实际选择数量不一致')
    frozen = data.get('cohort', {}).get('frozen') is True
    if not frozen:cost_reasons.append('对照题单与比较 cohort 未明确冻结')
    cost_reasons = list(dict.fromkeys(cost_reasons));matched = not cost_reasons;effects = []
    for role in ('baseline', 'random'):
        a, b = indexed[role], indexed['entropy'];reasons = []
        # A missing third arm cannot erase a valid two-arm descriptive result.
        # The random comparator is separately required for the random effect.
        for arm in (a, b):reasons.extend(arm['id']+': '+r for r in arm['cost_reasons'])
        for key in keys:
            if a['budget'].get(key)!=b['budget'].get(key):reasons.append('实际预算 '+key+' 不一致')
        if mode=='training' and a.get('training_controls')!=b.get('training_controls'):
            reasons.append('初始权重、训练数据、优化器、训练器或种子条件不一致')
        if not frozen:reasons.append('对照题单与比较 cohort 未明确冻结')
        if role=='random' and a['selection'].get('selected_count')!=b['selection'].get('selected_count'):
            reasons.append('Entropy 与随机对照的实际选择数量不一致')
        effect = {'id':'entropy_minus_'+role, 'a':a['evaluation_id'], 'b':b['evaluation_id'], 'status':'unmeasured',
                  'em_delta':None, 'value':None, 'metric':'macro_em_delta', 'reason':'需要两个明确的自主评测与成本证据'}
        if a['evaluation_id'] and b['evaluation_id']:
            if a['status']!='measured' or b['status']!='measured':reasons += a['reasons']+b['reasons']
            pair = _pair(repo, a['evaluation_id'], b['evaluation_id'], 'checkpoint' if mode=='training' else 'harness',
                         allow_same_harness=mode=='runtime')
            reasons += pair['reasons']
            if not pair['full'] or pair['macro_delta'] is None:reasons.append('对照没有完整可比的评分')
            if mode=='runtime' and a.get('entropy_context', {}).get('extra_resources')!=b.get('entropy_context', {}).get('extra_resources'):
                reasons.append('推理时额外资源条件不同，需单独控制预算而非归因于熵选择')
            reasons = list(dict.fromkeys(reasons))
            if not reasons:
                effect.update(status='measured', value=pair['macro_delta'], em_delta=pair['macro_delta'],
                    reason='所记录预算维度与已登记执行条件下的描述性自主 EM 差异',
                    groups=pair['groups'], rescued=pair['groups']['rescued'], harmed=pair['groups']['harmed'],
                    both_correct=pair['groups']['both_correct'], both_wrong=pair['groups']['both_wrong'],
                    paired_usage=pair['paired_usage'], paired=pair['paired'], expected=pair['expected'])
            else:effect.update(status='incomparable', reason='；'.join(reasons))
            effect.update(controls=pair['controls'], reasons=reasons)
        effects.append(effect)
    status = 'measured' if all(e['status']=='measured' for e in effects) else 'partial' if any(e['status']=='measured' for e in effects) else 'incomparable' if any(e['status']=='incomparable' for e in effects) else 'unmeasured'
    return {'status':status, 'mode':mode, 'arms':arms, 'effects':effects,
            'budget':{'matched':matched, 'keys':keys, 'status':'matched_recorded_dimensions' if matched else 'unverified_or_mismatched',
                      'reasons':cost_reasons, 'selection_counts_matched':counts[0]==counts[1] and all(type(c) is int and c>0 for c in counts),
                      'claim':'只核对声明的实际预算维度及选择数量，不代表全部计算量、时间或费用相同。'},
            'reason':'训练自主结果与推理时干预分别解释；随机选择对照用于检验熵选择是否优于相同已记录预算。'}


def entropy_comparison(repo, object_id):
    obj = repo.object(object_id)
    if obj['kind']!='entropy_comparison':raise ValidationError('Expected an entropy_comparison object')
    kind = obj['data']['comparison_type']
    result = {'fixed_prefix':_fixed_prefix, 'uncertainty':_uncertainty, 'intervention':_intervention}[kind](repo, obj)
    return {'id':object_id, 'scope':obj['scope'], 'demo':evidence_demo(repo, obj), 'comparison_type':kind,
            'cohort':copy.deepcopy(obj['data'].get('cohort', {})), **result,
            'claims':{'causal_training_credit':False, 'final_generalization':False, 'entropy_equals_confidence':False,
                      'tool_information_gain':False, 'metric_predictivity_implies_rl_utility':False},
            'notes':['所有结果都来自显式绑定的现有记录；缺测不补零，也不选择最新或最好的一次。',
                     '开发反馈、最终测试与训练访问状态分别声明；表中 measured 仅说明本次证据符合比较条件。',
                     '不同生成片段前后的熵变化不自动解释为工具信息增益；需要同一目标上的观察对照。']}
