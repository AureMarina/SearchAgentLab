"""OPD observations, explicitly separate from optimizer and task success.

Native losses and search execution stay outside this read model.  All claims
refer to recorded, scoped evidence.  Distribution reports retain their own
definition and support; a sampled log-probability never creates a top-k metric.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from urllib.parse import quote

from ..core import ValidationError, NotFoundError
from ..inspection.repository import digest
from .views import page_args

OPD_KINDS = ('opd_failure', 'opd_teacher', 'opd_supervision', 'opd_diagnostic')
DIAGNOSTIC_TYPES = ('group', 'step', 'token', 'distribution', 'observation')
CONTEXTS = ('training_on_policy', 'evaluation', 'failure_probe', 'fixed_prefix_probe', 'unknown')
CONTEXT_FLAGS = ('teacher_hints', 'peer_answers', 'future_observations', 'groundtruth_visible')
_NUMBER_COLUMNS = {'teacher_log_probs', 'student_log_probs', 'gaps', 'task_advantages',
                   'opd_advantages', 'token_advantages', 'effective_advantages',
                   'task_mask', 'opd_mask', 'loss_mask', 'weights', 'raw_weights',
                   'effective_weights', 'full_log_probs', 'ablated_log_probs', 'residuals'}


def _need(d, key, typ, label='OPD'):
    if not isinstance(d.get(key), typ) or (typ is str and not d[key].strip()):
        raise ValidationError(label + '.' + key + ' is required (' + typ.__name__ + ')')


def _integer(v, label, minimum=0):
    if v is not None and (type(v) is not int or v < minimum):
        raise ValidationError(label + ' must be an integer >= ' + str(minimum) + ' or null')


def _boolean(v, label):
    if v is not None and type(v) is not bool:raise ValidationError(label + ' must be boolean or null')


def _numbers(v, label, mask=False):
    if v is None:return
    values = v if isinstance(v, list) else [v]
    if any(x is not None and (not isinstance(x, (int, float)) or isinstance(x, bool) and not mask
            or not math.isfinite(x) or mask and x < 0) for x in values):
        raise ValidationError(label + ' must contain finite numbers or null')


def _context(value, label):
    if not isinstance(value, dict):raise ValidationError(label + ' must be an object')
    for key in CONTEXT_FLAGS:_boolean(value.get(key), label + '.' + key)
    if 'privileged_information' in value and not isinstance(value['privileged_information'], list):
        raise ValidationError(label + '.privileged_information must be an array')


def _config(value):
    if not isinstance(value, dict):raise ValidationError('configuration.opd must be an object')
    for key in ('enabled', 'use_task_rewards', 'use_policy_gradient'):_boolean(value.get(key), 'opd.' + key)
    _numbers(value.get('distillation_loss_coef'), 'distillation_loss_coef')
    if isinstance(value.get('distillation_loss_coef'), list):raise ValidationError('distillation_loss_coef must be a scalar coefficient')
    if value.get('distillation_loss_coef') is not None and value['distillation_loss_coef'] < 0:
        raise ValidationError('distillation_loss_coef must be nonnegative')
    if 'teacher' in value:
        _need(value, 'teacher', dict)
        if 'context' in value['teacher']:_context(value['teacher']['context'], 'teacher.context')


def _verify(value):
    if not isinstance(value, dict):raise ValidationError('verification must be an object')
    if value.get('status') not in ('passed', 'failed', 'error', 'not_run', 'unknown'):
        raise ValidationError('Unknown verifier status')
    if value.get('status') in ('passed', 'failed') and not value.get('verifier'):
        raise ValidationError('A recorded verifier outcome requires verifier identity/version')


def _signal(value, label):
    if not isinstance(value, dict):raise ValidationError(label + ' must be an object')
    for key in ('raw', 'effective', 'original_advantage', 'effective_advantage', 'loss_weight',
                'raw_weight', 'effective_weight', 'reward', 'loss_value'):_numbers(value.get(key), label + '.' + key)
    for key in ('loss_mask', 'mask'):_numbers(value.get(key), label + '.' + key, mask=True)
    _integer(value.get('loss_mask_tokens'), label + '.loss_mask_tokens')
    if isinstance(value.get('loss_weight'), list):
        raise ValidationError(label + '.loss_weight is the scalar coefficient; token weights belong in effective_weight')
    _boolean(value.get('loss_active'), label + '.loss_active')
    if value.get('loss_active') is True and (value.get('loss_mask_tokens') == 0 or value.get('loss_weight') == 0):
        raise ValidationError(label + ' active signal conflicts with zero mask or coefficient')
    if value.get('objective') == 'advantage_reweight' and value.get('original_advantage') == 0:
        if value.get('effective_advantage') not in (None, 0) or value.get('loss_active') is True:
            raise ValidationError('Pure advantage_reweight cannot create a nonzero task advantage from zero')


def validate_opd_contract(o):
    """Validate new observations without rewriting old OPD payload contracts."""
    d = o['data']; kind = o['kind']
    if kind == 'method_profile':
        config = d.get('configuration', {})
        if 'opd' in config:_config(config['opd'])
        if 'distillation' in config:
            _need(config, 'distillation', dict)
            native = config['distillation']; loss = native.get('distillation_loss', {})
            if not isinstance(loss, dict):raise ValidationError('distillation_loss must be an object')
            _config({**loss, **({'enabled':native['enabled']} if 'enabled' in native else {})})
        return
    if kind == 'evaluation_binding':
        if 'opd_context' in d:_context(d['opd_context'], 'opd_context')
        return
    if kind == 'opd_recovery':
        from .opd_recovery import validate_recovery_contract
        validate_recovery_contract(d)
        return
    if kind not in OPD_KINDS:return
    for key in ('step', 'training_step'):_integer(d.get(key), key)
    for key in ('evaluation_id', 'trajectory_id', 'source_format', 'source_version'):
        if key in d and d[key] is not None:_need(d, key, str, kind)
    if 'signals' in d:
        _need(d, 'signals', dict, kind)
        for key in ('task', 'opd'):
            if key in d['signals']:_signal(d['signals'][key], 'signals.' + key)
    if 'masks' in d:
        _need(d, 'masks', dict, kind)
        for key in ('sample', 'step', 'token'):_numbers(d['masks'].get(key), 'masks.' + key, mask=True)
    if 'opd_configuration' in d:_config(d['opd_configuration'])
    if 'costs' in d:_need(d, 'costs', dict, kind)
    if 'signal_alignment' in d:
        _need(d, 'signal_alignment', dict, kind); alignment = d['signal_alignment']
        _boolean(alignment.get('complete'), 'signal_alignment.complete')
        if 'positions' in alignment:
            if not isinstance(alignment['positions'], list):raise ValidationError('signal_alignment.positions must be an array')
            for position in alignment['positions']:_integer(position, 'signal_alignment.position')
            if len(alignment['positions']) != len(set(alignment['positions'])):
                raise ValidationError('signal_alignment.positions must be distinct original token positions')
    if kind == 'opd_teacher':_context(d['context'], 'teacher.context')
    if kind != 'opd_diagnostic':return
    if d.get('diagnostic_type') not in DIAGNOSTIC_TYPES:raise ValidationError('Unknown OPD diagnostic_type')
    if d.get('context', 'unknown') not in CONTEXTS:raise ValidationError('Unknown OPD diagnostic context')
    if 'source_refs' in d and not isinstance(d['source_refs'], list):raise ValidationError('source_refs must be an array')
    if 'tokens' in d:
        _need(d, 'tokens', dict, kind)
        sizes = set()
        for key, values in d['tokens'].items():
            if not isinstance(values, list):raise ValidationError('tokens columns must be parallel arrays')
            if len(values) > 131072:raise ValidationError('Token record exceeds 131072; split explicitly with original positions')
            sizes.add(len(values))
            if key in _NUMBER_COLUMNS:_numbers(values, 'tokens.' + key, mask='mask' in key)
            elif key in ('token_ids', 'positions'):
                for value in values:_integer(value, 'tokens.' + key)
            elif key == 'tokens' and any(value is not None and not isinstance(value, str) for value in values):
                raise ValidationError('tokens.tokens must contain text or null')
        if len(sizes) > 1:raise ValidationError('OPD token columns must have equal lengths; preserve missing entries as null')
    if 'group' in d:
        _need(d, 'group', dict, kind); g = d['group']
        _integer(g.get('expected_size'), 'group.expected_size', 1)
        _boolean(g.get('samples_complete'), 'group.samples_complete')
        samples = g.get('samples', [])
        if not isinstance(samples, list):raise ValidationError('group.samples must be an array')
        ids = []
        for s in samples:
            if not isinstance(s, dict):raise ValidationError('group sample must be an object')
            _need(s, 'rollout_id', str, 'group.samples'); ids.append(s['rollout_id'])
            if 'verification' in s:_verify(s['verification'])
            for key in ('original_advantage', 'effective_advantage', 'task_reward'):_numbers(s.get(key), key)
            _boolean(s.get('teacher_supervision'), 'teacher_supervision')
        if len(ids) != len(set(ids)):raise ValidationError('group samples require distinct rollout_id values')
        if g.get('expected_size') is not None and len(samples) > g['expected_size']:
            raise ValidationError('group records exceed expected_size')
        if g.get('samples_complete') is True and (g.get('expected_size') is None or len(samples) != g['expected_size']):
            raise ValidationError('Complete OPD group requires exactly expected_size distinct trials')
    if 'reported_metrics' in d:
        if not isinstance(d['reported_metrics'], list):raise ValidationError('reported_metrics must be an array')
        for m in d['reported_metrics']:
            if not isinstance(m, dict):raise ValidationError('reported metric must be an object')
            _need(m, 'name', str, 'metric'); _numbers(m.get('value'), 'metric.value')
            if isinstance(m.get('value'), list):raise ValidationError('metric.value must be a scalar report or null')
            if 'support' in m:
                _need(m, 'support', dict, 'metric')
                for key in ('count', 'total', 'k'):_integer(m['support'].get(key), 'support.' + key)
                _boolean(m['support'].get('available'), 'support.available')
    if 'observation' in d:
        _need(d, 'observation', dict, kind)
        for key in ('has_future_observation', 'is_nll_selected'):_boolean(d['observation'].get(key), 'observation.' + key)
        for key in ('full', 'ablated', 'residual'):_numbers(d['observation'].get(key), 'observation.' + key)


def _formal_binding(repo, o):
    d = o['data']; eid = d.get('evaluation_id'); tid = d.get('trajectory_id')
    ref = d.get('trace_ref')
    if isinstance(ref, dict) and ref.get('trajectory'):
        if tid and tid != ref['trajectory']:raise ValidationError('Conflicting OPD trajectory references')
        tid = ref['trajectory']
    if not eid and not tid:return
    if repo.store is None:raise ValidationError('OPD runtime binding requires a registered evaluation/trajectory')
    if tid:
        trajectory = repo.store.trajectory(tid)
        if eid and eid != trajectory['evaluation_id']:raise ValidationError('OPD trajectory belongs to another evaluation')
        eid = trajectory['evaluation_id']
    ev = repo.store.evaluation(eid)
    if any(o.get('scope', {}).get(k, '') != value for k, value in
           {'project_id':ev['project_id'], 'experiment_id':ev['experiment_id'], 'run_id':ev['checkpoint']['run_id']}.items()):
        raise ValidationError('OPD runtime binding belongs to another experiment/run scope')
    if evidence_demo(repo, o) != bool(ev.get('demo') or ev['run'].get('demo') or ev['panel'].get('demo')):
        raise ValidationError('OPD synthetic and real evaluation evidence must remain separate')
    if o['kind'] == 'opd_diagnostic' and d.get('student_checkpoint_id') and d['student_checkpoint_id'] != ev['checkpoint']['id']:
        raise ValidationError('OPD diagnostic checkpoint does not match its evaluation')


def evidence_demo(repo, obj):
    """Use an explicit object label, or its registered run for older records.

    Early collectors omitted object.demo even inside a declared synthetic run.
    Missing is not an explicit real-data label.  Resolve this only for scope
    checks; immutable source payloads and hashes must remain unchanged.
    """
    if 'demo' in obj:return obj['demo'] is True
    run_id = obj.get('scope', {}).get('run_id')
    if run_id and repo.store:return repo.store._get('runs', run_id).get('demo') is True
    return False


def validate_opd_links(repo, o, pending):
    d = o['data']; kind = o['kind']
    if kind == 'opd_recovery':
        from .opd_recovery import validate_recovery_links
        validate_recovery_links(repo, o, pending)
        return
    if kind not in OPD_KINDS and kind not in ('training_use', 'training_update'):return
    from .schema import direct_refs, evidence_refs
    for oid, _, _ in direct_refs(o):
        target = repo._raw_object(oid, pending)
        opd_consumption = kind=='training_update' and target['kind']=='training_use' and any(
            repo._raw_object(aid, pending)['kind'] in OPD_KINDS for aid in target['data'].get('artifact_ids', []))
        if kind in OPD_KINDS or target['kind'] in OPD_KINDS or opd_consumption:
            if not repo._same_scope(o, target):raise ValidationError('OPD evidence link must belong to the same run scope')
            if evidence_demo(repo, o) != evidence_demo(repo, target):
                raise ValidationError('OPD synthetic and real evidence links must remain separate')
    if kind not in OPD_KINDS:return
    _formal_binding(repo, o)
    if kind == 'opd_diagnostic' and d.get('student_checkpoint_id'):
        if repo.store is None:raise ValidationError('OPD student checkpoint requires registration')
        cp = repo.store._get('checkpoints', d['student_checkpoint_id'])
        if cp['run_id'] != o.get('scope', {}).get('run_id', ''):
            raise ValidationError('OPD diagnostic checkpoint belongs to another run')
    for ref in evidence_refs(o):
        if ref.get('object_id'):
            target = repo._raw_object(ref['object_id'], pending)
            if not repo._same_scope(o, target):raise ValidationError('OPD source evidence belongs to another run scope')
            if evidence_demo(repo, o) != evidence_demo(repo, target):
                raise ValidationError('OPD source evidence cannot mix synthetic and real records')
        elif ref.get('trajectory'):
            _formal_binding(repo, {**o, 'data':{**d, 'trajectory_id':ref['trajectory']}})
            if d.get('trajectory_id') and ref['trajectory'] != d['trajectory_id']:
                raise ValidationError('OPD source evidence belongs to another trajectory')
    if kind == 'opd_diagnostic':
        failure_id = d.get('failure_id')
        for tid in d.get('teacher_ids', []):
            teacher = repo._raw_object(tid, pending)
            if failure_id and teacher['data']['failure_id'] != failure_id:
                raise ValidationError('OPD diagnostic teacher belongs to another failure')
        if d.get('supervision_id'):
            supervision = repo._raw_object(d['supervision_id'], pending)
            if failure_id and supervision['data']['failure_id'] != failure_id:
                raise ValidationError('OPD diagnostic supervision belongs to another failure')


def _small(value, depth=0):
    """Bound list projections even when imported metadata contains huge arrays."""
    if depth > 5:return {'detail_only':True}
    if isinstance(value, str):return value[:400]
    if isinstance(value, list):
        if len(value) > 32:return {'count':len(value), 'detail_only':True}
        return [_small(v, depth + 1) for v in value]
    if isinstance(value, dict):return {k:_small(v, depth + 1) for k, v in list(value.items())[:40]}
    return value


def _numeric(value):
    if not isinstance(value, list):return value
    known = [v for v in value if v is not None]
    numeric = [v for v in known if isinstance(v, (int, float))]
    return {'count':len(value), 'known':len(numeric), 'unknown':len(value)-len(numeric),
            'nonzero':sum(v != 0 for v in numeric), 'min':min(numeric) if numeric else None,
            'max':max(numeric) if numeric else None, 'mean':sum(numeric)/len(numeric) if numeric else None}


def _flags(values):
    yes = sum(v is True for v in values); no = sum(v is False for v in values)
    return {'yes':yes, 'no':no, 'unknown':len(values)-yes-no, 'denominator':yes+no,
            'expected':len(values), 'value':yes/(yes+no) if yes+no else None}


def _nonzero(value):
    if value is None:return None
    values = value if isinstance(value, list) else [value]
    if not values or any(v is None for v in values):return None
    return any(v != 0 for v in values)


def _surviving_signal(value, common):
    """Only compare numeric vectors whose positions are explicitly aligned."""
    effective = value.get('effective', value.get('effective_advantage'))
    if effective is None:return None, '实际 effective signal 未记录'
    arrays = []
    for candidate in (value.get('loss_mask'), value.get('mask'), value.get('effective_weight'), common.get('token')):
        if isinstance(candidate, list):arrays.append(candidate)
        elif candidate is not None and candidate == 0:return False, '有效 mask 或 token 权重为零'
    for key in ('sample', 'step'):
        candidate = common.get(key)
        if isinstance(candidate, list):
            if _nonzero(candidate) is False:return False, '所记录的选择 mask 为零'
            # These arrays index samples/steps, not implicitly token positions.
            if len(candidate)>1:return None, 'sample / step 向量尚未映射到此 token 支持'
    if any(any(v is None for v in a) for a in arrays):return None, 'token mask / 权重支持存在缺失'
    if isinstance(effective, list):
        if not effective or any(v is None for v in effective):return None, 'effective token 支持不完整'
        if any(len(a)!=len(effective) for a in arrays):return None, 'effective signal 与 mask / 权重支持长度不一致'
        survives = any(v!=0 and all(a[i]!=0 for a in arrays) for i, v in enumerate(effective))
        return survives, '非零 effective signal 仅在所记录的相同 token mask / 权重支持上判断'
    if _nonzero(effective) is False:return False, '记录的 effective signal 为零'
    if arrays:
        if any(not a for a in arrays):return None, 'token 支持为空'
        if any(all(v==0 for v in a) for a in arrays):return False, '有效 token mask / 权重为零'
        if any(any(v==0 for v in a) for a in arrays):
            return None, '标量 effective 聚合值无法定位到部分保留的 token 支持'
    return True, '所记录 effective signal 非零'


def _term(value, masks=None):
    if not isinstance(value, dict):return {'active':None, 'stage':'unknown', 'reason':'未记录此学习项'}
    result = {key:_numeric(v) if isinstance(v, list) else _small(v) for key, v in value.items()
              if key in ('objective', 'raw', 'effective', 'original_advantage', 'effective_advantage', 'reward',
                         'loss_weight', 'raw_weight', 'effective_weight', 'loss_mask_tokens', 'loss_active', 'source', 'definition')}
    active = None; reason = '未记录实际 effective signal、mask 或系数'; stage = 'raw_only' if value.get('raw') is not None else 'unknown'
    mask = value.get('loss_mask_tokens')
    if mask is None and isinstance(value.get('loss_mask'), list) and all(v is not None for v in value['loss_mask']):
        mask = sum(v != 0 for v in value['loss_mask'])
    result['loss_mask_tokens'] = mask
    weight = value.get('loss_weight'); common = masks or {}
    surviving, support_reason = _surviving_signal(value, common)
    if value.get('loss_active') is False:active = False; reason = '训练器明确记录此学习项未生效'
    elif any(_nonzero(common.get(k)) is False for k in ('sample', 'step', 'token') if k in common):
        active = False; reason = '记录的 sample / step / token mask 为零'
    elif mask == 0 or weight == 0:active = False; reason = '记录的有效 mask 或 loss 系数为零'
    elif value.get('objective') == 'advantage_reweight' and value.get('original_advantage') == 0:
        active = False; reason = '纯乘法重加权不能恢复零任务 advantage；辅助项另列'
    elif surviving is False:active = False; reason = support_reason+'；不推断其他优化器项'
    elif any(_nonzero(common.get(k)) is None for k in ('sample', 'step', 'token') if k in common):
        reason = '实际选择 mask 支持不完整；effective 值不能补足缺失的 mask'
    elif surviving is None:reason = support_reason
    elif surviving is True and mask is not None and weight is not None:
        active = True; reason = '记录的 effective signal、mask 和系数非零；不等于梯度贡献或任务修复'
    if active is False:stage = 'inactive'
    elif active is True:stage = 'effective_recorded'
    result.update(active=active, stage=stage, reason=reason)
    return result


def _group(g):
    samples = g.get('samples', []); outcomes = []
    for s in samples:
        v = s.get('verification', {})
        outcomes.append(v.get('status') == 'passed' if v.get('status') in ('passed', 'failed')
                        and v.get('method') in ('environment', 'scorer') and v.get('verifier') else None)
    complete = g.get('samples_complete') is True and g.get('expected_size') == len(samples) and bool(samples)
    classification = 'unknown'
    if complete and all(v is not None for v in outcomes):
        classification = 'all_correct' if all(outcomes) else 'all_wrong' if not any(outcomes) else 'mixed'
    advantage = _flags([_nonzero(s.get('original_advantage')) for s in samples])
    teacher = _flags([s.get('teacher_supervision') for s in samples])
    missing = max(0, (g.get('expected_size') or len(samples)) - len(samples))
    for value in (advantage, teacher):
        value['unknown'] += missing; value['expected'] += missing
    return {'classification':classification, 'expected_size':g.get('expected_size'), 'recorded_size':len(samples),
            'verified_size':sum(v is not None for v in outcomes), 'complete':complete,
            'task_advantage':{'nonzero':advantage['yes'], 'zero':advantage['no'], **{k:advantage[k] for k in ('unknown', 'denominator', 'expected', 'value')}},
            'teacher_coverage':teacher, 'correct':sum(v is True for v in outcomes), 'wrong':sum(v is False for v in outcomes),
            'reason':'按完整 verifier outcomes 分类；任务 reward 不自动当作正确性'}


def _metric(m):
    p = _small(m); p['reported_value'] = m.get('value'); support = m.get('support', {})
    value = m.get('value'); status = 'reported_only'; reason = '保留来源口径；未具备定义、版本和有效支持的完整声明'
    named_topk = ('overlap' in m['name'].lower() or 'top_k' in m['name'].lower() or 'top-k' in m['name'].lower())
    if value is None:status = 'unmeasured'; reason = '来源未记录数值'
    elif support.get('available') is False or support.get('count') == 0:
        status = 'uninterpretable'; reason = '有效分布支持不可用或为零；来源数值不能解释为已对齐'
    elif named_topk and support.get('kind') in ('sampled_token', 'sampled_logprob', 'sampled_tokens'):
        status = 'uninterpretable'; reason = '采样 token logprob 无法提供 top-k overlap 分布支持'
    elif m.get('definition') and (m.get('definition_version') or m.get('version')) and m.get('source') and support.get('available') is True and type(support.get('count')) is int and support['count'] > 0:
        if not named_topk or type(support.get('k')) is int and support['k'] > 0 and support.get('normalization'):
            status = 'recorded'; reason = '沿用所记录的定义、实现版本、归一化与有效支持；不同口径不合并'
    p.update(value=None if status in ('uninterpretable', 'unmeasured') else value, status=status, missing_reason=reason,
             interpretation='mechanism_only', task_success_claim=False)
    return p


def _relation(d):
    """Descriptive relation on explicitly aligned final token supports only."""
    alignment = d.get('signal_alignment', {})
    positions = alignment.get('positions'); total = len(positions) if isinstance(positions, list) else None
    unknown = {'value':None, 'numerator':None, 'denominator':None, 'status':'unmeasured'}
    result = {'status':'unmeasured', 'total':total, 'shared_support_count':None, 'shared_nonzero':None,
              'shared_zero':None, 'excluded':None, 'unknown':total, 'sign_conflict':{**unknown},
              'pearson':{**unknown}, 'position_unit':alignment.get('position_unit'), 'source':_small(alignment.get('source')),
              'definition':_small(alignment.get('definition')), 'task_repair_claim':False, 'causal_credit_claim':False,
              'reason':'需要实际 task / OPD effective 向量、最终 mask 与显式相同 token 位置'}
    if alignment.get('kind') != 'token' or alignment.get('complete') is not True or not positions or any(v is None for v in positions):return result
    if not alignment.get('position_unit') or not alignment.get('source') or not alignment.get('definition'):
        result['reason'] = 'token 位置单位、来源或 effective 信号定义未记录';return result
    task = d.get('signals', {}).get('task', {}); opd = d.get('signals', {}).get('opd', {})
    a, b = task.get('effective'), opd.get('effective')
    am, bm = task.get('loss_mask'), opd.get('loss_mask')
    arrays = [a, b, am, bm]
    if any(not isinstance(v, list) or len(v)!=total for v in arrays):
        result['reason'] = 'task / OPD effective 与最终 mask 未按相同原始 token 位置完整对齐';return result
    common = d.get('masks', {})
    token_mask = common.get('token')
    if token_mask is not None:
        if not isinstance(token_mask, list) or len(token_mask)!=total:
            result['reason'] = '公共 token mask 的支持与声明位置不一致';return result
        arrays.append(token_mask)
    for term in (task, opd):
        if type(term.get('loss_weight')) not in (int, float):
            result['reason'] = '实际学习项标量系数未记录';return result
        weights = term.get('effective_weight')
        if isinstance(weights, list):
            if len(weights)!=total:result['reason'] = 'effective weight 与 token 支持不一致';return result
            arrays.append(weights)
        elif weights is not None and type(weights) not in (int, float):
            result['reason'] = 'effective weight 支持未记录';return result
    missing = sum(any(values[i] is None for values in arrays) for i in range(total))
    if missing:
        result.update(unknown=missing, reason='共同支持存在未知 token 值或 mask；不在任意截取的已知子集上报告相关性');return result
    if any(isinstance(common.get(k), list) or common.get(k) is None for k in ('sample', 'step') if k in common):
        result['reason'] = 'sample / step 选择尚未明确映射为本记录的最终 token 支持';return result
    enabled = all(term.get('loss_active') is not False and term['loss_weight']!=0 and term.get('effective_weight')!=0 for term in (task, opd))
    enabled = enabled and all(common.get(k)!=0 for k in ('sample', 'step') if k in common)
    supports = [am, bm]
    if token_mask is not None:supports.append(token_mask)
    supports.extend(term['effective_weight'] for term in (task, opd) if isinstance(term.get('effective_weight'), list))
    indices = [i for i in range(total) if enabled and all(mask[i]!=0 for mask in supports)]
    nonzero = [i for i in indices if a[i]!=0 and b[i]!=0]
    conflicts = sum((a[i]>0)!=(b[i]>0) for i in nonzero)
    pearson = None; pearson_reason = '至少需要两个共同支持位置与两侧非零方差'
    if len(indices)>=2:
        av = [a[i] for i in indices]; bv = [b[i] for i in indices]
        scale_a = max(abs(x) for x in av) or 1; scale_b = max(abs(x) for x in bv) or 1
        av = [x/scale_a for x in av];bv = [x/scale_b for x in bv]
        ma = sum(av)/len(av);mb = sum(bv)/len(bv)
        av = [x-ma for x in av];bv = [x-mb for x in bv]
        va = sum(x*x for x in av);vb = sum(x*x for x in bv)
        if va>0 and vb>0:
            pearson = max(-1.0, min(1.0, sum(x*y for x, y in zip(av, bv))/math.sqrt(va*vb)))
            pearson_reason = '共同最终 mask 上的 effective 值 Pearson（含零值）；仅为描述性关系'
    result.update(status='measured', shared_support_count=len(indices), shared_nonzero=len(nonzero),
                  shared_zero=len(indices)-len(nonzero), excluded=total-len(indices), unknown=0,
                  sign_conflict={'value':conflicts/len(nonzero) if nonzero else None, 'numerator':conflicts,
                                 'denominator':len(nonzero), 'status':'measured' if nonzero else 'uninterpretable',
                                 'reason':'分母仅含两侧 effective 值同时非零、final mask 同时有效的 token'},
                  pearson={'value':pearson, 'denominator':len(indices), 'status':'measured' if pearson is not None else 'uninterpretable', 'reason':pearson_reason},
                  reason='按记录的同一 token 位置与最终 mask 比较实际 effective 信号；不代表动作价值或修复效果')
    return result


def diagnostic_projection(repo, o):
    d = o['data']; p = {key:_small(d[key]) for key in ('diagnostic_type', 'context', 'source_format', 'source_version',
         'definition_version', 'step', 'training_step', 'sample_id', 'group_id', 'traj_uid', 'failure_id', 'teacher_ids',
         'supervision_id', 'student_checkpoint_id', 'evaluation_id', 'trajectory_id', 'coverage') if key in d}
    if p.get('trajectory_id') and not p.get('evaluation_id') and repo.store:
        p['evaluation_id'] = repo.store.trajectory(p['trajectory_id'])['evaluation_id']
    p['recorded_steps'] = [d['step']] if type(d.get('step')) is int else []
    masks = d.get('masks', {})
    p['masks'] = {k:_numeric(v) for k, v in masks.items() if k in ('sample', 'step', 'token')}
    p['signals'] = {key:_term(d.get('signals', {}).get(key), masks) for key in ('task', 'opd')}
    p['relation'] = _relation(d)
    if 'group' in d:p['group'] = _group(d['group'])
    if 'observation' in d:
        observation = d['observation']
        p['observation'] = {k:_numeric(v) if k in ('full', 'ablated', 'residual') else _small(v) for k, v in observation.items()}
        p['observation']['interpretation'] = '来源记录的观察残差；不推断实际 mask、有效更新或恢复成功'
    p['reported_metrics'] = [_metric(m) for m in d.get('reported_metrics', [])[:128]]
    p['reported_metric_count'] = len(d.get('reported_metrics', []))
    p['token_count'] = len(next(iter(d.get('tokens', {}).values()), []))
    p['token_columns'] = list(d.get('tokens', {}))
    p['costs'] = _small(d.get('costs', {}))
    p['claims'] = {'optimizer_update':False, 'task_repair':False, 'causal_credit':False}
    return p


def opd_projection(repo, o, base):
    """Add provenance to existing event summaries without changing old metrics."""
    d = o['data']; p = {**base}
    for key in ('evaluation_id', 'trajectory_id', 'training_step', 'source_format', 'source_version',
                'teacher_revision', 'teacher_construction', 'student_revision', 'costs'):
        if key in d:p[key] = _small(d[key])
    if p.get('trajectory_id') and not p.get('evaluation_id') and repo.store:
        p['evaluation_id'] = repo.store.trajectory(p['trajectory_id'])['evaluation_id']
    p['recorded_steps'] = [v for v in (d.get('step'), d.get('failure_step')) if type(v) is int]
    if 'context' in d:p['teacher_context'] = _small(d['context'])
    if 'signals' in d:
        p['signals'] = {key:_term(d['signals'].get(key), d.get('masks')) for key in ('task', 'opd')}
        p['masks'] = {key:_numeric(value) for key, value in d.get('masks', {}).items()}
    return p


def _scope(repo, experiment_id, run_id):
    if not experiment_id:raise ValidationError('An explicit experiment_id is required')
    scope = repo._scope({'experiment_id':experiment_id, **({'run_id':run_id} if run_id else {})})
    scope['run_id'] = run_id or ''
    return scope


def _read(repo, scope, kinds):
    from .assessment import _read as read
    return read(repo, scope, kinds)


def _item(row):
    return {**{k:row.get(k) for k in ('id', 'kind', 'title', 'usage', 'scope', 'demo', 'content_hash')},
            'assessment':row.get('assessment', {}), 'body_loaded':False}


def _page(items, offset, limit):
    return {'items':items[offset:offset + limit], 'total':len(items), 'offset':offset, 'limit':limit,
            'has_more':offset + limit < len(items)}


def opd_configuration(repo, scope):
    profiles = repo.objects(scope, kind='method_profile'); origin = 'run' if scope.get('run_id') else 'experiment'
    if not profiles and scope.get('run_id'):
        profiles = repo.objects({**scope, 'run_id':''}, kind='method_profile'); origin = 'experiment'
    declarations = []
    for profile in profiles:
        if 'opd' not in profile['data'].get('families', []):continue
        cfg = profile['data'].get('configuration', {})
        explicit = cfg.get('opd')
        if explicit is not None:declarations.append({'profile_id':profile['id'], 'source':'configuration.opd', **_small(explicit)})
        native = cfg.get('distillation')
        if isinstance(native, dict):
            loss = native.get('distillation_loss', {})
            declarations.append({'profile_id':profile['id'], 'source':'configuration.distillation',
                                 **_small(loss), **({'enabled':native['enabled']} if 'enabled' in native else {})})
    values = {}; conflicts = []
    keys = ('enabled', 'use_task_rewards', 'use_policy_gradient', 'distillation_loss_coef', 'teacher')
    for key in keys:
        observed = [d[key] for d in declarations if key in d]
        unique = {digest(v) for v in observed}
        values[key] = observed[0] if len(unique) == 1 else None
        if len(unique) > 1:conflicts.append(key)
    return {'status':'conflicting' if conflicts else 'recorded' if declarations else 'not_recorded', 'origin':origin if declarations else 'not_recorded',
            'profile_ids':[p['id'] for p in profiles], 'values':values, 'declarations':declarations, 'conflicts':conflicts,
            'note':'use_task_rewards 控制任务奖励项；use_policy_gradient 控制蒸馏信号路径。缺省值不按框架默认配置补造。'}


def _coverage(rows):
    diagnostics = [r for r in rows if r['kind'] == 'opd_diagnostic']
    training_groups = [r for r in diagnostics if r['assessment'].get('context')=='training_on_policy' and 'group' in r['assessment']]
    identities = {}; unidentified = []
    for row in training_groups:
        p = row['assessment']
        if not p.get('group_id') or type(p.get('training_step')) is not int:
            unidentified.append(row['id']); continue
        identities.setdefault((p['training_step'], p['group_id']), []).append(row)
    duplicates = [rs for rs in identities.values() if len(rs)>1]
    groups = [rs[0]['assessment']['group'] for rs in identities.values() if len(rs)==1]
    categories = Counter(g['classification'] for g in groups)
    classification = {k:categories[k] for k in ('all_correct', 'all_wrong', 'mixed', 'unknown')}
    classification['unknown'] += len(duplicates)
    classification['denominator'] = len(identities); classification['known_denominator'] = len(groups)-categories['unknown']
    classification['unit'] = 'unique_training_step_and_group_id'
    adv = {'nonzero':0, 'zero':0, 'unknown':0, 'denominator':0, 'expected':0}
    teacher = {'yes':0, 'no':0, 'unknown':0, 'denominator':0, 'expected':0}
    for g in groups:
        for key in adv:adv[key] += g['task_advantage'][key]
        for key in teacher:teacher[key] += g['teacher_coverage'][key]
    adv['value'] = adv['nonzero']/adv['denominator'] if adv['denominator'] else None
    teacher['value'] = teacher['yes']/teacher['denominator'] if teacher['denominator'] else None
    ids = {r['id'] for r in rows if r['kind'] in OPD_KINDS}
    uses = [r for r in rows if r['kind']=='training_use' and ids.intersection(r.get('assessment', {}).get('artifact_ids', []))]
    sampled = {r['id'] for r in uses if r['assessment'].get('status')=='sampled'}
    updates = [r for r in rows if r['kind']=='training_update' and sampled.intersection(r.get('assessment', {}).get('consumption_ids', []))]
    associated = {aid for r in uses for aid in r['assessment'].get('artifact_ids', [])}
    return {'diagnostic_records':len(diagnostics), 'failure_records':sum(r['kind']=='opd_failure' for r in rows),
            'teacher_records':sum(r['kind']=='opd_teacher' for r in rows), 'supervision_records':sum(r['kind']=='opd_supervision' for r in rows),
            'completed_updates':sum(r['assessment'].get('status')=='completed' for r in updates),
            'sampled_records':len(sampled), 'update_association_unknown':len(ids-associated),
            'group_classification':classification, 'task_advantage':adv, 'teacher_supervision':teacher,
            'training_group_records':len(training_groups),
            'evaluation_group_records':sum('group' in r['assessment'] and r['assessment'].get('context')!='training_on_policy' for r in diagnostics),
            'unidentified_group_records':unidentified,
            'duplicate_group_records':[[r['id'] for r in rs] for rs in duplicates],
            'group_aggregation':'只有训练上下文且 training_step + group_id 唯一的完整观测可汇总；重复导出不择优、不重复计数，缺身份记录单列。',
            'scope_complete':None, 'note':'统计已登记记录；不据此推算未导出的训练批次覆盖率。'}


def _training_for(oid, rows):
    uses = [r for r in rows if r['kind']=='training_use' and oid in r.get('assessment', {}).get('artifact_ids', [])]
    sampled = [r for r in uses if r['assessment'].get('status')=='sampled']
    updates = [r for r in rows if r['kind']=='training_update' and set(r.get('assessment', {}).get('consumption_ids', [])).intersection(x['id'] for x in sampled)]
    complete = [r for r in updates if r['assessment'].get('status')=='completed']
    failed = bool(updates) and all(r['assessment'].get('status') in ('failed', 'skipped') for r in updates)
    return {'training_use_ids':[r['id'] for r in uses], 'sampled_use_ids':[r['id'] for r in sampled],
            'update_ids':[r['id'] for r in updates], 'completed_update_ids':[r['id'] for r in complete],
            'sampled':True if sampled else False if uses else None,
            'update_completed':True if complete else False if failed else None,
            'update_status':'completed' if complete else 'failed' if failed else 'pending' if updates else 'not_recorded',
            'causal_contribution':False,
            'note':'仅证明此产物显式关联 sampled 与 completed 更新；不证明逐 token 梯度贡献或任务修复。'}


def opd_training(repo, experiment_id, run_id='', offset=0, limit=25):
    scope = _scope(repo, experiment_id, run_id); offset, limit = page_args(offset, limit)
    rows = _read(repo, scope, OPD_KINDS + ('training_use', 'training_update', 'opd_recovery'))
    config = opd_configuration(repo, scope); items = []
    for row in rows:
        if row['kind'] not in OPD_KINDS:continue
        item = _item(row); item['training'] = _training_for(row['id'], rows)
        if config['values']['use_task_rewards'] is False and row['kind']=='opd_diagnostic':
            # Configuration does not erase observed tensors; it only qualifies
            # whether the task-reward term is enabled in this declared recipe.
            item['task_term_enabled'] = False
        items.append(item)
    metrics = [{**m, 'record_id':r['id'], 'training_step':r['assessment'].get('training_step'), 'demo':bool(r.get('demo'))}
               for r in rows for m in r.get('assessment', {}).get('reported_metrics', [])]
    costs = []
    for row in rows:
        for stage, values in row.get('assessment', {}).get('costs', {}).items():
            costs.append({'record_id':row['id'], 'stage':stage, 'values':values})
    recovery = [_item(r) for r in rows if r['kind']=='opd_recovery']
    return {'scope':scope, 'status':'recorded' if items or recovery else 'not_recorded', **_page(items, offset, limit),
            'configuration':config, 'coverage':_coverage(rows), 'metrics':metrics,
            'costs':{'records':costs, 'aggregation':'not_pooled', 'note':'rollout、teacher 推理、teacher 环境交互分别保留；未声明完整边界的成本不相加。'},
            'recovery_records':recovery,
            'notes':['监督产生、有效信号、训练消费、完成更新、独立自主效果分别记录。',
                     '同名指标保留定义 / 版本 / k / 支持 / 归一化，不跨口径合并。',
                     '采样 token gap 或非零 credit 不构成失败修复证据。']}


def _teacher_condition(repo, scope, evaluation_id):
    result = {'status':'unknown', 'autonomous':None, 'independent':None, 'visible_information':[], 'context':{},
              'teacher':None, 'reason':'请选择一次有显式执行绑定的评测；缺日志不表示未使用教师信息'}
    if not evaluation_id:return result
    ev = repo.store.evaluation(evaluation_id)
    bindings = [o for o in repo.objects(scope, kind='evaluation_binding') if o['data']['evaluation_id']==evaluation_id]
    if not bindings:return result
    d = bindings[0]['data']; ctx = d.get('opd_context', {})
    result.update(binding_id=bindings[0]['id'], context=_small(ctx), independent=d.get('independent'),
                  declared_autonomous=d.get('autonomous'), teacher=_small(ctx.get('teacher')),
                  binding_origin=d.get('binding_origin'))
    visible = list(ctx.get('privileged_information', []))
    visible.extend(k for k in CONTEXT_FLAGS if ctx.get(k) is True)
    result['visible_information'] = _small(visible)
    if visible or ev.get('protocol', {}).get('groundtruth_to_agent') is True or d.get('autonomous') is False:
        result.update(status='privileged', autonomous=False, reason='评测记录明确包含特权信息或声明非自主执行')
    elif all(ctx.get(k) is False for k in CONTEXT_FLAGS) and ctx.get('privileged_information')==[] and d.get('autonomous') is True and d.get('binding_origin')=='recorded_execution' and ev.get('protocol', {}).get('groundtruth_to_agent') is False and ev.get('protocol', {}).get('mode')=='inference_only':
        result.update(status='recorded', autonomous=True, reason='执行绑定明确记录撤去四类训练特权信息；这是记录声明，不代替输入或数据隔离审计')
    else:result.update(status='partial', reason='自主声明或特权信息撤去条件不完整；不自动认为已撤去教师信息')
    return result


def opd_runtime(repo, experiment_id, run_id='', evaluation_id='', trajectory_id='', offset=0, limit=25, step=None):
    scope = _scope(repo, experiment_id, run_id); offset, limit = page_args(offset, limit)
    if step is not None and step!='':
        try:step = int(step)
        except (ValueError, TypeError) as exc:raise ValidationError('OPD step must be a nonnegative integer') from exc
        _integer(step, 'step')
    else:step = None
    if evaluation_id or trajectory_id:
        if repo.store is None:raise ValidationError('OPD runtime filters require a registered evaluation')
        if trajectory_id:
            trajectory = repo.store.trajectory(trajectory_id)
            if evaluation_id and evaluation_id != trajectory['evaluation_id']:raise ValidationError('OPD trajectory is outside selected evaluation')
            evaluation_id = trajectory['evaluation_id']
        ev = repo.store.evaluation(evaluation_id)
        if ev['experiment_id'] != scope['experiment_id'] or ev['checkpoint']['run_id'] != scope['run_id']:
            raise ValidationError('OPD runtime evaluation is outside selected experiment/run')
    rows = _read(repo, scope, OPD_KINDS)
    rows = [r for r in rows if r.get('assessment', {}).get('context')!='training_on_policy']
    if evaluation_id:rows = [r for r in rows if r.get('assessment', {}).get('evaluation_id')==evaluation_id]
    if trajectory_id:rows = [r for r in rows if r.get('assessment', {}).get('trajectory_id')==trajectory_id]
    if step is not None:rows = [r for r in rows if step in r.get('assessment', {}).get('recorded_steps', [])]
    return {'scope':scope, 'status':'recorded' if rows else 'not_recorded', **_page([_item(r) for r in rows], offset, limit),
            'coverage':_coverage(rows), 'filters':{'evaluation_id':evaluation_id, 'trajectory_id':trajectory_id, 'step':step},
            'teacher_condition':_teacher_condition(repo, scope, evaluation_id),
            'notes':['仅展示当前评测 / 轨迹 / 原始步骤明确关联的证据；同题号不自动跨运行匹配。',
                     '此接口只读取已保存记录，不调用教师、环境或训练器。']}


def _detail_data(d):
    omitted = []
    def visit(value, path):
        if path in ('/tokens', '/raw_payload', '/native_payload', '/raw', '/source_payload'):
            omitted.append(path); return {'detail_only':True, 'raw_pointer':path}
        if isinstance(value, list):
            if len(value)>100:
                omitted.append(path); return {'count':len(value), 'detail_only':True, 'raw_pointer':path}
            return [visit(v, path+'/'+str(i)) for i, v in enumerate(value)]
        if isinstance(value, dict):return {k:visit(v, path+'/'+k) for k, v in value.items()}
        return value
    return visit(d, ''), omitted


def opd_record(repo, oid):
    o = repo.object(oid)
    if o['kind'] not in OPD_KINDS:raise ValidationError('Expected an OPD failure, teacher, supervision or diagnostic')
    from .projection import object_projection
    p = object_projection(repo, o); data, omitted = _detail_data(o['data'])
    rows = _read(repo, {**o.get('scope', {}), 'run_id':o.get('scope', {}).get('run_id', '')}, ('training_use', 'training_update'))
    return {'id':oid, 'scope':o.get('scope', {}), 'object':{**o, 'data':data}, 'diagnostic':p,
            'training':_training_for(oid, rows), 'body_loaded':True, 'omitted_fields':omitted,
            'raw_object_url':'/api/methods/object?id='+quote(oid, safe=''),
            'tokens_url':'/api/methods/opd-tokens?id='+quote(oid, safe=''),
            'notes':['完整原始对象保留；大型向量按需分页读取，不放入初始详情或列表摘要。',
                     '概率差、观察残差、credit 变化均为诊断信号，不是因果动作价值或任务成功。']}


def opd_tokens(repo, oid, offset=0, limit=50):
    offset, limit = page_args(offset, limit); o = repo.object(oid)
    if o['kind'] != 'opd_diagnostic':raise ValidationError('Token view requires an opd_diagnostic')
    columns = o['data'].get('tokens', {}); total = len(next(iter(columns.values()), []))
    items = [{'index':i, **{key:values[i] for key, values in columns.items()}} for i in range(offset, min(total, offset+limit))]
    return {'scope':o.get('scope', {}), 'object_id':oid, 'columns':list(columns), 'items':items, 'total':total,
            'offset':offset, 'limit':limit, 'has_more':offset+limit<total, 'coverage':_small(o['data'].get('coverage', {})),
            'notes':['保留来源列名和原始位置；未导出的 mask / token 列保持缺失。',
                     'token_advantages 未明确目标来源时，不自动重命名为任务或 OPD advantage。']}
