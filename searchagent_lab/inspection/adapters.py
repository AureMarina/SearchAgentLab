"""Lossless view adapters with JSON-pointer provenance.

Adapters select *recorded* fields. They never infer hidden model inputs from a
final conversation, parse an answer, award a score, or invent tool returns.
Unrecognised fields are always available in the complete raw JSON tree.
"""
from __future__ import annotations
import json
from typing import Any, Iterator

ADAPTER_VERSION = 'explicit-fields-v2'

# Small, original JSON readers; no upstream implementation is copied/imported.
# Supported public shapes (checked 2026-10-09):
# https://inspect.aisi.org.uk/reference/inspect_ai.log.html#evalsample
# https://inspect.aisi.org.uk/reference/inspect_ai.event.html
# https://inspect.aisi.org.uk/reference/inspect_ai.model.html#modeloutput
# https://evalscope.readthedocs.io/zh-cn/v1.7.1/user_guides/agent.html
# https://github.com/modelscope/evalscope/blob/main/evalscope/api/agent/loop.py
# https://evalscope.readthedocs.io/zh-cn/latest/advanced_guides/add_benchmark.html
# Unknown fields, attachment references and condensed event pools stay raw.
PURPOSE_VALUES = {
    'agent_eval': 'agent_eval', 'checkpoint_eval': 'checkpoint_eval',
    'train_rollout': 'train_rollout', 'training_rollout': 'train_rollout',
    'harness_check': 'harness_check', 'unknown': 'unknown',
    'sft_demonstration':'sft_demonstration',
}
TRAINING_FIELDS = (
    'train_run_id', 'global_step', 'checkpoint_id', 'policy_version',
    'rollout_group_id', 'reward', 'rewards', 'reward_components', 'advantage',
    'advantages', 'old_logprobs', 'logprobs', 'loss_mask', 'response_mask',
    'is_truncated',
)


def pointer(base: str, key: Any) -> str:
    return base + '/' + str(key).replace('~', '~0').replace('/', '~1')


def resolve(value: Any, path: str) -> Any:
    if path == '':
        return value
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('Evidence path must be a JSON pointer')
    for bit in path[1:].split('/'):
        key = bit.replace('~1', '/').replace('~0', '~')
        if isinstance(value,list):
            if not key.isascii() or not key.isdecimal() or (key.startswith('0') and key!='0'):
                raise ValueError('Array JSON pointer must use a canonical nonnegative index')
            value=value[int(key)]
        else: value=value[key]
    return value


def leaves(value: Any, path: str = '') -> Iterator[tuple[str, Any]]:
    """Iterative traversal: useful even while all UI branches are collapsed."""
    todo = [(path, value)]
    while todo:
        p, v = todo.pop()
        if isinstance(v, dict) and v:
            todo.extend((pointer(p, k), x) for k, x in reversed(list(v.items())))
        elif isinstance(v, list) and v:
            todo.extend((pointer(p, i), v[i]) for i in range(len(v)-1, -1, -1))
        else:
            yield p, v


def text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)


def _first(record: dict, keys: tuple[str, ...], base: str = '') -> tuple[Any, str | None]:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key], pointer(base, key)
    return None, None


FIELD_LABELS = {
    'input': '本步记录的输入', 'observation': '本步记录的 observation（不推断完整 prompt）',
    'output': '模型 / 步骤原始输出', 'model_response': '模型原始输出', 'thought': '显式记录的 thought',
    'action': '记录的 action', 'tool_call': '工具调用', 'tool_calls': '工具调用列表',
    'tool_response': '工具完整返回', 'model_visible': '模型实际可见内容（单独记录）',
    'chat_completions': '记录的 chat_completions（不倒推早期上下文）',
    'messages': '本步保存的 messages', 'content': '消息原始内容', 'function_call': '函数调用',
    'tool_call_id': '工具关联 ID', 'name': '名称', 'reward': '本步 reward（原值）',
    'advantage': '本步 advantage（原值）', 'mc_return': '本步 return（原值）',
    'done': '结束标志', 'status': '执行状态', 'error': '记录到的异常',
    'info': 'Info', 'metadata': 'Metadata', 'logprobs': 'Log probabilities',
    'prompt_ids': 'Prompt token IDs', 'response_ids': 'Response token IDs',
    'event': '事件类型', 'payload': '事件原始数据', 'message_id': '消息关联 ID',
    'model': '记录的模型', 'result': '工具事件返回值', 'arguments': '调用参数',
    'function': '工具函数', 'timestamp': '记录时间', 'latency_ms': '耗时（ms）',
    'token_usage': '记录的 token 用量', 'stop_reason': '模型生成停止原因',
    'score': '评分器原始记录', 'reason': '记录的原因', 'explanation': '评分依据',
}
PRIORITY = tuple(FIELD_LABELS)


def _step(raw: Any, path: str, pos: int, message: bool = False) -> dict:
    if not isinstance(raw, dict):
        return {'index': pos, 'kind': 'raw', 'title': '非结构化步骤', 'pointer': path,
                'fields': [{'name': 'value', 'label': '原始值', 'pointer': path, 'value': raw}]}
    role = raw.get('role') if message else None
    kind = role or raw.get('kind') or raw.get('type') or ('tool' if 'tool_call' in raw else 'step')
    index = raw.get('index', pos)
    fields = []
    keys = [k for k in PRIORITY if k in raw]
    keys += [k for k in raw if k not in PRIORITY and k not in ('index', 'kind', 'title', 'role')]
    for k in keys:
        fields.append({'name': k, 'label': FIELD_LABELS.get(k, k), 'pointer': pointer(path, k), 'value': raw[k]})
    return {'index': index, 'position': pos, 'kind': str(kind), 'title': str(raw.get('title') or (f'{role} 消息' if role else f'{kind} 记录')),
            'pointer': path, 'fields': fields, 'message_only': message}


def _set_recorded(out: dict, target: str, obj: dict, key: str, base: str) -> None:
    if key in obj and obj[key] is not None:
        out[target] = obj[key]
        out['pointers'][target] = pointer(base, key)


def _record_context(out: dict, sources: list[tuple[dict, str]]) -> None:
    """Purpose is an explicit declaration, never inferred from reward or epoch."""
    evidence = []
    training = []
    seen = set()
    for obj, base in sources:
        for container, p in [(obj, base)] + ([(obj['metadata'], pointer(base, 'metadata'))]
                                             if isinstance(obj.get('metadata'), dict) else []):
            for key in ('record_purpose', 'evaluation_purpose', 'purpose', 'origin', 'record_kind', 'usage_context'):
                if key not in container or container[key] is None:
                    continue
                value, path = container[key], pointer(p, key)
                if isinstance(value, dict) and key == 'record_purpose' and 'value' in value:
                    value, path = value['value'], pointer(path, 'value')
                # Origin is often a filename/provider: only explicit known values count.
                if key in ('origin', 'record_kind', 'usage_context') and (not isinstance(value, str) or value not in PURPOSE_VALUES):
                    continue
                if path not in seen:
                    evidence.append({'value': value, 'pointer': path})
                    seen.add(path)
            for key in TRAINING_FIELDS:
                if key in container and container[key] is not None:
                    training.append({'name': key, 'value': container[key], 'pointer': pointer(p, key)})
    values = [PURPOSE_VALUES.get(e['value'], 'unknown') if isinstance(e['value'], str) else 'unknown' for e in evidence]
    purpose = values[0] if values and len(set(values)) == 1 else 'unknown'
    source = 'unrecorded' if not evidence else 'recorded' if len(set(values)) == 1 else 'conflicting'
    out['record_purpose'] = {'value': purpose, 'source': source, 'evidence': evidence}
    if evidence and source == 'recorded':
        out['record_purpose']['pointer'] = evidence[0]['pointer']
    if source == 'conflicting':
        out['warnings'].append('用途标记互相冲突；保留 unknown，不把记录自动归入评测或训练。')
    out['training_fields'] = training


def _inspect_sample(obj: Any) -> bool:
    return (isinstance(obj, dict) and isinstance(obj.get('id'), (str, int))
            and not isinstance(obj.get('id'), bool) and type(obj.get('epoch')) is int
            and isinstance(obj.get('input'), (str, list)) and isinstance(obj.get('target'), (str, list))
            and isinstance(obj.get('messages'), list) and isinstance(obj.get('output'), dict)
            and isinstance(obj['output'].get('choices'), list))


def _inspect_log(obj: Any) -> bool:
    return (isinstance(obj, dict) and type(obj.get('version')) is int
            and isinstance(obj.get('eval'), dict) and 'task' in obj['eval'] and 'model' in obj['eval']
            and isinstance(obj.get('samples'), list))


def _model_output(out: dict, obj: dict, base: str) -> None:
    """Copy an explicit completion/choice, without deriving an Agent stop reason."""
    if not isinstance(obj.get('output'), dict):
        return
    output, p = obj['output'], pointer(base, 'output')
    if out['raw_final_output'] is None:
        _set_recorded(out, 'raw_final_output', output, 'completion', p)
    _set_recorded(out, 'model_stop_reason', output, 'stop_reason', p)
    choices = output.get('choices')
    if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
        choice, cp = choices[0], pointer(pointer(p, 'choices'), 0)
        if out['model_stop_reason'] is None:
            _set_recorded(out, 'model_stop_reason', choice, 'stop_reason', cp)
        if isinstance(choice.get('message'), dict) and out['raw_final_output'] is None:
            _set_recorded(out, 'raw_final_output', choice['message'], 'content', pointer(cp, 'message'))


def _scorer(out: dict, name: str, score: dict, path: str, *, evalscope: bool = False) -> None:
    answer_key = 'extracted_prediction' if evalscope else 'answer'
    metrics = score['value'].items() if evalscope else [(name, score.get('value'))]
    for metric, value in metrics:
        paths = {k: pointer(path, k) for k in ('value', answer_key, 'reason', 'explanation', 'metadata') if k in score}
        if evalscope:
            paths['value'] = pointer(pointer(path, 'value'), metric)
        out['scorer_results'].append({
            'name': metric, 'value': value, 'answer': score.get(answer_key),
            'reason': score.get('reason'), 'explanation': score.get('explanation'),
            'pointer': path, 'pointers': paths,
        })


def _event_steps(events: list, base: str, *, dialect: str, messages: list | None = None,
                 messages_base: str = '', warnings: list) -> list[dict]:
    by_id: dict[str, list[tuple[dict, str]]] = {}
    for i, message in enumerate(messages or []):
        if isinstance(message, dict) and isinstance(message.get('id'), str):
            by_id.setdefault(message['id'], []).append((message, pointer(messages_base, i)))
    result = []
    titles = {'model': '模型调用', 'model_generate': '模型生成', 'tool': '工具调用与返回',
              'tool_call': '工具调用', 'tool_result': '工具结果', 'score': '评分',
              'env_exec': '环境命令', 'nudge': '系统提醒', 'error': '异常', 'submit': '提交事件',
              'sample_limit': '触发执行限制'}
    for i, event in enumerate(events):
        p = pointer(base, i)
        step = _step(event, p, i)
        if not isinstance(event, dict):
            result.append(step)
            continue
        event_type = event.get('event') if dialect == 'inspect' else event.get('type')
        event_type = event_type if isinstance(event_type, str) else 'unknown_event'
        # Distinguish external ToolEvent from the local tool_response contract.
        step['kind'] = 'tool_event' if event_type == 'tool' else event_type
        step['title'] = titles.get(event_type, event_type)
        step['index'] = event.get('step', i) if dialect == 'evalscope' else i
        if 'role' in event:
            step['fields'].append({'name': 'role', 'label': '记录的模型角色',
                                   'pointer': pointer(p, 'role'), 'value': event['role']})
            if dialect == 'inspect' and event_type == 'model' and event['role'] == 'grader':
                step['title'] = '评分模型调用'
        if dialect == 'evalscope':
            message_id = event.get('message_id')
            matches = by_id.get(message_id, []) if isinstance(message_id, str) else []
            if message_id is not None and len(matches) != 1:
                warnings.append(f'{p} 的 message_id 未找到唯一消息；保留事件，不猜测关联。')
            elif len(matches) == 1:
                message, mp = matches[0]
                step['fields'].append({'name': 'message', 'label': '按 message_id 关联的原始消息', 'pointer': mp, 'value': message})
                name = 'model_response' if event_type == 'model_generate' else 'tool_response' if event_type == 'tool_result' else None
                expected_role = (message.get('role') == 'assistant' if name == 'model_response'
                                 else message.get('role') in ('tool', 'user'))
                if name and 'content' in message and expected_role:
                    step['fields'].append({'name': name, 'label': '模型输出消息' if name == 'model_response' else '工具结果消息（记录值）',
                                           'pointer': pointer(mp, 'content'), 'value': message['content']})
                elif name and not expected_role:
                    warnings.append(f'{p} 关联消息的 role 与事件类型不符；仅保留原始消息。')
            if event_type == 'tool_call' and isinstance(event.get('payload'), dict):
                step['fields'].append({'name': 'tool_call', 'label': '工具调用（原始 payload）',
                                       'pointer': pointer(p, 'payload'), 'value': event['payload']})
            # payload.preview is not a full tool result. Never promote it.
        result.append(step)
    return result


def _external_adapter(out: dict, obj: dict, base: str) -> bool:
    inspect = _inspect_sample(obj)
    trace = obj.get('agent_trace')
    evalscope = (isinstance(trace, dict) and isinstance(trace.get('events'), list)
                 and (isinstance(trace.get('strategy'), str) or isinstance(trace.get('framework'), str)))
    if not inspect and not evalscope:
        return False
    if not isinstance(out['scores'], dict):
        out['warnings'].append('scores 不是对象；不把它当作 EM。')
        out['scores'] = {}
    out['adapter'] = 'inspect-json-sample' if inspect else 'evalscope-agent-trace'
    _model_output(out, obj, base)
    if inspect:
        _set_recorded(out, 'question', obj, 'input', base)
        _set_recorded(out, 'gold_answers', obj, 'target', base)
        _set_recorded(out, 'outcome_evidence', obj, 'store', base)
        _set_recorded(out, 'runtime_error', obj, 'error', base)
        if out['termination_reason'] is None and isinstance(obj.get('limit'), dict):
            limit, lp = obj['limit'], pointer(base, 'limit')
            value, path = _first(limit, ('reason', 'type'), lp)
            if path is not None:
                out['termination_reason'], out['pointers']['termination_reason'] = value, path
        if isinstance(obj.get('scores'), dict):
            for name, score in obj['scores'].items():
                if isinstance(score, dict) and 'value' in score:
                    _scorer(out, name, score, pointer(pointer(base, 'scores'), name))
        if len(out['scorer_results']) == 1:
            score = out['scorer_results'][0]
            if isinstance(score['answer'], str) and out['parsed_answer'] is None:
                out['parsed_answer'] = score['answer']
                out['pointers']['parsed_answer'] = score['pointers']['answer']
        if obj.get('attachments') or obj.get('events_data'):
            out['warnings'].append('Inspect 共享引用 / attachment 保持原样；需要完整内容时请用 Inspect 官方接口展开后导出 JSON。')
        events, ep = obj.get('events'), pointer(base, 'events')
    else:
        if isinstance(obj.get('sample'), dict):
            sample, sp = obj['sample'], pointer(base, 'sample')
            if out['question'] is None: _set_recorded(out, 'question', sample, 'input', sp)
            if not out['gold_answers']: _set_recorded(out, 'gold_answers', sample, 'target', sp)
        # Explicit documented Score / SampleScore objects only; no recursive hunt.
        score, sp = obj.get('score'), pointer(base, 'score')
        if isinstance(obj.get('sample_score'), dict):
            score, sp = obj['sample_score'].get('score'), pointer(pointer(base, 'sample_score'), 'score')
        if isinstance(score, dict) and isinstance(score.get('value'), dict):
            _scorer(out, 'EvalScope score', score, sp, evalscope=True)
            if not out['scores']:
                # Preserve literal metric names; acc/reward are never renamed em.
                _set_recorded(out, 'scores', score, 'value', sp)
            if out['parsed_answer'] is None: _set_recorded(out, 'parsed_answer', score, 'extracted_prediction', sp)
            if out['raw_final_output'] is None: _set_recorded(out, 'raw_final_output', score, 'prediction', sp)
        events, ep = trace['events'], pointer(pointer(base, 'agent_trace'), 'events')
        out['warnings'].append('EvalScope 事件按原序展示；工具结果消息不等于工具未处理的完整返回，preview 不补为完整结果。')
    if isinstance(out['gold_answers'], str):
        out['gold_answers'] = [out['gold_answers']]
    elif isinstance(out['gold_answers'], list):
        out['gold_answers'] = [x for x in out['gold_answers'] if isinstance(x, str)]
    else:
        out['gold_answers'] = []
    if isinstance(events, list) and events:
        steps = _event_steps(events, ep, dialect='inspect' if inspect else 'evalscope',
                             messages=obj.get('messages') if isinstance(obj.get('messages'), list) else [],
                             messages_base=pointer(base, 'messages'), warnings=out['warnings'])
        out['trajectories'] = [{'name': 'Inspect events' if inspect else 'EvalScope events',
                                'pointer': base, 'kind': 'steps', 'source_kind': 'events',
                                'reward': None, 'step_count': len(steps), 'steps': steps}]
        out['execution_kind'] = 'recorded_steps'
    elif isinstance(obj.get('messages'), list):
        steps = [_step(m, pointer(pointer(base, 'messages'), i), i, True) for i, m in enumerate(obj['messages'])]
        out['trajectories'] = [{'name': 'messages', 'pointer': base, 'kind': 'messages',
                                'reward': None, 'step_count': len(steps), 'steps': steps}]
        out['execution_kind'] = 'recorded_messages'
        out['warnings'].append('没有逐次调用事件；只展示已保存的 messages，不倒推历史模型输入。')
    else:
        out['execution_kind'] = 'final_only' if out['raw_final_output'] is not None or out['scorer_results'] else 'unknown'
    return True


def normalize_record(raw: Any) -> dict:
    """Return UI view plus pointers, without altering raw."""
    out = {'adapter_version': ADAPTER_VERSION, 'adapter': 'raw', 'warnings': [], 'trajectories': [],
           'question': None, 'dataset': None, 'sample_id': None, 'scores': {}, 'gold_answers': [],
           'raw_final_output': None, 'parsed_answer': None, 'runtime_status': None, 'termination_reason': None,
           'reward': None, 'pointers': {}, 'execution_kind': 'unknown',
           'model_stop_reason': None, 'runtime_error': None, 'outcome_evidence': None,
           'scorer_results': [], 'training_fields': [],
           'record_purpose': {'value': 'unknown', 'source': 'unrecorded', 'evidence': []}}
    if not isinstance(raw, dict):
        out['warnings'].append('不是对象记录；仍可展开完整原始 JSON。')
        return out
    obj, base = raw, ''
    if raw.get('type') == 'trajectory' and isinstance(raw.get('data'), dict):
        obj, base = raw['data'], '/data'
    elif isinstance(raw.get('episode'), dict):
        obj, base = raw['episode'], '/episode'
    if _inspect_log(obj):
        if len(obj['samples']) == 1 and _inspect_sample(obj['samples'][0]):
            obj, base = obj['samples'][0], pointer(pointer(base, 'samples'), 0)
        else:
            _record_context(out, [(raw, '')])
            out['warnings'].append('Inspect EvalLog JSON 需单 sample，或先导出为 sample JSON 数组 / JSONL；未擅自选择或合并样本。')
            return out
    mapping = {
        'question': ('question',), 'dataset': ('dataset',), 'sample_id': ('sample_id','id','source_id'),
        'scores': ('scores',), 'gold_answers': ('gold_answers','answers','answer_aliases'),
        'raw_final_output': ('raw_final_output','final_output'), 'parsed_answer': ('parsed_answer',),
        'runtime_status': ('runtime_status',), 'termination_reason': ('termination_reason',), 'reward': ('reward',),
    }
    for target, keys in mapping.items():
        v, p = _first(obj, keys, base)
        if p is not None:
            out[target] = v
            out['pointers'][target] = p
    _record_context(out, [(obj, base)] + ([(raw, '')] if obj is not raw else []))
    if _external_adapter(out, obj, base):
        return out
    if not isinstance(out['scores'], dict):
        out['warnings'].append('scores 不是对象；不把它当作 EM。')
        out['scores'] = {}
    if 'em' in out['scores'] and out['scores']['em'] is not None:
        em=out['scores']['em']
        if isinstance(em,bool) or not isinstance(em,(int,float)) or em not in (0,1):
            out['warnings'].append('记录的 EM 不是数值 0/1；保留原值，不据此分类正确或错误。')
    if isinstance(out['gold_answers'], str):
        out['gold_answers'] = [out['gold_answers']]
    if not isinstance(out['gold_answers'], list):
        out['gold_answers'] = []
    out['gold_answers'] = [x for x in out['gold_answers'] if isinstance(x, str)]
    if isinstance(obj.get('task'), dict) and out['question'] is None:
        v, p = _first(obj['task'], ('question','prompt'), pointer(base, 'task'))
        out['question'] = v
        if p is not None: out['pointers']['question'] = p
    if 'is_correct' in obj and isinstance(obj['is_correct'], bool):
        out['is_correct'] = obj['is_correct']
        out['pointers']['is_correct'] = pointer(base, 'is_correct')
    # Dataset rows and retrieval-only fixtures must not appear as agent rollouts.
    if isinstance(obj.get('trajectories'), list):
        out['adapter'] = 'rllm-episode'
        roots = [(t, pointer(pointer(base, 'trajectories'), i)) for i,t in enumerate(obj['trajectories'])]
    else:
        roots = [(obj, base)]
    for n, (t, p) in enumerate(roots):
        if not isinstance(t, dict): continue
        steps, kind = None, None
        if isinstance(t.get('steps'), list):
            steps, kind = t['steps'], 'steps'
            if out['adapter'] == 'raw': out['adapter'] = 'steps'
        elif isinstance(t.get('messages'), list):
            steps, kind = t['messages'], 'messages'
            if out['adapter'] == 'raw': out['adapter'] = 'messages'
        if steps is not None:
            out['trajectories'].append({'name': str(t.get('name') or t.get('uid') or ('agent' if len(roots)==1 else f'agent-{n+1}')),
                'pointer': p, 'kind': kind, 'reward': t.get('reward'), 'step_count': len(steps),
                'steps': [_step(s, pointer(pointer(p, kind), i), i, kind=='messages') for i,s in enumerate(steps)]})
    if out['trajectories']:
        out['execution_kind'] = 'recorded_steps' if any(t['kind']=='steps' for t in out['trajectories']) else 'recorded_messages'
        if any(t['kind']=='messages' for t in out['trajectories']):
            out['warnings'].append('仅消息日志：按消息原序展示，不推断每轮完整模型输入或被截断前的工具返回。')
    elif any(k in obj for k in ('diagnostic_tags','selection_reason','query_kind','supporting_titles')) and not any(k in obj for k in ('raw_final_output','final_output','parsed_answer','scores')):
        out['execution_kind'] = 'reference_only'
        out['adapter'] = 'reference'
        out['warnings'].append('识别为题单 / 参考检索记录，不是模型执行轨迹，不进行模型失败诊断。')
    elif 'answers' in obj and 'question' in obj and not any(k in obj for k in ('raw_final_output','final_output','parsed_answer','scores')):
        out['execution_kind'] = 'reference_only'
        out['adapter'] = 'reference'
        out['warnings'].append('只有题目与参考答案；未发现模型执行证据。')
    elif out['raw_final_output'] is not None or out['parsed_answer'] is not None or out['scores']:
        out['execution_kind'] = 'final_only'
    else:
        out['warnings'].append('没有识别到 steps / messages。保留原始结构；不猜测私有字段的执行含义。')
    return out
