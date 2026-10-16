"""Same-state recovery controls and collector roundtrip; synthetic only."""
import copy
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from searchagent_lab.core import ValidationError
from searchagent_lab.inspection.repository import digest
from searchagent_lab.server import LabServer
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub, gid
from searchagent_lab.team.package import init_source, export_run

PROTOCOL = json.loads((Path(__file__).resolve().parents[1] / 'examples/protocol_v2.synthetic.json').read_text())
CONTEXT = {'teacher_hints': False, 'peer_answers': False, 'future_observations': False,
           'groundtruth_visible': False, 'privileged_information': []}
VERIFIER = {'name': 'synthetic-verifier', 'version': 'fixture-v1'}


class OPDRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'local'
        self.rec = StudioRecorder(self.path); self.store = self.rec.store; self.methods = self.rec.methods
        self.store.register_project({'id': 'p', 'name': 'Synthetic'})
        self.store.register_experiment({'id': 'e', 'project_id': 'p', 'name': 'Synthetic OPD'})
        self.rec.repo.register_profile({'experiment_id': 'e', 'track': 'main'})
        self.store.register_run({'id': 'run', 'experiment_id': 'e', 'name': 'Synthetic OPD', 'demo': True,
                                 'base_model': 'fixture', 'model_revision': 'base-v1'})
        self.store.register_panel({'id': 'panel', 'name': 'Synthetic task panel', 'split': 'probe', 'demo': True,
            'samples': [{'id': 'q1', 'dataset': 'fixture', 'question': 'First?', 'answers': ['yes']},
                        {'id': 'q2', 'dataset': 'fixture', 'question': 'Second?', 'answers': ['yes']}]})
        for cid, step, char in [('cp0', 0, 'a'), ('cp1', 10, 'b')]:
            self.store.register_checkpoint({'id': cid, 'run_id': 'run', 'step': step, 'weights_sha256': char * 64})
        self.snapshot = {'history': [{'role': 'user', 'content': 'First?'}],
                         'environment': {'corpus_revision': 'fixture-v1'}, 'tool_state': {'search_calls': 1},
                         'remaining_budget': {'tool_calls': 3, 'tokens': 100}, 'harness': {'version': 'fixture-v1'}}
        self.failure_data = {'sample_id': 'q1', 'dataset': 'fixture', 'student_checkpoint_id': 'cp0',
            'failure_step': 2, 'attempt_budget': 2, 'eligible': True, 'original_advantage': 0,
            'state': {'status': 'captured', 'snapshot': self.snapshot, 'snapshot_hash': digest(self.snapshot)}}
        self.emit('failure', 'opd_failure', self.failure_data)

    def tearDown(self):
        self.rec.close(); self.tmp.cleanup()

    def emit(self, oid, kind, data, scope=None, demo=True):
        return self.methods.import_bundle({'method_schema': 'method-evidence-v1', 'objects': [
            {'id': oid, 'kind': kind, 'demo': demo, 'data': data}]}, scope=scope or {'run_id': 'run'})

    def attempt(self, role, *, suffix='', correct=True, index=1, failure_id='failure', mutate=None,
                status='completed', teacher_mode='continuation', raw_mutate=None):
        aid = role + (suffix or '-' + str(index)); trace_id = 'trace-' + aid
        start = {'snapshot_hash': digest(self.snapshot), 'state_alignment': 'restored',
                 'remaining_budget': copy.deepcopy(self.snapshot['remaining_budget']), 'decoding': {'temperature': 0}, 'seed': 42}
        start.update({k + '_hash': digest(self.snapshot[k]) for k in ('history', 'environment', 'tool_state', 'harness')})
        context = copy.deepcopy(CONTEXT)
        if role == 'teacher':context.update(future_observations=True, privileged_information=['future observation'])
        verification = {'status': 'passed' if correct else 'failed', 'method': 'scorer', 'verifier': VERIFIER}
        data = {'attempt_id': aid, 'attempt_index': index, 'status': status, 'start': start,
                'context': context, 'verification': verification, 'cost': {'input_tokens': 10, 'output_tokens': 5}}
        if role != 'teacher':data['checkpoint_id'] = 'cp0' if role == 'student_before' else 'cp1'
        else:data.update(failure_id=failure_id, teacher_id='fixture-teacher', teacher_revision='fixture-t-v1',
                         mode=teacher_mode, state_alignment='restored')
        if mutate:mutate(data)
        trace = {'demo': True, 'continuation_start': copy.deepcopy(data['start']), 'context': copy.deepcopy(data['context']),
                 'verification': copy.deepcopy(data['verification']), 'steps': [{'kind': 'model', 'output': '<answer>yes</answer>'}]}
        if raw_mutate:raw_mutate(trace)
        self.emit(trace_id, 'trace', trace)
        data.update(trace_ref={'object_id': trace_id, 'pointer': ''},
                    start_ref={'object_id': trace_id, 'pointer': '/continuation_start'},
                    verification_ref={'object_id': trace_id, 'pointer': '/verification'})
        if role == 'teacher':
            if teacher_mode == 'rescore':
                data['verification'] = {'status': 'not_run'}
                data.pop('verification_ref', None)
            self.emit(aid, 'opd_teacher', data)
            return {'role': role, 'teacher_record_id': aid}
        return {'role': role, **data}

    def comparison_data(self, attempts=(), *, k=1):
        return {'baseline_checkpoint_id': 'cp0', 'current_checkpoint_id': 'cp1', 'attempts_per_arm': k,
                'cohort': {'id': 'cohort-v1', 'usage': 'development', 'frozen': True,
                           'feedback_used': True, 'isolation_status': 'unverified'},
                'cases': [{'failure_id': 'failure', 'attempts': list(attempts)}]}

    def compare(self, data, oid='recovery'):
        self.emit(oid, 'opd_recovery', data)
        return self.methods.opd_recovery(oid)

    def complete(self):
        return [self.attempt('student_before', correct=False), self.attempt('teacher'), self.attempt('student_after')]

    def evaluate(self, eid, cp, scores, *, context=CONTEXT, changed=None):
        protocol = copy.deepcopy(PROTOCOL); protocol['harness_version'] = 'c' * 64
        if changed:changed(protocol)
        self.store.start_evaluation({'id': eid, 'checkpoint_id': cp, 'panel_id': 'panel', 'protocol': protocol, 'purpose': 'checkpoint_eval'})
        for n, score in enumerate(scores, 1):
            self.store.log_trajectory(eid, {'id': eid + '-q' + str(n), 'sample_id': 'q' + str(n), 'dataset': 'fixture',
                'scores': {'em': score}, 'steps': [], 'runtime_status': 'ok', 'termination_reason': 'final_answer'})
        self.store.finish_evaluation(eid)
        binding = {'evaluation_id': eid, 'binding_origin': 'recorded_execution', 'state_complete': True,
                   'independent': True, 'autonomous': True, 'skills_disabled': True,
                   'retrieval_config': {'mode': 'fixed', 'top_k': 2}}
        if context is not None:binding['opd_context'] = context
        self.emit('bind-' + eid, 'evaluation_binding', binding)

    def test_complete_recovery_separates_teacher_and_student(self):
        r = self.compare(self.comparison_data(self.complete()))
        self.assertEqual(r['status'], 'measured')
        self.assertEqual([m['value'] for m in r['metrics']], [0, 1, 1, 1])
        self.assertTrue(r['cases'][0]['arms']['teacher']['attempts'][0]['context']['future_observations'])
        self.assertFalse(r['claims']['causal_training_attribution'])
        self.assertFalse(r['claims']['final_generalization'])
        self.assertEqual(r['full_task']['status'], 'unmeasured')

    def test_missing_slots_remain_unknown_not_zero(self):
        r = self.compare(self.comparison_data([self.attempt('student_before', correct=False)], k=2))
        m = r['metrics'][0]
        self.assertIsNone(m['value']); self.assertEqual((m['known'], m['expected'], m['unknown']), (1, 2, 1))
        self.assertEqual(len(r['cases'][0]['arms']['student_after']['attempts']), 2)

    def test_rescore_never_becomes_teacher_continuation(self):
        r = self.compare(self.comparison_data([self.attempt('teacher', teacher_mode='rescore')]))
        self.assertEqual(r['cases'][0]['arms']['teacher']['attempts'][0]['status'], 'not_applicable')
        self.assertIsNone(r['metrics'][1]['value'])

    def test_wrong_state_retained_as_incomparable(self):
        attempts = [self.attempt('student_before', correct=False),
                    self.attempt('student_after', mutate=lambda d: d['start'].update(snapshot_hash='f' * 64))]
        r = self.compare(self.comparison_data(attempts))
        self.assertEqual(r['cases'][0]['arms']['student_after']['status'], 'incomparable')
        self.assertIsNone(r['metrics'][3]['value'])

    def test_budget_change_does_not_count_as_failure(self):
        a = self.attempt('student_after', mutate=lambda d: d['start']['remaining_budget'].update(tokens=999))
        r = self.compare(self.comparison_data([a]))['cases'][0]['arms']['student_after']
        self.assertEqual((r['successes'], r['failures'], r['unknown']), (0, 0, 1))
        self.assertIn('预算', r['attempts'][0]['reason'])

    def test_student_privilege_blocks_autonomous_recovery(self):
        a = self.attempt('student_after', mutate=lambda d: d['context'].update(teacher_hints=True))
        r = self.compare(self.comparison_data([a]))
        self.assertIsNone(r['metrics'][2]['value'])
        self.assertIn('撤去', r['cases'][0]['arms']['student_after']['attempts'][0]['reason'])

    def test_declared_start_must_match_same_trace_actual_start(self):
        a = self.attempt('student_after', raw_mutate=lambda t: t['continuation_start'].update(seed=7))
        r = self.compare(self.comparison_data([a]))
        self.assertIn('实际轨迹', r['cases'][0]['arms']['student_after']['attempts'][0]['reason'])

    def test_metadata_without_actual_action_not_execution(self):
        a = self.attempt('student_after', raw_mutate=lambda t: t.update(steps=[]))
        r = self.compare(self.comparison_data([a]))
        self.assertIsNone(r['metrics'][2]['value'])

    def test_pre_post_decoding_or_verifier_changes_block_delta(self):
        attempts = [self.attempt('student_before', correct=False),
                    self.attempt('student_after', mutate=lambda d: d['start'].update(decoding={'temperature': 1}))]
        r = self.compare(self.comparison_data(attempts))
        self.assertEqual(r['metrics'][2]['value'], 1)
        self.assertEqual(r['cases'][0]['student_delta']['status'], 'incomparable')

    def test_error_and_missing_teacher_version_stay_unknown(self):
        a = self.attempt('teacher', mutate=lambda d: d.pop('teacher_revision'))
        b = self.attempt('student_after', status='error')
        r = self.compare(self.comparison_data([a, b]))
        self.assertIsNone(r['metrics'][1]['value']); self.assertIsNone(r['metrics'][2]['value'])

    def test_reused_execution_and_duplicate_states_are_rejected(self):
        a = self.attempt('student_before', correct=False)
        b = {**a, 'role': 'student_after', 'attempt_id': 'new-id', 'checkpoint_id': 'cp1'}
        with self.assertRaises(ValidationError):self.compare(self.comparison_data([a, b]))
        d = self.comparison_data(); d['cases'] *= 2
        with self.assertRaises(ValidationError):self.compare(d)

    def test_unfrozen_cohort_does_not_get_complete_rate(self):
        d = self.comparison_data(self.complete()); d['cohort']['frozen'] = False
        r = self.compare(d)
        self.assertTrue(all(m['value'] is None for m in r['metrics']))

    def test_teacher_trace_alias_cannot_reuse_student_execution(self):
        before = self.attempt('student_before', correct=False)
        teacher = {k: copy.deepcopy(v) for k, v in before.items() if k not in ('role', 'checkpoint_id', 'attempt_id')}
        teacher.update(failure_id='failure', teacher_id='fixture', teacher_revision='v1', mode='continuation', state_alignment='restored')
        teacher['teacher_trace_ref'] = teacher.pop('trace_ref')
        self.emit('alias-teacher', 'opd_teacher', teacher)
        with self.assertRaises(ValidationError):
            self.compare(self.comparison_data([before, {'role': 'teacher', 'teacher_record_id': 'alias-teacher'}]))

    def test_grading_threshold_change_blocks_student_delta(self):
        before = self.attempt('student_before', correct=False, mutate=lambda d: d['verification'].update(threshold=.5))
        after = self.attempt('student_after', mutate=lambda d: d['verification'].update(threshold=.8))
        r = self.compare(self.comparison_data([before, after]))
        self.assertEqual(r['metrics'][2]['value'], 1)
        self.assertEqual(r['metrics'][3]['status'], 'incomparable')
        self.assertIn('评分条件', r['cases'][0]['student_delta']['reason'])

    def test_mixed_grading_controls_within_k_are_not_pooled(self):
        first = self.attempt('student_after', index=1, mutate=lambda d: d['verification'].update(threshold=.5))
        second = self.attempt('student_after', index=2, mutate=lambda d: d['verification'].update(threshold=.8))
        r = self.compare(self.comparison_data([first, second], k=2))
        self.assertIsNone(r['metrics'][2]['value'])
        self.assertEqual(r['cases'][0]['arms']['student_after']['status'], 'incomparable')

    def test_avg_at_k_is_not_any_success_at_k(self):
        attempts = [self.attempt('student_after', index=1, correct=True),
                    self.attempt('student_after', index=2, correct=False)]
        r = self.compare(self.comparison_data(attempts, k=2))
        arm = r['cases'][0]['arms']['student_after']
        self.assertEqual(arm['value'], .5); self.assertTrue(arm['any_success_at_k'])
        self.assertIn('avg@2', r['metrics'][2]['label'])

    def test_full_task_transitions_and_local_recovery_are_separate(self):
        self.evaluate('before', 'cp0', [0, 1]); self.evaluate('after', 'cp1', [1, 0])
        d = self.comparison_data(self.complete()); d['full_task'] = {'before_evaluation_id': 'before', 'after_evaluation_id': 'after'}
        r = self.compare(d)
        self.assertEqual(r['metrics'][3]['value'], 1)
        self.assertEqual(r['full_task']['macro_delta'], 0)
        self.assertEqual((r['full_task']['rescued'], r['full_task']['harmed']), (1, 1))
        self.assertEqual(r['full_task']['cases'][0]['group'], 'rescued')

    def test_full_task_requires_recorded_privilege_withdrawal(self):
        self.evaluate('before', 'cp0', [0, 1]); self.evaluate('after', 'cp1', [1, 1], context=None)
        d = self.comparison_data(); d['full_task'] = {'before_evaluation_id': 'before', 'after_evaluation_id': 'after'}
        r = self.compare(d)
        self.assertEqual(r['full_task']['status'], 'incomparable'); self.assertIsNone(r['full_task']['rescued'])

    def test_full_task_changed_search_budget_blocks_gain(self):
        self.evaluate('before', 'cp0', [0, 1]); self.evaluate('after', 'cp1', [1, 1], changed=lambda p: p['budget'].update(max_tool_calls=99))
        d = self.comparison_data(); d['full_task'] = {'before_evaluation_id': 'before', 'after_evaluation_id': 'after'}
        r = self.compare(d)
        self.assertIsNone(r['full_task']['macro_delta']); self.assertIn('预算', r['full_task']['reason'])

    def test_declared_heldout_never_automatically_certifies_generalization(self):
        d = self.comparison_data(); d['cohort'].update(usage='held_out', feedback_used=False, isolation_status='verified')
        r = self.compare(d)
        self.assertFalse(r['cohort']['final_generalization'])

    def test_wrong_run_checkpoint_and_evidence_rejected(self):
        self.store.register_run({'id': 'other', 'experiment_id': 'e', 'name': 'Other', 'demo': True})
        self.store.register_checkpoint({'id': 'other-cp', 'run_id': 'other', 'step': 0})
        d = self.comparison_data(); d['current_checkpoint_id'] = 'other-cp'
        with self.assertRaises(ValidationError):self.compare(d)
        a = self.attempt('student_after')
        raw = self.methods.object(a['trace_ref']['object_id'])['data']
        self.emit('foreign-trace', 'trace', raw, scope={'run_id': 'other'})
        a['trace_ref'] = {'object_id': 'foreign-trace', 'pointer': ''}
        with self.assertRaises(ValidationError):self.compare(self.comparison_data([a]))

    def test_collector_preserves_nested_refs_hashes_and_comparison(self):
        self.evaluate('before', 'cp0', [0, 1]); self.evaluate('after', 'cp1', [1, 0])
        d = self.comparison_data(self.complete()); d['full_task'] = {'before_evaluation_id': 'before', 'after_evaluation_id': 'after'}
        original = self.compare(d)
        self.emit('diagnostic', 'opd_diagnostic', {'diagnostic_type': 'step', 'context': 'evaluation',
            'student_checkpoint_id': 'cp1', 'evaluation_id': 'after', 'trajectory_id': 'after-q1', 'traj_uid': 'external:q1',
            'step': 0, 'source_format': 'fixture', 'source_version': '1', 'observation': {}})
        source = init_source(self.path, 'synthetic', 'test')
        package = export_run(self.path, 'run')
        with Hub(Path(self.tmp.name) / 'center') as hub:
            result = hub.ingest(package['path'], allow_definitions=True)
            recovery = hub.methods.opd_recovery(gid(source['workspace_id'], 'method', 'recovery'))
            self.assertEqual([m['value'] for m in recovery['metrics']], [m['value'] for m in original['metrics']])
            self.assertEqual(recovery['full_task']['macro_delta'], original['full_task']['macro_delta'])
            self.assertEqual(recovery['scope']['run_id'], result['run_id'])
            failure = hub.methods.object(gid(source['workspace_id'], 'method', 'failure'))
            self.assertEqual(failure['data']['state']['snapshot_hash'], digest(self.snapshot))
            diagnostic = hub.methods.object(gid(source['workspace_id'], 'method', 'diagnostic'))['data']
            self.assertEqual(diagnostic['trajectory_id'], gid(source['workspace_id'], 'trajectory', 'after-q1'))
            self.assertEqual(diagnostic['student_checkpoint_id'], gid(source['workspace_id'], 'checkpoint', 'cp1'))
            self.assertEqual(diagnostic['traj_uid'], 'external:q1')

    def test_http_recovery_reads_saved_evidence(self):
        self.compare(self.comparison_data(self.complete()))
        server = LabServer(('127.0.0.1', 0), self.store)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with urllib.request.urlopen('http://127.0.0.1:' + str(server.server_port) + '/api/methods/opd-recovery?id=recovery') as response:
                data = json.load(response)
            self.assertEqual(data['status'], 'measured')
            self.assertEqual(data['metrics'][2]['value'], 1)
        finally:server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__':unittest.main()
