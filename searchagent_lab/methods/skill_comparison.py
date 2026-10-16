"""Explicit, controlled Skill comparisons over saved executions.

No evaluation is selected by recency or score. Empty matrix cells stay empty;
reported lifecycle utility does not enter these calculations. The existing
checkpoint/method-state comparison supplies the execution controls.
"""
from __future__ import annotations

import copy
import json
import sqlite3

from ..core import ValidationError
from ..read_cache import request_scope
from ..studio.evaluation import EvaluationViews, _fingerprint
from .schema import identifier
from .skills import member

ROWS = ('initial', 'current')
COLUMNS = ('off', 'initial_bank', 'current_bank')


def validate_comparison_contract(data):
    kind = data.get('comparison_type')
    if kind not in ('cross_matrix', 'skill_ablation'):
        raise ValidationError('skill_comparison.comparison_type is cross_matrix or skill_ablation')
    if kind == 'cross_matrix':
        for key in ('initial_checkpoint_id', 'current_checkpoint_id', 'initial_bank_id', 'current_bank_id'):
            identifier(data.get(key), key)
        cells = data.get('cells', [])
        if not isinstance(cells, list) or len(cells) > 6:
            raise ValidationError('Cross matrix accepts at most six explicit cells')
        seen, evaluations = set(), set()
        for cell in cells:
            if not isinstance(cell, dict) or cell.get('row_id') not in ROWS or cell.get('column_id') not in COLUMNS:
                raise ValidationError('Matrix cell needs an explicit row_id and column_id')
            key = (cell['row_id'], cell['column_id'])
            eid = identifier(cell.get('evaluation_id'), 'cell.evaluation_id')
            if key in seen or eid in evaluations:
                raise ValidationError('Duplicate matrix cell or reused evaluation; omit unmeasured cells')
            seen.add(key); evaluations.add(eid)
    else:
        identifier(data.get('checkpoint_id'), 'checkpoint_id')
        asset = bool(data.get('skill_asset_id'))
        legacy = any(data.get(key) for key in ('bank_id', 'member', 'member_content_hash'))
        if asset == legacy:
            raise ValidationError('Ablation needs skill_asset_id OR bank_id + member + member_content_hash')
        if asset:
            identifier(data['skill_asset_id'], 'skill_asset_id')
        else:
            identifier(data.get('bank_id'), 'bank_id')
            if not isinstance(data.get('member'), str) or not data['member']:
                raise ValidationError('Ablation needs exact frozen member')
            if _fingerprint(data.get('member_content_hash')) is None:
                raise ValidationError('member_content_hash must be a SHA256 fingerprint')
        for key in ('with_evaluation_id', 'without_evaluation_id'):
            if data.get(key) is not None: identifier(data[key], key)
        if data.get('with_evaluation_id') and data.get('with_evaluation_id') == data.get('without_evaluation_id'):
            raise ValidationError('Ablation arms must be distinct executions')


def validate_comparison_links(repo, obj, pending):
    """Validate identity/scope on import; compatibility remains a read result."""
    data, scope = obj['data'], obj.get('scope', {})
    if not repo.store or not scope.get('run_id'):
        raise ValidationError('Skill comparison requires an explicit registered run scope')
    checkpoint_fields = ('initial_checkpoint_id', 'current_checkpoint_id') if data['comparison_type'] == 'cross_matrix' else ('checkpoint_id',)
    for key in checkpoint_fields:
        cp = repo.store._get('checkpoints', data[key])
        if cp['run_id'] != scope['run_id']:
            raise ValidationError('Skill comparison checkpoint outside selected run')
    evaluation_ids = [cell['evaluation_id'] for cell in data.get('cells', [])] if data['comparison_type'] == 'cross_matrix' else [data[key] for key in ('with_evaluation_id', 'without_evaluation_id') if data.get(key)]
    for eid in evaluation_ids:
        ev = repo.store.evaluation(eid)
        if ev['run']['id'] != scope['run_id']:
            raise ValidationError('Skill comparison evaluation outside selected run')
        if bool(obj.get('demo')) != bool(ev.get('demo')):
            raise ValidationError('Synthetic and real Skill comparisons cannot be mixed')
    if data['comparison_type'] == 'skill_ablation' and data.get('bank_id'):
        frozen = member(repo._raw_object(data['bank_id'], pending)['data'], data['member'])
        if frozen['content_hash'] != data['member_content_hash']:
            raise ValidationError('Ablation member content hash differs from frozen bank')


class _ReadOnlyStudio:
    """Minimal profile reader for the shared comparison when no server exists."""
    def __init__(self, repo):
        self.store = repo.store

    def profile(self, experiment_id):
        path = self.store.data_dir / 'experiment_studio.sqlite3'
        if path.exists():
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
                row = conn.execute('SELECT data FROM profiles WHERE experiment_id=?', (experiment_id,)).fetchone()
            if row: return json.loads(row[0])
        return {'track': 'unassigned', 'kind': None, 'participates_in_main': False}

    def pair(self, a_id, b_id):
        from ..studio.repository import StudioRepository
        return StudioRepository.pair(self, a_id, b_id)

    def _context(self, evaluation):
        from ..studio.repository import StudioRepository
        return StudioRepository._context(self, evaluation)


def _pair(repo, a_id, b_id, axis, *, allow_same_harness=False):
    studio = getattr(repo.store, '_team_studio', None) or _ReadOnlyStudio(repo)
    return EvaluationViews(studio).pair(a_id, b_id, axis, allow_same_harness=allow_same_harness)


def _cell(repo, evaluation_id, checkpoint_id, bank_id, *, bank_expected=True, allowed_purposes=('checkpoint_eval',)):
    result = {'evaluation_id': evaluation_id, 'checkpoint_id': checkpoint_id, 'bank_id': bank_id,
              'status': 'unmeasured', 'value': None, 'reason': '未测：没有指定评测记录', 'reasons': [],
              'metric': 'macro_em', 'raw_value': None}
    if not evaluation_id: return result
    ev = repo.store.evaluation(evaluation_id)
    state = repo.evaluation_state(evaluation_id)
    binding = (state.get('binding') or {}).get('data', {})
    reasons = []
    if ev['checkpoint']['id'] != checkpoint_id: reasons.append('评测不是此行指定的 checkpoint')
    if _fingerprint(ev['checkpoint'].get('weights_sha256')) is None:
        reasons.append('模型权重 SHA256 未记录')
    if state.get('status') != 'recorded' or binding.get('binding_origin') != 'recorded_execution':
        reasons.append('缺少完整的执行时方法状态')
    if bank_expected:
        if bank_id is None:
            if binding.get('skills_disabled') is not True or binding.get('bank_id'):
                reasons.append('Off 需要明确记录 skills_disabled，缺失绑定不代表 Off')
        elif binding.get('bank_id') != bank_id or binding.get('skills_disabled') is True:
            reasons.append('执行时技能库与此列指定的冻结版本不同')
    if binding.get('independent') is not True or binding.get('autonomous') is not True:
        reasons.append('未声明为独立、自主执行的评测')
    if ev.get('purpose') not in allowed_purposes:
        reasons.append('交叉／消融评测需要显式 checkpoint_eval 用途' if allowed_purposes==('checkpoint_eval',)
                       else '需要显式的独立 Agent 或 checkpoint 评测用途')
    if ev['protocol'].get('groundtruth_to_agent') is not False or ev['protocol'].get('mode') != 'inference_only':
        reasons.append('需要不向 Agent 提供标准答案的 inference_only 协议')
    if not ev.get('protocol_complete') or not ev['readiness']['complete']:
        reasons.append('执行协议未完整记录')
    complete = ev['status'] == 'completed' and ev['stats']['fully_scored']
    if not complete: reasons.append('该评测尚未完成全部题目的评分')
    raw_value = ev['stats'].get('macro_em') if complete else None
    result.update(status='incomparable' if reasons else 'measured', value=None if reasons else raw_value,
                  raw_value=raw_value, reasons=reasons, reason='；'.join(reasons) if reasons else '指定冻结组合的已完成评测',
                  panel={'id':ev['panel']['id'], 'hash':ev['panel_hash'], 'split':ev['panel'].get('split'), 'size':ev['panel']['size']},
                  datasets=ev['stats'].get('datasets', {}), purpose=ev.get('purpose'), demo=bool(ev.get('demo')),
                  interpretation='训练／开发反馈' if ev['panel'].get('split') != 'test' else '测试题结果；生命周期冻结与训练隔离资格需单独核查')
    return result


def _effect(repo, key, label, a, b, axis):
    result = {'id':key, 'label':label, 'status':'unmeasured', 'value':None, 'metric':'macro_em_delta',
              'reason':'未测：需要两个明确的对照结果', 'a':a.get('evaluation_id'), 'b':b.get('evaluation_id'),
              'causal_instance_credit':False}
    if not a.get('evaluation_id') or not b.get('evaluation_id'): return result
    if a['status'] != 'measured' or b['status'] != 'measured':
        result.update(status='incomparable', reason='；'.join(dict.fromkeys(a.get('reasons', [])+b.get('reasons', []))) or '指定组合未满足评测条件')
        return result
    pair = _pair(repo, a['evaluation_id'], b['evaluation_id'], axis)
    reasons = list(pair['reasons'])
    # These comparisons ask about a bank or policy, not a simultaneous routing
    # intervention. A separate generic method_state comparison may study routing.
    sa, sb = [repo.evaluation_state(eid) for eid in (a['evaluation_id'], b['evaluation_id'])]
    for key_name in ('retrieval_config', 'extensions'):
        if sa.get('runtime', {}).get(key_name) != sb.get('runtime', {}).get(key_name):
            reasons.append('技能路由／其他方法状态改变，不能单独比较技能库或策略')
    if reasons or not pair['full'] or pair['macro_delta'] is None:
        result.update(status='incomparable', reason='；'.join(dict.fromkeys(reasons)) or '对照评测未全部完成', controls=pair['controls'])
    else:
        result.update(status='measured', value=pair['macro_delta'], reason='固定已登记条件下的描述性 EM 差异',
                      paired_usage=pair['paired_usage'], controls=pair['controls'], paired=pair['paired'], expected=pair['expected'])
    return result


def _matrix(repo, obj):
    data = obj['data']
    rows = [{'id':'initial','label':'初始策略 θ0','checkpoint_id':data['initial_checkpoint_id']},
            {'id':'current','label':'当前策略 θt','checkpoint_id':data['current_checkpoint_id']}]
    columns = [{'id':'off','label':'Off · 不提供外部技能','bank_id':None},
               {'id':'initial_bank','label':'初始技能 S0','bank_id':data['initial_bank_id']},
               {'id':'current_bank','label':'当前技能 St','bank_id':data['current_bank_id']}]
    declarations = {(c['row_id'],c['column_id']):c['evaluation_id'] for c in data.get('cells', [])}
    cells, indexed = [], {}
    for row in rows:
        for column in columns:
            key = (row['id'],column['id'])
            cell = _cell(repo, declarations.get(key), row['checkpoint_id'], column['bank_id'])
            cell.update(row_id=row['id'], column_id=column['id'])
            cells.append(cell); indexed[key] = cell
    effects = []
    for column in columns:
        effects.append(_effect(repo, 'policy_'+column['id'], '固定 '+column['label']+'：策略变化',
                               indexed['initial',column['id']], indexed['current',column['id']], 'checkpoint'))
    for row in rows:
        effects.append(_effect(repo, 'bank_'+row['id'], '固定 '+row['label']+'：技能库变化',
                               indexed[row['id'],'initial_bank'], indexed[row['id'],'current_bank'], 'method_state'))
    interaction = {'id':'interaction','label':'策略与技能库的交互差异','status':'unmeasured','value':None,
                   'metric':'difference_in_macro_em_deltas','reason':'需要四个交叉组合及其控变量检查', 'causal_instance_credit':False}
    required = [e for e in effects if e['id'] in ('policy_initial_bank','policy_current_bank','bank_initial','bank_current')]
    if all(e['status'] == 'measured' for e in required):
        interaction.update(status='measured', value=effects[4]['value']-effects[3]['value'],
                           reason='(θt,St − θt,S0) − (θ0,St − θ0,S0)；描述已测组合的相互适配，不证明因果训练信用')
    elif any(e['status']=='incomparable' for e in required):
        interaction.update(status='incomparable', reason='交叉组合的控变量或记录不完整')
    effects.append(interaction)
    return {'matrix':{'rows':rows,'columns':columns,'cells':cells},'effects':effects,
            'status':'measured' if all(c['status']=='measured' for c in cells) else 'partial',
            'reason':'只使用逐格显式指定的评测；未测组合不会自动补齐。'}


def _remove_at_pointer(raw, pointer):
    value = copy.deepcopy(raw)
    keys = [key.replace('~1','/').replace('~0','~') for key in pointer.lstrip('/').split('/')]
    parent = value
    for key in keys[:-1]: parent = parent[int(key)] if isinstance(parent,list) else parent[key]
    if isinstance(parent,list): parent.pop(int(keys[-1]))
    else: del parent[keys[-1]]
    return value


def _removal(repo, data):
    with_state, without_state = [repo.evaluation_state(data[key]) for key in ('with_evaluation_id','without_evaluation_id')]
    with_bank = with_state.get('bank_id')
    if not with_bank: return False, '保留技能的一组未登记冻结技能库'
    original = repo.object(with_bank)['data']
    if data.get('skill_asset_id'):
        ids = original.get('asset_ids', [])
        if ids.count(data['skill_asset_id']) != 1:
            return False, '消融目标不在保留组的明确版本清单中'
        expected = copy.deepcopy(original)
        expected['asset_ids'].remove(data['skill_asset_id'])
    else:
        if with_bank != data['bank_id']: return False, '保留组使用的不是指定冻结库'
        frozen = member(original, data['member'])
        if frozen['content_hash'] != data['member_content_hash']: return False, '目标内容哈希不一致'
        expected = _remove_at_pointer(original, frozen['pointer'])
    without_bank = without_state.get('bank_id')
    if without_bank:
        matches = expected == repo.object(without_bank)['data']
    else:
        # Off is permitted only if removing this skill leaves no other Skill
        # or behavioural bank content. Empty arrays/dicts carry no instructions.
        def empty(value):
            if isinstance(value,dict): return all(empty(v) for v in value.values())
            if isinstance(value,list): return not value
            return False
        matches = (without_state.get('binding') or {}).get('data',{}).get('skills_disabled') is True and empty(expected)
    return matches, '仅移除指定版本，其他技能包内容保持一致' if matches else '两组技能库除目标技能以外还有变化；不能解释为逐技能移除贡献'


def _ablation(repo, obj):
    data = obj['data']
    with_arm = _cell(repo, data.get('with_evaluation_id'), data['checkpoint_id'], data.get('bank_id'), bank_expected=bool(data.get('bank_id')))
    without_arm = _cell(repo, data.get('without_evaluation_id'), data['checkpoint_id'], None, bank_expected=False)
    effect = _effect(repo, 'external_utility', '此版本的外部边际效用', without_arm, with_arm, 'method_state')
    removal = {'status':'unmeasured','reason':'未测：需要保留与移除两组'}
    if data.get('with_evaluation_id') and data.get('without_evaluation_id'):
        matches, reason = _removal(repo, data)
        removal = {'status':'verified' if matches else 'incomparable','reason':reason}
        if not matches:
            effect.update(status='incomparable', value=None, reason=reason)
    return {'arms':{'with':with_arm,'without':without_arm},'effects':[effect], 'utility':effect.get('value'),
            'status':effect['status'],'reason':effect['reason'],'removal_check':removal,
            'interpretation':'当前策略、其他技能与固定评测条件下的外部边际效用；不等同于实例训练信用，也不证明技能已内化。'}


@request_scope
def skill_comparison(repo, comparison_id):
    obj = repo.object(comparison_id)
    if obj['kind'] != 'skill_comparison': raise ValidationError('Expected skill_comparison')
    if not repo.store: raise ValidationError('Skill comparison requires registered evaluations')
    data = obj['data']
    result = _matrix(repo,obj) if data['comparison_type']=='cross_matrix' else _ablation(repo,obj)
    with repo.lock:
        feedback = [r[0] for r in repo.conn.execute("SELECT DISTINCT o.id FROM method_links l JOIN objects o ON o.id=l.source_id WHERE l.target_id=? AND o.kind='skill_evolution'", (comparison_id,))]
    cells = result.get('matrix',{}).get('cells',[]) if data['comparison_type']=='cross_matrix' else list(result['arms'].values())
    has_test = any(c.get('panel',{}).get('split') == 'test' for c in cells)
    feedback_state = {'lifecycle_event_ids':feedback,'used_for_skill_iteration':bool(feedback),
                      'classification':'test_used_as_development_feedback' if has_test and feedback else 'development_feedback' if feedback else 'no_linked_lifecycle_feedback',
                      'final_generalization_claim':False}
    if has_test and feedback:
        result['reason'] += ' 本对照已用于技能迭代；相关测试题属于开发反馈，不能作为本轮最终泛化证据。'
    return {'id':obj['id'],'scope':obj.get('scope',{}),'comparison_type':data['comparison_type'],
            'demo':bool(obj.get('demo')), 'causal_instance_credit':False, 'selection':'explicit_evaluation_ids',
            'source_hash':obj['content_hash'], 'feedback_state':feedback_state, **result}


def checked_skill_gain(repo, comparison_id, instance=None):
    result = {'status':'unknown','value':None,'effect':None,'reason':'未测：缺少此技能版本的受控移除对照',
              'evidence_refs':[], 'evidence_type':'controlled_external_utility','causal_instance_credit':False}
    obj = repo.object(comparison_id)
    if obj['kind'] != 'skill_comparison' or obj['data'].get('comparison_type') != 'skill_ablation':
        result['reason'] = '技能库整体交叉差异不能分配为单个技能实例的贡献'
        return result
    data = obj['data']
    if instance is not None:
        d = instance.get('data',instance)
        if 'asset_ref' in d:
            ref = d['asset_ref']
            d = {**d, **{k:ref[k] for k in ('asset_id','bank_id','member') if k in ref}}
            if ref.get('type') == 'bank_member': d['member_content_hash'] = ref.get('content_hash')
        if not repo._same_scope(obj,instance):
            result['reason'] = '对照与实例不在同一运行范围'; return result
        exact_target = data.get('skill_asset_id') == d.get('asset_id') if data.get('skill_asset_id') else all(data.get(k)==d.get(k) for k in ('bank_id','member','member_content_hash'))
        if not exact_target or d.get('evaluation_id') != data.get('with_evaluation_id'):
            result['reason'] = '对照未对应此实例的准确版本与执行条件'; return result
    comparison = skill_comparison(repo,comparison_id)
    if comparison['status'] == 'measured':
        utility = comparison['utility']
        result.update(status='observed',value=utility>0,effect=utility,
                      reason='已核验此版本的外部移除对照；不是该实例的因果训练信用',evidence_refs=[{'object_id':comparison_id}])
    else: result['reason'] = comparison['reason']
    return result
