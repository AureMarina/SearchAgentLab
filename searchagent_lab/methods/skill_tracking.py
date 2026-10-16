"""Frozen skill assets, observed runtime instances and scoped lifecycle records.

All read models are derived from immutable method evidence.  An asset snapshot
is not an inferred creation event, and a textual skill has no invented end time.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import PurePosixPath

from ..core import ValidationError, NotFoundError
from ..inspection.repository import digest
from ..inspection.adapters import text
from .diagnostics import visibility_evidence
from .skills import member
from .views import page_args

MODES = ('fixed_skills_rl', 'fixed_policy_skill_iteration', 'joint_evolution', 'unknown')
SOURCES = ('model', 'retriever', 'rule', 'user', 'unknown')
RUNTIME_KINDS = ('skill_instance', 'skill_retrieval')


def _need(d, key, typ, label='skill'):
    if not isinstance(d.get(key), typ) or (typ is str and not d[key].strip()):
        raise ValidationError(label + '.' + key + ' is required (' + typ.__name__ + ')')


def _integer(value, label):
    if value is not None and (type(value) is not int or value < 0):
        raise ValidationError(label + ' must be a nonnegative integer or null')


def _finite_signal(value, label, mask=False):
    if value is None:return
    values = value if isinstance(value, list) else [value]
    if any((not isinstance(v, (int, float)) or isinstance(v, bool) and not mask
            or not math.isfinite(v) or mask and v < 0) for v in values):
        raise ValidationError(label + ' must contain finite numeric observations or null')


def credit_entries(value):
    if value is None:return []
    return value if isinstance(value, list) else [value]


def validate_skill_contract(o):
    d = o['data']; kind = o['kind']
    if kind == 'method_profile':
        declared = [v for v in (d.get('skill_mode'), d.get('configuration', {}).get('skill_mode')) if v is not None]
        if any(v not in MODES for v in declared):raise ValidationError('Unknown skill_mode')
        if len(set(declared)) > 1:raise ValidationError('Conflicting skill_mode declarations')
    elif kind == 'skill_bank' and 'asset_ids' in d:
        ids = d['asset_ids']
        if not isinstance(ids, list) or len(ids)>2000 or any(not isinstance(v,str) or not v for v in ids) or len(set(ids))!=len(ids):
            raise ValidationError('skill_bank.asset_ids must contain unique frozen skill_asset IDs')
    elif kind == 'skill_asset':
        for key in ('skill_id', 'version', 'package_hash'):_need(d, key, str, kind)
        _need(d, 'package', dict, kind)
        files = d['package'].get('files')
        if not isinstance(files, dict) or not 1 <= len(files) <= 2000:
            raise ValidationError('skill_asset.package.files needs 1–2000 complete file contents')
        for path, content in files.items():
            if (not isinstance(path, str) or not path or '\\' in path or
                    PurePosixPath(path).is_absolute() or any(p in ('..', '.') for p in path.split('/'))):
                raise ValidationError('Skill package file paths must be safe relative paths')
            if isinstance(content, str):raw = content.encode('utf-8')
            elif isinstance(content, dict):
                if not isinstance(content.get('content'), str):raise ValidationError('Package file must retain complete content')
                encoding = content.get('encoding', 'utf-8')
                if encoding == 'utf-8':raw = content['content'].encode('utf-8')
                elif encoding == 'base64':
                    try:raw = base64.b64decode(content['content'], validate=True)
                    except (ValueError, TypeError) as exc:raise ValidationError('Invalid package file base64') from exc
                else:raise ValidationError('Package encoding is utf-8 or base64')
                if content.get('sha256') is not None and content['sha256'] != hashlib.sha256(raw).hexdigest():
                    raise ValidationError('Package file SHA256 differs from complete bytes')
            else:raise ValidationError('Package files must retain complete text or encoded bytes')
        if digest(d['package']) != d['package_hash']:
            raise ValidationError('skill_asset.package_hash must match the complete package')
        if o.get('version') is not None and o['version'] != d['version']:
            raise ValidationError('Skill asset envelope and data versions disagree')
    elif kind == 'skill_instance':
        if sum(bool(d.get(k)) for k in ('asset_id', 'bank_id', 'inline_skill')) != 1:
            raise ValidationError('skill_instance needs exactly one frozen asset_id, bank_id/member or inline_skill')
        if d.get('bank_id'):
            for key in ('member', 'member_content_hash'):_need(d, key, str, kind)
        elif d.get('member') or d.get('member_content_hash'):
            raise ValidationError('Member identity requires its frozen bank_id')
        if 'inline_skill' in d:
            _need(d, 'inline_skill', dict, kind)
            _need(d['inline_skill'], 'text', str, 'inline_skill')
            if d['inline_skill'].get('content_hash') != digest(d['inline_skill']['text']):
                raise ValidationError('inline_skill.content_hash must match the recorded instance text')
        selection = d.get('selection', {})
        if not isinstance(selection, dict) or selection.get('source', 'unknown') not in SOURCES:
            raise ValidationError('Skill selection.source is model/retriever/rule/user/unknown')
        _integer(selection.get('step'), 'selection.step');_integer(d.get('step'), 'step')
        for key in ('steps', 'related_actions', 'local_checks', 'comparison_ids'):
            if key in d and not isinstance(d[key], list):raise ValidationError(key + ' must be an array')
        for value in d.get('steps', []):_integer(value, 'steps')
        for action in d.get('related_actions', []):
            if not isinstance(action, dict):raise ValidationError('related_actions entries must be objects')
            _integer(action.get('step'), 'related_actions.step')
        for key, fragment in (('visibility', 'entry_text'), ('loading', 'rendered_text')):
            if key not in d:continue
            obs = d[key]
            if not isinstance(obs, dict):raise ValidationError(key + ' must be an object')
            if fragment in obs and not isinstance(obs[fragment], str):raise ValidationError(key + '.' + fragment + ' must be text')
            if 'complete' in obs and not isinstance(obs['complete'], bool):raise ValidationError(key + '.complete must be boolean')
            if 'final_input_sha256' in obs and ('final_input' not in obs or digest(obs['final_input']) != obs['final_input_sha256']):
                raise ValidationError(key + '.final_input_sha256 must match recorded final input')
        if 'execution' in d:
            e = d['execution']
            if not isinstance(e, dict) or e.get('status', 'unknown') not in ('observed', 'not_observed', 'unknown'):
                raise ValidationError('execution.status is observed/not_observed/unknown')
            if e.get('kind') is not None and e['kind'] not in ('script', 'verified_step'):
                raise ValidationError('execution.kind is script/verified_step')
            if 'complete' in e and not isinstance(e['complete'], bool):raise ValidationError('execution.complete must be boolean')
        for key in ('usage', 'task_result'):
            if key in d and not isinstance(d[key], dict):raise ValidationError(key + ' must be an object')
    elif kind == 'skill_evolution':
        for key in ('source_refs', 'comparison_ids'):
            if key in d and not isinstance(d[key], list):raise ValidationError(key + ' must be an array')
        if 'effective_scope' in d:
            if not isinstance(d['effective_scope'], dict):raise ValidationError('effective_scope must be an object')
            for key in ('project_id', 'experiment_id', 'run_id'):
                if d['effective_scope'].get(key) is not None and d['effective_scope'][key] != o.get('scope', {}).get(key, ''):
                    raise ValidationError('Lifecycle effective_scope must stay within its recorded owner scope')
        if d.get('event') == 'retired' or d.get('decision') == 'retired':
            if not o.get('scope', {}).get('experiment_id'):
                raise ValidationError('Retirement requires an explicit experiment scope; assets are never globally deleted')
    elif kind == 'training_use' and 'credit_assignment' in d:
        value = d['credit_assignment']
        if not isinstance(value, (dict, list)):raise ValidationError('credit_assignment must be an object or array')
        seen = set()
        for credit in credit_entries(value):
            if not isinstance(credit, dict):raise ValidationError('credit_assignment entry must be an object')
            _need(credit, 'instance_id', str, 'credit_assignment')
            if credit['instance_id'] not in d.get('artifact_ids', []):
                raise ValidationError('Credit instance_id must also occur in training_use.artifact_ids')
            if credit['instance_id'] in seen:raise ValidationError('Duplicate credit assignment for one instance in one use')
            seen.add(credit['instance_id'])
            for key in ('skill_decision', 'action_decision'):
                if key not in credit:continue
                v = credit[key]
                if not isinstance(v, dict):raise ValidationError(key + ' must be an object')
                for field in ('reward', 'advantage', 'original_advantage', 'effective_advantage', 'loss_weight'):
                    _finite_signal(v.get(field), key + '.' + field)
                for field in ('mask', 'loss_mask'):_finite_signal(v.get(field), key + '.' + field, mask=True)
                if 'signal' in v:
                    from .contracts import validate_iteration_contract
                    validate_iteration_contract({'kind':'training_use', 'data':{'signal':v['signal']}})
    elif kind == 'skill_comparison':
        from .skill_comparison import validate_comparison_contract
        validate_comparison_contract(d)


def validate_skill_links(repo, o, pending):
    d = o['data']; kind = o['kind']
    if kind == 'skill_asset':
        # The version label is stable within an explicit project.  Reusing the
        # same label for different bytes would make historical references lie.
        project = o.get('scope', {}).get('project_id', '')
        with repo.lock:
            rows = repo.conn.execute("SELECT id,json_extract(payload,'$.data.package_hash') AS h FROM objects WHERE kind='skill_asset' AND project_id=? AND json_extract(payload,'$.data.skill_id')=? AND json_extract(payload,'$.data.version')=?",
                (project, d['skill_id'], d['version'])).fetchall()
        conflicts = [(r['id'], r['h']) for r in rows]
        conflicts += [(p['id'], p['data'].get('package_hash')) for p in pending.values()
                      if p['kind']=='skill_asset' and p.get('scope', {}).get('project_id', '')==project
                      and p['data'].get('skill_id')==d['skill_id'] and p['data'].get('version')==d['version']]
        if any(oid != o['id'] and h != d['package_hash'] for oid, h in conflicts):
            raise ValidationError('A skill_id/version cannot name different package bytes within one project')
    if kind == 'skill_instance':
        if d.get('bank_id'):
            m = bank_member(repo, repo._raw_object(d['bank_id'], pending)['data'], d['member'], pending)
            if m['content_hash'] != d['member_content_hash']:
                raise ValidationError('Skill instance member_content_hash differs from frozen bank member')
        for key in ('trace_ref',):
            ref = d.get(key, {})
            if isinstance(ref, dict) and ref.get('object_id'):
                target = repo._raw_object(ref['object_id'], pending)
                if not repo._same_scope(o, target):raise ValidationError('Skill instance trace belongs to another run scope')
        _validate_runtime_binding(repo, o)
        from .schema import evidence_refs
        for ref in evidence_refs(o):
            if ref.get('object_id'):
                target=repo._raw_object(ref['object_id'],pending)
                if not repo._same_scope(o,target):
                    raise ValidationError('Skill runtime observation evidence belongs to another run scope')
            elif ref.get('trajectory'):
                _validate_runtime_binding(repo,{**o,'data':{**d,'trajectory_id':ref['trajectory']}})
                explicit=d.get('trajectory_id') or (d.get('trace_ref') or {}).get('trajectory')
                if explicit and explicit!=ref['trajectory']:
                    raise ValidationError('Skill observation evidence belongs to another trajectory')
    if kind == 'skill_evolution' and d.get('instance_id'):
        target = repo._raw_object(d['instance_id'], pending)
        if not repo._same_scope(o, target):raise ValidationError('Instance lifecycle must remain in the instance run scope')
    if kind == 'skill_evolution' and d.get('checkpoint_id') and repo.store:
        try:
            cp=repo.store._get('checkpoints',d['checkpoint_id'])
        except NotFoundError:
            if d.get('checkpoint_ref_status')!='external_unverified':
                raise ValidationError('Lifecycle checkpoint is not registered; record checkpoint_ref_status=external_unverified')
        else:
            if cp['run_id']!=o.get('scope',{}).get('run_id',''):
                raise ValidationError('Lifecycle checkpoint belongs to another run scope')
    if kind == 'training_use':
        for credit in credit_entries(d.get('credit_assignment')):
            instance = repo._raw_object(credit['instance_id'], pending)
            if instance['kind'] != 'skill_instance':raise ValidationError('Credit must reference a skill_instance')
            if not repo._same_scope(o, instance):raise ValidationError('Skill credit belongs to another run scope')
    if kind == 'skill_comparison':
        from .skill_comparison import validate_comparison_links
        validate_comparison_links(repo, o, pending)


def bank_asset_members(repo, data, pending=None):
    """Resolve declared immutable versions without duplicating package bodies."""
    members = []
    for i, aid in enumerate(data.get('asset_ids', [])):
        if pending is not None and aid in pending:
            a = pending[aid]['data']; sid=a['skill_id']; version=a['version']; h=a['package_hash']
        else:
            row = repo.conn.execute("SELECT json_extract(payload,'$.data.skill_id') AS sid,json_extract(payload,'$.data.version') AS version,json_extract(payload,'$.data.package_hash') AS h FROM objects WHERE id=? AND kind='skill_asset'", (aid,)).fetchone()
            if row is None:raise NotFoundError('Frozen skill asset not found: '+aid)
            sid=row['sid'];version=row['version'];h=row['h']
        members.append({'key':'assets/'+sid, 'source_id':sid, 'title':sid+' @ '+version,
                        'group':'assets', 'category':'', 'pointer':'/asset_ids/'+str(i), 'content_hash':h,
                        'content':{'asset_id':aid, 'skill_id':sid, 'version':version, 'package_hash':h},
                        'source_id_missing':False, 'asset_id':aid})
    keys = [m['key'] for m in members]
    for m in members:
        if keys.count(m['key'])>1:m['ambiguous_key']=True
    return members


def bank_member(repo, data, key, pending=None):
    from .skills import adapt_bank
    found = [m for m in adapt_bank(data)['members']+bank_asset_members(repo, data, pending)
             if key in (m['key'],m['pointer'],m['source_id'],m.get('asset_id'))]
    if len(found)!=1:raise ValidationError('Skill member missing or ambiguous: '+str(key)[:200])
    return found[0]


def _validate_runtime_binding(repo, o):
    d = o['data']; ev_id = d.get('evaluation_id'); trajectory_id = d.get('trajectory_id')
    ref = d.get('trace_ref', {})
    if isinstance(ref, dict) and ref.get('trajectory'):
        if trajectory_id and trajectory_id != ref['trajectory']:raise ValidationError('Conflicting skill trajectory references')
        trajectory_id = ref['trajectory']
    if not ev_id and not trajectory_id:return
    if repo.store is None:raise ValidationError('A formal skill runtime binding requires registered evaluation/trajectory')
    if trajectory_id:
        t = repo.store.trajectory(trajectory_id)
        if ev_id and ev_id != t['evaluation_id']:raise ValidationError('Skill trajectory belongs to another evaluation')
        ev_id = t['evaluation_id']
    ev = repo.store.evaluation(ev_id)
    expected = {'project_id':ev['project_id'], 'experiment_id':ev['experiment_id'], 'run_id':ev['checkpoint']['run_id']}
    if any(o.get('scope', {}).get(key, '') != value for key, value in expected.items()):
        raise ValidationError('Skill runtime binding belongs to another experiment/run scope')
    if o.get('demo') and not (ev['run'].get('demo') or ev['panel'].get('demo')):
        raise ValidationError('Synthetic skill instances cannot bind real evaluations')


def _observation(value=None, reason='未记录足够证据', refs=None, **extra):
    return {'status':'observed' if value is True else 'not_observed' if value is False else 'unknown',
            'value':value, 'reason':reason, 'evidence_refs':refs or [], **extra}


def input_contains(fragment, value):
    """Match actual string values, not escaped JSON serialization or field names.

    Each content part is checked independently: adjacent fields are not joined
    into a text fragment that the record never contained.  Newlines and quotes
    in a real prompt therefore retain their exact original bytes/characters.
    """
    if not isinstance(fragment,str) or not fragment:return False
    if isinstance(value,str):return fragment in value
    if isinstance(value,list):return any(input_contains(fragment,v) for v in value)
    if isinstance(value,dict):
        if 'messages' in value:return input_contains(fragment,value['messages'])
        if 'role' in value and 'content' in value:return input_contains(fragment,value['content'])
        return any(input_contains(fragment,v) for v in value.values())
    return False


def _input_observation(repo, o, key, fragment_key):
    d = o['data'].get(key, {})
    fragment = d.get(fragment_key)
    if not isinstance(fragment, str) or not fragment:
        return _observation(reason='未记录待核对的模型可见文本' if key=='visibility' else '未记录实际渲染的技能正文')
    raw = d.get('final_input'); refs = []
    if raw is None:
        ref = d.get('request_ref') or d.get('evidence_ref')
        if isinstance(ref, dict) and visibility_evidence(ref):
            checked = repo.resolve_ref(ref, owner=o); raw = checked['value']; refs = [checked['ref']]
    if raw is None:return _observation(reason='未记录可核对的最终模型输入；候选或选择日志不足以证明可见', refs=refs)
    if input_contains(fragment,raw):return _observation(True, '在记录的最终模型输入中核对到准确文本；不推断使用或增益', refs)
    if d.get('complete') is True:return _observation(False, '完整输入中未观察到所记录的准确文本；不推断改写后文本的语义等价性', refs)
    return _observation(reason='文本未匹配且输入完整性未声明', refs=refs)


def _execution_observation(repo, o):
    e = o['data'].get('execution', {})
    ref = e.get('evidence_ref')
    if e.get('status') == 'observed' and e.get('kind') in ('script', 'verified_step') and ref:
        checked = repo.resolve_ref(ref, owner=o)
        if e['kind']=='verified_step' and not e.get('verifier'):
            return _observation(reason='步骤检查没有验证器身份/版本')
        raw=checked['value']
        if not isinstance(raw,dict):
            return _observation(reason='引用只定位到文字，尚无结构化工具执行或步骤验证记录',refs=[checked['ref']])
        call=raw.get('tool_call')
        result=any(k in raw for k in ('tool_response','result','output','exit_code','returncode'))
        if e['kind']=='script':
            name=call.get('name','') if isinstance(call,dict) else call if isinstance(call,str) else ''
            arguments=call.get('arguments',{}) if isinstance(call,dict) else {}
            script=(name in ('run_script','exec','exec_command','shell','bash','python','execute_script')
                    or isinstance(name,str) and name.endswith(('.py','.sh','.bash','.js','.mjs'))
                    or isinstance(arguments,dict) and any(k in arguments for k in ('command','cmd'))
                    or raw.get('kind') in ('script','script_execution') or 'command' in raw)
            actual=bool(script and result)
        else:
            verified=(raw.get('status') in ('passed','failed') and bool(raw.get('verifier'))
                      or type(raw.get('verified')) is bool or isinstance(raw.get('verification'),dict))
            actual=bool(call and result or verified)
        if not actual:
            return _observation(reason='引用没有同时保留明确的执行/验证事件与结果，元数据描述不作为实际执行',refs=[checked['ref']])
        return _observation(True, '记录明确关联实际脚本执行或带验证器的步骤证据；不代表最终任务成功', [checked['ref']], kind=e['kind'])
    if e.get('status') == 'not_observed' and e.get('complete') is True:
        return _observation(False, '完整执行日志明确记录未观察到对应执行')
    return _observation(reason='仅关联动作或记录加载不证明技能执行；没有补造文字技能的结束边界')


def _asset_ref(repo, o):
    d = o['data']
    if d.get('asset_id'):
        row = repo.conn.execute("SELECT id,json_extract(payload,'$.data.skill_id') AS sid,json_extract(payload,'$.data.version') AS version,json_extract(payload,'$.data.package_hash') AS h FROM objects WHERE id=?", (d['asset_id'],)).fetchone()
        return {'type':'asset', 'asset_id':row['id'], 'skill_id':row['sid'], 'version':row['version'], 'content_hash':row['h']}
    if d.get('bank_id'):
        row = repo.conn.execute("SELECT id,content_hash,json_extract(payload,'$.version') AS version FROM objects WHERE id=?", (d['bank_id'],)).fetchone()
        out = {'type':'bank_member' if d.get('member') else 'bank', 'bank_id':row['id'], 'bank_content_hash':row['content_hash'], 'version':row['version']}
        if d.get('member'):
            out.update(member=d['member'], content_hash=d['member_content_hash'])
        return out
    inline = d['inline_skill']
    return {'type':'inline', 'instance_id':o['id'], 'skill_id':None, 'version':None, 'content_hash':inline['content_hash']}


def instance_projection(repo, o):
    d = o['data']; loaded = _input_observation(repo, o, 'loading', 'rendered_text')
    visible = _input_observation(repo, o, 'visibility', 'entry_text')
    if loaded['value'] is True and visible['value'] is None:
        visible = _observation(True, '实际技能正文已进入模型输入，因此可确认可见；不补造候选列表', loaded['evidence_refs'])
    steps = set(v for v in d.get('steps', []) if type(v) is int)
    for value in (d.get('step'), d.get('selection', {}).get('step')):
        if type(value) is int:steps.add(value)
    steps.update(a['step'] for a in d.get('related_actions', []) if type(a.get('step')) is int)
    trajectory_id = d.get('trajectory_id') or (d.get('trace_ref') or {}).get('trajectory')
    evaluation_id = d.get('evaluation_id')
    if trajectory_id and not evaluation_id and repo.store:evaluation_id = repo.store.trajectory(trajectory_id)['evaluation_id']
    p = {key:d[key] for key in ('step', 'trace_ref', 'task_result', 'usage', 'local_checks', 'comparison_ids') if key in d}
    p.update(asset_ref=_asset_ref(repo, o), selection_source=d.get('selection', {}).get('source', 'unknown'),
             evaluation_id=evaluation_id, trajectory_id=trajectory_id, recorded_steps=sorted(steps),
             observations={'visible':visible, 'loaded':loaded, 'execution':_execution_observation(repo, o),
                           'gain':_observation(reason='需要已核对控制条件的固定评测对照；调用后成功率不等于技能增益')})
    # Action summaries retain explicit locations and identifiers, never full
    # scripts/trace payloads or guessed temporal boundaries.
    p['related_actions'] = [{k:a[k] for k in ('step', 'action_id', 'tool_call_id', 'kind', 'evidence_ref') if k in a} for a in d.get('related_actions', [])]
    return p


def evolution_projection(o):
    d = o['data']
    out = {k:d[k] for k in ('step', 'checkpoint_id', 'event', 'decision', 'status', 'reason', 'source_refs', 'comparison_ids', 'reported_utility', 'utility', 'utility_ema', 'usage_count', 'low_utility_streak', 'domain', 'deployment_id', 'feedback_role', 'comparison_status') if k in d}
    out['target'] = {k:d[k] for k in ('asset_id', 'bank_id', 'instance_id', 'member', 'member_content_hash', 'parent_asset_id', 'parent_bank_id') if k in d}
    out['effective_scope'] = d.get('effective_scope') or dict(o.get('scope', {}))
    out['recorded_only'] = True
    out['gain_verified'] = False
    changes = d.get('event') in ('created','modified','accepted','retired','proposed') or d.get('decision') in ('accept','accepted','retire','retired','modify','modified','create','created')
    out['feedback_classification'] = ('test_feedback' if changes else 'test_report') if o.get('usage')=='test' else 'development_feedback' if o.get('usage') in ('validation','evolution') else 'not_recorded'
    return out


def _scope(repo, experiment_id, run_id=''):
    if not experiment_id:raise ValidationError('An explicit experiment_id is required')
    scope = repo._scope({'experiment_id':experiment_id, **({'run_id':run_id} if run_id else {})})
    scope['run_id'] = run_id or ''
    return scope


def skill_mode(repo, scope):
    profiles = repo.objects(scope, kind='method_profile'); origin = 'run' if scope.get('run_id') else 'experiment'
    if not profiles and scope.get('run_id'):
        profiles = repo.objects({**scope, 'run_id':''}, kind='method_profile'); origin = 'experiment'
    declared = []
    for p in profiles:
        d = p['data']
        if 'skill' not in d.get('families', []):continue
        value = d.get('skill_mode', d.get('configuration', {}).get('skill_mode'))
        if value is not None:declared.append(value)
    values = set(declared)
    mode = next(iter(values)) if len(values)==1 else 'unknown'
    return {'value':mode, 'status':'conflicting' if len(values)>1 else 'recorded' if mode!='unknown' else 'not_recorded',
            'origin':origin if declared else 'not_recorded', 'profile_ids':[p['id'] for p in profiles],
            'reason':'仅使用显式模式配置；不按方法名或技能快照数量推断'}


def _read(repo, scope, kinds, predicate='', values=None):
    from .indexes import ensure_assessment_index
    ensure_assessment_index(repo, scope, kinds)
    where, args = repo._where(scope, prefix='o.')
    where += ' AND o.kind IN (' + ','.join('?' for _ in kinds) + ')'
    args += list(kinds)
    if predicate:where += ' AND (' + predicate + ')';args += list(values or [])
    with repo.lock:
        rows = repo.conn.execute('SELECT s.summary FROM objects o JOIN method_summaries s ON s.id=o.id' + where + ' ORDER BY o.rowid', args).fetchall()
    return [json.loads(row[0]) for row in rows]


def _page(items, offset, limit):
    return {'items':items[offset:offset+limit], 'total':len(items), 'offset':offset, 'limit':limit, 'has_more':offset+limit<len(items)}


def _runtime_item(row):
    p = row.get('assessment', {}); legacy = row['kind']=='skill_retrieval'
    item = {**{k:row.get(k) for k in ('id', 'kind', 'title', 'scope', 'demo', 'content_hash')}, **p,
            'record_type':'legacy_retrieval' if legacy else 'instance', 'body_loaded':False}
    if legacy:
        loaded = _observation(True, '旧检索日志中的技能正文与最终模型输入准确匹配') if p.get('verified_injections', 0) else (
            _observation(False, '完整注入日志中没有核对到实际注入') if p.get('input_status')=='absent' else _observation(reason='旧日志未记录足够的实际注入证据'))
        item.update(asset_ref={'type':'bank', 'bank_id':p.get('bank_id'), 'content_hash':p.get('bank_content_hash')},
                    selection_source=p.get('selection_source', 'unknown'),
                    recorded_steps=p.get('recorded_steps', []), related_actions=[],
                    observations={'visible':_observation(True, '已核对实际注入，因此确认模型可见') if loaded['value'] is True else _observation(reason='检索候选列表不能证明已展示给模型'),
                                  'loaded':loaded, 'execution':_observation(reason='旧检索日志未证明脚本执行或可验证步骤'),
                                  'gain':_observation(reason='检索、注入与任务成功的相关性不证明增益')})
    return item


def _counts(items):
    out = {'total':len(items), 'instances':sum(i['kind']=='skill_instance' for i in items),
           'legacy_retrieval_records':sum(i['kind']=='skill_retrieval' for i in items)}
    for key in ('visible', 'loaded', 'execution', 'gain'):
        flags = [i['observations'][key]['value'] for i in items]
        yes = sum(v is True for v in flags); no = sum(v is False for v in flags)
        out[key] = {'observed':yes, 'not_observed':no, 'unknown':len(flags)-yes-no,
                    'denominator':len(flags), 'known_denominator':yes+no, 'value':yes/(yes+no) if yes+no else None}
    return out


def _qualify_gain(repo,item,cache=None):
    comparisons=item.get('comparison_ids',[])
    if len(comparisons)!=1:return
    from .skill_comparison import checked_skill_gain
    key=digest([comparisons[0],item.get('scope'),item.get('asset_ref'),item.get('evaluation_id')])
    gain=cache.get(key) if cache is not None else None
    if gain is None:
        gain=checked_skill_gain(repo,comparisons[0],instance=item)
        if cache is not None:cache[key]=gain
    if isinstance(gain,dict):item['observations']['gain']=gain


def skill_runtime(repo, experiment_id, run_id='', evaluation_id='', trajectory_id='', offset=0, limit=25, step=None):
    scope = _scope(repo, experiment_id, run_id); offset, limit = page_args(offset, limit)
    if step is not None and step!='':
        try:step = int(step)
        except (ValueError, TypeError) as exc:raise ValidationError('step must be nonnegative integer') from exc
        _integer(step, 'step')
    else:step = None
    if evaluation_id or trajectory_id:
        if repo.store is None:raise ValidationError('Runtime filters require a registered evaluation/trajectory')
        if trajectory_id:
            t = repo.store.trajectory(trajectory_id)
            if evaluation_id and evaluation_id != t['evaluation_id']:raise ValidationError('Trajectory is outside selected evaluation')
            evaluation_id = t['evaluation_id']
        ev = repo.store.evaluation(evaluation_id)
        if ev['experiment_id'] != scope['experiment_id'] or ev['checkpoint']['run_id'] != scope['run_id']:
            raise ValidationError('Runtime evaluation is outside selected experiment/run')
    rows = _read(repo, scope, RUNTIME_KINDS)
    items = [_runtime_item(row) for row in rows]
    if evaluation_id:items = [i for i in items if i.get('evaluation_id')==evaluation_id]
    if trajectory_id:items = [i for i in items if i.get('trajectory_id')==trajectory_id]
    if step is not None:items = [i for i in items if step in i.get('recorded_steps', [])]
    # Root comparison engine checks causal controls independently.  Multiple
    # requested comparisons stay separate rather than selecting a winner.
    gain_cache={}
    for item in items:_qualify_gain(repo,item,gain_cache)
    page = _page(items, offset, limit)
    return {'scope':scope, 'mode':skill_mode(repo, scope),
            'filters':{'evaluation_id':evaluation_id, 'trajectory_id':trajectory_id, 'step':step},
            **page, 'instances':page['items'], 'counts':_counts(items),
            'availability':{'status':'recorded' if items else 'not_recorded', 'reason':'未登记实例不表示未使用；旧检索事件单独标记，不补造实例'},
            'boundaries':'Only explicit recorded steps and associated actions. Text skill end times are not inferred.'}


def skill_instance(repo, oid):
    o = repo.object(oid)
    if o['kind'] not in RUNTIME_KINDS:raise ValidationError('Expected skill_instance or legacy skill_retrieval')
    from .projection import object_projection
    row = {**o, 'assessment':object_projection(repo, o)}
    item = _runtime_item(row); _qualify_gain(repo,item); ref = item['asset_ref']; d = o['data']
    if o['kind']=='skill_retrieval':
        bank = repo.object(d['bank_id']); keys = []
        for value in d.get('candidates', [])+d.get('selected', [])+d.get('injections', []):
            key = value if isinstance(value, str) else value['member']
            if key not in keys:keys.append(key)
        content = {'bank_id':bank['id'], 'bank_content_hash':bank['content_hash'], 'members':[bank_member(repo, bank['data'], key) for key in keys]}
        kind = 'bank_retrieval'; content_hash = bank['content_hash']
    elif ref['type']=='asset':
        asset = repo.object(ref['asset_id']); content = asset['data']['package']; kind = 'package'; content_hash = asset['data']['package_hash']
    elif ref['type']=='bank_member':
        bank = repo.object(ref['bank_id']); m = bank_member(repo, bank['data'], ref['member']); content = m['content']; kind = 'bank_member'; content_hash = ref['content_hash']
        if m.get('asset_id'):content = {**content, 'package':repo.object(m['asset_id'])['data']['package']}
    else:content = d['inline_skill']['text'];kind = 'inline';content_hash = d['inline_skill']['content_hash']
    return {**item, 'object':o, 'body_loaded':True,
            'frozen_content':{'status':'recorded', 'kind':kind, 'content':content, 'content_hash':content_hash}}


def _credit_rows(repo, scope, rows):
    uses = [r for r in rows if r['kind']=='training_use']; updates = [r for r in rows if r['kind']=='training_update']; out = []
    for use in uses:
        p = use.get('assessment', {})
        for credit in p.get('credit_assignment', []):
            iid = credit['instance_id']
            with repo.lock:row = repo.conn.execute('SELECT summary FROM method_summaries WHERE id=?', (iid,)).fetchone()
            if not row:continue
            instance = json.loads(row[0]); ip = instance.get('assessment', {})
            source = ip.get('selection_source', 'unknown'); skill = credit.get('skill_decision'); action = credit.get('action_decision')
            if skill is None:qualification = _observation(reason='技能决策信用未记录；动作信用单独保留')
            elif source == 'model':qualification = _observation(True, '日志明确记录模型作出技能选择并单独分配信用；不推断因果梯度贡献')
            elif source == 'unknown':qualification = _observation(reason='选择来源未知，不能将该项解释为模型选择技能的信用')
            else:qualification = _observation(False, '技能由 '+source+' 选择，该记录不能解释为模型技能选择信用；动作信用仍保留')
            matched = [r for r in updates if use['id'] in r.get('assessment', {}).get('consumption_ids', [])]
            out.append({'id':use['id'], 'instance_id':iid, 'selection_source':source, 'asset_ref':ip.get('asset_ref'),
                        'skill_decision':skill, 'action_decision':action, 'model_selection_credit':qualification,
                        'status':p.get('status'), 'batch_id':p.get('batch_id'), 'step':p.get('step'),
                        'update_ids':[r['id'] for r in matched],
                        'update_completed':p.get('status')=='sampled' and any(r.get('assessment', {}).get('status')=='completed' for r in matched),
                        'scope':scope, 'body_loaded':False})
    return out


def skill_lifecycle(repo, experiment_id, run_id='', offset=0, limit=25):
    scope = _scope(repo, experiment_id, run_id); offset, limit = page_args(offset, limit)
    kinds = ('skill_evolution', 'training_use', 'training_update', 'skill_comparison', 'skill_archive', 'skill_bank', 'skill_instance')
    rows = _read(repo, scope, kinds)
    events = [{**{k:r.get(k) for k in ('id', 'kind', 'title', 'scope', 'demo')}, **r.get('assessment', {})}
              for r in rows if r['kind']=='skill_evolution']
    credits = _credit_rows(repo, scope, rows)
    comparisons = [{k:r.get(k) for k in ('id', 'kind', 'title', 'scope', 'demo', 'assessment')} for r in rows if r['kind']=='skill_comparison']
    snapshots = [{k:r.get(k) for k in ('id', 'kind', 'title', 'version', 'scope', 'content_hash')} for r in rows if r['kind'] in ('skill_archive', 'skill_bank')]
    return {'scope':scope, 'mode':skill_mode(repo, scope), 'events':_page(events, offset, limit),
            'credits':_page(credits, offset, limit), 'comparisons':_page(comparisons, offset, limit),
            'observed_snapshots':_page(snapshots, offset, limit),
            'counts':{'events':len(events), 'credit_instances':len(credits), 'comparisons':len(comparisons), 'observed_snapshots':len(snapshots)},
            'availability':{'status':'recorded' if events or credits or snapshots or comparisons else 'not_recorded'},
            'limitations':['快照只证明观察到的状态，不等于创建、接受或退役事件。',
                           '退役仅对记录的实验/部署范围有效，历史资产与运行版本保持可查。',
                           '参与技能修改的验证结果属于开发反馈，独立泛化结论另需固定评测。']}


def skill_assets(repo, experiment_id, run_id='', offset=0, limit=25):
    scope = _scope(repo, experiment_id, run_id); offset, limit = page_args(offset, limit)
    rows = _read(repo, scope, ('skill_asset', 'skill_bank', 'skill_archive'))
    items = [{**{k:r.get(k) for k in ('id', 'kind', 'title', 'version', 'scope', 'demo', 'content_hash')},
              **r.get('assessment', {}), 'body_loaded':False} for r in rows]
    return {'scope':scope, 'mode':skill_mode(repo, scope), **_page(items, offset, limit),
            'no_implicit_latest':True, 'history_preserved':True}
