"""Counterexamples for self-play denominators, identity and frozen references.

All fixtures are synthetic CPU-only evidence. No models or training are run.
"""
from __future__ import annotations

import copy
import tempfile
import unittest

from searchagent_lab.core import ValidationError
from searchagent_lab.inspection.repository import digest
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.methods.schema import validate_object
from searchagent_lab.methods.selfplay_tracking import signal_state, selfplay_projection


class SelfplayTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='selfplay-semantics-v098-')
        self.rec = MethodRecorder(self.tmp.name);self.r = self.rec.repo;self.s = self.rec.store
        self.s.register_project({'id':'p', 'name':'Synthetic self-play'})
        for eid in ('e', 'elsewhere'):self.s.register_experiment({'id':eid, 'project_id':'p', 'name':eid})
        for rid, eid in (('a', 'e'), ('b', 'e'), ('z', 'elsewhere')):
            self.s.register_run({'id':rid, 'experiment_id':eid, 'name':rid, 'demo':True})
            for step in (0, 1, 2):
                self.s.register_checkpoint({'id':rid+'-cp'+str(step), 'run_id':rid, 'step':step, 'weights_sha256':digest('weights-'+str(step))})

    def tearDown(self):self.rec.close();self.tmp.cleanup()

    def obj(self, oid, kind, **data):return {'id':oid, 'kind':kind, 'demo':True, 'data':data}

    def put(self, *rows, run='a'):
        return self.r.import_bundle({'method_schema':'method-evidence-v1', 'objects':list(rows)}, scope={'run_id':run})

    def cycle(self, cid, index=1, complete=True):
        return self.obj(cid, 'selfplay_cycle', round_index=index,
            participants=[{'id':'s', 'role':'solver', 'revision':'fixture-solver'}, {'id':'p', 'role':'proposer'}],
            coverage={key:complete for key in ('generated_questions_complete', 'reviews_complete', 'consumption_complete', 'updates_complete', 'signals_complete')})

    def question(self, qid='q', cycle='c1', uid=None, **kw):
        data = {'question':'[Synthetic] What is the capital?', 'proposed_answer':'A',
                'stable_question_id':uid or qid, 'first_generated_cycle_id':cycle, 'deduplication':{'status':'unique'}}
        data.update(kw);return self.obj(qid, 'generated_question', **data)

    def review(self, rid, qid='q', status='passed', **kw):
        data = {'question_id':qid, 'status':'accepted' if status=='passed' else 'rejected' if status=='failed' else 'needs_review',
                'verification':{'status':status, 'verifier':{'id':'fixture-verifier', 'version':'1'}},
                'checks':{'format':{'status':'passed', 'verifier':'format-1'}, 'answerability':{'status':status, 'verifier':'answer-1'}}}
        data.update(kw);return self.obj(rid, 'question_review', **data)

    def consumption(self, uid='u', qid='q', cycle='c1', advantage=1, mask=None, role='s', **extra):
        attempt = self.obj(uid+'-attempt', 'solver_attempt', question_id=qid, cycle_id=cycle, actor_id=role,
            context='training', status='completed', groundtruth_visible=False, scores={'em':0})
        signal = {'objective':'policy_gradient', 'effective_advantage':advantage, 'loss_mask':[1, 1] if mask is None else mask, 'loss_weight':1.0}
        data = {'artifact_ids':[qid], 'attempt_id':attempt['id'], 'rollout_id':uid+'-rollout', 'cycle_id':cycle,
                'batch_id':'batch-'+cycle, 'status':'sampled', 'task_signal':signal, 'selfplay_rollouts_complete':True}
        data.update(extra)
        use = self.obj(uid, 'training_use', **data)
        update = self.obj(uid+'-update', 'training_update', cycle_id=cycle, status='completed', consumption_ids=[uid],
            input_checkpoint_id='a-cp0', output_checkpoint_id='a-cp1')
        return attempt, use, update

    def data(self, cycle='c1', **kw):return self.r.selfplay_training('e', 'a', cycle_id=cycle, **kw)

    def base(self):self.put(self.cycle('c0', 0), self.cycle('c1', 1), self.question(), self.review('review'))

    def reference(self, rid='ref', **kw):
        data = {'solver_revision':'fixture-reference', 'solver_weights_sha256':digest('solver'), 'harness_sha256':digest('harness'),
                'sampling':{'temperature':0.7, 'top_p':1.0, 'max_output_tokens':128},
                'environment':{'retrieval_corpus_sha256':digest('corpus'), 'search_budget':3, 'max_context_tokens':1024},
                'scoring':{'protocol_sha256':digest('protocol'), 'scorer':{'id':'em', 'version':'1'}},
                'attempts_per_question':2, 'verifier':{'id':'em', 'version':'1'}, 'seed_policy':{'base':42}}
        data.update(kw);return self.obj(rid, 'selfplay_reference', **data)

    def probe(self, oid, reference, index, em=0, **extra):
        d = reference['data'];execution_keys = ('solver_weights_sha256', 'harness_sha256', 'sampling', 'environment', 'scoring', 'verifier', 'seed_policy')
        data = {'cycle_id':'c1', 'question_id':'q', 'actor_id':'s', 'context':'reference', 'reference_id':reference['id'],
                'solver_revision':d['solver_revision'], 'attempt_index':index, 'status':'completed', 'groundtruth_visible':False,
                'scores':{'em':em}, 'reference_configuration_hash':digest(d),
                'execution_config':{k:d[k] for k in execution_keys if k in d}}
        data.update(extra);return self.obj(oid, 'solver_attempt', **data)

    def test_empty_run_is_unmeasured_not_zero(self):
        data = self.r.selfplay_training('e', 'a')
        self.assertTrue(all(m['value'] is None for m in data['metrics']))
        self.assertEqual(data['questions']['total'], 0)

    def test_default_selects_explicit_latest_round(self):
        self.put(self.cycle('late', 20), self.cycle('early', 1))
        self.assertEqual(self.r.selfplay_training('e', 'a')['selected_cycle_id'], 'late')

    def test_zero_advantage_used_question_is_supply(self):
        self.base();self.put(*self.consumption(advantage=0))
        data = self.data()
        self.assertEqual(data['counts']['new_verified_consumed_questions'], 1)
        self.assertEqual(data['task_signal']['value'], 0)

    def test_selected_only_does_not_count_as_training(self):
        self.base();self.put(self.obj('selected', 'training_use', artifact_ids=['q'], cycle_id='c1', status='selected'))
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 0)

    def test_sampled_without_completed_update_does_not_count(self):
        self.base();attempt, use, update = self.consumption();self.put(attempt, use)
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 0)
        self.assertEqual(self.data()['task_signal']['total'], 0)

    def test_multiple_rollouts_count_one_new_question(self):
        self.base();self.put(*self.consumption('u1'), *self.consumption('u2'))
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 1)
        self.assertEqual(self.data()['task_signal']['total'], 2)

    def test_same_rollout_same_batch_duplicate_logs_deduplicate(self):
        self.base();self.put(*self.consumption())
        use = self.r.object('u');use['id'] = 'u-copy'
        use = {k:v for k,v in use.items() if k not in ('hash', 'content_hash', 'imported_at')}
        self.put(use, self.obj('update-copy', 'training_update', cycle_id='c1', status='completed', consumption_ids=['u-copy']))
        self.assertEqual(self.data()['task_signal']['total'], 1)

    def test_unknown_signal_remains_in_total_denominator(self):
        self.base();self.put(*self.consumption('good'), *self.consumption('unknown', advantage=None))
        signal = self.data()['task_signal']
        self.assertEqual((signal['active'], signal['unknown'], signal['total']), (1, 1, 2))
        self.assertIsNone(signal['value']);self.assertEqual(signal['lower_bound'], 0.5);self.assertEqual(signal['upper_bound'], 1)

    def test_nonzero_advantage_on_masked_position_is_inactive(self):
        self.base();self.put(*self.consumption(task_signal={'objective':'policy_gradient', 'effective_advantages':[0, 3], 'loss_mask':[1, 0], 'loss_weight':1}))
        self.assertEqual(self.data()['task_signal']['value'], 0)

    def test_token_advantages_without_aligned_mask_are_unknown(self):
        flag, _ = signal_state({'objective':'policy_gradient', 'effective_advantages':[0, 3], 'loss_mask_tokens':1, 'loss_weight':1})
        self.assertIsNone(flag)

    def test_loss_active_cannot_override_disjoint_mask(self):
        row = self.obj('bad', 'training_use', artifact_ids=['q'], status='sampled', batch_id='b', task_signal={
            'objective':'policy_gradient', 'effective_advantages':[0, 3], 'loss_mask':[1, 0], 'loss_weight':1, 'loss_active':True})
        with self.assertRaises(ValidationError):validate_object(row)

    def test_task_and_opd_signal_objectives_cannot_be_swapped(self):
        for field, signal in (('task_signal', {'objective':'distillation', 'loss_mask':[1], 'loss_weight':1}),
                              ('opd_signal', {'objective':'policy_gradient', 'effective_advantage':1, 'loss_mask':[1], 'loss_weight':1})):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate_object(self.obj('bad', 'training_use', artifact_ids=['q'], status='sampled', batch_id='b', **{field:signal}))

    def test_opd_can_be_active_when_task_signal_is_zero(self):
        self.base();self.put(*self.consumption(advantage=0, opd_signal={'objective':'distillation', 'loss_mask':[1], 'loss_weight':0.2}))
        self.assertEqual(self.data()['task_signal']['value'], 0)
        self.assertEqual(self.data()['opd_signal']['value'], 1)

    def test_batch_record_without_rollout_identity_is_unknown(self):
        self.base()
        self.put(self.obj('batch', 'training_use', artifact_ids=['q'], cycle_id='c1', status='sampled', batch_id='b', actor_role='solver',
            task_signal={'objective':'policy_gradient', 'effective_advantage':1, 'loss_mask':[1], 'loss_weight':1}),
            self.obj('done', 'training_update', consumption_ids=['batch'], cycle_id='c1', status='completed'))
        signal = self.data()['task_signal'];self.assertIsNone(signal['value'])
        self.assertEqual((signal['unknown'], signal['missing_rollout_identity']), (1, 1))

    def test_incomplete_consumption_log_does_not_certify_full_coverage(self):
        self.put(self.cycle('c1', complete=False), self.question(), self.review('r'), *self.consumption())
        self.assertIsNone(self.data()['task_signal']['value']);self.assertFalse(self.data()['task_signal']['denominator_complete'])

    def test_proposer_update_is_not_solver_supply_or_signal(self):
        self.base();self.put(*self.consumption(role='p'))
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 0)
        self.assertEqual(self.data()['task_signal']['total'], 0)

    def test_unknown_actor_role_stays_unknown(self):
        self.put(self.obj('c1', 'selfplay_cycle', participants=[{'id':'s'}], round_index=1), self.question(), self.review('r'), *self.consumption())
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 0)
        self.assertEqual(self.data()['task_signal']['unknown'], 1)

    def test_old_pool_first_consumption_is_distinct_from_reuse(self):
        self.put(self.cycle('c0', 0), self.cycle('c1', 1), self.cycle('c2', 2), self.question(cycle='c0'), self.review('r'), *self.consumption())
        self.assertEqual(self.data()['counts']['old_first_consumed_questions'], 1)
        self.assertEqual(self.data()['counts']['reused_questions'], 0)
        self.put(*self.consumption('again', cycle='c2'))
        self.assertEqual(self.data('c2')['counts']['reused_questions'], 1)
        self.assertEqual(self.data('c2')['counts']['old_first_consumed_questions'], 0)

    def test_validation_error_failed_unknown_are_separate(self):
        self.put(self.cycle('c1'))
        for suffix, status in (('pass', 'passed'), ('fail', 'failed'), ('error', 'error'), ('unknown', 'not_run')):
            self.put(self.question(suffix), self.review('r-'+suffix, suffix, status))
        counts = self.data()['counts']
        self.assertEqual(counts['generated_questions'], 4)
        self.assertEqual([counts['validation_'+s] for s in ('passed', 'failed', 'error', 'unknown')], [1, 1, 1, 1])

    def test_rejected_question_remains_in_generation_denominator(self):
        self.put(self.cycle('c1'), self.question(), self.review('r', status='failed'))
        self.assertEqual(self.data()['counts']['generated_questions'], 1)
        self.assertEqual(self.data()['questions']['total'], 1)

    def test_superseded_error_is_preserved_in_question_detail(self):
        self.put(self.cycle('c1'), self.question(), self.review('r-error', status='error'), self.review('r-pass', supersedes='r-error'))
        detail = self.r.selfplay_question('q', 'c1')
        self.assertEqual(detail['assessment']['validation_status'], 'passed');self.assertEqual(len(detail['reviews']), 2)
        self.assertEqual(self.data()['counts']['validation_error_events'], 1)

    def test_conflicting_unsuperseded_reviews_cannot_pass(self):
        self.base();self.put(self.review('reject', status='failed'), *self.consumption())
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 0)
        self.assertEqual(self.r.selfplay_question('q')['assessment']['validation_status'], 'conflicting')

    def test_proposed_answer_is_part_of_question_content_identity(self):
        a = selfplay_projection(self.question(proposed_answer='A'))
        b = selfplay_projection(self.question(proposed_answer='B'))
        self.assertNotEqual(a['question_content_sha256'], b['question_content_sha256'])

    def test_rejected_duplicate_cannot_make_source_new_used(self):
        self.base();self.put(self.question('dup', uid='q', duplicate_of='q', proposed_answer='B', deduplication={'status':'duplicate'}),
            self.review('dup-reject', 'dup', 'failed'), *self.consumption('duse', 'dup'))
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 0)
        detail = self.r.selfplay_question('dup', 'c1')
        self.assertFalse(detail['assessment']['new_verified_consumed'])

    def test_identical_aliases_are_counted_once(self):
        self.base();self.put(self.question('copy', uid='q'), *self.consumption('alias-use', 'copy'))
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 1)

    def test_changed_content_same_stable_id_is_not_auto_verified(self):
        self.base();self.put(self.question('copy', uid='q', proposed_answer='B'), *self.consumption('alias-use', 'copy'))
        self.assertEqual(self.data()['counts']['new_verified_consumed_questions'], 0)

    def test_scoped_reads_do_not_union_sibling_run_questions(self):
        self.base();self.put(self.cycle('other-c'), self.question('other-q', 'other-c'), run='b')
        self.assertEqual(self.data()['questions']['total'], 1)
        self.assertEqual(self.r.selfplay_training('e', '')['questions']['total'], 0)

    def test_foreign_cycle_selection_rejected(self):
        self.base();self.put(self.cycle('other-c'), run='b')
        with self.assertRaises(ValidationError):self.data('other-c')
        with self.assertRaises(ValidationError):self.r.selfplay_question('q', 'other-c')

    def test_typed_validation_and_pool_cannot_link_other_run(self):
        self.base();self.put(self.cycle('other-c'), run='b')
        with self.assertRaises(ValidationError):self.put(self.review('foreign'), run='b')
        with self.assertRaises(ValidationError):self.put(self.obj('pool', 'selfplay_pool_event', question_id='q', cycle_id='other-c', pool_id='pool', status='admitted', occurred_at='2026-01-01'), run='b')

    def test_reference_can_arrive_with_question_in_one_atomic_bundle(self):
        ref = self.reference()
        self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 1, 0), self.probe('p2', ref, 2, 1))
        self.assertEqual(self.data()['difficulty']['groups']['partial_success'], 1)

    def test_fixed_reference_all_k_failures_are_not_errors(self):
        ref = self.reference();self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 1), self.probe('p2', ref, 2))
        self.assertEqual(self.data()['difficulty']['groups']['all_failed'], 1)

    def test_reference_error_keeps_group_incomplete(self):
        ref = self.reference();self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 1),
            self.probe('p2', ref, 2, status='error', scores={'em':None}))
        data = self.data()['difficulty'];self.assertEqual(data['groups']['all_failed'], 0);self.assertEqual(data['groups']['incomplete'], 1)

    def test_reference_execution_config_mismatch_not_comparable(self):
        ref = self.reference();changed = self.probe('p2', ref, 2, 1);changed['data']['execution_config']['harness_sha256'] = digest('changed')
        self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 1), changed)
        self.assertEqual(self.data()['difficulty']['groups']['incomplete'], 1)

    def test_reference_without_weight_fingerprint_is_unqualified(self):
        ref = self.reference();ref['data'].pop('solver_weights_sha256')
        self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 1), self.probe('p2', ref, 2))
        self.assertEqual(self.data()['difficulty']['groups']['incomplete'], 1)

    def test_reference_duplicate_index_rejected_atomically(self):
        ref = self.reference()
        with self.assertRaises(ValidationError):self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 1), self.probe('p2', ref, 1))
        self.assertEqual(self.r.catalog({'run_id':'a'})['total'], 0)

    def test_reference_attempt_beyond_k_rejected(self):
        ref = self.reference()
        with self.assertRaises(ValidationError):self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 3))

    def test_reference_probe_cannot_be_solver_training_rollout(self):
        ref = self.reference();self.put(self.cycle('c1'), self.question(), ref, self.probe('p1', ref, 1))
        with self.assertRaises(ValidationError):self.put(self.obj('misuse', 'training_use', artifact_ids=['q'], attempt_id='p1', cycle_id='c1', status='sampled', batch_id='b'))

    def test_legacy_zero_based_training_attempt_is_preserved(self):
        self.put(self.cycle('c1'), self.question(), self.obj('old-attempt', 'solver_attempt', cycle_id='c1', question_id='q', actor_id='s', attempt_index=0))
        self.assertEqual(self.r.object('old-attempt')['data']['attempt_index'], 0)

    def test_question_detail_includes_actual_proposer_input(self):
        self.base();self.put(self.obj('proposal', 'proposal', cycle_id='c1', question_id='q', actor_id='p', raw_input={'seed':42}, raw_output={'question':'Synthetic?'}))
        detail = self.r.selfplay_question('q', 'c1')
        self.assertEqual(detail['proposals'][0]['data']['raw_input'], {'seed':42})


if __name__=='__main__':unittest.main()
