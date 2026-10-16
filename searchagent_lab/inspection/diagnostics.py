"""Versioned, local evidence checks. Findings are not causal proof or re-scoring.

No LLM calls, no reward writes. Each finding contains literal source pointers,
rule conditions, limitations, and a suggested verification step.
"""
from __future__ import annotations
import hashlib
import json
import re
import string
from typing import Any
from .adapters import normalize_record, resolve, pointer, text

RULESET_VERSION = 'trace-evidence-v1'
RULES = {
 'json_invalid': ('JSON 无法解析', 'error', 'observed'),
 'reference_only': ('参考材料，不是模型轨迹', 'info', 'insufficient'),
 'no_steps': ('缺少逐步执行记录', 'gap', 'insufficient'),
 'unscored': ('缺少记录的答案评分', 'gap', 'insufficient'),
 'runtime_error': ('记录到运行异常 / 超时', 'error', 'observed'),
 'tool_error': ('步骤记录到工具 / 执行异常', 'error', 'observed'),
 'tool_args': ('工具 arguments 不是合法 JSON', 'error', 'observed'),
 'tool_empty': ('记录到空工具返回', 'warning', 'observed'),
 'tool_missing_return': ('工具返回未记录', 'gap', 'insufficient'),
 'visible_missing': ('未单独记录模型可见内容', 'gap', 'insufficient'),
 'repeated_query': ('重复发出了相同查询', 'warning', 'review'),
 'alias_lost': ('答案词面在完整返回中，但未出现在可见内容中', 'warning', 'review'),
 'parser_mismatch': ('答案标签与记录的解析答案不同', 'warning', 'review'),
 'parser_missing': ('有最终输出，但没有解析答案', 'gap', 'insufficient'),
 'score_mismatch': ('解析答案命中别名，但记录 EM 为 0', 'warning', 'review'),
 'budget_stop': ('记录的终止原因涉及预算 / 长度', 'warning', 'observed'),
 'uniform_rewards': ('本记录内各轨迹 reward 相同', 'info', 'review'),
}


def normalize_answer(s: str) -> str:
    """Diagnostic only: English SQuAD-like surface normalisation, NOT scoring."""
    s = s.lower().translate(str.maketrans('', '', string.punctuation))
    s = re.sub(r'\b(a|an|the)\b', ' ', s)
    return ' '.join(s.split())


def _contains(v: Any, alias: str) -> bool:
    value = normalize_answer(text(v))
    return bool(re.search(r'(?<!\w)' + re.escape(normalize_answer(alias)) + r'(?!\w)', value))


def diagnose(raw: Any, *, parse_error: str | None = None) -> dict:
    view = normalize_record(raw)
    findings: list[dict] = []

    def add(rule: str, paths: list[str], explanation: str, check: str, limitation: str, step: Any = None):
        title, level, basis = RULES[rule]
        evidence = []
        for p in dict.fromkeys(paths):
            try: v = resolve(raw, p)
            except (ValueError, KeyError, TypeError, IndexError): continue
            rendered = text(v)
            evidence.append({'pointer': p, 'preview': rendered[:600], 'truncated': len(rendered)>600})
        identity = json.dumps([RULESET_VERSION, rule, paths, step], ensure_ascii=False, sort_keys=True)
        findings.append({'id': hashlib.sha256(identity.encode()).hexdigest()[:20], 'rule':rule,
            'title':title, 'level':level, 'basis':basis, 'step_index':step, 'evidence':evidence,
            'explanation':explanation, 'next_check':check, 'limitation':limitation})

    if parse_error:
        add('json_invalid', [], parse_error, '检查该文件原始行；修正源文件后另存导入，不覆盖现有证据。', '其他有效行仍可检查；这行没有被静默丢弃。')
    elif view['execution_kind'] == 'reference_only':
        add('reference_only', [], '这条记录只有题单或参考检索材料。', '先导入实际 checkpoint 的输出及工具日志，再检查模型失败。', '不能把参考段落当成模型看见的文本或模型执行步骤。')
    else:
        if not any(t['steps'] for t in view['trajectories']):
            add('no_steps', [], '没有可定位到步骤的执行日志。', '在原有日志出口保存 steps 或 messages；保留各轮实际可见输入。', '不能从错误答案反推出最早失败位置。')
        if not view['scores'] and 'is_correct' not in view:
            add('unscored', [], '没有原始 scores 或 is_correct；reward 不被当成答案正确率。', '接入原评分器的结果及解析答案；不要在查看器补奖。', '未评分不等于答案错误。')
        if view['runtime_status'] in ('error','timeout','cancelled','failed'):
            add('runtime_error', [view['pointers']['runtime_status']], '终局运行状态明确记录了异常。', '查看关联步骤 error 与终止原因，区分环境故障和模型决策。', '运行异常不自动改写答案得分。')
        reason = str(view['termination_reason'] or '')
        if re.search(r'budget|max[_ -]?(?:tokens|steps|turns|length|tool)|length_limit', reason, re.I):
            add('budget_stop', [view['pointers']['termination_reason']], '原始终止原因：'+reason, '结合实际调用数、token 使用量与协议预算核对。', '达到预算不等于预算是首要根因，也不意味着增加预算一定有效。')
        queries: dict[str, list[tuple[str, Any]]] = {}
        tool_steps = []
        for trajectory in view['trajectories']:
            for s in trajectory['steps']:
                p, idx = s['pointer'], s['index']
                obj = resolve(raw, p)
                if not isinstance(obj, dict): continue
                error_paths = []
                if obj.get('error'): error_paths.append(pointer(p,'error'))
                if obj.get('status') in ('error','timeout','failed'): error_paths.append(pointer(p,'status'))
                response = obj.get('tool_response')
                if isinstance(response, dict) and response.get('error'):
                    error_paths.append(pointer(pointer(p,'tool_response'),'error'))
                if obj.get('is_error') is True: error_paths.append(pointer(p,'is_error'))
                if error_paths:
                    add('tool_error', error_paths, '执行字段明确记录了 error / timeout / failed。', '定位完整错误与关联调用参数；确认是否影响后续可用证据。', '不依据错误字符串猜测因果或责任方。', idx)
                calls = []
                if isinstance(obj.get('tool_call'), dict): calls.append((obj['tool_call'],pointer(p,'tool_call')))
                if isinstance(obj.get('tool_calls'), list):
                    calls.extend((c,pointer(pointer(p,'tool_calls'),i)) for i,c in enumerate(obj['tool_calls']) if isinstance(c,dict))
                if isinstance(obj.get('function_call'), dict): calls.append((obj['function_call'],pointer(p,'function_call')))
                for call, cp in calls:
                    if isinstance(call.get('function'),dict): call,cp=call['function'],pointer(cp,'function')
                    arg = call.get('arguments')
                    ap = pointer(cp,'arguments')
                    parsed = arg
                    if isinstance(arg,str):
                        try: parsed=json.loads(arg)
                        except (ValueError,RecursionError):
                            add('tool_args',[ap], '此工具调用的 arguments 字符串不能解析为 JSON。', '核对原 harness 是否要求 JSON arguments，查看解析器是否拒绝该调用。', '若私有工具协议允许普通字符串，此规则可人工驳回；不等于已证实调用失败。', idx)
                            parsed=None
                    if isinstance(parsed,dict) and isinstance(parsed.get('query'),str):
                        qp=pointer(ap,'query') if isinstance(arg,dict) else ap
                        key=' '.join(parsed['query'].casefold().split())
                        if key: queries.setdefault(key,[]).append((qp,idx))
                if s['kind']=='tool' and not s.get('message_only'):
                    tool_steps.append((obj,p,idx))
                    if 'tool_response' not in obj:
                        add('tool_missing_return',[p], '这条工具步骤没有 tool_response 字段。', '补充原执行日志，而不是用参考检索材料代替。', '这里只能认定未记录，不能认定检索器没有返回。', idx)
                    elif response in ('',[],{}):
                        add('tool_empty',[pointer(p,'tool_response')], '工具完整返回的记录为空。', '检查 query、过滤规则与检索服务返回状态。', '空结果可能是正常查询结果，不足以证明最早失败位置。', idx)
                    if 'tool_response' in obj and obj.get('model_visible') is None:
                        add('visible_missing',[pointer(p,'tool_response')], '完整返回存在，但截断 / 渲染后的可见内容未单独保存。', '在观察拼接出口记录 model_visible 后再判断证据是否被截断。', '不会把 tool_response 自动冒充 model_visible。', idx)
                    elif 'tool_response' in obj and obj.get('model_visible') is not None:
                        eligible=[a for a in view['gold_answers'] if len(normalize_answer(a))>=4 and normalize_answer(a) not in ('yes','no','true','false')]
                        hit=next((a for a in eligible if _contains(response,a) and not _contains(obj['model_visible'],a)),None)
                        if hit:
                            add('alias_lost',[pointer(p,'tool_response'),pointer(p,'model_visible')], f'词面检查发现别名 {hit!r} 仅在完整返回中。', '查看命中片段是否真是问题所需证据，以及截断 / 去重 / 排序发生在哪里。', '字符串命中不等于有效证据；可见文本可能用其他正确表达，需人工复核。', idx)
        for key, locations in queries.items():
            if len(locations)>1:
                add('repeated_query',[p for p,_ in locations],f'同一条记录内，该查询出现 {len(locations)} 次：{key}', '比较相邻工具返回；判断是否必要重试、服务异常，还是无效重复。', '不比较不同记录，也不把合理重试自动归因成模型循环。',locations[1][1])
        final=view['raw_final_output']; answer=view['parsed_answer']; pp=view['pointers']
        if final is not None and final!='' and answer in (None,''):
            add('parser_missing',[pp['raw_final_output']], '保存了最终输出，但没有非空 parsed_answer。', '检查 parser 日志和结束标记，区分缺少字段与实际解析失败。', '不能仅因字段未写入日志就宣称 parser 失败。')
        if isinstance(final,str) and isinstance(answer,str):
            tags=re.findall(r'<answer>(.*?)</answer>',final,re.S|re.I)
            if len(tags)==1 and tags[0].strip()!=answer.strip():
                add('parser_mismatch',[pp['raw_final_output'],pp['parsed_answer']], '唯一完整 <answer> 标签中的文本，与日志的 parsed_answer 不一致。', '对照该次运行的真实 parser 版本与多 final 处理规则。', '这不是替换 scorer；某些解析规则允许合法规范化或重写。')
        if isinstance(answer,str) and view['scores'].get('em')==0 and any(normalize_answer(answer)==normalize_answer(a) for a in view['gold_answers'] if normalize_answer(a)):
            add('score_mismatch',[pp['parsed_answer'],pointer(pp['scores'],'em'),pp['gold_answers']], '诊断用的英文表面规范化命中了某个答案别名，但记录的 EM 为 0。', '核对本次 gold 映射、别名集、答案提取与实际 scorer；人工决定是否是评分问题。', '诊断规范化不代表你的官方 scorer。原始 EM 保持 0，不自动修正。')
        rewards=[t.get('reward') for t in view['trajectories']]
        if len(rewards)>1 and all(isinstance(r,(int,float)) and not isinstance(r,bool) for r in rewards) and len(set(rewards))==1:
            add('uniform_rewards',[pointer(t['pointer'],'reward') for t in view['trajectories']], '本 Episode 各已记录 Trajectory 的 reward 相同。', '先确认这些轨迹是否真是同一问题的组采样，再核对实际 advantage。', '不同角色不一定是 RL group；不能据此证明 actor loss 或梯度为零。')
    counts={k:sum(f['basis']==k for f in findings) for k in ('observed','review','insufficient')}
    return {'ruleset':RULESET_VERSION, 'findings':findings, 'counts':counts,
            'root_cause':'未确定；自动检查只给出证据与候选，需人工复核。',
            'scope':'仅当前保存记录；不运行模型、不调用外部 API、不修改答案/评分/奖励。'}
