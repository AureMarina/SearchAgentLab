"""Synthetic evidence semantics. No teacher, environment or optimizer runs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from searchagent_lab.core import ValidationError
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.methods import views


class OPDTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.rec = MethodRecorder(self.tmp.name)
        self.r = self.rec.repo; self.s = self.rec.store
        self.s.register_project({'id':'p', 'name':'Synthetic OPD'})
        self.s.register_experiment({'id':'e', 'project_id':'p', 'name':'OPD experiment'})
        for run in ('a', 'b'):self.s.register_run({'id':run, 'experiment_id':'e', 'name':run, 'demo':True})
        self.s.register_checkpoint({'id':'cp-a', 'run_id':'a', 'step':1})
        self.s.register_checkpoint({'id':'cp-b', 'run_id':'b', 'step':1})

    def tearDown(self):self.rec.close(); self.tmp.cleanup()
    def obj(self, oid, kind='opd_diagnostic', **data):return {'id':oid, 'kind':kind, 'demo':True, 'data':data}
    def put(self, *objects, run='a'):
        return self.r.import_bundle({'method_schema':'method-evidence-v1', 'objects':list(objects)},
                                    scope={'run_id':run} if run else {'experiment_id':'e'})
    def diag(self, oid='d', **data):return self.obj(oid, diagnostic_type='step', **data)
    def verification(self, correct):
        return {'status':'passed' if correct else 'failed', 'method':'scorer', 'verifier':{'id':'strict-em', 'version':'synthetic-1'}}
    def group(self, oid='g', outcomes=(False, False), **data):
        g = {'expected_size':len(outcomes), 'samples_complete':True,
             'samples':[{'rollout_id':'rollout-'+str(i), 'verification':self.verification(v), 'original_advantage':0}
                        for i, v in enumerate(outcomes)]}
        g.update(data);return self.obj(oid, diagnostic_type='group', context='training_on_policy', group_id=oid, training_step=1, group=g)
    def term(self, **kw):
        d = {'objective':'distillation', 'raw':3.0, 'effective':0.5, 'loss_mask_tokens':2, 'loss_weight':1, 'source':'synthetic native tensor'}
        d.update(kw);return d
    def profile(self, oid='profile', **cfg):
        return self.obj(oid, 'method_profile', method_name='Synthetic', families=['opd'], configuration={'opd':cfg})
    def evaluation(self, eid='eval', run='a', bind=False, context=None):
        if not self.s.conn.execute('SELECT id FROM panels WHERE id="panel"').fetchone():
            self.s.register_panel({'id':'panel', 'name':'Synthetic panel', 'split':'probe', 'demo':True,
                                   'samples':[{'id':'q', 'dataset':'fixture', 'question':'Synthetic?', 'answers':['yes']}]})
        protocol = json.loads((Path(__file__).parents[1]/'examples/protocol_v2.synthetic.json').read_text())
        self.s.start_evaluation({'id':eid, 'checkpoint_id':'cp-'+run, 'panel_id':'panel', 'protocol':protocol, 'purpose':'checkpoint_eval'})
        self.s.log_trajectory(eid, {'sample_id':'q', 'dataset':'fixture', 'scores':{'em':0}, 'steps':[],
                                   'runtime_status':'ok', 'termination_reason':'final_answer'})
        self.s.finish_evaluation(eid)
        tid = self.s.conn.execute('SELECT id FROM trajectories WHERE evaluation_id=?', (eid,)).fetchone()[0]
        if bind:
            data = {'evaluation_id':eid, 'binding_origin':'recorded_execution', 'autonomous':True,
                    'independent':True, 'assessment_families':['opd']}
            if context is not None:data['opd_context'] = context
            self.put(self.obj('binding-'+eid, 'evaluation_binding', **data), run=run)
        return tid

    def test_empty_run_preserves_unknowns_and_no_invented_data(self):
        data = self.r.opd_training('e', 'a')
        self.assertEqual(data['status'], 'not_recorded');self.assertEqual(data['items'], [])
        self.assertIsNone(data['coverage']['task_advantage']['value'])
        self.assertIsNone(data['configuration']['values']['use_task_rewards'])
        self.assertEqual(self.r.opd_runtime('e', 'a')['teacher_condition']['autonomous'], None)

    def test_only_explicit_current_scope_activates_opd_family(self):
        self.put(self.diag())
        self.assertEqual([f['id'] for f in views.experiment_overview(self.r, 'e', 'a')['families']], ['opd'])
        self.assertEqual(self.r.opd_training('e', 'b')['total'], 0)
        self.assertEqual(self.r.opd_training('e')['total'], 0)
        self.put(self.diag('unbound'), run='')
        self.assertEqual(self.r.opd_training('e')['items'][0]['id'], 'unbound')

    def test_full_verifier_groups_distinguish_all_wrong_and_mixed(self):
        self.put(self.group('wrong'), self.group('right', (True, True)), self.group('mixed', (True, False)))
        coverage = self.r.opd_training('e', 'a')['coverage']['group_classification']
        self.assertEqual((coverage['all_wrong'], coverage['all_correct'], coverage['mixed'], coverage['unknown']), (1, 1, 1, 0))

    def test_rewards_never_synthesize_correctness(self):
        self.put(self.group(samples=[{'rollout_id':'r1', 'task_reward':1}, {'rollout_id':'r2', 'task_reward':1}]))
        p = self.r.opd_record('g')['diagnostic']['group']
        self.assertEqual(p['classification'], 'unknown');self.assertEqual(p['verified_size'], 0)

    def test_partial_group_keeps_missing_trial_denominators(self):
        g = self.group(expected_size=4, samples_complete=False)
        self.put(g);p = self.r.opd_record('g')['diagnostic']['group']
        self.assertEqual(p['classification'], 'unknown')
        self.assertEqual((p['task_advantage']['zero'], p['task_advantage']['unknown'], p['task_advantage']['expected']), (2, 2, 4))
        self.assertEqual(p['teacher_coverage']['unknown'], 4)

    def test_duplicate_rollouts_and_false_completeness_rejected(self):
        g = self.group();g['data']['group']['samples'][1]['rollout_id'] = 'rollout-0'
        with self.assertRaisesRegex(ValidationError, 'distinct rollout'):self.put(g)
        with self.assertRaisesRegex(ValidationError, 'expected_size'):self.put(self.group(expected_size=4))

    def test_model_judge_outcomes_do_not_become_verified_group(self):
        g = self.group()
        for s in g['data']['group']['samples']:s['verification']['method'] = 'model_judge'
        self.put(g);self.assertEqual(self.r.opd_record('g')['diagnostic']['group']['classification'], 'unknown')

    def test_verifier_error_retained_as_unknown(self):
        g = self.group();g['data']['group']['samples'][1]['verification'] = {'status':'error', 'reason':'timeout'}
        self.put(g);p = self.r.opd_record('g')['diagnostic']['group']
        self.assertEqual((p['classification'], p['verified_size'], p['wrong']), ('unknown', 1, 1))

    def test_raw_and_effective_task_opd_signals_stay_separate(self):
        self.put(self.diag(signals={'task':self.term(objective='policy_gradient', raw=0, effective=0), 'opd':self.term()}))
        p = self.r.opd_record('d')['diagnostic']['signals']
        self.assertFalse(p['task']['active']);self.assertTrue(p['opd']['active'])
        self.assertEqual((p['opd']['raw'], p['opd']['effective']), (3.0, 0.5))
        self.assertFalse(self.r.opd_record('d')['diagnostic']['claims']['task_repair'])

    def test_raw_gap_alone_is_not_effective_update(self):
        self.put(self.diag(signals={'opd':self.term(effective=None)}))
        p = self.r.opd_record('d')
        self.assertIsNone(p['diagnostic']['signals']['opd']['active'])
        self.assertIsNone(p['training']['update_completed'])

    def test_zero_or_incomplete_masks_block_effective_claim(self):
        self.put(self.diag('zero', masks={'token':[0, 0]}, signals={'opd':self.term()}),
                 self.diag('partial', masks={'token':[1, None]}, signals={'opd':self.term()}))
        self.assertFalse(self.r.opd_record('zero')['diagnostic']['signals']['opd']['active'])
        self.assertIsNone(self.r.opd_record('partial')['diagnostic']['signals']['opd']['active'])

    def test_effective_vector_requires_surviving_aligned_token(self):
        self.put(self.diag('disjoint', signals={'opd':self.term(effective=[1, 0], loss_mask=[0, 1], loss_mask_tokens=1)}),
                 self.diag('common', masks={'token':[0, 1]}, signals={'opd':self.term(effective=[1, 0], loss_mask_tokens=1)}),
                 self.diag('mismatch', signals={'opd':self.term(effective=[1, 0], loss_mask=[1], loss_mask_tokens=1)}),
                 self.diag('weighted', signals={'opd':self.term(effective=[1, 0], effective_weight=[0, 1], loss_mask_tokens=1)}))
        for oid in ('disjoint', 'common', 'weighted'):
            self.assertFalse(self.r.opd_record(oid)['diagnostic']['signals']['opd']['active'])
        self.assertIsNone(self.r.opd_record('mismatch')['diagnostic']['signals']['opd']['active'])

    def test_duplicate_group_exports_are_not_counted_twice(self):
        a = self.group('first');b = self.group('duplicate');b['data']['group_id'] = 'first'
        probe = self.group('probe');probe['data']['context'] = 'evaluation'
        unbound = self.group('unknown');unbound['data'].pop('group_id')
        self.put(a, b, probe, unbound)
        coverage = self.r.opd_training('e', 'a')['coverage']
        self.assertEqual((coverage['group_classification']['all_wrong'], coverage['group_classification']['unknown']), (0, 1))
        self.assertEqual(coverage['duplicate_group_records'], [['first', 'duplicate']])
        self.assertEqual(coverage['unidentified_group_records'], ['unknown'])
        self.assertEqual(coverage['evaluation_group_records'], 1)
        self.assertIsNone(coverage['task_advantage']['value'])

    def test_pure_multiplicative_reweight_cannot_rescue_zero(self):
        with self.assertRaisesRegex(ValidationError, 'nonzero task advantage'):
            self.put(self.diag(signals={'task':self.term(objective='advantage_reweight', original_advantage=0, effective_advantage=2)}))

    def test_task_reward_flag_and_opd_pg_flag_are_independent(self):
        self.put(self.profile(enabled=True, use_task_rewards=False, use_policy_gradient=True, distillation_loss_coef=1),
                 self.diag(signals={'task':self.term()}))
        data = self.r.opd_training('e', 'a')
        self.assertFalse(data['configuration']['values']['use_task_rewards'])
        self.assertTrue(data['configuration']['values']['use_policy_gradient'])
        self.assertFalse(data['items'][0]['task_term_enabled'])
        self.assertEqual(self.r.object('d')['data']['signals']['task']['effective'], 0.5)

    def test_profile_inheritance_and_conflicts_do_not_pick_latest(self):
        self.put(self.profile('parent', use_task_rewards=True), run='')
        self.assertTrue(self.r.opd_training('e', 'a')['configuration']['values']['use_task_rewards'])
        self.put(self.profile('a1', use_task_rewards=False), self.profile('a2', use_task_rewards=True))
        cfg = self.r.opd_training('e', 'a')['configuration']
        self.assertEqual(cfg['status'], 'conflicting');self.assertIsNone(cfg['values']['use_task_rewards'])
        self.assertEqual(len(cfg['declarations']), 2)

    def test_native_joint_configuration_is_not_default_filled(self):
        self.put(self.obj('p', 'method_profile', method_name='Native', families=['opd'], configuration={
            'distillation':{'enabled':True, 'distillation_loss':{'use_task_rewards':True}}}))
        cfg = self.r.opd_training('e', 'a')['configuration']['values']
        self.assertTrue(cfg['enabled']);self.assertTrue(cfg['use_task_rewards']);self.assertIsNone(cfg['use_policy_gradient'])

    def test_config_coefficients_fail_cleanly_on_lists(self):
        with self.assertRaisesRegex(ValidationError, 'scalar'):self.put(self.profile(distillation_loss_coef=[1]))
        with self.assertRaisesRegex(ValidationError, 'scalar'):self.put(self.diag(signals={'opd':self.term(loss_weight=[1])}))

    def test_long_token_arrays_are_lazy_and_paged(self):
        n = 20000
        self.put(self.obj('tokens', diagnostic_type='token', tokens={'tokens':['token']*n, 'token_ids':list(range(n)), 'gaps':[0.0]*n}, raw_payload={'token_ids':list(range(n))}))
        with patch.object(self.r, 'object', side_effect=AssertionError('list must not load object body')):
            data = self.r.opd_training('e', 'a')
        self.assertLess(len(json.dumps(data)), 10000)
        detail = self.r.opd_record('tokens');self.assertLess(len(json.dumps(detail)), 12000)
        self.assertIn('/tokens', detail['omitted_fields']);self.assertIn('/raw_payload', detail['omitted_fields'])
        tokens = self.r.opd_tokens('tokens', 19000, 5)
        self.assertEqual((tokens['total'], len(tokens['items']), tokens['items'][0]['token_ids']), (n, 5, 19000))
        self.assertTrue(tokens['has_more']);self.assertEqual(self.r.object('tokens')['data']['tokens']['token_ids'][-1], 19999)

    def test_token_columns_require_aligned_lengths_and_keep_null(self):
        with self.assertRaisesRegex(ValidationError, 'equal lengths'):
            self.put(self.obj('bad', diagnostic_type='token', tokens={'tokens':['a'], 'gaps':[]}))
        self.put(self.obj('ok', diagnostic_type='token', tokens={'tokens':['a', None], 'gaps':[None, 0]}))
        self.assertIsNone(self.r.opd_tokens('ok')['items'][0]['gaps'])
        with self.assertRaises(ValidationError):self.r.opd_tokens('ok', 0, 201)

    def metric(self, **kw):
        d = {'name':'overlap_token_advantage', 'value':0, 'definition':'weighted expectation', 'definition_version':'native-1',
             'source':'trainer revision abc', 'support':{'kind':'teacher_top_k', 'available':True, 'count':10, 'total':12, 'k':20, 'normalization':'teacher_mass'}}
        d.update(kw);return d

    def test_zero_overlap_support_is_uninterpretable_not_aligned(self):
        self.put(self.obj('dist', diagnostic_type='distribution', reported_metrics=[self.metric(support={'kind':'teacher_top_k', 'available':True, 'count':0, 'k':20})]))
        m = self.r.opd_record('dist')['diagnostic']['reported_metrics'][0]
        self.assertEqual(m['reported_value'], 0);self.assertIsNone(m['value']);self.assertEqual(m['status'], 'uninterpretable')

    def test_sampled_token_logprob_cannot_produce_topk_overlap(self):
        self.put(self.obj('dist', diagnostic_type='distribution', reported_metrics=[self.metric(value=0.8, support={'kind':'sampled_token', 'available':True, 'count':10})]))
        m = self.r.opd_record('dist')['diagnostic']['reported_metrics'][0]
        self.assertIsNone(m['value']);self.assertEqual(m['reported_value'], 0.8)

    def test_metric_zero_with_known_support_and_definition_is_recorded(self):
        self.put(self.obj('dist', diagnostic_type='distribution', reported_metrics=[self.metric()]))
        m = self.r.opd_record('dist')['diagnostic']['reported_metrics'][0]
        self.assertEqual((m['value'], m['status'], m['support']['normalization']), (0, 'recorded', 'teacher_mass'))

    def test_null_support_and_different_definitions_are_not_merged(self):
        m1 = self.metric(support={'available':True, 'count':None, 'k':None})
        m2 = self.metric(definition_version='paper-2', value=0.3)
        self.put(self.obj('dist', diagnostic_type='distribution', reported_metrics=[m1, m2]))
        metrics = self.r.opd_training('e', 'a')['metrics']
        self.assertEqual(len(metrics), 2);self.assertEqual(metrics[0]['status'], 'reported_only')
        self.assertEqual(metrics[1]['definition_version'], 'paper-2')

    def test_ocsd_future_observation_and_nll_selection_are_distinct(self):
        self.put(self.obj('obs', diagnostic_type='observation', observation={'has_future_observation':True, 'is_nll_selected':False,
                    'full':1.2, 'ablated':1.0, 'residual':0.2, 'definition':'reported means', 'source':'OCSD synthetic'}))
        p = self.r.opd_record('obs')['diagnostic']
        self.assertTrue(p['observation']['has_future_observation']);self.assertFalse(p['observation']['is_nll_selected'])
        self.assertEqual(p['observation']['residual'], 0.2);self.assertIsNone(p['signals']['opd']['active'])

    def test_runtime_matches_evaluation_trajectory_original_step_only(self):
        t1 = self.evaluation('ev1');t2 = self.evaluation('ev2')
        self.put(self.diag('a1', evaluation_id='ev1', trajectory_id=t1, step=3, context='evaluation'),
                 self.diag('a2', evaluation_id='ev2', trajectory_id=t2, step=3, context='evaluation'),
                 self.diag('training', evaluation_id='ev1', trajectory_id=t1, step=3, context='training_on_policy'))
        data = self.r.opd_runtime('e', 'a', 'ev1', t1, step=3)
        self.assertEqual([r['id'] for r in data['items']], ['a1'])
        self.assertEqual(self.r.opd_runtime('e', 'a', 'ev1', t1, step=2)['total'], 0)
        with self.assertRaisesRegex(ValidationError, 'outside'):self.r.opd_runtime('e', 'b', 'ev1')

    def test_formal_checkpoint_and_evaluation_must_match_scope(self):
        t = self.evaluation()
        with self.assertRaisesRegex(ValidationError, 'another run'):self.put(self.diag(student_checkpoint_id='cp-b'))
        with self.assertRaisesRegex(ValidationError, 'checkpoint does not match'):
            self.put(self.diag(student_checkpoint_id='cp-b', evaluation_id='eval', trajectory_id=t))

    def test_teacher_conditions_require_explicit_removed_privileges(self):
        self.evaluation('missing', bind=True)
        self.assertIsNone(self.r.opd_runtime('e', 'a', 'missing')['teacher_condition']['autonomous'])
        ctx = {k:False for k in ('teacher_hints', 'peer_answers', 'future_observations', 'groundtruth_visible')}
        ctx['privileged_information'] = []
        self.evaluation('clean', bind=True, context=ctx)
        self.assertTrue(self.r.opd_runtime('e', 'a', 'clean')['teacher_condition']['autonomous'])
        ctx = {**ctx, 'future_observations':True}
        self.evaluation('privileged', bind=True, context=ctx)
        self.assertFalse(self.r.opd_runtime('e', 'a', 'privileged')['teacher_condition']['autonomous'])
        data = self.r.method_assessment('e', 'a', 'opd')
        self.assertEqual([v['evaluation_id'] for v in data['evaluation_options']], ['clean'])
        self.assertEqual(len(data['evaluation']['excluded']), 2)

    def test_cross_run_source_evidence_rejected(self):
        self.put(self.obj('source', 'trace', data={'x':1}), run='b')
        with self.assertRaisesRegex(ValidationError, 'another run scope'):
            self.put(self.diag(source_refs=[{'object_id':'source', 'pointer':''}]))

    def test_synthetic_real_evidence_cannot_be_mixed(self):
        source = self.obj('source', 'trace', message='real');source['demo'] = False;self.put(source)
        with self.assertRaisesRegex(ValidationError, 'synthetic and real'):
            self.put(self.diag(source_refs=[{'object_id':'source', 'pointer':''}]))

    def test_legacy_missing_source_demo_inherits_run_without_payload_rewrite(self):
        source = self.obj('legacy-source', 'trace', message='Original synthetic source');source.pop('demo')
        self.put(source)
        original = self.r.object('legacy-source')
        self.put(self.diag(source_refs=[{'object_id':'legacy-source', 'pointer':''}]))
        after = self.r.object('legacy-source')
        self.assertNotIn('demo', after)
        self.assertEqual((after['hash'], after['content_hash'], after['data']),
                         (original['hash'], original['content_hash'], original['data']))
        self.assertEqual(self.r.opd_record('d')['object']['data']['source_refs'][0]['object_id'], 'legacy-source')

    def test_explicit_false_owner_cannot_inherit_synthetic_run_for_opd_refs(self):
        self.put(self.obj('source', 'trace', message='Explicit synthetic source'))
        missing = self.diag('legacy', source_refs=[{'object_id':'source', 'pointer':''}]);missing.pop('demo')
        self.put(missing)
        explicit = self.diag('real', source_refs=[{'object_id':'source', 'pointer':''}]);explicit['demo'] = False
        with self.assertRaisesRegex(ValidationError, 'synthetic and real'):self.put(explicit)
        self.assertEqual(self.r.opd_training('e', 'a')['total'], 1)

    def test_update_association_needs_sampled_and_completed(self):
        self.put(self.diag())
        self.assertIsNone(self.r.opd_record('d')['training']['sampled'])
        self.put(self.obj('use', 'training_use', artifact_ids=['d'], status='sampled', batch_id='batch'))
        self.put(self.obj('pending', 'training_update', consumption_ids=['use'], status='pending'))
        self.assertIsNone(self.r.opd_record('d')['training']['update_completed'])
        self.put(self.obj('done', 'training_update', consumption_ids=['use'], status='completed'))
        result = self.r.opd_record('d')
        self.assertTrue(result['training']['update_completed']);self.assertFalse(result['training']['causal_contribution'])
        self.assertEqual(self.r.opd_training('e', 'a')['coverage']['completed_updates'], 1)

    def test_cross_run_update_of_opd_consumption_is_rejected(self):
        self.put(self.diag(), self.obj('use', 'training_use', artifact_ids=['d'], status='sampled', batch_id='batch'))
        with self.assertRaisesRegex(ValidationError, 'same run'):
            self.put(self.obj('cross', 'training_update', consumption_ids=['use'], status='completed'), run='b')

    def test_cost_stages_remain_separate_and_not_pooled(self):
        self.put(self.diag(costs={'student_rollout':{'output_tokens':10}, 'teacher_inference':{'output_tokens':20},
                                 'teacher_environment':{'search_calls':1}}))
        costs = self.r.opd_training('e', 'a')['costs']
        self.assertEqual(costs['aggregation'], 'not_pooled');self.assertEqual(len(costs['records']), 3)
        self.assertNotIn('total_tokens', costs)

    def aligned(self, oid='aligned', task=None, opd=None, mask=None):
        a = [-1.0, 1.0, -1.0, 1.0] if task is None else task
        b = [-0.5, 0.5, 0.5, 0.0] if opd is None else opd
        final_mask = [1]*len(a) if mask is None else mask
        return self.diag(oid, signal_alignment={'kind':'token', 'complete':True, 'positions':list(range(len(a))),
                         'position_unit':'response_token_index', 'source':'synthetic tensors', 'definition':'actual effective signal after selection'},
                         signals={'task':self.term(effective=a, loss_mask=final_mask),
                                  'opd':self.term(effective=b, loss_mask=final_mask)})

    def test_relation_uses_shared_nonzero_denominator_and_mask(self):
        self.put(self.aligned(), self.aligned('masked', mask=[1, 1, 0, 1]))
        r = self.r.opd_record('aligned')['diagnostic']['relation']
        self.assertEqual((r['shared_support_count'], r['shared_nonzero'], r['shared_zero']), (4, 3, 1))
        self.assertEqual((r['sign_conflict']['value'], r['sign_conflict']['numerator'], r['sign_conflict']['denominator']), (1/3, 1, 3))
        self.assertIsNotNone(r['pearson']['value']);self.assertFalse(r['task_repair_claim'])
        masked = self.r.opd_record('masked')['diagnostic']['relation']
        self.assertEqual(masked['sign_conflict']['value'], 0);self.assertEqual(masked['excluded'], 1)

    def test_relation_requires_exact_explicit_alignment_not_gap(self):
        a = self.aligned('missing');a['data'].pop('signal_alignment')
        b = self.aligned('incomplete');b['data']['signals']['task']['effective'][1] = None
        c = self.aligned('shape');c['data']['signals']['opd']['loss_mask'] = [1]
        self.put(a, b, c)
        for oid in ('missing', 'incomplete', 'shape'):
            r = self.r.opd_record(oid)['diagnostic']['relation']
            self.assertEqual(r['status'], 'unmeasured');self.assertIsNone(r['sign_conflict']['value'])
        self.assertEqual(self.r.opd_record('incomplete')['diagnostic']['relation']['unknown'], 1)

    def test_relation_zero_variance_and_zero_shared_support_are_uninterpretable(self):
        self.put(self.aligned('constant', task=[1, 1], opd=[1, 2]), self.aligned('zero', mask=[0, 0, 0, 0]))
        self.assertIsNone(self.r.opd_record('constant')['diagnostic']['relation']['pearson']['value'])
        zero = self.r.opd_record('zero')['diagnostic']['relation']
        self.assertEqual(zero['shared_support_count'], 0);self.assertIsNone(zero['sign_conflict']['value'])
        self.assertEqual(zero['sign_conflict']['denominator'], 0)


if __name__ == '__main__':unittest.main()
