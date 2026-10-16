"""Controlled entropy comparisons over deliberately synthetic saved evidence."""
import copy
import itertools
import json
import math
from pathlib import Path
import tempfile
import unittest

from searchagent_lab.core import ValidationError
from searchagent_lab.inspection.repository import digest
from searchagent_lab.methods.entropy_evaluation import error_auroc, risk_coverage
from searchagent_lab.methods.skill_comparison import _pair
from searchagent_lab.studio.sdk import StudioRecorder
from test_entropy_tracking_v096 import complete_definition


PROTOCOL = json.loads((Path(__file__).parents[1] / 'examples/protocol_v2.synthetic.json').read_text())
CONTEXT = {'runtime_intervention':'none', 'extra_resources':{'branching':False, 'summary_model':False, 'teacher':False},
           'training_only_guidance_removed':True}
COHORT = {'id':'frozen-synthetic', 'usage':'development', 'frozen':True, 'feedback_used':True, 'isolation_status':'unverified'}
TRAINING = {'initial_weights_sha256':'a'*64, 'training_data_sha256':'f'*64, 'trainer_revision':'synthetic-v1',
            'optimizer':{'name':'fixture-optimizer', 'learning_rate':0.001}, 'seed':42}


class EntropyRankingTests(unittest.TestCase):
    def test_perfect_error_ranking_and_discrete_aurc(self):
        values, errors = [.1, .2, .8, .9], [0, 0, 1, 1]
        self.assertEqual(error_auroc(values, errors), 1)
        aurc, curve = risk_coverage(values, errors)
        self.assertAlmostEqual(aurc, 5/24)
        self.assertEqual(curve[-1], {'coverage':1, 'risk':.5, 'retained':4})

    def test_ties_are_label_order_independent(self):
        for errors in set(itertools.permutations([0, 0, 1, 1])):
            self.assertEqual(error_auroc([.5]*4, errors), .5)
            self.assertEqual(risk_coverage([.5]*4, errors)[0], .5)

    def test_orientation_does_not_flip_automatically(self):
        self.assertEqual(error_auroc([.9, .8, .2, .1], [0, 0, 1, 1]), 0)
        self.assertAlmostEqual(risk_coverage([.9, .8, .2, .1], [0, 0, 1, 1])[0], 19/24)

    def test_one_class_is_undefined_auroc_but_known_risk(self):
        self.assertIsNone(error_auroc([1, 2], [0, 0]))
        self.assertEqual(risk_coverage([1, 2], [0, 0])[0], 0)
        self.assertIsNone(error_auroc([1, 2], [1, 1]))
        self.assertEqual(risk_coverage([1, 2], [1, 1])[0], 1)
        self.assertEqual(risk_coverage([], []), (None, []))

    def test_curve_downsampling_preserves_exact_metric(self):
        values = list(range(501));errors = [i % 2 for i in values]
        a, curve = risk_coverage(values, errors, max_points=25)
        b, _ = risk_coverage(values, errors, max_points=501)
        self.assertEqual(a, b);self.assertEqual(len(curve), 25)
        self.assertEqual(curve[-1]['retained'], 501)


class EntropyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.rec = StudioRecorder(self.tmp.name)
        self.s, self.r = self.rec.store, self.rec.methods;self.serial = 0
        self.s.register_project({'id':'p', 'name':'Synthetic'})
        self.s.register_experiment({'id':'e', 'project_id':'p', 'name':'Synthetic entropy'})
        self.rec.repo.register_profile({'experiment_id':'e', 'track':'main'})
        for role, char in (('entropy', 'b'), ('baseline', 'c'), ('random', 'd')):
            self.s.register_run({'id':role, 'experiment_id':'e', 'name':role, 'demo':True,
                                 'base_model':'fixture-model', 'model_revision':'fixture-base'})
            self.s.register_checkpoint({'id':'cp-'+role, 'run_id':role, 'step':1, 'weights_sha256':char*64})
        self.s.register_checkpoint({'id':'cp0', 'run_id':'entropy', 'step':0, 'weights_sha256':'a'*64})
        self.s.register_panel({'id':'panel', 'name':'Synthetic questions', 'split':'probe', 'demo':True,
            'samples':[{'id':'q'+str(i), 'dataset':'fixture', 'question':'Synthetic question '+str(i)+'?', 'answers':['yes']} for i in range(4)]})
        definition = complete_definition();definition['aggregation']['name']='masked_token_mean'
        self.emit('h', 'entropy_definition', definition)

    def tearDown(self):self.rec.close();self.tmp.cleanup()

    def emit(self, oid, kind, data, run='entropy', **extra):
        obj = {'id':oid, 'kind':kind, 'demo':True, 'data':copy.deepcopy(data), **extra}
        self.r.import_bundle({'method_schema':'method-evidence-v1', 'objects':[obj]},
                             scope={'run_id':run} if run else {'experiment_id':'e'})
        return oid

    def comparison(self, data, run='entropy'):
        self.serial += 1;oid = 'comparison-'+str(self.serial)
        self.emit(oid, 'entropy_comparison', data, run=run)
        return self.r.entropy_comparison(oid)

    def evaluation(self, eid, cpid='cp-entropy', scores=(1, 0, 1, 0), *, context=CONTEXT, purpose='checkpoint_eval', change=None,
                   panel_id='panel', sample_prefix='q'):
        protocol = copy.deepcopy(PROTOCOL);protocol['harness_version']='e'*64
        if change:change(protocol)
        self.s.start_evaluation({'id':eid, 'checkpoint_id':cpid, 'panel_id':panel_id, 'protocol':protocol, 'purpose':purpose})
        for i, score in enumerate(scores):
            self.s.log_trajectory(eid, {'id':eid+'-q'+str(i), 'sample_id':sample_prefix+str(i), 'dataset':'fixture',
                'scores':{'em':score}, 'steps':[], 'runtime_status':'ok', 'termination_reason':'final_answer',
                'usage':{'input_tokens':10, 'output_tokens':5, 'tool_calls':1}})
        self.s.finish_evaluation(eid)
        binding = {'evaluation_id':eid, 'binding_origin':'recorded_execution', 'state_complete':True,
                   'independent':True, 'autonomous':True, 'skills_disabled':True, 'retrieval_config':{'mode':'fixed'}}
        if context is not None:binding['entropy_context']=copy.deepcopy(context)
        self.emit('binding-'+eid, 'evaluation_binding', binding, run=self.s._get('checkpoints', cpid)['run_id'])
        return [eid+'-q'+str(i) for i in range(4)]

    def fixed_pair(self, *, after_change=None):
        self.emit('prefix-source', 'trace', {'input':['Synthetic frozen question?', 'Saved answer prefix']})
        definition = self.r.object('h')['data'];prefix = self.r.object('prefix-source')['data']['input']
        frozen = {'id':'prefix-1', 'sha256':digest(prefix), 'input_ref':{'object_id':'prefix-source', 'pointer':'/input'},
                  'tokenizer':definition['tokenizer'], 'scoring_temperature':1.0}
        for oid, cp, value in (('before', 'cp0', 1.0), ('after', 'cp-entropy', .5)):
            data = {'definition_id':'h', 'context':'fixed_prefix_probe', 'checkpoint_id':cp, 'value':value,
                    'prefix_hash':digest(prefix), 'frozen_prefix':copy.deepcopy(frozen),
                    'tokens':{'token_ids':[1, 2, 3], 'positions':[0, 1, 2], 'mask':[1, 0, 1],
                              'segment_types':['query']*3, 'entropy':[value, None, value]}}
            if oid=='after' and after_change:after_change(data)
            self.emit(oid, 'entropy_observation', data)
        return {'comparison_type':'fixed_prefix', 'cohort':copy.deepcopy(COHORT),
                'pairs':[{'id':'q', 'before_observation_id':'before', 'after_observation_id':'after'}]}

    def uncertainty(self, *, second_values=None, scores=(1, 0, 1, 0), context=CONTEXT, probability=None):
        tids = self.evaluation('uncertainty-ev', scores=scores, context=context);estimators = []
        for key, values in (('a', [.1, .9, .2, .8]), ('b', second_values or [.2, .7, .3, .6])):
            evidence_scores = []
            for i, (tid, value) in enumerate(zip(tids, values)):
                oid = 'score-'+key+'-'+str(i)
                data = {'definition_id':'h', 'probe_type':'estimator_score', 'context':'evaluation',
                        'checkpoint_id':'cp-entropy', 'evaluation_id':'uncertainty-ev', 'trajectory_id':tid, 'value':value}
                if value is None:data['missing_reason']='Synthetic missing export'
                if probability and value is not None:data['probability_error']=probability(value)
                self.emit(oid, 'entropy_probe', data)
                score = {'trajectory_id':tid, 'value':value, 'evidence_ref':{'object_id':oid, 'pointer':''}}
                if probability and value is not None:score['probability_error']=probability(value)
                evidence_scores.append(score)
            estimators.append({'id':key, 'definition_id':'h', 'higher_is_uncertain':True, 'scores':evidence_scores,
                'probe_protocol':{'sampling_budget':{'num_samples':1, 'max_new_tokens':128}, 'sample_set_hash':digest(sorted(tids))}})
        return {'comparison_type':'uncertainty', 'evaluation_id':'uncertainty-ev', 'trajectory_ids':tids,
                'estimators':estimators, 'cohort':copy.deepcopy(COHORT)}

    def interventions(self, *, mode='training', proof_change=None, update_status='completed', runtime_weight_change=False):
        arms = []
        for role, scores in (('baseline', [0, 0, 1, 0]), ('random', [1, 0, 1, 0]), ('entropy', [1, 1, 1, 0])):
            cp = 'cp-'+role if mode=='training' or runtime_weight_change else 'cp-entropy'
            run = self.s._get('checkpoints', cp)['run_id'];eid = mode+'-ev-'+role
            context = copy.deepcopy(CONTEXT)
            if mode=='runtime':context['runtime_intervention']='none' if role=='baseline' else role
            self.evaluation(eid, cp, scores, context=context, purpose='checkpoint_eval' if mode=='training' else 'agent_eval')
            did = 'h-'+role;self.emit(did, 'entropy_definition', complete_definition(), run=run)
            intervention_ids = []
            if role!='baseline':
                oid = 'intervention-'+role
                self.emit(oid, 'entropy_intervention', {'definition_id':did, 'mode':mode, 'intervention_type':'token_selection',
                    'selection':{'policy':role, 'selected':True, 'selected_count':2}}, run=run)
                intervention_ids.append(oid)
                if mode=='training':
                    self.emit('use-'+role, 'training_use', {'artifact_ids':[oid], 'status':'sampled', 'step':1, 'batch_id':'batch-'+role}, run=run)
                    self.emit('update-'+role, 'training_update', {'consumption_ids':['use-'+role], 'status':update_status,
                        'step':1, 'output_checkpoint_id':cp}, run=run)
            selection = {'policy':'none' if role=='baseline' else role, 'selected_count':0 if role=='baseline' else 2, 'seed':42}
            budget = {'generated_tokens':100, 'tool_calls':10, 'optimizer_updates':1 if mode=='training' else 0}
            proof = {'definition_id':did, 'probe_type':'budget_record', 'context':'evaluation', 'value':0,
                'evaluation_id':eid, 'checkpoint_id':cp, 'budget':budget, 'selection':selection, 'training_controls':copy.deepcopy(TRAINING)}
            if proof_change:proof_change(role, proof)
            self.emit('cost-'+role, 'entropy_probe', proof, run=run)
            arms.append({'id':role, 'checkpoint_id':cp, 'evaluation_id':eid, 'budget':copy.deepcopy(proof['budget']),
                'selection':copy.deepcopy(proof['selection']), 'intervention_ids':intervention_ids,
                'cost_ref':{'object_id':'cost-'+role, 'pointer':''}})
        return {'comparison_type':'intervention', 'mode':mode, 'budget_keys':list(arms[0]['budget']),
                'arms':arms, 'cohort':copy.deepcopy(COHORT)}

    def test_fixed_prefix_verified_real_token_support(self):
        r = self.comparison(self.fixed_pair())
        self.assertEqual(r['status'], 'measured');self.assertEqual(r['summary']['mean_delta'], -.5)
        self.assertEqual(r['pairs'][0]['token_pairing']['selected_count'], 2)
        self.assertFalse(r['claims']['tool_information_gain'])

    def test_same_length_different_token_is_incomparable(self):
        d = self.fixed_pair(after_change=lambda d:d['tokens']['token_ids'].__setitem__(2, 99))
        r = self.comparison(d);self.assertEqual(r['status'], 'incomparable');self.assertIsNone(r['pairs'][0]['delta'])
        self.assertIn('token IDs', r['pairs'][0]['reason'])

    def test_frozen_prefix_hash_must_resolve_to_original_input(self):
        d = self.fixed_pair(after_change=lambda d:d['frozen_prefix'].update(sha256='f'*64))
        self.assertIn('哈希', self.comparison(d)['pairs'][0]['reason'])

    def test_fixed_prefix_mean_needs_registered_frozen_cohort(self):
        d = self.fixed_pair();d['cohort']['frozen']=False;r = self.comparison(d)
        self.assertEqual(r['pairs'][0]['delta'], -.5);self.assertIsNone(r['summary']['mean_delta'])

    def test_masked_missing_tokens_do_not_become_zero(self):
        d = self.fixed_pair(after_change=lambda d:d['tokens']['entropy'].__setitem__(0, None))
        r = self.comparison(d);self.assertIsNone(r['pairs'][0]['delta'])

    def test_reported_mean_must_match_saved_selected_values(self):
        d = self.fixed_pair(after_change=lambda d:d.update(value=.8))
        self.assertIn('masked-token mean', self.comparison(d)['pairs'][0]['reason'])

    def test_training_on_policy_never_becomes_same_state_probe(self):
        d = self.fixed_pair(after_change=lambda d:d.update(context='training_on_policy'))
        self.assertIn('固定前缀', self.comparison(d)['pairs'][0]['reason'])

    def test_no_reusing_observation_to_overweight_pair(self):
        d = self.fixed_pair();d['pairs'].append({'id':'again', 'before_observation_id':'before'})
        with self.assertRaisesRegex(ValidationError, 'observation slot'):self.comparison(d)

    def test_uncertainty_full_shared_cohort_is_qualified(self):
        r = self.comparison(self.uncertainty())
        self.assertTrue(r['comparison_qualified'], r['qualification_reasons'])
        self.assertEqual(r['common_count'], 4)
        self.assertTrue(all(x['auroc']['value']==1 for x in r['estimators']))
        self.assertTrue(all(x['calibration']['brier'] is None for x in r['estimators']))

    def test_missing_score_uses_same_common_cohort_for_all(self):
        r = self.comparison(self.uncertainty(second_values=[.2, None, .3, .6]))
        self.assertEqual(r['common_count'], 3);self.assertEqual(r['excluded_count'], 1)
        self.assertEqual([x['common_count'] for x in r['estimators']], [3, 3])
        self.assertFalse(r['comparison_qualified'])

    def test_score_cannot_override_formal_source_value(self):
        d = self.uncertainty();d['estimators'][0]['scores'][0]['value']=.999
        r = self.comparison(d);self.assertEqual(r['common_count'], 3)
        self.assertIn('score_evidence_mismatch', r['exclusion_reasons'])

    def test_sampling_budget_mismatch_leaves_descriptive_metrics_only(self):
        d = self.uncertainty();d['estimators'][1]['probe_protocol']['sampling_budget']['num_samples']=8
        r = self.comparison(d);self.assertEqual(r['estimators'][0]['auroc']['value'], 1)
        self.assertFalse(r['comparison_qualified']);self.assertFalse(r['measurement_budget']['matched'])

    def test_wrong_sample_fingerprint_is_not_equal_budget_evidence(self):
        d = self.uncertainty();d['estimators'][1]['probe_protocol']['sample_set_hash']='0'*64
        self.assertFalse(self.comparison(d)['comparison_qualified'])

    def test_missing_runtime_condition_is_unknown_not_off(self):
        r = self.comparison(self.uncertainty(context=None));self.assertFalse(r['comparison_qualified'])
        self.assertIn('评测时熵策略', '；'.join(r['qualification_reasons']))

    def test_wrong_evaluation_cannot_supply_uncertainty_labels(self):
        d = self.uncertainty();other = self.evaluation('other')
        d['trajectory_ids'][0]=other[0];d['estimators'][0]['scores'][0]['trajectory_id']=other[0]
        d['estimators'][1]['scores'][0]['trajectory_id']=other[0]
        with self.assertRaisesRegex(ValidationError, 'another evaluation'):self.comparison(d)

    def test_one_class_metrics_do_not_claim_error_discrimination(self):
        r = self.comparison(self.uncertainty(scores=[1]*4))
        self.assertIsNone(r['estimators'][0]['auroc']['value']);self.assertEqual(r['estimators'][0]['aurc']['value'], 0)
        self.assertFalse(r['comparison_qualified'])

    def calibration(self, *, mismatched_probability=False, overlap=False):
        data = self.uncertainty(probability=(lambda v:.5) if mismatched_probability else (lambda v:1/(1+math.exp(1-2*v))))
        if overlap:fit = data['evaluation_id']
        else:
            self.s.register_panel({'id':'fit-panel', 'name':'Synthetic calibration development', 'split':'dev', 'demo':True,
                'samples':[{'id':'fit'+str(i), 'dataset':'fixture', 'question':'Disjoint calibration '+str(i)+'?', 'answers':['yes']} for i in range(4)]})
            fit = 'fit-eval';self.evaluation(fit, panel_id='fit-panel', sample_prefix='fit')
        self.emit('calibration-proof', 'entropy_probe', {'probe_type':'calibration_mapping', 'context':'evaluation', 'value':0,
            'definition_id':'h', 'fit_evaluation_id':fit, 'kind':'logistic', 'frozen':True,
            'parameters':{'intercept':-1, 'coefficient':2}})
        data['estimators'][0]['calibration'] = {'kind':'logistic', 'frozen':True, 'fit_evaluation_id':fit,
                                               'fit_ref':{'object_id':'calibration-proof', 'pointer':''}}
        return self.comparison(data)['estimators'][0]['calibration']

    def test_ece_brier_require_disjoint_dev_and_reproducible_probability_mapping(self):
        r = self.calibration();self.assertEqual(r['status'], 'measured', r)
        self.assertIsNotNone(r['ece']);self.assertIsNotNone(r['brier'])

    def test_declared_mapping_cannot_certify_unrelated_probabilities(self):
        r = self.calibration(mismatched_probability=True)
        self.assertIsNone(r['brier']);self.assertIn('实际输出不一致', r['reason'])

    def test_fitting_on_evaluation_itself_is_not_independent_calibration(self):
        r = self.calibration(overlap=True)
        self.assertIsNone(r['ece']);self.assertIn('重叠', r['reason'])

    def test_cross_run_training_budget_and_update_controls(self):
        r = self.comparison(self.interventions(), run=None)
        self.assertEqual(r['status'], 'measured', r)
        self.assertEqual([e['value'] for e in r['effects']], [.5, .25])
        self.assertTrue(r['arms'][1]['update_evidence']['completed'])
        self.assertFalse(r['claims']['causal_training_credit'])

    def test_unfinished_update_is_not_training_effect(self):
        r = self.comparison(self.interventions(update_status='pending'), run=None)
        self.assertEqual(r['status'], 'incomparable');self.assertIsNone(r['effects'][0]['value'])
        self.assertIn('sampled use', r['effects'][0]['reason'])

    def test_different_training_data_does_not_isolate_entropy(self):
        def change(role, proof):
            if role=='entropy':proof['training_controls']['training_data_sha256']='1'*64
        r = self.comparison(self.interventions(proof_change=change), run=None)
        self.assertFalse(r['budget']['matched']);self.assertIsNone(r['effects'][1]['value'])

    def test_equal_steps_with_more_tokens_are_not_matched_budget(self):
        def change(role, proof):
            if role=='entropy':proof['budget']['generated_tokens']=200
        r = self.comparison(self.interventions(proof_change=change), run=None)
        self.assertFalse(r['budget']['matched']);self.assertIn('generated_tokens', r['effects'][1]['reason'])

    def test_same_budget_different_selected_count_is_incomparable(self):
        def change(role, proof):
            if role=='entropy':proof['selection']['selected_count']=4
        r = self.comparison(self.interventions(proof_change=change), run=None)
        self.assertFalse(r['budget']['selection_counts_matched'])

    def test_run_scoped_comparison_rejects_foreign_run(self):
        with self.assertRaisesRegex(ValidationError, 'another run|another experiment/run'):
            self.comparison(self.interventions())

    def test_runtime_strategy_same_weights_and_protocol_can_compare(self):
        data = self.interventions(mode='runtime');r = self.comparison(data)
        self.assertEqual(r['status'], 'measured', r)
        default_pair = _pair(self.r, 'runtime-ev-random', 'runtime-ev-entropy', 'harness')
        self.assertIn('没有登记 Harness 变化', default_pair['reasons'])

    def test_runtime_different_weights_cannot_isolate_strategy(self):
        data = self.interventions(mode='runtime', runtime_weight_change=True)
        with self.assertRaisesRegex(ValidationError, 'Only cross-run training'):
            self.comparison(data, run=None)


if __name__=='__main__':unittest.main()
