"""Scoped entropy evidence; measurements, interventions and outcomes stay separate.

No estimator, model, optimizer or environment is executed here.  Definitions are
immutable objects and incomplete or incompatible quantities are never pooled.
Large native vectors remain in their original object and are read in pages.
"""
from __future__ import annotations

from collections import Counter
import math
import re
from urllib.parse import quote

from ..core import ValidationError
from ..inspection.repository import digest
from .views import page_args
from .opd_tracking import (_small, _numeric, _term, _training_for,
                           _integer, _boolean, _numbers, _signal, _need, evidence_demo)

ENTROPY_KINDS = ('entropy_definition', 'entropy_observation', 'entropy_probe',
                 'entropy_intervention', 'entropy_comparison')
RECORD_KINDS = ('entropy_observation', 'entropy_probe', 'entropy_intervention')
CONTEXTS = ('training_on_policy', 'fixed_prefix_probe', 'evaluation', 'unknown')
_NUMERIC_COLUMNS = {'entropy', 'entropies', 'values', 'sampled_log_probs', 'sampled_probs',
                    'token_entropy', 'full_vocab_entropy', 'surprisal', 'contribution', 'log_probs', 'probabilities',
                    'advantages', 'effective_advantages', 'weights', 'raw_weights',
                    'effective_weights', 'task_advantages', 'raw', 'effective',
                    'mask', 'loss_mask', 'response_mask', 'model_token_mask', 'selection_mask'}


def _finite(value, label, nonnegative=False):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                              or nonnegative and value < 0):
        raise ValidationError(label + ' must be a finite number' + (' >= 0' if nonnegative else '') + ' or null')


def _hash(value, label):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)):
        raise ValidationError(label + ' must be a SHA256 hexadecimal digest')


def _tokens(value):
    if not isinstance(value, dict):raise ValidationError('tokens must be parallel column arrays')
    sizes = set()
    for key, values in value.items():
        if not isinstance(values, list):raise ValidationError('tokens columns must be parallel arrays')
        sizes.add(len(values))
        if len(values)>131072:raise ValidationError('Token record exceeds 131072; split with original positions')
        if key in _NUMERIC_COLUMNS:
            if 'mask' in key:_numbers(values, 'tokens.' + key, mask=True)
            else:
                for item in values:_finite(item, 'tokens.' + key, nonnegative=key in ('entropy', 'entropies', 'token_entropy', 'full_vocab_entropy'))
        elif key in ('ids', 'token_ids', 'positions'):
            for item in values:_integer(item, 'tokens.' + key)
        elif key in ('text', 'tokens', 'segment_type', 'segment_types'):
            if any(item is not None and not isinstance(item, str) for item in values):
                raise ValidationError('tokens.' + key + ' must contain text or null')
    if len(sizes)>1:raise ValidationError('Entropy token columns must have equal lengths; preserve missing entries as null')
    positions = value.get('positions', [])
    known = [v for v in positions if v is not None]
    if len(known)!=len(set(known)):raise ValidationError('Entropy token positions must be distinct')


def _entropy_configuration(value):
    if not isinstance(value, dict):raise ValidationError('configuration.entropy must be an object')
    for key in ('enabled', 'training_enabled', 'runtime_enabled'):_boolean(value.get(key), 'entropy.' + key)
    for key in ('entropy_coeff', 'entropy_coef', 'loss_weight', 'selection_ratio'):
        _finite(value.get(key), 'entropy.' + key)
    if value.get('selection_ratio') is not None and not 0<=value['selection_ratio']<=1:
        raise ValidationError('entropy.selection_ratio must be within [0, 1]')


def validate_entropy_contract(o):
    d = o['data']; kind = o['kind']
    if kind=='method_profile':
        if 'entropy' in d.get('configuration', {}):_entropy_configuration(d['configuration']['entropy'])
        return
    if kind=='evaluation_binding':
        if 'entropy_context' in d:
            _need(d, 'entropy_context', dict, kind)
            if d['entropy_context'].get('runtime_intervention') not in (None, 'none', 'entropy', 'random'):
                raise ValidationError('entropy_context.runtime_intervention must be none, entropy, random or null')
            for key in ('extra_branching', 'extra_model', 'training_privilege_withdrawn', 'training_only_guidance_removed'):
                _boolean(d['entropy_context'].get(key), 'entropy_context.' + key)
            if 'extra_resources' in d['entropy_context']:
                _need(d['entropy_context'], 'extra_resources', dict, 'entropy_context')
                for key in ('branching', 'summary_model', 'teacher'):
                    _boolean(d['entropy_context']['extra_resources'].get(key), 'entropy_context.extra_resources.' + key)
        return
    if kind=='entropy_comparison':
        from .entropy_evaluation import validate_entropy_comparison_contract
        validate_entropy_comparison_contract(d)
        return
    if kind not in ENTROPY_KINDS:return
    if kind=='entropy_definition':
        for key in ('quantity_kind', 'formula'):
            if key in d and d[key] is not None:_need(d, key, str, kind)
        for key in ('scoring_temperature', 'sampling_temperature'):
            _finite(d.get(key), key, nonnegative=True)
            _finite(d.get('distribution', {}).get(key), 'distribution.' + key, nonnegative=True)
        if d.get('scoring_temperature')==0 or d.get('distribution', {}).get('scoring_temperature')==0:
            raise ValidationError('Entropy scoring_temperature must be positive; greedy sampling_temperature may be zero')
        if 'support' in d and not isinstance(d['support'], (dict, str)):
            raise ValidationError('Entropy support must be an explicit object or named support')
        if 'top_k' in d and d['top_k'] is not None and d['top_k']!='disabled':_integer(d['top_k'], 'top_k')
        return
    for key in ('step', 'training_step', 'token_count', 'sample_count'):_integer(d.get(key), key)
    for key in ('definition_id', 'checkpoint_id', 'evaluation_id', 'trajectory_id', 'source_format', 'source_version', 'prefix_id'):
        if key in d and d[key] is not None:_need(d, key, str, kind)
    if 'context' in d and d['context'] not in CONTEXTS:raise ValidationError('Unknown entropy context')
    if 'tokens' in d:_tokens(d['tokens'])
    if 'source_refs' in d and not isinstance(d['source_refs'], list):raise ValidationError('source_refs must be an array')
    for key in ('coverage', 'costs', 'budget', 'measurement_support'):
        if key in d:_need(d, key, dict, kind)
    if 'measurement_support' in d:
        _boolean(d['measurement_support'].get('available'), 'measurement_support.available')
        for key in ('count', 'total', 'k'):_integer(d['measurement_support'].get(key), 'measurement_support.' + key)
    if 'frozen_prefix' in d:
        _need(d, 'frozen_prefix', dict, kind); prefix = d['frozen_prefix']
        _hash(prefix.get('sha256'), 'frozen_prefix.sha256')
        _finite(prefix.get('scoring_temperature'), 'frozen_prefix.scoring_temperature', True)
        if prefix.get('scoring_temperature')==0:raise ValidationError('Fixed-prefix scoring_temperature must be positive')
    if kind=='entropy_probe':
        _need(d, 'definition_id', str, kind);_need(d, 'probe_type', str, kind)
        if d.get('context') not in CONTEXTS:raise ValidationError('Explicit entropy probe context required')
        if 'value' not in d:raise ValidationError('Probe value required; use null with missing_reason')
        _finite(d['value'], 'probe.value')
        if d['value'] is None:_need(d, 'missing_reason', str, kind)
    if kind=='entropy_intervention':
        _need(d, 'definition_id', str, kind);_need(d, 'intervention_type', str, kind)
        if d.get('mode') not in ('training', 'runtime'):raise ValidationError('Entropy intervention mode must be training or runtime')
        if 'observation_ids' in d:
            if not isinstance(d['observation_ids'], list):raise ValidationError('observation_ids must be an array')
            if any(not isinstance(v, str) or not v for v in d['observation_ids']):raise ValidationError('observation_ids must contain explicit object IDs')
            if len(d['observation_ids'])!=len(set(d['observation_ids'])):raise ValidationError('Duplicate entropy observation_ids')
        if 'selection' in d:
            _need(d, 'selection', dict, kind)
            for key in ('selected', 'triggered'):_boolean(d['selection'].get(key), 'selection.' + key)
            for key in ('candidate_count', 'selected_count', 'branch_count'):_integer(d['selection'].get(key), 'selection.' + key)
        if 'signals' in d:
            _need(d, 'signals', dict, kind)
            for key in ('task', 'entropy'):
                if key in d['signals']:
                    signal = d['signals'][key]
                    _signal(signal, 'signals.' + key)
                    mask = signal.get('loss_mask');count = signal.get('loss_mask_tokens')
                    if isinstance(mask, list) and all(v is not None for v in mask) and count is not None:
                        if count!=sum(v!=0 for v in mask):
                            raise ValidationError('signals.' + key + '.loss_mask_tokens conflicts with its recorded loss_mask')
        if 'masks' in d:
            _need(d, 'masks', dict, kind)
            for key in ('sample', 'step', 'token'):_numbers(d['masks'].get(key), 'masks.' + key, mask=True)
    if 'reported_metrics' in d:
        if not isinstance(d['reported_metrics'], list):raise ValidationError('reported_metrics must be an array')
        for metric in d['reported_metrics']:
            if not isinstance(metric, dict):raise ValidationError('Reported metric must be an object')
            _need(metric, 'name', str, 'metric');_finite(metric.get('value'), 'metric.value')


def _same_evidence(repo, owner, target):
    if not repo._same_scope(owner, target):raise ValidationError('Entropy evidence must belong to the same exact run scope')
    if evidence_demo(repo, owner)!=evidence_demo(repo, target):
        raise ValidationError('Entropy synthetic and real evidence must remain separate')


def _formal_binding(repo, obj):
    d = obj['data']; eid = d.get('evaluation_id');tid = d.get('trajectory_id')
    ref = d.get('trace_ref')
    if isinstance(ref, dict) and ref.get('trajectory'):
        if tid and tid!=ref['trajectory']:raise ValidationError('Conflicting entropy trajectory references')
        tid = ref['trajectory']
    if not eid and not tid:return
    if repo.store is None:raise ValidationError('Entropy binding requires a registered evaluation/trajectory')
    if tid:
        trajectory = repo.store.trajectory(tid)
        if eid and eid!=trajectory['evaluation_id']:raise ValidationError('Entropy trajectory belongs to another evaluation')
        eid = trajectory['evaluation_id']
    ev = repo.store.evaluation(eid)
    expected = {'project_id':ev['project_id'], 'experiment_id':ev['experiment_id'], 'run_id':ev['checkpoint']['run_id']}
    if any(obj.get('scope', {}).get(key, '')!=value for key, value in expected.items()):
        raise ValidationError('Entropy evaluation belongs to another experiment/run scope')
    demo = bool(ev.get('demo') or ev['run'].get('demo') or ev['panel'].get('demo'))
    if evidence_demo(repo, obj)!=demo:raise ValidationError('Entropy synthetic and real evaluation evidence must remain separate')
    if d.get('checkpoint_id') and d['checkpoint_id']!=ev['checkpoint']['id']:
        raise ValidationError('Entropy checkpoint does not match its evaluation')


def validate_entropy_links(repo, o, pending):
    kind = o['kind'];d = o['data']
    if kind=='entropy_comparison':
        from .entropy_evaluation import validate_entropy_comparison_links
        validate_entropy_comparison_links(repo, o, pending)
        return
    if kind not in ENTROPY_KINDS and kind not in ('training_use', 'training_update'):return
    from .schema import direct_refs, evidence_refs
    for oid, _, _ in direct_refs(o):
        target = repo._raw_object(oid, pending)
        entropy_consumption = kind=='training_update' and target['kind']=='training_use' and any(
            repo._raw_object(aid, pending)['kind'] in RECORD_KINDS for aid in target['data'].get('artifact_ids', []))
        if target['kind']=='entropy_definition':
            ts = target.get('scope', {});os = o.get('scope', {})
            if evidence_demo(repo, o)!=evidence_demo(repo, target):
                raise ValidationError('Entropy synthetic and real definitions must remain separate, including shareable definitions')
            if ts.get('run_id') and ts.get('run_id')!=os.get('run_id') and target.get('shareable') is not True:
                raise ValidationError('Entropy definition from another run must be explicitly shareable')
            if ts.get('experiment_id') and ts['experiment_id']!=os.get('experiment_id') and target.get('shareable') is not True:
                raise ValidationError('Entropy definition from another experiment must be explicitly shareable')
        elif kind in RECORD_KINDS or target['kind'] in RECORD_KINDS or entropy_consumption:
            _same_evidence(repo, o, target)
        if kind=='training_use' and target['kind']=='entropy_intervention' and target['data']['mode']=='runtime':
            raise ValidationError('A runtime entropy intervention cannot be labeled training consumption')
    if kind not in RECORD_KINDS:return
    _formal_binding(repo, o)
    if d.get('checkpoint_id'):
        if repo.store is None:raise ValidationError('Entropy checkpoint requires registration')
        cp = repo.store._get('checkpoints', d['checkpoint_id'])
        if cp['run_id']!=o.get('scope', {}).get('run_id', ''):
            raise ValidationError('Entropy checkpoint belongs to another run')
    for ref in evidence_refs(o):
        if ref.get('object_id'):_same_evidence(repo, o, repo._raw_object(ref['object_id'], pending))
        elif ref.get('trajectory'):
            _formal_binding(repo, {**o, 'data':{**d, 'trajectory_id':ref['trajectory']}})
            if d.get('trajectory_id') and d['trajectory_id']!=ref['trajectory']:
                raise ValidationError('Entropy source evidence belongs to another trajectory')
    if kind=='entropy_intervention':
        for oid in d.get('observation_ids', []):
            observation = repo._raw_object(oid, pending)['data']
            if observation['definition_id']!=d['definition_id']:
                raise ValidationError('Entropy intervention must use its declared measurement definition')
            for key in ('checkpoint_id', 'evaluation_id', 'trajectory_id', 'step', 'training_step'):
                if key in d and key in observation and d[key]!=observation[key]:
                    raise ValidationError('Entropy intervention and observation disagree on ' + key)


def _present(value):
    if isinstance(value, str) and value.strip().lower() in ('', 'unknown', 'unavailable', 'not_recorded', 'unrecorded', 'not_collected', 'not_exported', 'unspecified'):
        return False
    return value is not None and value!={} and value!=[]


def _unknown_paths(value, path):
    if isinstance(value, dict):
        return [p for key, child in value.items() for p in _unknown_paths(child, path+'.'+key)]
    if isinstance(value, list):
        return [p for index, child in enumerate(value) for p in _unknown_paths(child, path+'.'+str(index))]
    return [path] if not _present(value) else []


def definition_semantics(definition_or_data):
    """Return exact estimator semantics without pretending missing fields agree.

    Model revision is deliberately outside the quantity signature, allowing a
    controlled checkpoint comparison; tokenizer and every distribution transform
    remain inside it. Full immutable data still has its separate content hash.
    """
    d = definition_or_data.get('data', definition_or_data)
    dist = d.get('distribution', {});ts = d.get('token_scope', {});impl = d.get('implementation', {})
    choices = {
        'quantity_kind':[d.get('quantity_kind')], 'estimator':[d.get('estimator')],
        'unit':[d.get('unit')], 'formula':[d.get('formula'), impl.get('formula')],
        'support':[d.get('support'), dist.get('support')],
        'top_k':[d.get('top_k'), dist.get('top_k')],
        'window':[d.get('window'), ts.get('window')],
        'scoring_temperature':[d.get('scoring_temperature'), dist.get('scoring_temperature')],
        'sampling_temperature':[d.get('sampling_temperature'), dist.get('sampling_temperature')],
        'log_base':[d.get('log_base'), impl.get('log_base')],
        'normalization':[d.get('normalization'), d.get('aggregation', {}).get('normalization')],
        'token_mask':[ts.get('mask_definition'), ts.get('token_mask')],
        'segment_type':[ts.get('segment_type'), ts.get('region')],
        'tokenizer':[d.get('tokenizer'), dist.get('tokenizer')],
        'implementation_version':[impl.get('version'), impl.get('git_commit'), impl.get('source_hash')],
    }
    semantics = {};missing = [];conflicts = []
    for key, values in choices.items():
        observed = [v for v in values if _present(v)]
        semantics[key] = observed[0] if observed else None
        if not observed:missing.append(key)
        # A release version and a commit are complementary identifiers. Other
        # duplicate spelling variants must agree rather than selecting one.
        if key!='implementation_version' and len({digest(v) for v in observed})>1:conflicts.append(key)
    semantics.update(distribution={k:v for k, v in dist.items() if k not in ('policy_revision', 'checkpoint_id', 'model_revision', 'model_id')},
                     token_scope=ts, aggregation=d.get('aggregation', {}), implementation=impl)
    for key in ('distribution', 'token_scope', 'aggregation', 'implementation', 'support', 'tokenizer'):
        missing += _unknown_paths(semantics.get(key), key)
    if not d.get('aggregation'):missing.append('aggregation')
    if not dist:missing.append('distribution')
    support = semantics.get('support');support_kind = support.get('kind') if isinstance(support, dict) else support
    estimator = str(d.get('estimator', '')).lower()
    if ('full_vocab' in estimator or 'full-vocab' in estimator) and support_kind in ('sampled_token', 'sampled_tokens', 'top_k', 'topk_candidates'):
        conflicts.append('estimator_support')
    if d.get('unit')=='bits' and semantics.get('log_base') not in (None, 2, '2', 'log2'):
        conflicts.append('unit_log_base')
    if d.get('unit')=='nats' and semantics.get('log_base') not in (None, 'e', 'natural', 'ln', math.e):
        conflicts.append('unit_log_base')
    missing = list(dict.fromkeys(missing))
    return {'semantics':semantics, 'definition_hash':digest(d), 'compatibility_key':digest(semantics),
            'complete':not missing and not conflicts, 'missing_fields':missing, 'conflicts':conflicts,
            'quantity_kind':semantics['quantity_kind'], 'unit':d.get('unit'),
            'note':'同名不等于同一量；缺口径不自动补全。此定义不声称高熵、低熵或零熵代表正确率。'}


def entropy_projection(repo, o):
    d = o['data'];kind = o['kind']
    if kind=='entropy_definition':
        semantic = definition_semantics(d)
        return {'name':d['name'], 'estimator':d['estimator'], **_small(semantic),
                'source':_small(d.get('source')), 'version':o.get('version', d.get('version')),
                'evidence_demo':evidence_demo(repo, o)}
    if kind=='entropy_comparison':
        from .entropy_evaluation import entropy_comparison_summary
        return {**entropy_comparison_summary(o), 'evidence_demo':evidence_demo(repo, o)}
    p = {key:_small(d[key]) for key in ('label', 'native_metric_name', 'definition_id', 'context', 'value', 'missing_reason', 'checkpoint_id',
        'evaluation_id', 'trajectory_id', 'sample_id', 'dataset', 'traj_uid', 'step', 'training_step',
        'prefix_id', 'prefix_hash', 'frozen_prefix', 'token_count', 'sample_count', 'source_format',
        'source_version', 'coverage', 'probe_type', 'mode', 'intervention_type', 'selection', 'costs', 'budget', 'measurement_support') if key in d}
    if p.get('trajectory_id') and not p.get('evaluation_id') and repo.store:
        p['evaluation_id'] = repo.store.trajectory(p['trajectory_id'])['evaluation_id']
    p['recorded_steps'] = [d['step']] if type(d.get('step')) is int else []
    p['evidence_demo'] = evidence_demo(repo, o)
    if d.get('definition_id'):
        definition = repo._raw_object(d['definition_id']);semantic = definition_semantics(definition)
        p['definition'] = {k:semantic[k] for k in ('definition_hash', 'compatibility_key', 'complete', 'missing_fields', 'conflicts', 'quantity_kind', 'unit')}
        p['definition'].update(id=definition['id'], name=definition['data']['name'], estimator=definition['data']['estimator'])
    p['token_rows'] = len(next(iter(d.get('tokens', {}).values()), []));p['token_columns'] = list(d.get('tokens', {}))
    p['measurement_status'] = 'not_measured' if d.get('value') is None else 'recorded' if p.get('definition', {}).get('complete') else 'definition_incomplete'
    if kind=='entropy_probe':p['interpretation'] = 'signed_diagnostic_proxy_not_entropy'
    support = d.get('measurement_support', {})
    if 'reported_native_value' in d:p['reported_value'] = _small(d['reported_native_value'])
    if support.get('available') is False or support.get('count')==0 or d.get('coverage', {}).get('support_count')==0:
        p.update(reported_value=d.get('reported_native_value', d.get('value')), value=None, measurement_status='uninterpretable',
                 missing_reason='已记录有效支持不可用或为零；来源 fallback 数值不能解释为测得的熵。')
    if kind=='entropy_probe' and d.get('probe_type')=='budget_record':
        p.update(measurement_status='metadata_only', interpretation='budget_record_not_entropy')
    if kind=='entropy_intervention':
        p['signals'] = {key:_entropy_term(d.get('signals', {}).get(key), d.get('masks')) for key in ('task', 'entropy')}
        if d['mode']=='runtime':
            for signal in p['signals'].values():signal.update(active=None, stage='runtime', reason='推理时策略干预；不声明优化器学习项')
        p['masks'] = {key:_numeric(value) for key, value in d.get('masks', {}).items()}
        p['observation_ids'] = _small(d.get('observation_ids', []))
    p['reported_metrics'] = [_small(value) for value in d.get('reported_metrics', [])[:32]]
    p['reported_metric_count'] = len(d.get('reported_metrics', []))
    p['claims'] = {'gradient_contribution':False, 'effective_exploration':False, 'task_success':False,
                   'calibrated_error_probability':False, 'information_gain':False}
    return p


def _entropy_term(value, masks=None):
    """Honor the explicitly logged scalar final-weight spelling locally.

    Native entropy hooks may log raw_weight/effective_weight instead of the
    shared loss_weight coefficient. A scalar effective_weight can supply that
    coefficient; a vector remains token weights. Conflicting scalar spellings
    remain unknown rather than selecting one. Source JSON is not modified.
    """
    if not isinstance(value, dict):return _term(value, masks)
    final_weight = value.get('effective_weight');coefficient = value.get('loss_weight')
    scalar_final = type(final_weight) in (int, float)
    if scalar_final and coefficient is not None and coefficient!=final_weight:
        result = _term(value, masks)
        result.update(active=None, stage='conflicting', weight_source='conflicting_scalar_fields',
                      reason='loss_weight 与 scalar effective_weight 不一致，无法确定实际最终系数；原始两值均保留。')
        return result
    if scalar_final and coefficient is None:
        result = _term({**value, 'loss_weight':final_weight}, masks)
        result.update(weight_source='effective_weight', weight_interpretation='explicit_scalar_final_coefficient')
        return result
    result = _term(value, masks)
    result['weight_source'] = 'loss_weight' if coefficient is not None else 'not_recorded'
    return result


def _scope(repo, experiment_id, run_id):
    if not experiment_id:raise ValidationError('An explicit experiment_id is required')
    scope = repo._scope({'experiment_id':experiment_id, **({'run_id':run_id} if run_id else {})})
    scope['run_id'] = run_id or ''
    return scope


def _read(repo, scope, kinds):
    from .assessment import _read as read
    return read(repo, scope, kinds)


def _item(row):
    return {**{key:row.get(key) for key in ('id', 'kind', 'title', 'usage', 'scope', 'demo', 'content_hash')},
            'demo':row.get('assessment', {}).get('evidence_demo', row.get('demo')),
            'assessment':row.get('assessment', {}), 'body_loaded':False}


def _page(items, offset, limit):
    return {'items':items[offset:offset+limit], 'total':len(items), 'offset':offset, 'limit':limit,
            'has_more':offset+limit<len(items)}


def entropy_configuration(repo, scope):
    profiles = repo.objects(scope, kind='method_profile');origin = 'run' if scope.get('run_id') else 'experiment'
    if not profiles and scope.get('run_id'):
        profiles = repo.objects({**scope, 'run_id':''}, kind='method_profile');origin = 'experiment'
    declarations = []
    for profile in profiles:
        if 'entropy' not in profile['data'].get('families', []):continue
        cfg = profile['data'].get('configuration', {}).get('entropy')
        if isinstance(cfg, dict):declarations.append({'profile_id':profile['id'], 'source':'configuration.entropy', **_small(cfg)})
    keys = set().union(*(set(value)-{'profile_id', 'source'} for value in declarations)) if declarations else set()
    keys.update(('enabled', 'training_enabled', 'runtime_enabled', 'entropy_coeff', 'intervention_type', 'definition_id'))
    values = {};conflicts = []
    for key in sorted(keys):
        observed = [value[key] for value in declarations if key in value];unique = {digest(v) for v in observed}
        values[key] = observed[0] if len(unique)==1 else None
        if len(unique)>1:conflicts.append(key)
    return {'status':'conflicting' if conflicts else 'recorded' if declarations else 'not_recorded',
            'origin':origin if declarations else 'not_recorded', 'values':values, 'declarations':declarations,
            'conflicts':conflicts, 'note':'配置声明与实际选择、有效信号、采样及完成更新分开记录；不补训练框架默认值。'}


def _definition_rows(repo, scope, rows):
    """Only current definitions and explicitly referenced parent definitions."""
    selected = {r['id']:r for r in rows if r['kind']=='entropy_definition'}
    ids = {r['assessment'].get('definition_id') for r in rows if r['kind'] in RECORD_KINDS} - {None}
    for oid in ids-selected.keys():
        # Definition bodies are small, but read the existing index to avoid
        # loading unrelated trace or token vectors in a list screen.
        with repo.lock:
            row = repo.conn.execute('SELECT summary FROM method_summaries WHERE id=?', (oid,)).fetchone()
            if row:
                import json
                summary = json.loads(row[0])
                from .projection import VERSION
                if summary.get('assessment_version', 0)<VERSION:
                    summary.update(assessment_version=VERSION, assessment=entropy_projection(repo, repo._raw_object(oid)))
                    from ..inspection.repository import encode
                    repo.conn.execute('UPDATE method_summaries SET summary=? WHERE id=?', (encode(summary), oid))
                selected[oid] = summary
    return [_item(value) for value in selected.values()]


def _series(rows):
    groups = {};point_budget = 200
    for row in rows:
        if row['kind'] not in ('entropy_observation', 'entropy_probe') or row['assessment'].get('probe_type')=='budget_record':continue
        p = row['assessment'];definition = p.get('definition', {})
        # Preserve definition identity even when two signatures happen to agree.
        # The chart therefore cannot silently turn a changed estimator into a
        # continuous series or average fixed and visited states together.
        key = (p.get('definition_id'), p.get('context', 'unknown'), p.get('checkpoint_id'),
               p.get('prefix_hash') or p.get('frozen_prefix', {}).get('sha256'), p.get('probe_type'), p.get('evidence_demo', row.get('demo')) is True)
        entry = groups.setdefault(key, {'definition_id':key[0], 'context':key[1], 'checkpoint_id':key[2],
            'prefix_hash':key[3], 'probe_type':key[4], 'demo':key[5], 'compatibility_key':definition.get('compatibility_key'),
            'definition_complete':definition.get('complete', False), 'points':[], 'record_count':0, 'missing_count':0,
            'aggregation':'not_pooled'})
        entry['record_count'] += 1;entry['missing_count'] += p.get('value') is None
        if len(entry['points'])<200 and point_budget>0:
            entry['points'].append({k:p.get(k) for k in ('value', 'step', 'training_step', 'sample_id', 'token_count', 'sample_count')}
                                   | {'record_id':row['id'], 'status':p.get('measurement_status')})
            point_budget -= 1
    out = list(groups.values())
    for entry in out:entry['points_truncated'] = entry['record_count']>len(entry['points'])
    return out[:100], len(out)


def _coverage(rows):
    observations = [r for r in rows if r['kind']=='entropy_observation']
    probes = [r for r in rows if r['kind']=='entropy_probe']
    measured_probes = [r for r in probes if r['assessment'].get('probe_type')!='budget_record']
    interventions = [r for r in rows if r['kind']=='entropy_intervention']
    contexts = Counter(r['assessment'].get('context', 'unknown') for r in observations+measured_probes)
    modes = Counter(r['assessment'].get('mode') for r in interventions)
    measured = sum(r['assessment'].get('value') is not None for r in observations+measured_probes)
    return {'observations':len(observations), 'probes':len(probes), 'interventions':len(interventions),
            'contexts':{key:contexts[key] for key in CONTEXTS}, 'modes':{key:modes[key] for key in ('training', 'runtime')},
            'measured_records':measured, 'unmeasured_records':len(observations)+len(measured_probes)-measured,
            'metadata_records':len(probes)-len(measured_probes),
            'definition_incomplete_records':sum(not r['assessment'].get('definition', {}).get('complete') for r in observations+measured_probes),
            'scope_complete':None, 'note':'只统计已登记记录；采集覆盖率与整个训练批次的完整性未自动推断。'}


def entropy_training(repo, experiment_id, run_id='', offset=0, limit=25):
    scope = _scope(repo, experiment_id, run_id);offset, limit = page_args(offset, limit)
    rows = _read(repo, scope, ENTROPY_KINDS + ('training_use', 'training_update'))
    items = []
    for row in rows:
        if row['kind'] not in RECORD_KINDS:continue
        item = _item(row);item['training'] = _training_for(row['id'], rows)
        items.append(item)
    comparisons = [_item(row) for row in rows if row['kind']=='entropy_comparison']
    experiment_comparisons = [_item(row) for row in _read(repo, {**scope, 'run_id':''}, ('entropy_comparison',))] if run_id else []
    series, series_count = _series(rows)
    definitions = _definition_rows(repo, scope, rows)
    return {'scope':scope, 'status':'recorded' if items or comparisons else 'not_recorded', **_page(items, offset, limit),
            'configuration':entropy_configuration(repo, scope), 'definitions':definitions[:100], 'definition_count':len(definitions),
            'coverage':_coverage(rows), 'series':series, 'series_count':series_count,
            'comparison_records':comparisons[:100], 'comparison_count':len(comparisons),
            'experiment_comparison_records':experiment_comparisons[:100], 'experiment_comparison_count':len(experiment_comparisons),
            'costs':{'records':[{'record_id':r['id'], 'values':r['assessment']['costs']} for r in rows if r['assessment'].get('costs')][:100],
                     'aggregation':'not_pooled'},
            'notes':['训练访问状态与固定前缀 probe 分别显示；同名的不同定义不拼成同一条熵曲线。',
                     'Entropy scalar、有效选择信号、实际更新和自主 EM 是不同层面的证据。',
                     'token 与完整原始载荷按需读取；MI proxy 与输入辨识率不当作严格互信息或搜索命中率。']}


def _runtime_condition(repo, scope, evaluation_id):
    out = {'status':'unknown', 'context':{}, 'binding_id':None,
           'note':'评测时熵策略与额外预算需明确记录；缺日志不代表关闭了干预。'}
    if not evaluation_id:return out
    rows = [o for o in repo.objects(scope, kind='evaluation_binding') if o['data']['evaluation_id']==evaluation_id]
    if not rows:return out
    definitions = [o['data'].get('entropy_context') for o in rows if isinstance(o['data'].get('entropy_context'), dict)]
    if not definitions:return out
    if len({digest(d) for d in definitions})>1:return {**out, 'status':'conflicting'}
    return {**out, 'status':'recorded', 'binding_id':rows[0]['id'], 'context':_small(definitions[0])}


def entropy_runtime(repo, experiment_id, run_id='', evaluation_id='', trajectory_id='', offset=0, limit=25, step=None):
    scope = _scope(repo, experiment_id, run_id);offset, limit = page_args(offset, limit)
    if step not in (None, ''):
        try:step = int(step)
        except (TypeError, ValueError) as exc:raise ValidationError('Entropy step must be a nonnegative integer') from exc
        _integer(step, 'step')
    else:step = None
    if evaluation_id or trajectory_id:
        if repo.store is None:raise ValidationError('Entropy runtime filters require a registered evaluation')
        if trajectory_id:
            tid = repo.store.trajectory(trajectory_id)
            if evaluation_id and tid['evaluation_id']!=evaluation_id:raise ValidationError('Entropy trajectory is outside selected evaluation')
            evaluation_id = tid['evaluation_id']
        ev = repo.store.evaluation(evaluation_id)
        if ev['experiment_id']!=scope['experiment_id'] or ev['checkpoint']['run_id']!=scope['run_id']:
            raise ValidationError('Entropy runtime evaluation is outside selected experiment/run')
    all_rows = _read(repo, scope, ENTROPY_KINDS)
    rows = [row for row in all_rows if row['kind'] in RECORD_KINDS and row['assessment'].get('context')!='training_on_policy'
            and not (row['kind']=='entropy_intervention' and row['assessment'].get('mode')=='training')]
    if evaluation_id:rows = [row for row in rows if row['assessment'].get('evaluation_id')==evaluation_id]
    if trajectory_id:rows = [row for row in rows if row['assessment'].get('trajectory_id')==trajectory_id]
    if step is not None:rows = [row for row in rows if step in row['assessment'].get('recorded_steps', [])]
    series, series_count = _series(rows)
    definitions = _definition_rows(repo, scope, rows)
    return {'scope':scope, 'status':'recorded' if rows else 'not_recorded', **_page([_item(row) for row in rows], offset, limit),
            'filters':{'evaluation_id':evaluation_id, 'trajectory_id':trajectory_id, 'step':step},
            'coverage':_coverage(rows), 'definitions':definitions[:100], 'definition_count':len(definitions), 'series':series, 'series_count':series_count,
            'runtime_condition':_runtime_condition(repo, scope, evaluation_id),
            'notes':['只读取当前评测、轨迹和原始步骤的显式绑定；外部 traj_uid 不升格为正式轨迹。',
                     '工具前后生成片段的平均熵差不直接解释为 observation 信息增益。']}


def entropy_record(repo, oid):
    obj = repo.object(oid)
    if obj['kind'] not in RECORD_KINDS + ('entropy_definition',):raise ValidationError('Expected an entropy definition, observation, probe or intervention')
    projection = entropy_projection(repo, obj);data, omitted = _bounded_detail(obj['data'])
    rows = _read(repo, {**obj.get('scope', {}), 'run_id':obj.get('scope', {}).get('run_id', '')}, ('training_use', 'training_update'))
    return {'id':oid, 'scope':obj.get('scope', {}), 'object':{**obj, 'data':data}, 'diagnostic':projection,
            'training':_training_for(oid, rows), 'body_loaded':True, 'omitted_fields':omitted,
            'raw_object_url':'/api/methods/object?id='+quote(oid, safe=''),
            'tokens_url':'/api/methods/entropy-tokens?id='+quote(oid, safe=''),
            'notes':['完整载荷保持不可变；大型 token 与原始向量不进入列表或首个详情。',
                     '所记录的熵量不是校准错误概率，也不证明因果梯度贡献或有效探索。']}


def _bounded_detail(data):
    omitted = []
    def visit(value, path):
        root = path.split('/')[1] if path else ''
        if path in ('/tokens', '/raw_payload', '/native_payload', '/raw', '/source_payload', '/raw_rows', '/native_source/row'):
            omitted.append(path);return {'detail_only':True, 'raw_pointer':path}
        if isinstance(value, list):
            if root in ('signals', 'masks') or len(value)>32:
                omitted.append(path);return {'count':len(value), 'detail_only':True, 'raw_pointer':path}
            return [visit(v, path+'/'+str(i)) for i, v in enumerate(value)]
        if isinstance(value, dict):
            return {k:visit(v, path+'/'+k) for k, v in list(value.items())[:100]}
        if isinstance(value, str) and len(value)>4000:
            omitted.append(path);return {'preview':value[:400], 'characters':len(value), 'detail_only':True, 'raw_pointer':path}
        return value
    return visit(data, ''), omitted


def entropy_tokens(repo, oid, offset=0, limit=50):
    offset, limit = page_args(offset, limit);obj = repo.object(oid)
    if obj['kind'] not in RECORD_KINDS:raise ValidationError('Entropy token view requires observation, probe or intervention')
    columns = obj['data'].get('tokens', {});total = len(next(iter(columns.values()), []))
    items = [{'index':i, **{key:values[i] for key, values in columns.items()}} for i in range(offset, min(total, offset+limit))]
    return {'scope':obj.get('scope', {}), 'object_id':oid, 'columns':list(columns), 'items':items, 'total':total,
            'offset':offset, 'limit':limit, 'has_more':offset+limit<total, 'coverage':_small(obj['data'].get('coverage', {})),
            'notes':['原始位置、片段与实际 mask 按来源保留；没有导出的列保持缺失。',
                     '只有 sampled probability 或有限候选时，不生成全词表 entropy。']}
