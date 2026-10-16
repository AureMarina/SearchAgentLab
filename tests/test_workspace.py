"""Workspace taxonomy, aggregate semantics, and guarded global references."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from unittest.mock import patch

from searchagent_lab.core import ValidationError
from searchagent_lab.demo import PROTOCOL
from searchagent_lab.server import LabServer
from searchagent_lab.store import Store
from searchagent_lab.studio.repository import StudioRepository


BASE_EXP = 'native-qwen3-8b-sevenqa-20261009'
SDAR_EXP = 'sdar-searchqa-4b-reproduction'
HASHES = {'dataset_manifest_sha256': 'a' * 64, 'protocol_sha256': 'b' * 64,
          'scorer_sha256': 'c' * 64, 'base_model_revision': 'base-revision-1'}


class WorkspaceFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(self.tmp.name)
        self.r = StudioRepository(self.s)
        self.add_experiment(BASE_EXP, 'native-qwen-searchqa', 'main')
        self.add_experiment(SDAR_EXP, 'distillation', 'reproduction')
        self.add_run('native8', BASE_EXP, 'Qwen3-8B')
        self.add_run('sdar4', SDAR_EXP, 'Qwen3-4B-Instruct-2507')
        self.s.log_metrics('native8', 0, {
            'eval/nq/em_pct': 36.796759738303884, 'eval/nq/expected': 3610,
            'eval/popqa/em_pct': 37.0, 'eval/popqa/accuracy_pct': 44.0,
            'eval/popqa/expected': 14267, 'eval/2wiki/em_pct': 35.0,
            'eval/2wiki/expected': 12576, 'eval/macro/em_pct': 36.0,
            'eval/macro/accuracy_pct': 38.0})

    def tearDown(self):
        self.r.close()
        self.s.close()
        self.tmp.cleanup()

    def add_experiment(self, eid, pid='research', track='main'):
        self.s.register_project({'id': pid, 'name': pid})
        self.s.register_experiment({'id': eid, 'project_id': pid, 'name': eid})
        if track:
            profile = {'experiment_id': eid, 'track': track}
            if track == 'reproduction':
                profile['reproduction'] = {'method_name': 'SDAR'}
            self.r.register_profile(profile)

    def add_run(self, rid, eid, model, evidence=None, created_at='2026-10-09T00:00:00+00:00'):
        self.s.register_run({'id': rid, 'name': rid, 'experiment_id': eid,
            'base_model': model, 'config': {'aggregate_comparison': evidence} if evidence else {},
            'seed': 42, 'demo': True, 'created_at': created_at})

    def add_diagnostic(self, run='sdar4', full_validation=None):
        self.s.register_panel({'id': 'diagnostic', 'name': 'Two diagnostic questions',
            'version': 'v1', 'split': 'probe', 'samples': [
                {'id': 'q1', 'dataset': 'NQ', 'question': 'One?', 'answers': ['yes']},
                {'id': 'q2', 'dataset': 'NQ', 'question': 'Two?', 'answers': ['no']}]})
        self.s.register_checkpoint({'id': run + '-cp', 'run_id': run, 'step': 150})
        evaluation = {'id': run + '-eval', 'checkpoint_id': run + '-cp',
                      'panel_id': 'diagnostic', 'protocol': deepcopy(PROTOCOL)}
        if full_validation:
            evaluation['full_validation'] = full_validation
        self.s.start_evaluation(evaluation)
        self.s.log_trajectory(run + '-eval', {'dataset': 'NQ', 'sample_id': 'q1',
            'scores': {'em': 1}, 'parsed_answer': 'yes', 'raw_final_output': 'LARGE_RAW_SENTINEL',
            'steps': [{'index': 0, 'kind': 'model', 'output': 'LARGE_RAW_SENTINEL'}]})

    def add_matching_baseline(self, evidence=None, model='Qwen3-4B-Instruct-2507'):
        self.add_experiment('base4')
        self.add_run('native4', 'base4', model, evidence)
        self.r.register_presentation({'experiment_id': 'base4', 'family': 'baseline', 'is_baseline': True})
        self.s.log_metrics('native4', 0, {'eval/nq/em_pct': 30.0, 'eval/nq/sample_count': 10,
                                          'eval/macro/em_pct': 30.0})


class WorkspaceTests(WorkspaceFixture, unittest.TestCase):
    def test_exact_audited_taxonomy_and_baseline_first(self):
        self.add_experiment('uniopsd-qwen3-4b-reproduction', 'uniopsd-searchqa', 'reproduction')
        self.add_experiment('skillrl-qwen3-4b-reproduction', 'skillrl-searchqa', 'reproduction')
        w = self.r.workspace()
        self.assertEqual(w['experiments'][0]['experiment']['id'], BASE_EXP)
        by_id = {e['experiment']['id']: e for e in w['experiments']}
        self.assertEqual(by_id[SDAR_EXP]['family'], 'self_distillation')
        self.assertEqual(by_id['uniopsd-qwen3-4b-reproduction']['family'], 'self_distillation')
        self.assertEqual(by_id['skillrl-qwen3-4b-reproduction']['family'], 'skill')
        self.assertEqual(w['counts']['main'], 0)
        self.assertEqual(w['counts']['reproduction'], 3)

    def test_unknown_name_does_not_classify_research(self):
        self.add_experiment('self_distillation_sdar_own', track=None)
        entry = next(e for e in self.r.workspace()['experiments'] if e['experiment']['id'] == 'self_distillation_sdar_own')
        self.assertEqual(entry['family'], 'other')
        self.assertEqual(entry['profile']['track'], 'unassigned')

    def test_same_baseline_experiment_keeps_each_model_run_and_score(self):
        self.add_run('native4-same-experiment', BASE_EXP, 'Qwen3-4B-Instruct-2507',
                     created_at='2026-10-10T00:00:00+00:00')
        self.s.log_metrics('native4-same-experiment', 0, {
            'eval/nq/em_pct': 29.0, 'eval/nq/sample_count': 3610, 'eval/macro/em_pct': 29.0})
        workspace = self.r.workspace()
        rows = {row['run']['id']: row for row in workspace['baseline_runs']}
        self.assertEqual(set(rows), {'native8', 'native4-same-experiment'})
        self.assertEqual(rows['native8']['run']['base_model'], 'Qwen3-8B')
        self.assertEqual(rows['native8']['aggregate']['macro_em_pct'], 36.0)
        self.assertEqual(rows['native4-same-experiment']['run']['base_model'], 'Qwen3-4B-Instruct-2507')
        self.assertEqual(rows['native4-same-experiment']['aggregate']['macro_em_pct'], 29.0)
        self.assertTrue(all(row['experiment']['id'] == BASE_EXP for row in rows.values()))
        self.assertEqual(len(workspace['baselines']), 1)
        self.assertEqual(len(workspace['experiments']), 2)

    def test_presentation_mutable_but_profile_ownership_immutable(self):
        before = self.r.profile(SDAR_EXP)
        raw = self.s._get('experiments', SDAR_EXP)
        self.r.register_presentation({'experiment_id': SDAR_EXP, 'family': 'entropy', 'is_baseline': False})
        self.r.register_presentation({'experiment_id': SDAR_EXP, 'family': 'self_distillation', 'is_baseline': False})
        self.assertEqual(before, self.r.profile(SDAR_EXP))
        self.assertEqual(raw, self.s._get('experiments', SDAR_EXP))
        changed = deepcopy(before)
        changed.update(track='main', reproduction={})
        with self.assertRaises(ValidationError):
            self.r.register_profile(changed)

    def test_invalid_presentation_rejected(self):
        for data in ({'family': 'unknown'}, {'family': 'baseline', 'is_baseline': False},
                     {'family': 'skill', 'is_baseline': 'yes'}):
            with self.assertRaises(ValidationError):
                self.r.register_presentation({'experiment_id': SDAR_EXP, **data})

    def test_metrics_preserve_float_accuracy_and_missing_f1(self):
        a = self.r.reference(run_id='native8')['current']
        self.assertEqual(a['datasets'][0]['em_pct'], 36.796759738303884)
        popqa = next(row for row in a['datasets'] if row['id'] == 'popqa')
        self.assertEqual(popqa['em_pct'], 37.0)
        self.assertEqual(popqa['accuracy_pct'], 44.0)
        self.assertIsNone(a['macro_f1_pct'])
        self.assertEqual(a['sample_count'], 3610 + 14267 + 12576)

    def test_absent_4b_baseline_never_invented_from_8b(self):
        self.s.log_metrics('sdar4', 150, {'eval/nq/em_pct': 50.0, 'eval/nq/sample_count': 10})
        r = self.r.reference(experiment_id=SDAR_EXP)
        self.assertIsNone(r['baseline'])
        self.assertEqual(r['available_reference']['run']['base_model'], 'Qwen3-8B')
        self.assertEqual(r['comparison']['status'], 'missing_baseline')
        self.assertTrue(all(row['delta_em_pp'] is None for row in r['rows']))

    def test_model_versions_are_not_collapsed(self):
        self.add_matching_baseline(model='Qwen3-4B-Instruct')
        self.assertEqual(self.r.reference(run_id='sdar4')['comparison']['status'], 'missing_baseline')

    def test_same_model_and_dataset_are_insufficient_for_delta(self):
        self.add_matching_baseline()
        self.s.log_metrics('sdar4', 150, {'eval/nq/em_pct': 50.0, 'eval/nq/sample_count': 10})
        r = self.r.reference(run_id='sdar4')
        self.assertEqual(r['baseline']['run']['id'], 'native4')
        self.assertEqual(r['comparison']['status'], 'reference_only')
        self.assertFalse(r['comparison']['compatible'])
        self.assertTrue(any('题单' in x for x in r['comparison']['reasons']))

    def test_explicit_matching_evidence_allows_descriptive_delta_only(self):
        self.add_matching_baseline(HASHES)
        self.add_run('controlled4', SDAR_EXP, 'Qwen3-4B-Instruct-2507', HASHES)
        self.s.log_metrics('controlled4', 150, {'eval/nq/em_pct': 50.0, 'eval/nq/sample_count': 10})
        r = self.r.reference(run_id='controlled4')
        self.assertEqual(r['comparison']['status'], 'comparable')
        self.assertEqual(r['rows'][0]['delta_em_pp'], 20.0)
        self.assertFalse(r['comparison']['included_in_main_iteration'])

    def test_mismatched_protocol_and_denominator_block_delta(self):
        self.add_matching_baseline(HASHES)
        changed = {**HASHES, 'protocol_sha256': 'd' * 64}
        self.add_run('mismatch4', SDAR_EXP, 'Qwen3-4B-Instruct-2507', changed)
        self.s.log_metrics('mismatch4', 150, {'eval/nq/em_pct': 50.0, 'eval/nq/sample_count': 11})
        r = self.r.reference(run_id='mismatch4')
        self.assertEqual(r['comparison']['status'], 'reference_only')
        self.assertTrue(any('协议不一致' in x for x in r['comparison']['reasons']))
        self.assertIsNone(r['rows'][0]['delta_em_pp'])

    def test_dataset_alias_aligns_columns_only(self):
        self.s.log_metrics('sdar4', 150, {'eval/2wikimultihopqa/em_pct': 42.0,
            'eval/2wikimultihopqa/sample_count': 12576})
        r = self.r.reference(run_id='sdar4')
        rows = [row for row in r['rows'] if row['id'] == '2wiki']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['current']['source_dataset'], '2wikimultihopqa')
        self.assertEqual(rows[0]['reference']['source_dataset'], '2wiki')
        self.assertIsNone(rows[0]['delta_em_pp'])

    def test_logged_batch_mean_never_reported_as_exact_em(self):
        self.add_diagnostic(full_validation={'kind': 'aggregate_from_training_validation_log',
            'question_count': 51713, 'step': 150, 'dataset_macro_mean_em': 0.46,
            'overall_logged_batch_mean_em': 0.47,
            'datasets': [{'name': 'NQ', 'question_count': 3610, 'em': 0.445, 'mean_reward': 0.41}]})
        r = self.r.reference(run_id='sdar4')
        self.assertEqual(r['current']['kind'], 'training_log_batch_mean')
        self.assertIsNone(r['current']['macro_em_pct'])
        self.assertIsNone(r['current']['datasets'][0]['em_pct'])
        self.assertEqual(r['current']['datasets'][0]['logged_success_pct'], 44.5)
        self.assertEqual(r['current']['sample_count'], 51713)
        self.assertEqual(r['diagnostics']['trajectory_count'], 1)
        self.assertEqual(r['diagnostics']['latest']['panel_size'], 2)
        self.assertIsNone(r['diagnostics']['latest']['em_pct'])

    def test_latest_aggregate_survives_newer_training_only_metric(self):
        self.s.log_metrics('sdar4', 100, {'eval/nq/em_pct': 50.0, 'eval/nq/sample_count': 10})
        self.s.log_metrics('sdar4', 150, {'actor/pg_loss': -0.1, 'eval/nq/sample_count': 12})
        r = self.r.reference(run_id='sdar4')
        self.assertEqual(r['current']['step'], 100)
        self.assertEqual(r['run']['latest_step'], 150)

    def test_latest_run_not_best_score_is_default(self):
        self.s.log_metrics('sdar4', 150, {'eval/macro/em_pct': 70.0})
        self.add_run('sdar4-new', SDAR_EXP, 'Qwen3-4B-Instruct-2507', created_at='2026-10-10T00:00:00+00:00')
        self.s.log_metrics('sdar4-new', 150, {'eval/macro/em_pct': 20.0})
        r = self.r.reference(experiment_id=SDAR_EXP)
        self.assertEqual(r['run']['id'], 'sdar4-new')
        self.assertEqual(r['current']['macro_em_pct'], 20.0)

    def test_reference_scope_and_baseline_registration_enforced(self):
        with self.assertRaises(ValidationError):
            self.r.reference(experiment_id=SDAR_EXP, run_id='native8')
        with self.assertRaises(ValidationError):
            self.r.reference(run_id='native8', baseline_run='sdar4')

    def test_index_avoids_all_heavy_read_paths(self):
        self.add_diagnostic()
        with patch.object(self.s, 'metrics', side_effect=AssertionError('metric history')), \
             patch.object(self.s, 'evaluation', side_effect=AssertionError('full evaluation')), \
             patch.object(self.s, 'trajectory', side_effect=AssertionError('raw trajectory')):
            index = self.r.workspace()
            reference = self.r.reference(run_id='sdar4')
        self.assertNotIn('LARGE_RAW_SENTINEL', json.dumps(index))
        self.assertNotIn('LARGE_RAW_SENTINEL', json.dumps(reference))
        self.assertEqual(index['counts']['trajectories'], 1)

    def test_read_cache_does_not_hide_subsequent_write(self):
        self.r.workspace()
        self.s.log_metrics('sdar4', 150, {'eval/nq/em_pct': 22.0, 'eval/nq/sample_count': 10})
        self.assertEqual(self.r.reference(run_id='sdar4')['current']['datasets'][0]['em_pct'], 22.0)


class WorkspaceHTTPTests(WorkspaceFixture, unittest.TestCase):
    def test_http_endpoints_and_presentation_csrf(self):
        server = LabServer(('127.0.0.1', 0), self.s)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:' + str(server.server_address[1])
        try:
            with urllib.request.urlopen(base + '/api/studio/workspace') as response:
                self.assertEqual(json.load(response)['baselines'][0]['experiment']['id'], BASE_EXP)
            with urllib.request.urlopen(base + '/api/studio/reference?run=sdar4') as response:
                self.assertEqual(json.load(response)['comparison']['status'], 'missing_baseline')
            data = json.dumps({'experiment_id': SDAR_EXP, 'family': 'self_distillation', 'is_baseline': False}).encode()
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(urllib.request.Request(base + '/api/studio/presentation', data=data,
                    headers={'Content-Type': 'application/json'}))
            self.assertEqual(rejected.exception.code, 403)
            with urllib.request.urlopen(urllib.request.Request(base + '/api/studio/presentation', data=data,
                headers={'Content-Type': 'application/json', 'X-Local-Token': server.csrf_token})) as response:
                self.assertEqual(response.status, 201)
                self.assertEqual(json.load(response)['family'], 'self_distillation')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
