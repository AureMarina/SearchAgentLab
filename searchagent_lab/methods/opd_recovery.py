"""Explicit failure-state continuation comparisons over saved evidence.

No environment, teacher or optimizer is executed here. Continuation verification,
student transfer and ordinary end-to-end evaluation remain separate evidence.
"""
from __future__ import annotations

import copy

from ..core import ValidationError
from ..inspection.repository import digest
from ..read_cache import request_scope
from ..studio.evaluation import _fingerprint
from .schema import identifier
from .skill_comparison import _cell, _pair

ROLES = ('student_before', 'teacher', 'student_after')
LABELS = {'student_before': '训练前 Student', 'teacher': 'Teacher 续跑', 'student_after': '训练后 Student'}
PRIVILEGE_FLAGS = ('teacher_hints', 'peer_answers', 'future_observations', 'groundtruth_visible')
SNAPSHOT_FIELDS = ('history', 'environment', 'tool_state', 'remaining_budget', 'harness')


def _evaluation_demo(ev):
    return bool(ev.get('demo') or ev.get('run', {}).get('demo') or ev.get('panel', {}).get('demo'))


def _verification_controls(verification):
    # Keep every declared grading-control field, including threshold/rubric or
    # configuration outside the verifier identity. Observed outcomes/cost and
    # source references may differ legitimately between the two executions.
    observed = {'status', 'score', 'scores', 'value', 'result', 'correct', 'passed',
                'error', 'message', 'reason', 'output', 'answer', 'prediction',
                'elapsed_ms', 'cost', 'usage', 'notice', 'timestamp', 'created_at',
                'evidence_ref', 'trace_ref', 'observations'}
    return {key: value for key, value in verification.items() if key not in observed}


def validate_recovery_contract(data):
    for key in ('baseline_checkpoint_id', 'current_checkpoint_id'):
        identifier(data.get(key), key)
    if data['baseline_checkpoint_id'] == data['current_checkpoint_id']:
        raise ValidationError('Recovery comparison requires distinct before/after checkpoint IDs')
    k = data.get('attempts_per_arm')
    if type(k) is not int or not 1 <= k <= 1000:
        raise ValidationError('attempts_per_arm must be an explicit integer in 1..1000')
    cohort = data.get('cohort')
    if not isinstance(cohort, dict):
        raise ValidationError('Recovery cohort must be explicitly recorded')
    identifier(cohort.get('id'), 'cohort.id')
    if cohort.get('usage', 'unknown') not in ('development', 'held_out', 'test', 'unknown'):
        raise ValidationError('cohort.usage is development/held_out/test/unknown')
    for key in ('frozen', 'feedback_used'):
        if key in cohort and cohort[key] is not None and type(cohort[key]) is not bool:
            raise ValidationError('cohort.' + key + ' must be boolean/null')
    if cohort.get('isolation_status', 'unverified') not in ('verified', 'unverified', 'overlap', 'unknown'):
        raise ValidationError('Unknown cohort isolation_status')
    cases = data.get('cases')
    if not isinstance(cases, list) or len(cases) > 1000:
        raise ValidationError('Recovery cases must be an explicit array of at most 1000 states')
    seen, attempt_ids, teacher_ids = set(), set(), set()
    for case in cases:
        if not isinstance(case, dict):raise ValidationError('Recovery case must be an object')
        fid = identifier(case.get('failure_id'), 'case.failure_id')
        if fid in seen:raise ValidationError('Duplicate failure state in the fixed cohort')
        seen.add(fid)
        attempts = case.get('attempts', [])
        if not isinstance(attempts, list) or len(attempts) > k * 3:
            raise ValidationError('Too many recovery attempts; preserve missing slots')
        slots = set()
        for attempt in attempts:
            if not isinstance(attempt, dict) or attempt.get('role') not in ROLES:
                raise ValidationError('Recovery attempt role must be student_before/teacher/student_after')
            if attempt['role'] == 'teacher':
                tid = identifier(attempt.get('teacher_record_id'), 'teacher_record_id')
                if tid in teacher_ids:raise ValidationError('Reused teacher execution in a recovery cohort')
                teacher_ids.add(tid)
                if set(attempt) - {'role', 'teacher_record_id'}:
                    raise ValidationError('Teacher arms reference the immutable teacher record; do not override its data')
                continue
            aid = identifier(attempt.get('attempt_id'), 'attempt_id')
            if aid in attempt_ids:raise ValidationError('Duplicate recovery attempt_id')
            attempt_ids.add(aid)
            index = attempt.get('attempt_index')
            if type(index) is not int or not 1 <= index <= k:
                raise ValidationError('Student attempt_index outside fixed attempt budget')
            slot = (attempt['role'], index)
            if slot in slots:raise ValidationError('Duplicate role/attempt_index in one recovery case')
            slots.add(slot)
            identifier(attempt.get('checkpoint_id'), 'attempt.checkpoint_id')
            if attempt.get('status') not in ('completed', 'failed', 'error', 'not_run'):
                raise ValidationError('Unknown recovery attempt status')
            for key in ('start', 'context', 'verification', 'cost'):
                if key in attempt and not isinstance(attempt[key], dict):
                    raise ValidationError('attempt.' + key + ' must be an object')
            for flag in PRIVILEGE_FLAGS:
                value = attempt.get('context', {}).get(flag)
                if value is not None and type(value) is not bool:
                    raise ValidationError('Context privilege flags must be boolean/null')
            if 'privileged_information' in attempt.get('context', {}) and not isinstance(attempt['context']['privileged_information'], list):
                raise ValidationError('context.privileged_information must be an array')
    full = data.get('full_task', {})
    if not isinstance(full, dict):raise ValidationError('full_task must be an object')
    for key in ('before_evaluation_id', 'after_evaluation_id'):
        if full.get(key):identifier(full[key], key)
    if full.get('before_evaluation_id') and full.get('before_evaluation_id') == full.get('after_evaluation_id'):
        raise ValidationError('Full-task arms must be distinct executions')


def recovery_refs(obj):
    if obj['kind'] != 'opd_recovery':return
    for case in obj['data'].get('cases', []):
        yield case['failure_id'], {'opd_failure'}, '/data/cases/failure_id'
        for attempt in case.get('attempts', []):
            if attempt.get('teacher_record_id'):
                yield attempt['teacher_record_id'], {'opd_teacher'}, '/data/cases/attempts/teacher_record_id'


def recovery_evidence_refs(obj):
    if obj['kind'] != 'opd_recovery':return
    for case in obj['data'].get('cases', []):
        for attempt in case.get('attempts', []):
            for key in ('start_ref', 'trace_ref', 'verification_ref'):
                if attempt.get(key) is not None:yield attempt[key]
    for ref in obj['data'].get('cohort', {}).get('isolation_evidence_refs', []):yield ref


def _scope_ref(repo, obj, ref, pending):
    if ref.get('object_id'):
        target = repo._raw_object(ref['object_id'], pending)
        if not repo._same_scope(obj, target):
            raise ValidationError('Recovery evidence belongs to another run scope')
        if bool(obj.get('demo')) != bool(target.get('demo')):
            raise ValidationError('Synthetic and real recovery evidence cannot be mixed')
    elif ref.get('trajectory'):
        trajectory = repo.store.trajectory(ref['trajectory'])
        ev = repo.store.evaluation(trajectory['evaluation_id'])
        if ev['run']['id'] != obj['scope']['run_id'] or bool(obj.get('demo')) != _evaluation_demo(ev):
            raise ValidationError('Recovery formal trajectory belongs to another run/demo scope')
    repo.resolve_ref(ref, pending, obj)


def validate_recovery_links(repo, obj, pending):
    if obj['kind'] != 'opd_recovery':return
    data, scope = obj['data'], obj.get('scope', {})
    if not repo.store or not scope.get('run_id'):
        raise ValidationError('Recovery comparisons require a registered run scope')
    checkpoints = {data['baseline_checkpoint_id'], data['current_checkpoint_id']}
    traces = set()
    for case in data['cases']:
        failure = repo._raw_object(case['failure_id'], pending)
        if failure['kind'] != 'opd_failure' or not repo._same_scope(obj, failure):
            raise ValidationError('Recovery failure must belong to the same run')
        if bool(failure.get('demo')) != bool(obj.get('demo')):
            raise ValidationError('Synthetic failure cannot enter a real recovery cohort')
        teacher_slots = set()
        for attempt in case.get('attempts', []):
            source = attempt
            if attempt['role'] == 'teacher':
                teacher = repo._raw_object(attempt['teacher_record_id'], pending)
                if teacher['kind'] != 'opd_teacher' or teacher['data']['failure_id'] != failure['id'] or not repo._same_scope(obj, teacher):
                    raise ValidationError('Teacher arm must reference this failure in the same run')
                if bool(teacher.get('demo')) != bool(obj.get('demo')):
                    raise ValidationError('Synthetic and real teacher evidence cannot be mixed')
                source = teacher['data']
                if source.get('mode') == 'continuation':
                    index = source['attempt_index']
                    if index > data['attempts_per_arm'] or index in teacher_slots:
                        raise ValidationError('Teacher continuation slot outside/duplicated in fixed recovery K')
                    teacher_slots.add(index)
            else:
                checkpoints.add(attempt['checkpoint_id'])
            for key in ('start_ref', 'trace_ref', 'teacher_trace_ref', 'verification_ref'):
                if source.get(key):_scope_ref(repo, obj, source[key], pending)
            trace_ref = source.get('trace_ref') or source.get('teacher_trace_ref')
            if trace_ref and (attempt['role'] != 'teacher' or source.get('mode') == 'continuation'):
                identity = _ref_identity(trace_ref) + (trace_ref.get('pointer', ''),)
                if identity in traces:
                    raise ValidationError('A recovery execution trace cannot be reused across roles or states')
                traces.add(identity)
    for cid in checkpoints:
        cp = repo.store._get('checkpoints', cid)
        if cp['run_id'] != scope['run_id']:
            raise ValidationError('Recovery checkpoint belongs to another run')
    for key in ('before_evaluation_id', 'after_evaluation_id'):
        eid = data.get('full_task', {}).get(key)
        if not eid:continue
        ev = repo.store.evaluation(eid)
        if ev['run']['id'] != scope['run_id'] or bool(obj.get('demo')) != _evaluation_demo(ev):
            raise ValidationError('Full-task evaluation belongs to another run/demo scope')


def recovery_summary(obj):
    d = obj['data']
    return {'baseline_checkpoint_id': d.get('baseline_checkpoint_id'),
            'current_checkpoint_id': d.get('current_checkpoint_id'),
            'attempts_per_arm': d.get('attempts_per_arm'), 'case_count': len(d.get('cases', [])),
            'cohort': {k: d.get('cohort', {}).get(k) for k in ('id', 'usage', 'frozen', 'feedback_used', 'isolation_status')},
            'full_task': copy.deepcopy(d.get('full_task', {})), 'body_loaded': False}


def _known(value):
    if value is None or value == '':return False
    if isinstance(value, dict):return bool(value) and all(_known(v) for v in value.values())
    if isinstance(value, list):return bool(value) and all(_known(v) for v in value)
    return True


def _snapshot(failure):
    data = failure['data']; state = data.get('state', {}); raw = state.get('snapshot')
    reasons = []
    if data.get('eligible') is not True:reasons.append('此失败状态的续跑资格未明确登记为 eligible')
    if state.get('status') not in ('captured', 'replayed'):
        reasons.append('只有文字前缀或未保存现场，不能核对环境恢复')
    if not isinstance(raw, dict) or any(not _known(raw.get(k)) for k in SNAPSHOT_FIELDS):
        reasons.append('现场未完整记录历史、环境、工具状态、剩余预算和 harness')
    if not isinstance(raw, dict) or _fingerprint(state.get('snapshot_hash')) != digest(raw):
        reasons.append('冻结现场内容与 SHA256 未完整对应')
    return raw if isinstance(raw, dict) else {}, reasons


def _ref_identity(ref):
    return (ref.get('object_id'), ref.get('file'), ref.get('record'), ref.get('trajectory'))


def _same_trace_field(trace_ref, field_ref):
    if not isinstance(trace_ref, dict) or not isinstance(field_ref, dict):return False
    if _ref_identity(trace_ref) != _ref_identity(field_ref):return False
    root = trace_ref.get('pointer', '').rstrip('/')
    pointer = field_ref.get('pointer', '')
    return pointer.startswith(root + '/') and pointer != root


def _verify_field(repo, obj, trace_ref, ref, expected, label, reasons):
    if not _same_trace_field(trace_ref, ref):
        reasons.append(label + '没有引用本次执行轨迹内的具体字段')
        return
    actual = repo.resolve_ref(ref, owner=obj)['value']
    if actual != expected:reasons.append(label + '与实际轨迹字段不一致')


def _context_reasons(context, student):
    reasons = []
    if not isinstance(context, dict):return ['未保存输入信息条件']
    if any(type(context.get(key)) is not bool for key in PRIVILEGE_FLAGS) or not isinstance(context.get('privileged_information'), list):
        reasons.append('教师提示、成功同伴、未来观察、标准答案及特权信息清单未完整声明')
    if student and (any(context.get(key) is not False for key in PRIVILEGE_FLAGS) or context.get('privileged_information') != []):
        reasons.append('Student 未明确撤去全部训练专用指导')
    return reasons


def _attempt(repo, obj, failure, entry, k):
    role = entry['role']; teacher = None
    if role == 'teacher':
        teacher = repo.object(entry['teacher_record_id']); data = teacher['data']
    else:data = entry
    start, context = data.get('start', {}), data.get('context', {})
    trace_ref = data.get('trace_ref') or data.get('teacher_trace_ref')
    result = {'role': role, 'attempt_id': data.get('attempt_id'), 'teacher_record_id': teacher['id'] if teacher else None,
              'attempt_index': data.get('attempt_index'), 'checkpoint_id': data.get('checkpoint_id'),
              'teacher_id': data.get('teacher_id'), 'teacher_revision': data.get('teacher_revision'),
              'status': 'unmeasured', 'outcome': None, 'context': copy.deepcopy(context),
              'cost': copy.deepcopy(data.get('cost', {})), 'start': copy.deepcopy(start),
              'trace_ref': trace_ref, 'verification': copy.deepcopy(data.get('verification', {})), 'reasons': []}
    reasons = result['reasons']
    if teacher and data.get('mode') != 'continuation':
        result.update(status='not_applicable', reason='此教师记录只重打分，没有执行续跑')
        reasons.append(result['reason']);return result
    if data.get('status') != 'completed':
        result.update(reason='执行出错、失败或未运行；没有完整续跑与验证结果')
        reasons.append(result['reason']);return result
    snapshot, state_reasons = _snapshot(failure); reasons.extend(state_reasons)
    if failure['data'].get('student_checkpoint_id') != obj['data']['baseline_checkpoint_id']:
        reasons.append('失败现场不属于指定训练前 checkpoint')
    if not isinstance(start, dict):start = {}
    if start.get('state_alignment') not in ('restored', 'replayed'):
        reasons.append('未记录实际恢复或重放到此状态')
    if start.get('snapshot_hash') != failure['data'].get('state', {}).get('snapshot_hash'):
        reasons.append('续跑开始的现场不是这个冻结失败状态')
    for field in ('history', 'environment', 'tool_state', 'harness'):
        if field not in snapshot or start.get(field + '_hash') != digest(snapshot[field]):
            reasons.append({'history': '历史', 'environment': '环境', 'tool_state': '工具状态', 'harness': 'Harness'}[field] + '指纹与失败现场不一致')
    if not snapshot.get('remaining_budget') or start.get('remaining_budget') != snapshot['remaining_budget']:
        reasons.append('剩余预算与冻结现场不一致')
    if not _known(start.get('decoding')) or start.get('seed') is None:
        reasons.append('续跑采样设置或随机种子未记录')
    if teacher:
        if not data.get('teacher_revision'):reasons.append('Teacher 的准确版本未记录')
        if data.get('state_alignment') != start.get('state_alignment'):
            reasons.append('Teacher 恢复状态声明与实际起点记录不一致')
    else:
        expected_cp = obj['data']['baseline_checkpoint_id' if role == 'student_before' else 'current_checkpoint_id']
        if data.get('checkpoint_id') != expected_cp:reasons.append('Student 不是该列指定的 checkpoint')
        cp = repo.store._get('checkpoints', data['checkpoint_id'])
        if _fingerprint(cp.get('weights_sha256')) is None:reasons.append('Student 权重 SHA256 未记录')
    reasons.extend(_context_reasons(context, not teacher))
    trace = None
    if not trace_ref:reasons.append('未保存本次实际续跑轨迹')
    else:
        trace = repo.resolve_ref(trace_ref, owner=obj)['value']
        if not isinstance(trace, dict):reasons.append('续跑引用没有定位到完整结构化执行记录')
        else:
            steps = trace.get('steps', [])
            has_execution = isinstance(steps, list) and any(isinstance(step, dict) and any(key in step for key in ('output', 'model_response', 'tool_call', 'tool_response', 'action')) for step in steps)
            if not has_execution:reasons.append('轨迹没有实际模型输出或工具执行步骤')
            if trace.get('context') != context:reasons.append('信息条件与实际续跑轨迹中的 context 不一致')
    _verify_field(repo, obj, trace_ref, data.get('start_ref'), start, '恢复起点', reasons)
    verification = data.get('verification', {})
    _verify_field(repo, obj, trace_ref, data.get('verification_ref'), verification, '任务验证', reasons)
    verifier = verification.get('verifier') if isinstance(verification, dict) else None
    complete_verifier = isinstance(verifier, dict) and bool(verifier.get('version') or verifier.get('revision')) and bool(verifier.get('name') or verifier.get('id'))
    if not isinstance(verification, dict) or verification.get('method') not in ('environment', 'scorer') or not complete_verifier:
        reasons.append('未保存环境／评分器的验证身份和准确版本')
    elif verification.get('status') not in ('passed', 'failed'):
        reasons.append('任务验证出错或未运行')
    reasons[:] = list(dict.fromkeys(reasons))
    if reasons:result.update(status='incomparable', reason='；'.join(reasons))
    else:result.update(status='measured', outcome=verification['status'] == 'passed', reason='同一冻结现场、已记录输入条件与实际执行验证')
    return result


def _arm(attempts, k):
    measured = [a for a in attempts if a['status'] == 'measured']
    success = sum(a['outcome'] is True for a in measured)
    failures = sum(a['outcome'] is False for a in measured)
    unknown = k - len(measured)
    mixed_verifier = len({digest(_verification_controls(a['verification'])) for a in measured}) > 1
    incompatible = mixed_verifier or any(a['status'] == 'incomparable' for a in attempts)
    status = 'incomparable' if incompatible else 'measured' if not unknown else 'partial' if measured else 'unmeasured'
    present = {a['attempt_index'] for a in attempts if a['status'] != 'not_applicable'}
    slots = [*attempts, *({'attempt_index': i, 'status': 'unmeasured', 'outcome': None, 'reason': '此尝试未测', 'reasons': ['此尝试未测']} for i in range(1, k + 1) if i not in present)]
    return {'status': status, 'value': success / k if status == 'measured' else None, 'successes': success,
            'failures': failures, 'known': len(measured), 'unknown': unknown, 'expected': k,
            'any_success_at_k': bool(success) if status == 'measured' else None,
            'attempts': sorted(slots, key=lambda a: a.get('attempt_index') or 0),
            'reason': '同一现场不同尝试的验证协议变化，不能合并成功率' if mixed_verifier else '完整 K 次验证的平均成功率 avg@K' if not unknown else '保留全部 K 个尝试槽；未测、出错或条件不符不计为失败'}


def _student_delta(repo, obj, before, after):
    result = {'status': 'unmeasured', 'value': None, 'reason': '训练前后均需完整、匹配的续跑证据'}
    if before['status'] != 'measured' or after['status'] != 'measured':
        if 'incomparable' in (before['status'], after['status']):result['status'] = 'incomparable'
        return result
    a = {x['attempt_index']: x for x in before['attempts']}; b = {x['attempt_index']: x for x in after['attempts']}
    reasons = []
    for index in a:
        for field in ('decoding', 'seed'):
            if a[index]['start'].get(field) != b[index]['start'].get(field):reasons.append('同一尝试序号的采样设置或种子改变')
        if _verification_controls(a[index]['verification']) != _verification_controls(b[index]['verification']):
            reasons.append('训练前后续跑验证器或评分条件发生变化')
    cps = [repo.store._get('checkpoints', obj['data'][key]) for key in ('baseline_checkpoint_id', 'current_checkpoint_id')]
    if cps[0].get('weights_sha256') == cps[1].get('weights_sha256'):
        reasons.append('训练前后权重指纹相同，不能作为训练变化')
    if reasons:return {**result, 'status': 'incomparable', 'reason': '；'.join(dict.fromkeys(reasons))}
    return {'status': 'measured', 'value': after['value'] - before['value'],
            'reason': '同状态、预算、执行及采样条件下的描述性续跑差异；不单独归因于 OPD'}


def _full_task(repo, obj):
    data = obj['data']; full = data.get('full_task', {}); before = full.get('before_evaluation_id'); after = full.get('after_evaluation_id')
    result = {'status': 'unmeasured', 'reason': '未指定训练前后两次从头自主评测', 'before': {}, 'after': {},
              'macro_delta': None, 'rescued': None, 'harmed': None, 'cases': [], 'causal_training_attribution': False}
    for side, eid, checkpoint in (('before', before, data['baseline_checkpoint_id']), ('after', after, data['current_checkpoint_id'])):
        cell = _cell(repo, eid, checkpoint, None, bank_expected=False)
        if eid:
            binding = (repo.evaluation_state(eid).get('binding') or {}).get('data', {})
            context_reasons = _context_reasons(binding.get('opd_context'), True)
            if context_reasons:
                cell['reasons'] += context_reasons
                cell.update(status='incomparable', value=None, reason='；'.join(cell['reasons']))
            cell['opd_context'] = binding.get('opd_context')
        result[side] = cell
    if not before or not after:return result
    a, b = result['before'], result['after']
    if a['status'] != 'measured' or b['status'] != 'measured':
        result.update(status='incomparable', reason='；'.join(dict.fromkeys(a['reasons'] + b['reasons'])))
        return result
    pair = _pair(repo, before, after, 'checkpoint')
    result['controls'] = pair['controls']
    if not pair['compatible'] or not pair['full']:
        result.update(status='incomparable', reason='；'.join(pair['reasons']) or '从头评测未全部完成')
        return result
    result.update(status='measured', reason='从原始任务开始、撤去训练专用信息的固定评测；与局部续跑分开',
                  macro_delta=pair['macro_delta'], rescued=pair['groups']['rescued'], harmed=pair['groups']['harmed'],
                  both_correct=pair['groups']['both_correct'], both_wrong=pair['groups']['both_wrong'],
                  expected=pair['expected'], paired=pair['paired'], paired_usage=pair['paired_usage'])
    for case in data['cases']:
        f = repo.object(case['failure_id'])['data']; sample_id, dataset = f['sample_id'], f.get('dataset')
        matches = [item for item in pair['items'] if item['sample']['id'] == sample_id and (not dataset or item['sample']['dataset'] == dataset)]
        item = matches[0] if len(matches) == 1 else None
        result['cases'].append({'failure_id': case['failure_id'], 'sample_id': sample_id,
            'status': 'measured' if item else 'unmeasured', 'group': item['group'] if item else 'unmatched',
            'before_trajectory_id': item['a']['id'] if item and item['a'] else None,
            'after_trajectory_id': item['b']['id'] if item and item['b'] else None})
    return result


def _cohort(data):
    cohort = copy.deepcopy(data['cohort']); usage = cohort.get('usage', 'unknown')
    development = usage == 'development' or cohort.get('feedback_used') is True
    cohort.update(case_count=len(data['cases']), case_list_hash=digest([case['failure_id'] for case in data['cases']]),
                  interpretation='开发反馈' if development else '声明隔离的固定现场' if usage in ('held_out', 'test') and cohort.get('isolation_status') == 'verified' else '隔离与使用范围尚未核查',
                  final_generalization=False)
    cohort['reason'] = ('反复用于方法修改的现场属于开发反馈。' if development else 'held-out 标签和隔离声明保留；本视图不自动认证最终泛化。')
    return cohort


@request_scope
def opd_recovery(repo, object_id):
    obj = repo.object(object_id)
    if obj['kind'] != 'opd_recovery':raise ValidationError('Expected an opd_recovery object')
    data = obj['data']; k = data['attempts_per_arm']; cases = []
    for case in data['cases']:
        failure = repo.object(case['failure_id'])
        attempts = [_attempt(repo, obj, failure, entry, k) for entry in case.get('attempts', [])]
        arms = {role: _arm([a for a in attempts if a['role'] == role], k) for role in ROLES}
        cases.append({'failure_id': failure['id'], 'sample_id': failure['data']['sample_id'],
                      'dataset': failure['data'].get('dataset'), 'failure_step': failure['data'].get('failure_step'),
                      'arms': arms, 'student_delta': _student_delta(repo, obj, arms['student_before'], arms['student_after'])})
    metrics = []
    frozen = data['cohort'].get('frozen') is True
    for role in ROLES:
        arms = [case['arms'][role] for case in cases]
        known = sum(a['known'] for a in arms); expected = len(cases) * k
        successes = sum(a['successes'] for a in arms); failures = sum(a['failures'] for a in arms)
        complete = bool(expected) and known == expected and frozen and all(a['status'] == 'measured' for a in arms)
        metric = {'id': role, 'label': LABELS[role] + ' · avg@' + str(k), 'value': successes / expected if complete else None,
                  'known': known, 'expected': expected, 'unknown': expected - known, 'successes': successes, 'failures': failures,
                  'status': 'measured' if complete else 'partial' if known else 'unmeasured',
                  'reason': '固定现场全集、完整尝试预算上的平均验证成功率；不是 pass@K' if complete else '现场集未冻结或存在未测／条件不符，保留分母但不补零'}
        if any(a['status'] == 'incomparable' for a in arms) and not complete:metric['status'] = 'incomparable'
        metrics.append(metric)
    deltas = [case['student_delta'] for case in cases]; known_delta = [d['value'] for d in deltas if d['status'] == 'measured']
    metrics.append({'id': 'student_delta', 'label': 'Student 同状态恢复变化',
                    'value': sum(known_delta) / len(deltas) if deltas and len(known_delta) == len(deltas) and frozen else None,
                    'known': len(known_delta), 'expected': len(deltas), 'unknown': len(deltas) - len(known_delta),
                    'status': 'measured' if deltas and len(known_delta) == len(deltas) and frozen else 'incomparable' if any(d['status'] == 'incomparable' for d in deltas) else 'partial' if known_delta else 'unmeasured',
                    'reason': '固定同一批现场的训练后 − 训练前 avg@K；不单独证明 OPD 因果提升'})
    measured = sum(m['status'] == 'measured' for m in metrics)
    status = 'measured' if measured == len(metrics) else 'partial' if measured else 'incomparable' if any(m['status'] == 'incomparable' for m in metrics) else 'unmeasured'
    return {'id': obj['id'], 'scope': obj.get('scope', {}), 'demo': bool(obj.get('demo')), 'content_hash': obj['content_hash'],
            'cohort': _cohort(data), 'status': status, 'reason': '只使用此声明中的固定现场和明确执行记录；教师打分、续跑与自主任务结果分别判断',
            'attempts_per_arm': k, 'roles': [{'id': r, 'label': LABELS[r]} for r in ROLES], 'cases': cases, 'metrics': metrics,
            'full_task': _full_task(repo, obj), 'claims': {'causal_training_attribution': False, 'final_generalization': False}}
