"""Fixed-pool versus fresh-supply comparisons over recorded self-play runs."""
from __future__ import annotations

import copy
import math

from ..core import ValidationError, NotFoundError
from ..inspection.repository import digest
from .schema import identifier
from .selfplay_tracking import _dict, _number, _fingerprint, _same_scope, _model, _round_data

LABELS = ('fixed_pool', 'fresh_supply')
COMPONENTS = ('generation', 'validation', 'solving', 'training')
CONTROLS = ('initial_solver_sha256', 'initial_pool_sha256', 'harness_sha256', 'optimizer_sha256', 'training_seed')


def validate_selfplay_comparison_contract(data):
    if data.get('comparison_type')!='pool_supply':
        raise ValidationError('selfplay_comparison.comparison_type is pool_supply')
    arms = data.get('arms', [])
    if not isinstance(arms, list) or len(arms)>2:raise ValidationError('Self-play comparison supports at most two arms')
    labels = set();runs = set();evaluations = set()
    for arm in arms:
        _dict(arm, 'arm')
        if arm.get('label') not in LABELS:raise ValidationError('Arm label is fixed_pool/fresh_supply')
        if arm['label'] in labels:raise ValidationError('Duplicate self-play comparison arm')
        labels.add(arm['label'])
        for key in ('run_id', 'checkpoint_id', 'evaluation_id'):
            if arm.get(key) is not None:identifier(arm[key], key)
        if arm.get('run_id'):
            if arm['run_id'] in runs:raise ValidationError('Comparison arms require distinct runs')
            runs.add(arm['run_id'])
        if arm.get('evaluation_id'):
            if not arm.get('checkpoint_id') or not arm.get('run_id'):
                raise ValidationError('Measured comparison arms require explicit run and checkpoint')
            if arm['evaluation_id'] in evaluations:raise ValidationError('Duplicate comparison evaluation')
            evaluations.add(arm['evaluation_id'])
        cycles = arm.get('cycle_ids', [])
        if not isinstance(cycles, list) or len(cycles)>1000:raise ValidationError('cycle_ids must have at most 1000 entries')
        if len(cycles)!=len(set(cycles)):raise ValidationError('Duplicate cycle_ids')
        for cid in cycles:identifier(cid, 'cycle_id')
        for key in CONTROLS:
            if key.endswith('_sha256') and arm.get(key) is not None and not _fingerprint(arm[key]):
                raise ValidationError(key + ' must be SHA256')
        if arm.get('training_seed') is not None and type(arm['training_seed']) is not int:
            raise ValidationError('training_seed must be an integer')
        budget = _dict(arm.get('budget', {}), 'budget')
        if budget.get('unit') not in (None, 'gpu_seconds', 'usd'):
            raise ValidationError('Total compute budget unit is gpu_seconds or usd')
        if 'complete' in budget and type(budget['complete']) is not bool:raise ValidationError('budget.complete must be boolean')
        components = _dict(budget.get('components', {}), 'budget.components')
        for key, value in components.items():
            if value is not None and (not _number(value) or value<0):raise ValidationError('Budget components must be nonnegative or null')
        if budget.get('total') is not None and (not _number(budget['total']) or budget['total']<0):
            raise ValidationError('budget.total must be nonnegative or null')
        for key in ('accounting_protocol_sha256', 'hardware_sha256'):
            if budget.get(key) is not None and not _fingerprint(budget[key]):raise ValidationError(key + ' must be SHA256')
    tolerance = data.get('budget_tolerance')
    if tolerance is not None and (not _number(tolerance) or not 0<=tolerance<=1):
        raise ValidationError('budget_tolerance must be in [0, 1]')


def selfplay_comparison_refs(obj):
    for arm in obj['data'].get('arms', []):
        for cid in arm.get('cycle_ids', []):yield cid, {'selfplay_cycle'}, '/data/arms/cycle_ids'


def selfplay_comparison_evidence_refs(obj):
    for arm in obj['data'].get('arms', []):
        if arm.get('cost_ref') is not None:yield arm['cost_ref']
        if arm.get('controls_ref') is not None:yield arm['controls_ref']
        if arm.get('initial_pool_ref') is not None:yield arm['initial_pool_ref']


def validate_selfplay_comparison_links(repo, obj, pending):
    scope = obj.get('scope', {})
    if repo.store is None or not scope.get('experiment_id'):
        raise ValidationError('Self-play comparison requires a registered experiment')
    if scope.get('run_id'):raise ValidationError('Cross-run self-play comparison uses experiment-only scope')
    from .opd_tracking import evidence_demo
    for arm in obj['data'].get('arms', []):
        rid = arm.get('run_id')
        if rid:
            run = repo.store.run(rid)
            if run['experiment_id']!=scope['experiment_id']:
                raise ValidationError('Comparison run belongs to another experiment')
            if bool(run.get('demo'))!=evidence_demo(repo, obj):
                raise ValidationError('Synthetic and real self-play comparison runs cannot be mixed')
        if arm.get('checkpoint_id'):
            cp = repo.store._get('checkpoints', arm['checkpoint_id'])
            if not rid or cp['run_id']!=rid:raise ValidationError('Comparison checkpoint belongs to another run')
        if arm.get('evaluation_id'):
            ev = repo.store.evaluation(arm['evaluation_id'])
            if ev['experiment_id']!=scope['experiment_id'] or ev['checkpoint']['run_id']!=rid or ev['checkpoint']['id']!=arm.get('checkpoint_id'):
                raise ValidationError('Comparison evaluation belongs to another experiment/run/checkpoint')
            if bool(ev.get('demo') or ev['run'].get('demo') or ev['panel'].get('demo'))!=evidence_demo(repo, obj):
                raise ValidationError('Synthetic and real self-play comparison evaluations cannot be mixed')
        for cid in arm.get('cycle_ids', []):
            cycle = repo._raw_object(cid, pending);_same_scope(repo, obj, cycle, experiment=True)
            if not rid or cycle.get('scope', {}).get('run_id')!=rid:
                raise ValidationError('Comparison cycle belongs to another arm/run')
        for key in ('cost_ref', 'controls_ref', 'initial_pool_ref'):
            ref = arm.get(key)
            if ref is None:continue
            repo.resolve_ref(ref, pending, obj)
            if ref.get('object_id'):
                other = repo._raw_object(ref['object_id'], pending);_same_scope(repo, obj, other, experiment=True)
                if not rid or other.get('scope', {}).get('run_id')!=rid:
                    raise ValidationError('Comparison evidence belongs to another arm/run')
            elif ref.get('trajectory'):
                t = repo.store.trajectory(ref['trajectory']);ev = repo.store.evaluation(t['evaluation_id'])
                if ev['experiment_id']!=scope['experiment_id'] or ev['checkpoint']['run_id']!=rid:
                    raise ValidationError('Comparison trajectory belongs to another arm/run')


def _proof(repo, obj, arm, key):
    ref = arm.get(key)
    if not isinstance(ref, dict):return None, ['缺少 ' + key + ' 原始证据引用']
    value = repo.resolve_ref(ref, owner=obj)['value']
    if ref.get('object_id'):
        record = repo.object(ref['object_id']);source = record['data']
        owner_run = record.get('scope', {}).get('run_id')
    else:
        source = repo.resolve_ref({**ref, 'pointer':''}, owner=obj)['value'];owner_run = source.get('run_id') if isinstance(source, dict) else None
    reasons = []
    if not isinstance(source, dict) or source.get('selfplay_evidence_type')!='run_accounting':
        reasons.append('证据未按 selfplay run_accounting 明确记录成本与控制所属区间')
    else:
        if owner_run!=arm.get('run_id') or source.get('run_id')!=arm.get('run_id'):
            reasons.append('原始证据不属于此 arm 运行')
        if sorted(source.get('cycle_ids', []))!=sorted(arm.get('cycle_ids', [])):
            reasons.append('原始证据覆盖的训练轮次与比较区间不同')
        if source.get('output_checkpoint_id')!=arm.get('checkpoint_id'):
            reasons.append('原始证据未绑定此输出 checkpoint')
    return value, reasons


def _budget(repo, obj, arm):
    budget = copy.deepcopy(arm.get('budget', {}));reasons = []
    components = budget.get('components', {})
    if budget.get('complete') is not True:reasons.append('总预算未明确采集完整')
    if any(not _number(components.get(key)) for key in COMPONENTS):
        reasons.append('生成、验证、解题与训练四阶段成本必须分别记录，零成本也须明确为零')
    if any(not _number(v) for v in components.values()):reasons.append('预算包含未测分项')
    total = sum(components.values()) if components and all(_number(v) for v in components.values()) else None
    if total is None or not _number(budget.get('total')) or not math.isclose(total, budget.get('total', -1), rel_tol=1e-9, abs_tol=1e-9):
        reasons.append('预算 total 与实际分项合计不一致或未记录')
    if budget.get('unit') not in ('gpu_seconds', 'usd'):reasons.append('缺少共同资源预算单位')
    for key in ('accounting_protocol_sha256', 'hardware_sha256'):
        if not _fingerprint(budget.get(key)):reasons.append('预算缺少 ' + key)
    saved, proof_reasons = _proof(repo, obj, arm, 'cost_ref');reasons += proof_reasons
    if not isinstance(saved, dict) or any(saved.get(k)!=budget.get(k) for k in ('unit', 'components', 'total', 'complete', 'accounting_protocol_sha256', 'hardware_sha256')):
        reasons.append('对照预算与所引用的原始成本记录不一致')
    return {**budget, 'verified_total':total if not reasons else None, 'qualified':not reasons,
            'qualification_reasons':reasons}


def selfplay_evaluation_cell(repo, evaluation_id, checkpoint_id):
    from .skill_comparison import _cell
    result = _cell(repo, evaluation_id, checkpoint_id, None, bank_expected=False)
    if not evaluation_id:return result
    binding = (repo.evaluation_state(evaluation_id).get('binding') or {}).get('data', {})
    context = binding.get('selfplay_context', {})
    reasons = list(result.get('reasons', []))
    if 'self_play' not in binding.get('assessment_families', []):reasons.append('评测未明确绑定 Self-play 研究')
    if context.get('evaluation_source')!='fixed_external':reasons.append('未明确声明固定外部题集')
    for key in ('proposer_hints', 'teacher_hints', 'groundtruth_visible', 'generated_answers_visible'):
        if context.get(key) is not False:reasons.append('未明确撤去 ' + key)
    if context.get('training_only_guidance_removed') is not True:reasons.append('未明确撤去训练专用指导')
    ev = repo.store.evaluation(evaluation_id)
    from .skill_comparison import _ReadOnlyStudio
    from ..studio.evaluation import EvaluationViews
    studio = getattr(repo.store, '_team_studio', None) or _ReadOnlyStudio(repo)
    panel = EvaluationViews(studio)._context(ev)['panel']
    result['isolation'] = {key:panel.get(key) for key in ('split', 'isolation', 'isolation_verified', 'classification', 'eligibility_reasons') if key in panel}
    result['isolation_note'] = '固定外部评测及显式隔离声明；未自动验证未知训练语料的重叠，不自动宣称泛化。'
    result.update(status='incomparable' if reasons else result['status'], value=None if reasons else result['value'],
                  reasons=list(dict.fromkeys(reasons)), reason='；'.join(dict.fromkeys(reasons)) if reasons else result['reason'])
    return result


def _arm_counts(repo, obj, arm):
    from .assessment import _read
    from .selfplay_tracking import READ_KINDS, _p
    rid = arm.get('run_id');cycle_ids = arm.get('cycle_ids', []);reasons = []
    if not rid:return {}, ['未指定训练运行']
    rows = _read(repo, {**obj['scope'], 'run_id':rid}, READ_KINDS);model = _model(rows)
    if not cycle_ids:return {}, ['未指定纳入预算比较的训练轮次']
    totals = {'new_verified_consumed_questions':0, 'completed_questions':0, 'reused_questions':0,
              'old_first_consumed_questions':0, 'completed_rollout_uses':0}
    # Round-supply counts count a question only in its generation round.
    for cid in cycle_ids:
        data = _round_data(model, cid)
        for key in totals:totals[key] += data['counts'][key]
        coverage = _p(model['cycle_map'].get(cid, {})).get('coverage', {})
        if any(coverage.get(k) is not True for k in ('generated_questions_complete', 'reviews_complete', 'consumption_complete', 'updates_complete')):
            reasons.append('轮次 ' + cid + ' 的供给、验证、消费或更新记录不完整')
    target = arm.get('checkpoint_id')
    usable = {r['id'] for r in model['bykind']['training_use'] if _p(r).get('status')=='sampled' and model['training_roles'].get(r['id'])=='solver' and _p(r).get('cycle_id') in cycle_ids and set(_p(r).get('artifact_ids', [])).intersection(model['question_map'])}
    updates = [r for r in model['bykind']['training_update'] if _p(r).get('status')=='completed' and _p(r).get('output_checkpoint_id')==target and usable.intersection(_p(r).get('consumption_ids', []))]
    if not updates:reasons.append('缺少实际采样 → 完成更新 → 本 arm 输出 checkpoint 的关联')
    totals['output_update_ids'] = [r['id'] for r in updates]
    controls, control_reasons = _proof(repo, obj, arm, 'controls_ref');reasons += control_reasons
    if not isinstance(controls, dict) or any(controls.get(key)!=arm.get(key) or controls.get(key) is None for key in CONTROLS):
        reasons.append('初始 solver、题池、harness、optimizer 或 seed 与原始控制记录不一致或未记录')
    pool, pool_reasons = _proof(repo, obj, arm, 'initial_pool_ref');reasons += pool_reasons
    valid_pool = isinstance(pool, list) and all(isinstance(x, str) and x for x in pool) and len(pool)==len(set(pool))
    if not valid_pool or digest(sorted(pool))!=arm.get('initial_pool_sha256'):
        reasons.append('初始题池稳定题号清单与冻结哈希不匹配或未记录');pool_set = None
    else:pool_set = set(pool)
    completed = [u for u in model['bykind']['training_use'] if u['id'] in model['solver_completed_ids'] and _p(u).get('status')=='sampled' and _p(u).get('cycle_id') in cycle_ids]
    consumed_qids = {qid for u in completed for qid in _p(u).get('artifact_ids', []) if qid in model['question_map']}
    consumed_uids = {model['canonical_ids'][qid] for qid in consumed_qids if model['canonical_ids'][qid]}
    unknown_ids = sum(not model['canonical_ids'][qid] for qid in consumed_qids)
    outside = consumed_uids - pool_set if pool_set is not None else set()
    valid_outside = set()
    from .selfplay_tracking import validation_state
    for qid in consumed_qids:
        q = model['question_map'][qid];p = _p(q);uid = model['canonical_ids'][qid]
        if (uid in outside and p.get('first_generated_cycle_id') in cycle_ids and p.get('deduplication')=='unique'
                and validation_state(model['reviews'][qid])=='passed'):
            valid_outside.add(uid)
    totals.update(initial_pool_size=len(pool_set) if pool_set is not None else None,
                  distinct_consumed_questions=len(consumed_uids), outside_initial_pool_questions=len(outside),
                  new_valid_used_outside_initial_pool_questions=len(valid_outside), unknown_consumed_identity_questions=unknown_ids)
    if unknown_ids:reasons.append('实际消费题目缺少稳定身份，无法核对初始池归属')
    if arm['label']=='fixed_pool' and outside:reasons.append('固定题池组消费了冻结初始题池之外的题目')
    if arm['label']=='fresh_supply' and not valid_outside:reasons.append('持续供给组缺少新增有效题在比较区间内实际完成训练的证据')
    # The initial solver fingerprint is checked against the recorded input
    # checkpoint of the first completed update in this comparison interval.
    interval_updates = [r for r in model['bykind']['training_update'] if _p(r).get('status')=='completed' and _p(r).get('cycle_id') in cycle_ids and usable.intersection(_p(r).get('consumption_ids', []))]
    interval_updates.sort(key=lambda r:(model['ranks'].get(_p(r).get('cycle_id'), 10**9), _p(r).get('step', 0)))
    first_cp = _p(interval_updates[0]).get('input_checkpoint_id') if interval_updates else None
    try:initial = repo.store._get('checkpoints', first_cp) if first_cp else {}
    except NotFoundError:initial = {}
    if initial.get('run_id')!=rid or initial.get('weights_sha256')!=arm.get('initial_solver_sha256'):
        reasons.append('首个已完成 solver 更新的输入 checkpoint 与初始权重指纹未匹配')
    totals['initial_checkpoint_id'] = first_cp
    return totals, list(dict.fromkeys(reasons))


def selfplay_comparison(repo, comparison_id):
    from .skill_comparison import _pair
    obj = repo.object(comparison_id)
    if obj['kind']!='selfplay_comparison':raise ValidationError('Expected selfplay_comparison')
    reasons = [];arms = []
    for arm in obj['data'].get('arms', []):
        result = selfplay_evaluation_cell(repo, arm.get('evaluation_id'), arm.get('checkpoint_id'))
        budget = _budget(repo, obj, arm);counts, count_reasons = _arm_counts(repo, obj, arm)
        arm_reasons = list(result.get('reasons', [])) + budget['qualification_reasons'] + count_reasons
        if result['status']!='measured':arm_reasons.append(result.get('reason') or 'arm 评测尚未完成')
        arms.append({**copy.deepcopy(arm), 'budget':budget, 'counts':counts, 'result':result,
                     'qualification_reasons':list(dict.fromkeys(arm_reasons))})
        reasons += [arm['label'] + '：' + reason for reason in arm_reasons]
    indexed = {a['label']:a for a in arms};pair = None;raw_delta = None;relative_gap = None
    if set(indexed)!=set(LABELS):reasons.append('需要固定旧题池与持续补充新题两个独立训练运行')
    else:
        a, b = indexed['fixed_pool'], indexed['fresh_supply']
        for key in CONTROLS:
            if a.get(key) is None or b.get(key) is None or a[key]!=b[key]:reasons.append('两组初始条件不匹配或未记录：' + key)
        for key in ('unit', 'accounting_protocol_sha256', 'hardware_sha256'):
            if a['budget'].get(key) is None or a['budget'].get(key)!=b['budget'].get(key):reasons.append('总预算计量口径不一致：' + key)
        av, bv = a['budget'].get('verified_total'), b['budget'].get('verified_total')
        tolerance = obj['data'].get('budget_tolerance')
        if av is not None and bv is not None and max(av, bv)>0:
            relative_gap = abs(av-bv)/max(av, bv)
            if tolerance is None:reasons.append('未明确声明总预算匹配容差')
            elif relative_gap>tolerance:reasons.append('两组包括出题、验证、解题和训练的总预算超出容差')
        else:reasons.append('缺少正值且完整的总计算预算')
        if a.get('evaluation_id') and b.get('evaluation_id'):
            pair = _pair(repo, a['evaluation_id'], b['evaluation_id'], 'checkpoint')
            reasons += pair.get('reasons', [])
            if not pair.get('full'):reasons.append('固定外部评测尚未完整配对')
            if a['result'].get('raw_value') is not None and b['result'].get('raw_value') is not None:
                raw_delta = (b['result']['raw_value'] - a['result']['raw_value'])*100
    reasons = list(dict.fromkeys(reasons));qualified = not reasons
    measured_any = any(a['result'].get('raw_value') is not None for a in arms)
    summary = {k:obj.get(k) for k in ('id', 'kind', 'title', 'scope', 'demo', 'hash', 'content_hash')}
    return {'scope':obj['scope'], 'comparison':summary, 'comparison_type':'pool_supply',
            'status':'measured' if qualified else 'incomparable' if measured_any else 'unmeasured',
            'comparison_qualified':qualified, 'qualification_reasons':reasons, 'arms':arms,
            'budget_relative_gap':relative_gap, 'budget_tolerance':obj['data'].get('budget_tolerance'),
            'delta':{'value':raw_delta if qualified else None, 'unit':'pp', 'raw_value':raw_delta,
                     'reason':'同初始条件与匹配总预算下的描述性宏平均 EM 差异' if qualified else '资格条件未满足；原始成绩保留，不能归因为自博弈供给收益'},
            'paired':{'expected':pair.get('expected'), 'paired':pair.get('paired'), 'controls':pair.get('controls')} if pair else None,
            'notes':['训练后题目分布是研究变量，不要求两组训练后的完整数据哈希相同。',
                     '总预算包含出题、验证、解题、训练及已记录其他成本，optimizer step 相同不足以证明计算预算相同。',
                     '单次对照只提供记录条件下的描述性差异，不能替代多种子实验或数据隔离核查。'],
            'models_executed':False, 'causal_claim':False}
