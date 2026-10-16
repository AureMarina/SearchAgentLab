"""Self-play supply, learning opportunities and frozen solver observations.

Only recorded evidence is read here.  Generating a question, admitting it to a
pool, sampling a rollout and completing an update are distinct events.  None of
these events establishes a causal improvement in the solver.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import math

from ..core import ValidationError
from ..inspection.repository import digest
from .schema import identifier

SELFPLAY_KINDS = ('selfplay_cycle', 'generated_question', 'proposal', 'question_review',
                  'solver_attempt', 'selfplay_reference', 'selfplay_pool_event', 'selfplay_comparison')
READ_KINDS = SELFPLAY_KINDS + ('training_use', 'training_update', 'evaluation_binding')
VALIDATION_STATUSES = ('passed', 'failed', 'error', 'not_run')


def _dict(value, label):
    if not isinstance(value, dict):raise ValidationError(label + ' must be an object')
    return value


def _number(value):return type(value) in (int, float) and math.isfinite(value)


def _integer(value, label, minimum=0):
    if value is not None and (type(value) is not int or value<minimum):
        raise ValidationError(label + ' must be an integer >= ' + str(minimum))


def _fingerprint(value):
    import re
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _verification(value, label):
    _dict(value, label)
    if value.get('status') not in VALIDATION_STATUSES:
        raise ValidationError(label + '.status is passed/failed/error/not_run')
    if value['status'] in ('passed', 'failed') and not value.get('verifier'):
        raise ValidationError(label + ' passed/failed requires verifier identity/version')


def _signal(value, label):
    """Validate aligned evidence without converting rewards into advantages."""
    _dict(value, label)
    from .contracts import validate_iteration_contract
    scalar = {k:v for k,v in value.items() if k!='effective_advantages'}
    validate_iteration_contract({'kind':'training_use', 'data':{'signal':scalar}})
    advantages = value.get('effective_advantages')
    if advantages is not None:
        if not isinstance(advantages, list) or any(not _number(x) for x in advantages):
            raise ValidationError(label + '.effective_advantages must be finite numbers')
        if value.get('effective_advantage') is not None:
            raise ValidationError('Choose scalar effective_advantage or aligned effective_advantages, not both')
        mask = value.get('loss_mask')
        if mask is not None and len(mask)!=len(advantages):
            raise ValidationError('effective_advantages and loss_mask must align position by position')
        if value.get('loss_active') is True and mask is not None and not any(a!=0 and m!=0 for a,m in zip(advantages, mask)):
            raise ValidationError('loss_active conflicts with zero aligned advantage × mask')


def validate_selfplay_contract(obj):
    kind, d = obj['kind'], obj['data']
    if kind=='selfplay_cycle':
        for key in ('round_index', 'round'):
            if key in d and isinstance(d[key], (int, float)):_integer(d[key], key)
        if 'coverage' in d:
            for key in ('pool_complete', 'attempts_complete', 'updates_complete'):
                if key in d['coverage'] and type(d['coverage'][key]) is not bool:
                    raise ValidationError('coverage.' + key + ' must be boolean')
    elif kind=='generated_question':
        if 'generation_cycle_id' in d:identifier(d['generation_cycle_id'], 'generation_cycle_id')
        if 'hop_count' in d:_integer(d['hop_count'], 'hop_count', 1)
    elif kind=='question_review' and 'checks' in d:
        checks = _dict(d['checks'], 'checks')
        for key, value in checks.items():_verification(value, 'checks.' + key)
    elif kind=='selfplay_reference':
        if not isinstance(d.get('solver_revision'), str) or not d['solver_revision'].strip():
            raise ValidationError('selfplay_reference requires solver_revision')
        for key in ('harness_sha256', 'solver_weights_sha256'):
            if key in d and not _fingerprint(d[key]):raise ValidationError(key + ' must be SHA256')
        if 'sampling' in d:
            _dict(d['sampling'], 'sampling')
            for key in ('temperature', 'top_p'):
                if key in d['sampling'] and (not _number(d['sampling'][key]) or d['sampling'][key]<0):
                    raise ValidationError('sampling.' + key + ' must be nonnegative')
            if d['sampling'].get('top_p', 1)>1:raise ValidationError('top_p must be <= 1')
            _integer(d['sampling'].get('max_output_tokens'), 'max_output_tokens', 1)
        _integer(d.get('attempts_per_question'), 'attempts_per_question', 1)
        if d.get('attempts_per_question', 1)>10000:raise ValidationError('attempts_per_question exceeds 10000')
    elif kind=='selfplay_pool_event':
        for key in ('question_id', 'cycle_id', 'pool_id'):identifier(d.get(key), key)
        if d.get('status') not in ('admitted', 'removed', 'rejected'):
            raise ValidationError('selfplay_pool_event.status is admitted/removed/rejected')
        if not isinstance(d.get('occurred_at'), str) or not d['occurred_at'].strip():
            raise ValidationError('selfplay_pool_event requires occurred_at')
    elif kind=='solver_attempt':
        if 'context' in d and d['context'] not in ('reference', 'training', 'validation', 'unknown'):
            raise ValidationError('solver_attempt.context is reference/training/validation/unknown')
        if 'status' in d and d['status'] not in ('completed', 'failed', 'error', 'not_run'):
            raise ValidationError('Unknown solver_attempt.status')
        _integer(d.get('attempt_index'), 'attempt_index', 1 if d.get('context')=='reference' else 0)
        if d.get('context')=='reference':
            identifier(d.get('reference_id'), 'reference_id')
            if d.get('attempt_index') is None:raise ValidationError('Reference attempts require attempt_index')
            if not d.get('solver_revision'):raise ValidationError('Reference attempt needs recorded solver_revision')
            if d.get('groundtruth_visible') is True:raise ValidationError('Reference difficulty cannot expose the ground truth to the solver')
            if d.get('reference_configuration_hash') is not None and not _fingerprint(d['reference_configuration_hash']):
                raise ValidationError('reference_configuration_hash must be SHA256')
            if 'execution_config' in d:_dict(d['execution_config'], 'execution_config')
        if d.get('scores', {}).get('em') is not None and d.get('status') in ('error', 'not_run'):
            raise ValidationError('Error/not_run attempts cannot claim an EM score')
    elif kind=='training_use':
        for key in ('attempt_id', 'rollout_id'):
            if key in d:identifier(d[key], key)
        if d.get('actor_role') not in (None, 'solver', 'proposer', 'unknown'):
            raise ValidationError('training_use.actor_role is solver/proposer/unknown')
        for key in ('task_signal', 'opd_signal'):
            if key in d:
                _signal(d[key], key)
                allowed = ('policy_gradient', 'advantage_reweight') if key=='task_signal' else ('distillation', 'supervised_finetuning')
                if d[key].get('objective') not in allowed:
                    raise ValidationError(key + ' objective belongs to the wrong learning-term family')
        if 'selfplay_rollouts_complete' in d and type(d['selfplay_rollouts_complete']) is not bool:
            raise ValidationError('selfplay_rollouts_complete must be boolean')
    elif kind=='selfplay_comparison':
        from .selfplay_evaluation import validate_selfplay_comparison_contract
        validate_selfplay_comparison_contract(d)
    elif kind=='trace' and d.get('selfplay_evidence_type')=='run_accounting':
        for key in ('run_id', 'output_checkpoint_id'):identifier(d.get(key), key)
        ids = d.get('cycle_ids')
        if not isinstance(ids, list) or not ids or len(ids)>1000 or len(ids)!=len(set(ids)):
            raise ValidationError('Self-play accounting requires distinct cycle_ids')
        for cid in ids:identifier(cid, 'cycle_id')
        for key in ('budget', 'controls'):_dict(d.get(key), key)
        pool = d.get('stable_question_ids')
        if not isinstance(pool, list) or len(pool)!=len(set(pool)) or any(not isinstance(v, str) or not v.strip() for v in pool):
            raise ValidationError('Self-play accounting needs an explicit unique initial stable_question_ids list')


def _same_scope(repo, obj, other, *, reference=False, experiment=False):
    a, b = obj.get('scope', {}), other.get('scope', {})
    if any(a.get(k, '')!=b.get(k, '') for k in ('project_id', 'experiment_id')):
        raise ValidationError('Self-play evidence belongs to another experiment')
    if not experiment and a.get('run_id', '')!=b.get('run_id', '') and not (reference and not b.get('run_id')):
        raise ValidationError('Self-play evidence must belong to the same run')
    from .opd_tracking import evidence_demo
    if evidence_demo(repo, obj)!=evidence_demo(repo, other):
        raise ValidationError('Synthetic and real self-play evidence cannot be mixed')


def validate_selfplay_links(repo, obj, pending):
    kind, d = obj['kind'], obj['data']
    if kind=='selfplay_comparison':
        from .selfplay_evaluation import validate_selfplay_comparison_links
        return validate_selfplay_comparison_links(repo, obj, pending)
    if kind=='trace' and d.get('selfplay_evidence_type')=='run_accounting':
        if repo.store is None or obj.get('scope', {}).get('run_id')!=d['run_id']:
            raise ValidationError('Self-play accounting must belong to its explicit run')
        if repo.store._get('checkpoints', d['output_checkpoint_id'])['run_id']!=d['run_id']:
            raise ValidationError('Self-play accounting checkpoint belongs to another run')
        for cid in d['cycle_ids']:_same_scope(repo, obj, repo._raw_object(cid, pending))
    if kind in ('selfplay_reference', 'selfplay_pool_event') and not obj.get('scope', {}).get('experiment_id'):
        raise ValidationError('Self-play reference/pool events require a registered experiment')
    # Older free-form objects remain readable and import-compatible; their
    # out-of-scope links never enter a scoped read model. New typed links are
    # strict, including experiment-scoped frozen reference configurations.
    if kind=='selfplay_pool_event':
        for key in ('question_id', 'cycle_id'):_same_scope(repo, obj, repo._raw_object(d[key], pending))
    if kind=='generated_question' and d.get('generation_cycle_id'):
        _same_scope(repo, obj, repo._raw_object(d['generation_cycle_id'], pending))
    if kind=='question_review' and 'checks' in d:
        _same_scope(repo, obj, repo._raw_object(d['question_id'], pending))
    if kind=='solver_attempt' and d.get('reference_id'):
        ref = repo._raw_object(d['reference_id'], pending)
        _same_scope(repo, obj, ref, reference=True)
        for key in ('question_id', 'cycle_id'):_same_scope(repo, obj, repo._raw_object(d[key], pending))
        if d.get('context')!='reference':raise ValidationError('reference_id requires reference context')
        if d.get('solver_revision')!=ref['data']['solver_revision']:
            raise ValidationError('Attempt solver_revision differs from frozen reference')
        if ref['data'].get('attempts_per_question') is not None and d['attempt_index']>ref['data']['attempts_per_question']:
            raise ValidationError('Reference attempt_index exceeds frozen K')
        from ..core import NotFoundError
        try:existing = repo.related(d['question_id'])
        except NotFoundError:existing = []
        rows = [x for x in existing if x['kind']=='solver_attempt'] + list(pending.values())
        for other in rows:
            od = other.get('data', {})
            if other['id']!=obj['id'] and other['kind']=='solver_attempt' and all(
                    od.get(k)==d.get(k) for k in ('question_id', 'reference_id', 'attempt_index')):
                raise ValidationError('Duplicate frozen-reference attempt_index for a question')
    if kind=='training_use' and d.get('attempt_id'):
        attempt = repo._raw_object(d['attempt_id'], pending)
        _same_scope(repo, obj, attempt)
        if attempt['data']['question_id'] not in d['artifact_ids']:
            raise ValidationError('Training attempt question must be among artifact_ids')
        if d.get('cycle_id') and attempt['data'].get('cycle_id')!=d['cycle_id']:
            raise ValidationError('Training use and attempt cycle must match')
        if attempt['data'].get('context') in ('reference', 'validation'):
            raise ValidationError('Frozen reference/validation probes cannot be training rollout attempts')
    if kind=='training_use' and ('task_signal' in d or 'opd_signal' in d):
        for oid in d['artifact_ids']:
            other = repo._raw_object(oid, pending)
            if other['kind'] in SELFPLAY_KINDS:_same_scope(repo, obj, other)
    if kind=='training_update':
        for uid in d.get('consumption_ids', []):
            use = repo._raw_object(uid, pending)
            if use['data'].get('attempt_id') or 'task_signal' in use['data'] or 'opd_signal' in use['data']:
                _same_scope(repo, obj, use)
                if d.get('cycle_id') and use['data'].get('cycle_id')!=d['cycle_id']:
                    raise ValidationError('Self-play completed update and sampled use cycles must match')


def signal_state(signal):
    """Actual learning-term support, not parameter attribution or task success."""
    if not isinstance(signal, dict):return None, '未记录实际 advantage / mask / weight'
    if signal.get('loss_active') is False:return False, '训练器明确关闭此学习项'
    weight = signal.get('loss_weight')
    mask = signal.get('loss_mask')
    tokens = sum(x!=0 for x in mask) if isinstance(mask, list) else signal.get('loss_mask_tokens')
    if weight==0 or tokens==0:return False, '实际权重或有效 mask 为零'
    objective = signal.get('objective')
    if objective in ('policy_gradient', 'advantage_reweight'):
        if objective=='advantage_reweight' and signal.get('original_advantage')==0:
            return False, '纯乘法变换的原始 advantage 为零'
        advantages = signal.get('effective_advantages')
        if isinstance(advantages, list):
            if not advantages or all(x==0 for x in advantages):return False, '实际 advantage 全零'
            if not isinstance(mask, list):return None, '逐位置 advantage 缺少对齐 mask'
            if len(advantages)!=len(mask):return None, 'advantage / mask 位置未对齐'
            if not any(a!=0 and m!=0 for a,m in zip(advantages, mask)):
                return False, '非零 advantage 与有效 mask 没有位置交集'
        else:
            advantage = signal.get('effective_advantage')
            if advantage==0:return False, '实际 advantage 为零'
            if advantage is None:return None, '未记录实际 advantage'
    elif objective not in ('distillation', 'supervised_finetuning'):
        return None, '未记录可核对的学习项目标'
    if weight is None or tokens is None:return None, '实际 mask 或学习权重缺失'
    return True, '实际学习项存在非零支持；不推断参数变化或自主能力提升'


def selfplay_projection(obj, projection=None):
    p = dict(projection or {});d, kind = obj['data'], obj['kind']
    def pick(*keys):p.update({k:copy.deepcopy(d[k]) for k in keys if k in d})
    if kind=='selfplay_cycle':
        pick('coverage', 'round_index', 'round', 'source', 'seed', 'budget')
        p['participants'] = [{k:x[k] for k in ('id', 'role', 'model', 'revision', 'version', 'checkpoint_id') if k in x} for x in d.get('participants', [])][:32]
    elif kind=='generated_question':
        pick('generation_cycle_id', 'first_generated_cycle_id', 'duplicate_of', 'hop_count')
        p['question_content_sha256'] = digest({'question':d['question'], 'answer':d.get('answer', d.get('answers', d.get('proposed_answer')))})
    elif kind=='question_review':
        pick('status', 'supersedes')
        p['checks'] = {k:{'status':v.get('status'), 'verifier_recorded':bool(v.get('verifier'))} for k,v in d.get('checks', {}).items()}
        p['reason'] = str(d.get('reason') or d.get('rejection_reason') or d.get('verification', {}).get('reason') or '')[:500]
    elif kind=='solver_attempt':
        pick('reference_id', 'context', 'attempt_index', 'status', 'solver_revision', 'groundtruth_visible', 'rollout_id', 'reference_configuration_hash')
        if 'execution_config' in d:p['execution_config_hash'] = digest(d['execution_config'])
        p['cost'] = d.get('cost', {})
        # Internal reward is deliberately a separate field from binary EM.
        p['reward'] = d.get('reward', d.get('scores', {}).get('reward'))
    elif kind=='selfplay_reference':
        pick('solver_revision', 'solver_weights_sha256', 'harness_sha256', 'sampling', 'environment', 'scoring', 'attempts_per_question', 'verifier', 'seed_policy')
        execution_keys = ('solver_weights_sha256', 'harness_sha256', 'sampling', 'environment', 'scoring', 'verifier', 'seed_policy')
        env, scoring = d.get('environment', {}), d.get('scoring', {})
        p['complete'] = (all(bool(d.get(k)) for k in execution_keys) and bool(d.get('attempts_per_question'))
            and _fingerprint(d.get('solver_weights_sha256')) and _fingerprint(d.get('harness_sha256'))
            and all(k in d.get('sampling', {}) for k in ('temperature', 'top_p', 'max_output_tokens'))
            and isinstance(env, dict) and _fingerprint(env.get('retrieval_corpus_sha256'))
            and type(env.get('search_budget')) is int and env['search_budget']>=0 and type(env.get('max_context_tokens')) is int and env['max_context_tokens']>0
            and isinstance(scoring, dict) and _fingerprint(scoring.get('protocol_sha256'))
            and isinstance(scoring.get('scorer'), dict) and bool(scoring['scorer'].get('id')) and bool(scoring['scorer'].get('version'))
            and isinstance(d.get('verifier'), dict) and bool(d['verifier'].get('id')) and bool(d['verifier'].get('version')))
        p['execution_config_hash'] = digest({key:d[key] for key in execution_keys if key in d})
        p['configuration_hash'] = digest(d)
    elif kind=='selfplay_pool_event':pick('question_id', 'cycle_id', 'pool_id', 'status', 'occurred_at', 'reason')
    elif kind=='training_use':
        pick('attempt_id', 'rollout_id', 'actor_role', 'selfplay_rollouts_complete')
        legacy = d.get('signal', {})
        task = d.get('task_signal', legacy if legacy.get('objective') in ('policy_gradient', 'advantage_reweight') else None)
        opd = d.get('opd_signal', legacy if legacy.get('objective') in ('distillation', 'supervised_finetuning') else None)
        p['task_signal_active'], p['task_signal_reason'] = signal_state(task)
        p['opd_signal_active'], p['opd_signal_reason'] = signal_state(opd)
        p['opd_signal_recorded'] = opd is not None
    elif kind=='selfplay_comparison':
        p.update(comparison_type=d['comparison_type'], arm_count=len(d.get('arms', [])),
                 run_ids=[a.get('run_id') for a in d.get('arms', [])])
    if kind in ('selfplay_cycle', 'selfplay_reference', 'selfplay_pool_event', 'solver_attempt'):
        from .opd_tracking import _small
        p = _small(p)
    return p


def _p(row):return row.get('assessment', {})


def _scope(repo, experiment_id, run_id=''):
    if not experiment_id:raise ValidationError('Self-play needs explicit experiment_id')
    scope = repo._scope({'experiment_id':experiment_id, **({'run_id':run_id} if run_id else {})})
    scope['run_id'] = run_id or ''
    return scope


def _page(items, offset, limit):
    return {'items':items[offset:offset+limit], 'total':len(items), 'offset':offset, 'limit':limit, 'has_more':offset+limit<len(items)}


def _active_reviews(reviews):
    superseded = {_p(r).get('supersedes') for r in reviews}
    return [r for r in reviews if r['id'] not in superseded]


def validation_state(reviews):
    active = _active_reviews(reviews)
    if not active:return 'unknown'
    results = []
    for row in active:
        p = _p(row);checks = p.get('checks', {})
        states = [v.get('status') if v.get('verifier_recorded') or v.get('status') in ('error', 'not_run') else 'not_run' for v in checks.values()]
        v = p.get('verification_status')
        if 'error' in states or v=='error':result = 'error'
        elif 'failed' in states or v=='failed' or p.get('status')=='rejected':result = 'failed'
        elif p.get('status')!='accepted':result = 'unknown'
        elif checks:
            result = 'passed' if all(checks.get(k, {}).get('status')=='passed' and checks.get(k, {}).get('verifier_recorded') for k in ('format', 'answerability')) and all(x=='passed' for x in states) else 'unknown'
        elif v=='passed' and p.get('verifier_recorded') or v is None and p.get('legacy_verified_answer'):result = 'passed'
        else:result = 'unknown'
        results.append(result)
    return results[0] if len(set(results))==1 else 'conflicting'


def _model(rows):
    bykind = defaultdict(list)
    for row in rows:bykind[row['kind']].append(row)
    cycles = bykind['selfplay_cycle'];cycle_map = {r['id']:r for r in cycles}
    cycle_order = {r['id']:i for i,r in enumerate(cycles)}
    cycles.sort(key=lambda r:(_p(r).get('round_index') if type(_p(r).get('round_index')) is int else _p(r).get('round') if type(_p(r).get('round')) is int else cycle_order[r['id']], cycle_order[r['id']]))
    ranks = {r['id']:i for i,r in enumerate(cycles)}
    questions = bykind['generated_question'];question_map = {r['id']:r for r in questions}
    reviews, uses, attempts, pools, proposals = (defaultdict(list) for _ in range(5))
    for row in bykind['question_review']:reviews[_p(row).get('question_id')].append(row)
    for row in bykind['solver_attempt']:attempts[_p(row).get('question_id')].append(row)
    for row in bykind['selfplay_pool_event']:pools[_p(row).get('question_id')].append(row)
    for row in bykind['proposal']:proposals[_p(row).get('question_id')].append(row)
    completed_ids = set()
    update_map = defaultdict(list)
    for row in bykind['training_update']:
        for uid in _p(row).get('consumption_ids', []):
            update_map[uid].append(row)
            if _p(row).get('status')=='completed':completed_ids.add(uid)
    for row in bykind['training_use']:
        for qid in _p(row).get('artifact_ids', []):
            if qid in question_map:uses[qid].append(row)
    def canonical(qid):
        seen = set();row = question_map[qid]
        while _p(row).get('duplicate_of') in question_map:
            if row['id'] in seen:return None
            seen.add(row['id']);row = question_map[_p(row)['duplicate_of']]
        return _p(row).get('question_uid')
    canonical_ids = {q['id']:canonical(q['id']) for q in questions}
    aliases = defaultdict(list)
    for q in questions:aliases[canonical_ids[q['id']] or ('unkeyed:' + q['id'])].append(q)
    attempt_map = {r['id']:r for r in bykind['solver_attempt']};training_roles = {}
    for use in bykind['training_use']:
        p = _p(use);attempt = attempt_map.get(p.get('attempt_id'));declared = p.get('actor_role');inferred = None
        if attempt:
            ap = _p(attempt);cycle = cycle_map.get(ap.get('cycle_id'))
            if ap.get('context') in ('reference', 'validation'):inferred = 'reference'
            elif ap.get('cycle_id')==p.get('cycle_id'):
                inferred = next((actor.get('role') for actor in _p(cycle or {}).get('participants', []) if actor.get('id')==ap.get('actor_id')), None)
        role = declared or inferred or 'unknown'
        if declared and inferred and declared!=inferred:role = 'unknown'
        training_roles[use['id']] = role
    solver_completed_ids = {uid for uid in completed_ids if training_roles.get(uid)=='solver'}
    return {'rows':rows, 'bykind':bykind, 'cycles':cycles, 'cycle_map':cycle_map, 'ranks':ranks,
            'questions':questions, 'question_map':question_map, 'reviews':reviews, 'uses':uses,
            'attempts':attempts, 'pools':pools, 'proposals':proposals, 'completed_ids':completed_ids, 'updates':update_map,
            'canonical_ids':canonical_ids, 'aliases':aliases, 'training_roles':training_roles,
            'solver_completed_ids':solver_completed_ids}


def _question_assessment(model, row, cycle_id):
    p = dict(_p(row));qid = row['id'];uid = model['canonical_ids'][qid]
    aliases = model['aliases'][uid or ('unkeyed:' + qid)]
    original = [q for q in aliases if _p(q).get('deduplication')!='duplicate']
    firsts = {_p(q).get('first_generated_cycle_id') for q in original if _p(q).get('first_generated_cycle_id')}
    first = next(iter(firsts)) if len(firsts)==1 else None
    conflict = len(firsts)>1 or len({_p(q).get('question_content_sha256') for q in original})>1
    used = [u for q in aliases for u in model['uses'][q['id']] if _p(u).get('status')=='sampled' and model['training_roles'].get(u['id']) not in ('proposer', 'reference')]
    # Preserve an actual sampling row once even if it lists multiple aliases.
    used = list({u['id']:u for u in used}.values())
    current = [u for u in used if _p(u).get('cycle_id')==cycle_id] if cycle_id else used
    completed = [u for u in current if u['id'] in model['solver_completed_ids']]
    previous = [u for u in used if u['id'] in model['solver_completed_ids'] and _p(u).get('cycle_id') in model['ranks'] and cycle_id in model['ranks'] and model['ranks'][_p(u)['cycle_id']]<model['ranks'][cycle_id]]
    coverage = _p(model['cycle_map'].get(cycle_id, {})).get('coverage', {})
    consumption_complete = coverage.get('consumption_complete') is True
    update_complete = coverage.get('updates_complete') is True
    sampled = True if current else False if consumption_complete else None
    unknown_role = any(model['training_roles'].get(u['id']) not in ('solver', 'proposer', 'reference') for u in current)
    update_status = True if completed else False if sampled is False or current and update_complete and not unknown_role else None
    verification = validation_state(model['reviews'][qid])
    if conflict:verification = 'conflicting'
    # If aliases preserve the identical immutable question, a complete review
    # of its canonical source can qualify consumption through another alias.
    source_states = [validation_state(model['reviews'][q['id']]) for q in original]
    canonical_verified = 'passed' in source_states and not any(x in ('failed', 'error', 'conflicting') for x in source_states) and not conflict
    unique = any(_p(q).get('deduplication')=='unique' for q in original)
    qualifying_aliases = {q['id'] for q in original if _p(q).get('question_content_sha256')==p.get('question_content_sha256') and validation_state(model['reviews'][q['id']]) not in ('failed', 'error', 'conflicting')}
    qualifying_completed = [u for u in completed if qualifying_aliases.intersection(_p(u).get('artifact_ids', []))]
    known_false = first and cycle_id and first!=cycle_id or p.get('deduplication')=='duplicate' or verification=='failed' or update_status is False
    supplied = bool(not known_false and uid and first==cycle_id and unique and canonical_verified and qualifying_completed and not conflict) if cycle_id else bool(not known_false and uid and first in model['cycle_map'] and unique and canonical_verified and qualifying_completed and not conflict)
    supply_status = True if supplied else False if known_false else None
    reuse_status = 'unknown'
    if completed and first:
        if first==cycle_id:reuse_status = 'new'
        elif previous:reuse_status = 'reused'
        else:reuse_status = 'old_first_use' if all(_p(c).get('coverage', {}).get('consumption_complete') is True and _p(c).get('coverage', {}).get('updates_complete') is True for c in model['cycles'] if model['ranks'][c['id']]<model['ranks'].get(cycle_id, -1)) else 'unknown'
    generation = p.get('generation_cycle_id') or p.get('first_generated_cycle_id')
    return {**p, 'question_uid':uid, 'first_generated_cycle_id':first, 'generation_cycle_id':generation,
            'identity_conflict':conflict, 'validation_status':verification, 'sampled':sampled,
            'completed':update_status, 'new_verified_consumed':supply_status, 'reuse_status':reuse_status,
            'previous_completed_uses':len(previous), 'completed_use_count':len(completed),
            'attempt_count':len(model['attempts'][qid]), 'review_ids':[r['id'] for r in model['reviews'][qid]],
            'training_use_ids':[r['id'] for r in current], 'completed_use_ids':[r['id'] for r in completed]}


def _eligible_uses(model, cycle_id):
    rows = []
    for use in model['bykind']['training_use']:
        p = _p(use)
        if p.get('status')!='sampled' or use['id'] not in model['completed_ids']:continue
        if model['training_roles'].get(use['id']) in ('proposer', 'reference'):continue
        if cycle_id and p.get('cycle_id')!=cycle_id:continue
        if not set(p.get('artifact_ids', [])).intersection(model['question_map']):continue
        row = copy.deepcopy(use);row['assessment']['solver_role_verified'] = model['training_roles'].get(use['id'])=='solver'
        rows.append(row)
    groups = defaultdict(list)
    for row in rows:
        p = _p(row);identity = p.get('rollout_id') or p.get('attempt_id')
        groups[(p.get('batch_id'), identity or row['id'])].append(row)
    return list(groups.values())


def _signal_summary(groups, field):
    flags = [];ambiguous = 0;missing_identity = 0;not_recorded = 0
    for records in groups:
        identity = any(_p(r).get('rollout_id') or _p(r).get('attempt_id') for r in records)
        values = {_p(r).get(field + '_active') if _p(r).get('solver_role_verified') is True else None for r in records} if identity else {None}
        flags.append(next(iter(values)) if len(values)==1 else None)
        ambiguous += len(values)>1
        missing_identity += not identity
        if field=='opd_signal':not_recorded += not any(_p(r).get('opd_signal_recorded') for r in records)
    active = sum(v is True for v in flags);inactive = sum(v is False for v in flags);unknown = sum(v is None for v in flags);total = len(flags)
    return {'active':active, 'inactive':inactive, 'unknown':unknown, 'total':total,
            'value':active/total if total and not unknown else None,
            'lower_bound':active/total if total else None, 'upper_bound':(active+unknown)/total if total else None,
            'conflicting_rollouts':ambiguous, 'missing_rollout_identity':missing_identity,
            'not_recorded':not_recorded, 'denominator_unit':'completed_sampled_rollout_uses',
            'note':'按实际完成更新的 (batch, rollout) 计数；同批重复日志去重。缺失信号保留在总分母，非零支持不等于样本导致能力提升。'}


def _round_data(model, cycle_id):
    assessments = {r['id']:_question_assessment(model, r, cycle_id) for r in model['questions']}
    generated = [r for r in model['questions'] if assessments[r['id']].get('generation_cycle_id')==cycle_id] if cycle_id else list(model['questions'])
    generated_ids = {r['id'] for r in generated}
    visible = [r for r in model['questions'] if r['id'] in generated_ids or assessments[r['id']]['training_use_ids'] or any(_p(a).get('cycle_id')==cycle_id for a in model['attempts'][r['id']])]
    values = [assessments[r['id']] for r in generated]
    current = [assessments[r['id']] for r in visible]
    def unique(items, predicate):return len({x['question_uid'] or ('unkeyed:' + str(i)) for i,x in enumerate(items) if predicate(x)})
    validation = Counter(x['validation_status'] for x in values)
    relevant_pool = [e for e in model['bykind']['selfplay_pool_event'] if not cycle_id or _p(e).get('cycle_id')==cycle_id]
    counts = {'generated_questions':len(generated), 'validation_passed':validation['passed'],
              'validation_failed':validation['failed'], 'validation_error':validation['error'],
              'validation_unknown':validation['unknown'] + validation['conflicting'], 'validation_conflicting':validation['conflicting'],
              'unique_questions':unique(values, lambda p:p.get('deduplication')=='unique' and bool(p['question_uid'])),
              'duplicate_questions':sum(x.get('deduplication')=='duplicate' for x in values),
              'deduplication_unknown':sum(x.get('deduplication') not in ('unique', 'duplicate') for x in values),
              'pool_admitted_questions':len({model['canonical_ids'].get(_p(e).get('question_id')) or _p(e).get('question_id') for e in relevant_pool if _p(e).get('status')=='admitted'}),
              'sampled_questions':unique(current, lambda p:p['sampled'] is True),
              'completed_questions':unique(current, lambda p:p['completed'] is True),
              'new_verified_consumed_questions':unique(current, lambda p:p['new_verified_consumed'] is True),
              'old_first_consumed_questions':unique(current, lambda p:p['reuse_status']=='old_first_use'),
              'reused_questions':unique(current, lambda p:p['reuse_status']=='reused'),
              'reuse_unknown_questions':unique(current, lambda p:p['completed'] is True and p['reuse_status']=='unknown'),
              'unknown_identity_questions':sum(not x['question_uid'] for x in values),
              'unknown_supply_questions':unique(values, lambda p:p['new_verified_consumed'] is None),
              'validation_events':sum(len(model['reviews'][r['id']]) for r in generated),
              'validation_error_events':sum(_p(v).get('verification_status')=='error' or any(x.get('status')=='error' for x in _p(v).get('checks', {}).values()) for r in generated for v in model['reviews'][r['id']])}
    groups = _eligible_uses(model, cycle_id)
    task, opd = _signal_summary(groups, 'task_signal'), _signal_summary(groups, 'opd_signal')
    coverage = _p(model['cycle_map'].get(cycle_id, {})).get('coverage', {})
    denominator_complete = (coverage.get('consumption_complete') is True and coverage.get('updates_complete') is True
        and all(_p(r).get('selfplay_rollouts_complete') is True and _p(r).get('solver_role_verified') is True for group in groups for r in group)
        and not task['missing_rollout_identity'])
    for value in (task, opd):
        value['denominator_complete'] = denominator_complete
        if not denominator_complete:
            value['value'] = None
            value['note'] += ' 当前完整消费分母尚未证实；上下界仅描述已记录的消费项。'
    counts['completed_rollout_uses'] = task['total']
    return {'counts':counts, 'task_signal':task, 'opd_signal':opd,
            'questions':[{**r, 'assessment':assessments[r['id']]} for r in visible]}


def _difficulty(model, reference, cycle_id):
    groups = {k:0 for k in ('all_failed', 'partial_success', 'all_succeeded', 'incomplete')}
    items = [];p = _p(reference or {});k = p.get('attempts_per_question');ref_id = (reference or {}).get('id')
    questions = [q for q in model['questions'] if not cycle_id or (_p(q).get('generation_cycle_id') or _p(q).get('first_generated_cycle_id'))==cycle_id]
    for question in questions:
        attempts = [a for a in model['attempts'][question['id']] if ref_id and _p(a).get('reference_id')==ref_id and _p(a).get('context')=='reference']
        completed = [a for a in attempts if _p(a).get('status') in ('completed', 'failed') and _p(a).get('em') in (0, 1) and _p(a).get('groundtruth_visible') is False]
        scored = len(completed);successes = sum(_p(a)['em'] for a in completed)
        indices = {_p(a).get('attempt_index') for a in completed}
        config_matched = all(_p(a).get('reference_configuration_hash')==p.get('configuration_hash') and _p(a).get('execution_config_hash')==p.get('execution_config_hash') for a in completed)
        full = bool(p.get('complete') and k and scored==k and indices==set(range(1, k+1)) and config_matched)
        group = ('all_failed' if successes==0 else 'all_succeeded' if successes==k else 'partial_success') if full else 'incomplete'
        if ref_id:groups[group] += 1
        items.append({'question_id':question['id'], 'successes':successes if scored else None,
                      'attempts':scored, 'recorded_attempts':len(attempts), 'expected_attempts':k,
                      'errors':sum(_p(a).get('status')=='error' for a in attempts), 'group':group,
                      'reference_id':ref_id, 'complete':full, 'execution_config_matched':config_matched if completed else None})
    return {'groups':groups, 'questions':items, 'total':len(questions),
            'status':'measured' if ref_id and any(v for k,v in groups.items() if k!='incomplete') else 'not_recorded',
            'note':'同一冻结参考 solver、harness、采样、验证器与 K 下比较题目。全部 K 次计分且不向 solver 暴露答案才分组；出错和缺失不当作全失败。跳数仅描述结构。'}


def selfplay_runtime(repo, experiment_id, run_id='', evaluation_id=''):
    from .assessment import _external, _read
    scope = _scope(repo, experiment_id, run_id)
    if evaluation_id:
        ev = repo.store.evaluation(evaluation_id)
        if ev['experiment_id']!=scope['experiment_id'] or ev['checkpoint']['run_id']!=scope['run_id']:
            raise ValidationError('Self-play evaluation belongs to another experiment/run')
    rows = _read(repo, scope, ('evaluation_binding',))
    metric, cost, external = _external(repo, scope, rows, 'self_play', evaluation_id)
    if external.get('evaluation_id'):
        from .selfplay_evaluation import selfplay_evaluation_cell
        checked = selfplay_evaluation_cell(repo, external['evaluation_id'], external['checkpoint_id'])
        external.update(qualification_reasons=checked.get('reasons', []), isolation=checked.get('isolation', {}), isolation_note=checked.get('isolation_note'))
        metric['raw_value'] = checked.get('raw_value')
        if checked['status']!='measured':
            metric.update(value=None, status=checked['status'], missing_reason=checked['reason'], qualification_reasons=checked.get('reasons', []))
    return {'scope':scope, 'metrics':[metric, cost], 'external':external,
            'notes':['仅固定外部题集上的独立自主评测；当轮生成题得分、内部 reward 与参考 solver 难度探测分别保存。'],
            'models_executed':False}


def selfplay_training(repo, experiment_id, run_id='', cycle_id='', reference_id='', evaluation_id='', offset=0, limit=25):
    from .assessment import _read, _metric
    from .views import page_args
    scope = _scope(repo, experiment_id, run_id);offset, limit = page_args(offset, limit)
    rows = _read(repo, scope, READ_KINDS);model = _model(rows)
    if cycle_id and cycle_id not in model['cycle_map']:raise ValidationError('Selected self-play cycle is outside this run')
    selected = cycle_id or (model['cycles'][-1]['id'] if model['cycles'] else '')
    parent = _read(repo, {**scope, 'run_id':''}, ('selfplay_reference', 'selfplay_comparison')) if run_id else []
    references = model['bykind']['selfplay_reference'] + [r for r in parent if r['kind']=='selfplay_reference']
    if reference_id and reference_id not in {r['id'] for r in references}:
        raise ValidationError('Selected frozen reference is outside this experiment/run')
    chosen_ref = next((r for r in references if r['id']==reference_id), None) if reference_id else references[0] if len(references)==1 else None
    selected_ref = chosen_ref['id'] if chosen_ref else ''
    current = _round_data(model, selected);counts = current['counts'];task = current['task_signal']
    coverage = _p(model['cycle_map'].get(selected, {})).get('coverage', {})
    observed = bool(counts['generated_questions'] or counts['completed_questions'] or coverage.get('generated_questions_complete') is True)
    supply = _metric('new_verified_consumed', '本轮新增有效训练题', unit='count',
        value=counts['new_verified_consumed_questions'] if observed else None,
        numerator=counts['new_verified_consumed_questions'], unknown=counts['unknown_supply_questions'],
        missing_reason='尚无首次轮次、验证、去重及完成更新的完整题目链条',
        note='按稳定题号去重；本轮首次生成、明确验证通过及 unique，实际 sampled 并关联 completed update。零 advantage 的已用题也计入供给。')
    signal = _metric('signal_coverage', '训练信号覆盖率', value=task['value'],
        numerator=task['active'], denominator=task['total'], unknown=task['unknown'],
        status='partial' if task['unknown'] or task['total'] and not task['denominator_complete'] else 'measured' if task['total'] else 'not_recorded',
        missing_reason='缺少全部已消费 rollout 的实际信号记录' if task['total'] else '尚无实际 sampled → completed update 记录',
        lower_bound=task['lower_bound'], upper_bound=task['upper_bound'], denominator_complete=task['denominator_complete'], note=task['note'])
    runtime = selfplay_runtime(repo, experiment_id, run_id, evaluation_id)
    difficulty = _difficulty(model, chosen_ref, selected)
    difficulty_map = {q['question_id']:q for q in difficulty['questions']}
    for q in current['questions']:
        d = difficulty_map.get(q['id'], {})
        q['assessment'].update(reference_successes=d.get('successes'), reference_attempts=d.get('attempts'),
            reference_expected=d.get('expected_attempts'), reference_group=d.get('group', 'incomplete'))
    rounds = [];difficulty_rounds = []
    for row in model['cycles']:
        rd = current if row['id']==selected else _round_data(model, row['id'])
        rounds.append({'id':row['id'], 'title':row.get('title') or row['id'], 'round_index':_p(row).get('round_index', _p(row).get('round')),
                       'selected':row['id']==selected, 'counts':rd['counts'], 'coverage':_p(row).get('coverage', {}),
                       'participants':_p(row).get('participants', [])})
        dd = difficulty if row['id']==selected else _difficulty(model, chosen_ref, row['id'])
        difficulty_rounds.append({'cycle_id':row['id'], 'title':row.get('title') or row['id'], 'groups':dd['groups'], 'total':dd['total']})
    difficulty['rounds'] = difficulty_rounds[:500]
    comparisons = model['bykind']['selfplay_comparison'] + [r for r in parent if r['kind']=='selfplay_comparison' and (not run_id or run_id in _p(r).get('run_ids', []))]
    return {'scope':scope, 'status':'recorded' if model['questions'] or model['cycles'] else 'not_recorded',
            'selected_cycle_id':selected, 'rounds':rounds[:500], 'round_count':len(rounds),
            'metrics':[supply, signal, runtime['metrics'][0]], 'counts':counts, 'coverage':coverage,
            'task_signal':task, 'opd_signal':current['opd_signal'],
            'references':[{'id':r['id'], 'title':r.get('title') or r['id'], **_p(r), 'selected':r['id']==selected_ref} for r in references[:100]],
            'reference_count':len(references), 'selected_reference_id':selected_ref,
            'difficulty':{**difficulty, 'questions':difficulty['questions'][offset:offset+limit]},
            'questions':_page(current['questions'], offset, limit), 'external':runtime['external'],
            'comparisons':_page(comparisons, 0, 100),
            'notes':['只读取选定运行。外部固定参照可在实验级配置，其他运行的题、验证和训练记录不会并入。',
                     '题目验证的 pass / fail / error / 未记录分别保留；拒绝题不会从生成数分母中移除。',
                     '旧题首次消费与重复使用分列；供给、学习信号和 solver 自主能力是三个独立结果。',
                     '范围完整性来自采集端的明确声明；没有记录不当作零或已关闭。'],
            'models_executed':False, 'causal_claim':False}


def _bounded_object(repo, obj):
    from .entropy_tracking import _bounded_detail
    data, omitted = _bounded_detail(obj['data'])
    return {**obj, 'data':data, 'omitted_fields':omitted}


def selfplay_question(repo, question_id, cycle_id=''):
    from .assessment import _read
    question = repo.object(question_id)
    if question['kind']!='generated_question':raise ValidationError('Expected generated_question')
    scope = {**question.get('scope', {}), 'run_id':question.get('scope', {}).get('run_id', '')}
    rows = _read(repo, scope, READ_KINDS);model = _model(rows);summary = model['question_map'][question_id]
    related_uses = model['uses'][question_id]
    cycle_ids = {_p(summary).get('first_generated_cycle_id'), _p(summary).get('generation_cycle_id')}
    cycle_ids.update(_p(u).get('cycle_id') for u in related_uses)
    cycle_ids.update(_p(a).get('cycle_id') for a in model['attempts'][question_id])
    cycles = [r for r in model['cycles'] if r['id'] in cycle_ids]
    if cycle_id and cycle_id not in model['cycle_map']:
        raise ValidationError('Selected question cycle is outside this run')
    cycle_id = cycle_id or (cycles[-1]['id'] if cycles else '')
    updates = list({u['id']:u for use in related_uses for u in model['updates'][use['id']]}.values())
    refs = {_p(a).get('reference_id') for a in model['attempts'][question_id] if _p(a).get('reference_id')}
    sets = {'cycles':cycles, 'proposals':model['proposals'][question_id], 'reviews':model['reviews'][question_id], 'pool_events':model['pools'][question_id],
            'attempts':model['attempts'][question_id], 'training_uses':related_uses, 'updates':updates,
            'references':[repo.object(rid) for rid in sorted(refs)]}
    result = {'scope':scope, 'question':_bounded_object(repo, question),
              'assessment':_question_assessment(model, summary, cycle_id), 'truncation':{},
              'notes':['原始拒绝记录与验证器错误保留；多次解答的 reward、EM、实际 advantage 与 mask 分开显示。'],
              'models_executed':False}
    for key, values in sets.items():
        result[key] = [_bounded_object(repo, repo.object(v['id'])) for v in values[:100]]
        result['truncation'][key] = {'total':len(values), 'shown':min(100, len(values)), 'has_more':len(values)>100}
    return result
