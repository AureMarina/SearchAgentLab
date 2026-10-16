"""Entropy evidence semantics, entirely synthetic and without model execution."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from searchagent_lab.core import ValidationError
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.methods import views
from searchagent_lab.methods.entropy_tracking import definition_semantics
from searchagent_lab.server import LabServer


def complete_definition():
    return {'name':'Synthetic full vocabulary entropy', 'quantity_kind':'entropy', 'estimator':'full_vocab',
            'unit':'nats', 'formula':'-sum_v p(v|h) log p(v|h)', 'log_base':'e', 'normalization':'none',
            'support':{'kind':'full_vocabulary'}, 'top_k':'disabled', 'window':'all',
            'scoring_temperature':1.0, 'sampling_temperature':0.0,
            'tokenizer':{'id':'synthetic-tokenizer', 'revision':'fixture-1'},
            'distribution':{'logits_stage':'after_scoring_temperature', 'top_p':1.0, 'top_k':'disabled'},
            'token_scope':{'segment_type':'query', 'mask_definition':'logged_response_tokens_only'},
            'aggregation':{'level':'token', 'weighting':'equal'},
            'implementation':{'version':'synthetic-1', 'function':'fixture.entropy'}}


class EntropyTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.rec = MethodRecorder(self.tmp.name)
        self.r = self.rec.repo;self.s = self.rec.store
        self.s.register_project({'id':'p', 'name':'Synthetic entropy'})
        self.s.register_experiment({'id':'e', 'project_id':'p', 'name':'Entropy experiment'})
        for run in ('a', 'b'):
            self.s.register_run({'id':run, 'experiment_id':'e', 'name':run, 'demo':True})
            self.s.register_checkpoint({'id':'cp-'+run, 'run_id':run, 'step':1})

    def tearDown(self):self.rec.close();self.tmp.cleanup()
    def obj(self, oid, kind='entropy_observation', **data):return {'id':oid, 'kind':kind, 'demo':True, 'data':data}
    def put(self, *objects, run='a'):
        return self.r.import_bundle({'method_schema':'method-evidence-v1', 'objects':list(objects)},
                                    scope={'run_id':run} if run else {'experiment_id':'e'})
    def definition(self, oid='h', **kw):return self.obj(oid, 'entropy_definition', **(complete_definition() | kw))
    def observation(self, oid='o', **kw):return self.obj(oid, **({'definition_id':'h', 'value':1., 'context':'training_on_policy'} | kw))
    def intervention(self, oid='i', **kw):
        return self.obj(oid, 'entropy_intervention', **({'definition_id':'h', 'intervention_type':'token_reweight', 'mode':'training'} | kw))
    def term(self, **kw):
        return {'objective':'entropy_regularization', 'raw':1., 'effective':.5, 'loss_mask_tokens':2, 'loss_weight':.1} | kw
    def evaluation(self, eid='ev', run='a'):
        if not self.s.conn.execute('SELECT id FROM panels WHERE id="panel"').fetchone():
            self.s.register_panel({'id':'panel', 'name':'Synthetic panel', 'split':'probe', 'demo':True,
                'samples':[{'id':'q', 'dataset':'fixture', 'question':'Synthetic?', 'answers':['yes']}]})
        protocol = json.loads((Path(__file__).parents[1]/'examples/protocol_v2.synthetic.json').read_text())
        self.s.start_evaluation({'id':eid, 'checkpoint_id':'cp-'+run, 'panel_id':'panel', 'protocol':protocol, 'purpose':'checkpoint_eval'})
        self.s.log_trajectory(eid, {'sample_id':'q', 'dataset':'fixture', 'scores':{'em':0}, 'steps':[],
                                   'runtime_status':'ok', 'termination_reason':'final_answer'})
        self.s.finish_evaluation(eid)
        return self.s.conn.execute('SELECT id FROM trajectories WHERE evaluation_id=?', (eid,)).fetchone()[0]

    def test_empty_scope_stays_unmeasured(self):
        data = self.r.entropy_training('e', 'a')
        self.assertEqual(data['status'], 'not_recorded');self.assertEqual(data['items'], [])
        self.assertIsNone(data['configuration']['values']['entropy_coeff'])
        self.assertIsNone(data['coverage']['scope_complete'])

    def test_family_is_explicit_and_run_scoped(self):
        self.put(self.definition(), self.observation())
        self.assertEqual([x['id'] for x in views.experiment_overview(self.r, 'e', 'a')['families']], ['entropy'])
        self.assertEqual(self.r.entropy_training('e', 'b')['items'], [])
        self.assertEqual(self.r.entropy_training('e')['items'], [])

    def test_complete_definition_and_immutable_identity(self):
        self.put(self.definition())
        p = self.r.entropy_record('h')['diagnostic'];self.assertTrue(p['complete'])
        with self.assertRaises(ValidationError):self.put(self.definition(log_base=2, unit='bits'))

    def test_every_measurement_semantic_changes_signature(self):
        original = complete_definition();base = definition_semantics(original)['compatibility_key']
        changes = {'estimator':'sampled_contribution', 'formula':'other', 'support':{'kind':'top_k'}, 'top_k':10,
                   'window':20, 'scoring_temperature':.8, 'sampling_temperature':.8, 'log_base':2,
                   'normalization':'log_vocab', 'aggregation':{'level':'decision', 'weighting':'mean'},
                   'token_scope':{'segment_type':'answer', 'mask_definition':'response_only'},
                   'tokenizer':{'id':'other', 'revision':'2'}}
        for key, value in changes.items():
            with self.subTest(key=key):self.assertNotEqual(base, definition_semantics(original | {key:value})['compatibility_key'])

    def test_legacy_incomplete_definition_never_default_filled(self):
        data = complete_definition()
        for key in ('scoring_temperature', 'sampling_temperature', 'support', 'tokenizer'):data.pop(key)
        self.put(self.obj('h', 'entropy_definition', **data), self.observation())
        p = self.r.entropy_record('o')['diagnostic']
        self.assertFalse(p['definition']['complete']);self.assertEqual(p['measurement_status'], 'definition_incomplete')
        self.assertIn('scoring_temperature', p['definition']['missing_fields'])

    def test_support_conflicts_are_visible_not_treated_as_full_entropy(self):
        self.put(self.definition(support={'kind':'sampled_token'}), self.observation())
        p = self.r.entropy_record('o')['diagnostic']['definition']
        self.assertFalse(p['complete']);self.assertIn('estimator_support', p['conflicts'])

    def test_scoring_greedy_and_sampling_greedy_are_different(self):
        self.put(self.definition(sampling_temperature=0))
        with self.assertRaisesRegex(ValidationError, 'scoring_temperature'):
            self.put(self.definition('bad', scoring_temperature=0))

    def test_signed_probe_is_not_entropy_or_search_hit_rate(self):
        self.put(self.definition(), self.obj('mi', 'entropy_probe', definition_id='h', probe_type='mi_proxy',
                                            value=-.2, context='fixed_prefix_probe'))
        p = self.r.entropy_record('mi')['diagnostic']
        self.assertEqual(p['value'], -.2);self.assertEqual(p['interpretation'], 'signed_diagnostic_proxy_not_entropy')
        self.assertFalse(p['claims']['information_gain'])

    def test_negative_entropy_still_rejected(self):
        self.put(self.definition())
        with self.assertRaisesRegex(ValidationError, 'nonnegative'):self.put(self.observation(value=-1))

    def test_null_and_zero_preserve_distinct_status(self):
        self.put(self.definition(), self.observation('zero', value=0),
                 self.observation('none', value=None, missing_reason='not exported'))
        p = self.r.entropy_record('zero')['diagnostic'];self.assertEqual(p['measurement_status'], 'recorded')
        self.assertFalse(p['claims']['task_success']);self.assertFalse(p['claims']['calibrated_error_probability'])
        self.assertEqual(self.r.entropy_record('none')['diagnostic']['measurement_status'], 'not_measured')

    def test_explicit_no_support_keeps_fallback_zero_uninterpretable(self):
        self.put(self.definition(), self.observation(value=0, measurement_support={'available':False, 'count':0}))
        p = self.r.entropy_record('o')['diagnostic']
        self.assertIsNone(p['value']);self.assertEqual(p['reported_value'], 0)
        self.assertEqual(p['measurement_status'], 'uninterpretable');self.assertEqual(self.r.object('o')['data']['value'], 0)

    def test_budget_record_is_not_an_entropy_point(self):
        self.put(self.definition(), self.obj('budget', 'entropy_probe', definition_id='h', probe_type='budget_record',
                                            context='evaluation', value=0, budget={'generated_tokens':10}))
        p = self.r.entropy_record('budget')['diagnostic'];self.assertEqual(p['measurement_status'], 'metadata_only')
        data = self.r.entropy_training('e', 'a');self.assertEqual(data['series'], [])
        self.assertEqual(data['coverage']['measured_records'], 0);self.assertEqual(data['coverage']['metadata_records'], 1)

    def test_unknown_source_revision_never_becomes_complete_template(self):
        data = complete_definition();data['implementation'].update(producer_revision='unknown', schema_reference_sha256='a'*64)
        semantics = definition_semantics(data)
        self.assertFalse(semantics['complete']);self.assertIn('implementation.producer_revision', semantics['missing_fields'])

    def test_series_do_not_merge_definition_context_or_checkpoint(self):
        self.put(self.definition(), self.definition('h2', name='Same name, changed window', window=20),
                 self.observation('train'), self.observation('fixed', context='fixed_prefix_probe', prefix_hash='a'*64),
                 self.observation('other', definition_id='h2'))
        data = self.r.entropy_training('e', 'a');self.assertEqual(data['series_count'], 3)
        self.assertTrue(all(x['aggregation']=='not_pooled' for x in data['series']))

    def test_token_arrays_are_lazy_bounded_and_paged(self):
        n = 20000
        self.put(self.definition(), self.observation(tokens={'tokens':['x']*n, 'token_ids':list(range(n)), 'entropy':[0.]*n},
                                                    native_source={'row':{'huge':list(range(n))}}))
        with patch.object(self.r, 'object', side_effect=AssertionError('list must not load token body')):
            listing = self.r.entropy_training('e', 'a')
        self.assertLess(len(json.dumps(listing)), 16000)
        detail = self.r.entropy_record('o');self.assertLess(len(json.dumps(detail)), 10000)
        self.assertIn('/tokens', detail['omitted_fields']);self.assertIn('/native_source/row', detail['omitted_fields'])
        page = self.r.entropy_tokens('o', 19000, 5)
        self.assertEqual((page['total'], page['items'][0]['token_ids'], len(page['items'])), (n, 19000, 5))

    def test_missing_token_columns_not_synthesized(self):
        self.put(self.definition(), self.observation(tokens={'probabilities':[.1, .2], 'log_probs':[-2.3, -1.6]}))
        data = self.r.entropy_tokens('o');self.assertNotIn('entropy', data['columns']);self.assertNotIn('mask', data['columns'])

    def test_token_lengths_and_positions_validated(self):
        self.put(self.definition())
        with self.assertRaisesRegex(ValidationError, 'equal lengths'):
            self.put(self.observation(tokens={'entropy':[1, 2], 'mask':[1]}))
        with self.assertRaisesRegex(ValidationError, 'distinct'):
            self.put(self.observation(tokens={'positions':[1, 1], 'entropy':[1, 2]}))

    def test_model_token_mask_requires_numeric_nonnegative_support(self):
        self.put(self.definition())
        for mask in (['yes'], [-1]):
            with self.subTest(mask=mask), self.assertRaisesRegex(ValidationError, 'model_token_mask'):
                self.put(self.observation(tokens={'entropy':[.2], 'model_token_mask':mask}))
        self.put(self.observation(tokens={'entropy':[.2, None], 'model_token_mask':[1, None]}))
        self.assertEqual(self.r.entropy_tokens('o')['items'][1]['model_token_mask'], None)

    def test_conflicting_recorded_mask_count_is_rejected(self):
        self.put(self.definition())
        with self.assertRaisesRegex(ValidationError, 'loss_mask_tokens conflicts'):
            self.put(self.intervention(signals={'entropy':self.term(effective=[.2, .1], loss_mask=[1, 0], loss_mask_tokens=2)}))

    def test_final_mask_can_remove_all_nonzero_entropy_signal(self):
        self.put(self.definition(), self.intervention(signals={'entropy':self.term(effective=[1, 0], loss_mask=[0, 1], loss_mask_tokens=1)}))
        p = self.r.entropy_record('i')['diagnostic']['signals']['entropy']
        self.assertFalse(p['active']);self.assertEqual(p['stage'], 'inactive')

    def test_signal_with_incomplete_support_remains_unknown(self):
        self.put(self.definition(), self.intervention(signals={'entropy':self.term(effective=[1, 0], loss_mask=[1], loss_mask_tokens=1)}))
        self.assertIsNone(self.r.entropy_record('i')['diagnostic']['signals']['entropy']['active'])

    def test_observed_entropy_scalar_does_not_claim_effective_loss(self):
        self.put(self.definition(), self.intervention(signals={'entropy':{'raw':.5}}))
        p = self.r.entropy_record('i');self.assertIsNone(p['diagnostic']['signals']['entropy']['active'])
        self.assertIsNone(p['training']['update_completed']);self.assertFalse(p['diagnostic']['claims']['gradient_contribution'])

    def test_logged_scalar_effective_weight_supplies_final_coefficient(self):
        signal = self.term(effective=[.2, 0, -.1], loss_mask=[1, 1, 1], loss_mask_tokens=3, raw_weight=.01, effective_weight=.01)
        signal.pop('loss_weight')
        self.put(self.definition(), self.intervention(signals={'entropy':signal}))
        result = self.r.entropy_record('i')['diagnostic']['signals']['entropy']
        self.assertTrue(result['active']);self.assertEqual(result['weight_source'], 'effective_weight')
        self.assertEqual(result['loss_weight'], .01)
        self.assertNotIn('loss_weight', self.r.object('i')['data']['signals']['entropy'])

    def test_conflicting_scalar_weight_spellings_stay_unknown(self):
        self.put(self.definition(), self.intervention(signals={'entropy':self.term(loss_weight=.1, effective_weight=.2)}))
        result = self.r.entropy_record('i')['diagnostic']['signals']['entropy']
        self.assertIsNone(result['active']);self.assertEqual(result['stage'], 'conflicting')

    def test_vector_final_weights_do_not_supply_missing_scalar_coefficient(self):
        signal = self.term(effective=[.2, -.1], loss_mask=[1, 1], effective_weight=[.01, .02]);signal.pop('loss_weight')
        self.put(self.definition(), self.intervention(signals={'entropy':signal}))
        result = self.r.entropy_record('i')['diagnostic']['signals']['entropy']
        self.assertIsNone(result['active']);self.assertEqual(result['weight_source'], 'not_recorded')

    def test_runtime_intervention_never_becomes_training_update(self):
        self.put(self.definition(), self.intervention(mode='runtime', signals={'entropy':self.term()}))
        self.assertIsNone(self.r.entropy_record('i')['diagnostic']['signals']['entropy']['active'])
        with self.assertRaisesRegex(ValidationError, 'runtime entropy intervention'):
            self.put(self.obj('u', 'training_use', artifact_ids=['i'], status='sampled', batch_id='batch'))

    def test_selection_sample_update_stages_remain_separate(self):
        self.put(self.definition(), self.intervention(selection={'selected':True}, signals={'entropy':self.term()}),
                 self.obj('u', 'training_use', artifact_ids=['i'], status='sampled', batch_id='batch'))
        data = self.r.entropy_record('i');self.assertTrue(data['training']['sampled']);self.assertIsNone(data['training']['update_completed'])
        self.put(self.obj('update', 'training_update', consumption_ids=['u'], status='completed'))
        data = self.r.entropy_record('i');self.assertTrue(data['training']['update_completed']);self.assertFalse(data['training']['causal_contribution'])

    def test_intervention_must_use_matching_definition_and_position(self):
        self.put(self.definition(), self.definition('h2', window=20), self.observation(step=2))
        with self.assertRaisesRegex(ValidationError, 'measurement definition'):
            self.put(self.intervention(definition_id='h2', observation_ids=['o']))
        with self.assertRaisesRegex(ValidationError, 'disagree on step'):
            self.put(self.intervention(observation_ids=['o'], step=3))

    def test_cross_run_observation_link_is_rejected(self):
        self.put(self.definition(), self.observation())
        self.put(self.definition('h2'), run='b')
        with self.assertRaisesRegex(ValidationError, 'same exact run'):
            self.put(self.intervention(definition_id='h2', observation_ids=['o']), run='b')

    def test_parent_definition_reusable_but_sibling_record_not_inherited(self):
        self.put(self.definition(), run='')
        self.put(self.observation())
        data = self.r.entropy_training('e', 'a');self.assertEqual(data['definitions'][0]['id'], 'h')
        self.assertEqual(self.r.entropy_training('e', 'b')['items'], [])

    def test_synthetic_real_link_cannot_mix(self):
        self.put(self.definition(), self.observation())
        intervention = self.intervention(observation_ids=['o']);intervention['demo'] = False
        with self.assertRaisesRegex(ValidationError, 'synthetic and real'):self.put(intervention)

    def test_shareable_definition_does_not_bypass_synthetic_boundary(self):
        definition = self.definition();definition.update(demo=False, shareable=True)
        self.put(definition, run='')
        with self.assertRaisesRegex(ValidationError, 'synthetic and real definitions'):
            self.put(self.observation())

    def test_legacy_missing_demo_inherits_run_in_list_without_changing_raw(self):
        definition = self.definition();observation = self.observation()
        definition.pop('demo');observation.pop('demo')
        self.put(definition, observation)
        listing = self.r.entropy_training('e', 'a')
        self.assertTrue(listing['items'][0]['demo']);self.assertTrue(listing['definitions'][0]['demo'])
        self.assertTrue(listing['series'][0]['demo'])
        self.assertNotIn('demo', self.r.object('o'));self.assertNotIn('demo', self.r.object('h'))

    def test_runtime_uses_exact_eval_trajectory_step(self):
        tid = self.evaluation();other = self.evaluation('ev2')
        self.put(self.definition(), self.observation('x', context='evaluation', evaluation_id='ev', trajectory_id=tid, step=0),
                 self.observation('y', context='evaluation', evaluation_id='ev2', trajectory_id=other, step=1),
                 self.observation('train', context='training_on_policy', evaluation_id='ev', trajectory_id=tid))
        self.assertEqual([x['id'] for x in self.r.entropy_runtime('e', 'a', 'ev', tid, step=0)['items']], ['x'])
        self.assertEqual(self.r.entropy_runtime('e', 'a', 'ev', tid, step=1)['items'], [])
        with self.assertRaisesRegex(ValidationError, 'outside selected evaluation'):self.r.entropy_runtime('e', 'a', 'ev2', tid)

    def test_formal_checkpoint_or_scope_mismatch_rejected(self):
        tid = self.evaluation();self.put(self.definition())
        with self.assertRaisesRegex(ValidationError, 'checkpoint does not match'):
            self.put(self.observation(context='evaluation', trajectory_id=tid, checkpoint_id='cp-b'))
        with self.assertRaisesRegex(ValidationError, 'outside selected experiment/run'):
            self.r.entropy_runtime('e', 'b', 'ev')

    def test_external_traj_uid_never_becomes_formal_binding(self):
        self.put(self.definition(), self.observation(context='evaluation', traj_uid='native-row-17'))
        p = self.r.entropy_record('o')['diagnostic'];self.assertEqual(p['traj_uid'], 'native-row-17');self.assertNotIn('trajectory_id', p)

    def test_eval_runtime_context_preserves_extra_resources(self):
        self.evaluation()
        context = {'runtime_intervention':'entropy', 'extra_resources':{'branching':True, 'summary_model':False, 'teacher':False},
                   'training_only_guidance_removed':True}
        self.put(self.obj('binding', 'evaluation_binding', evaluation_id='ev', binding_origin='recorded_execution', entropy_context=context))
        got = self.r.entropy_runtime('e', 'a', 'ev')['runtime_condition']
        self.assertEqual(got['context'], context);self.assertEqual(got['status'], 'recorded')

    def test_profile_values_inherit_without_default_or_conflict_choice(self):
        self.put(self.obj('parent', 'method_profile', method_name='Synthetic entropy', families=['entropy'],
                         configuration={'entropy':{'entropy_coeff':.1}}), run='')
        self.assertEqual(self.r.entropy_training('e', 'a')['configuration']['values']['entropy_coeff'], .1)
        self.put(self.obj('p1', 'method_profile', method_name='A', families=['entropy'], configuration={'entropy':{'entropy_coeff':.2}}),
                 self.obj('p2', 'method_profile', method_name='B', families=['entropy'], configuration={'entropy':{'entropy_coeff':.3}}))
        cfg = self.r.entropy_training('e', 'a')['configuration'];self.assertEqual(cfg['status'], 'conflicting')
        self.assertIsNone(cfg['values']['entropy_coeff'])

    def test_http_reads_saved_scoped_data_and_token_page(self):
        self.put(self.definition(), self.observation(tokens={'positions':list(range(63)), 'entropy':[.1]*63}))
        server = LabServer(('127.0.0.1', 0), self.s)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            with urllib.request.urlopen(base+'/api/methods/entropy-training?experiment_id=e&run_id=a') as response:
                data = json.load(response)
            self.assertEqual(data['items'][0]['id'], 'o');self.assertEqual(data['coverage']['observations'], 1)
            with urllib.request.urlopen(base+'/api/methods/entropy-tokens?id=o&offset=50&limit=50') as response:
                page = json.load(response)
            self.assertEqual((page['total'], len(page['items']), page['items'][0]['positions']), (63, 13, 50))
            self.assertFalse(page['has_more'])
        finally:server.shutdown();thread.join();server.server_close()


if __name__=='__main__':unittest.main()
