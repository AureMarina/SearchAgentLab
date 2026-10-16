"""Read-model regressions: bounded list work, unchanged evidence, fresh writes."""
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from helpers import seed_minimal, trajectory
from searchagent_lab.core import NotFoundError, ValidationError
from searchagent_lab.inspection.repository import digest
from searchagent_lab.methods.repository import MethodRepository
from searchagent_lab.methods.schema import SCHEMA
from searchagent_lab.read_cache import read_scope
from searchagent_lab.store import Store


@contextmanager
def forbid_raw_trajectory_reads(store):
    """Fail even if raw trace JSON is read and discarded before serialization."""
    def authorize(action, table, column, *_):
        if action == sqlite3.SQLITE_READ and table == 'trajectories' and column == 'data':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    store.conn.set_authorizer(authorize)
    try:
        yield
    finally:
        store.conn.set_authorizer(None)


class SummaryReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        seed_minimal(self.store, n=4, datasets=['HotpotQA', 'TriviaQA'])
        self.records = []
        for dataset in ('HotpotQA', 'TriviaQA'):
            for i in range(4):
                raw = trajectory(i, dataset=dataset, em=i % 2)
                raw['steps'][1]['tool_response']['text'] = 'ONLY_IN_RAW_TRACE_' + 'x' * 65536
                if i == 2:
                    raw['runtime_status'] = 'timeout'
                if i == 3:
                    raw['scores'] = {}
                tid = self.store.log_trajectory('eval0', raw)
                self.records.append(self.store.trajectory(tid))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_pagination_matches_original_evidence_without_reading_raw(self):
        expected = [self.store._summary(r) for r in sorted(self.records, key=lambda r: (r['dataset'], r['sample_id']))]
        with forbid_raw_trajectory_reads(self.store):
            first = self.store.trajectories('eval0', offset=0, limit=3)
            second = self.store.trajectories('eval0', offset=3, limit=5)
        self.assertEqual(first['items'] + second['items'], expected)
        self.assertEqual(first['total'], 8)
        self.assertEqual(first['counts'], {'correct': 2, 'incorrect': 2, 'error': 2, 'unscored': 2, 'all': 8})
        self.assertNotIn('steps', first['items'][0])

    def test_filtered_counts_include_other_outcomes_in_selected_dataset(self):
        with forbid_raw_trajectory_reads(self.store):
            data = self.store.trajectories('eval0', dataset='HotpotQA', outcome='error', query='Question')
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['counts']['all'], 4)
        self.assertEqual(data['items'][0]['sample_id'], 'q2')

    def test_search_uses_question_and_sample_id_only_and_escapes_wildcards(self):
        with forbid_raw_trajectory_reads(self.store):
            self.assertEqual(self.store.trajectories('eval0', query='q1')['total'], 2)
            for query in ('gold-1', 'ONLY_IN_RAW_TRACE_', 'q_', '%', '\\'):
                self.assertEqual(self.store.trajectories('eval0', query=query)['total'], 0, query)

    def test_detail_still_preserves_full_trace_and_gold(self):
        data = self.store.trajectory(self.records[0]['id'])
        self.assertEqual(data['gold_answers'], ['gold-0'])
        self.assertEqual(len(data['steps'][1]['tool_response']['text']), len('ONLY_IN_RAW_TRACE_') + 65536)

    def test_missing_evaluation_and_invalid_filters_keep_contract(self):
        with self.assertRaises(NotFoundError):
            self.store.trajectories('missing')
        for kwargs in ({'limit': 201}, {'limit': 0}, {'offset': -1}, {'outcome': 'failed'}):
            with self.assertRaises(ValidationError):
                self.store.trajectories('eval0', **kwargs)

    def test_external_committed_append_is_visible_on_next_read(self):
        self.assertEqual(self.store.trajectories('eval1')['total'], 0)
        with Store(self.tmp.name) as writer:
            tid = writer.log_trajectory('eval1', trajectory())
        data = self.store.trajectories('eval1')
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['id'], tid)


class CatalogReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        for project in ('a', 'b'):
            self.store.register_project({'id': project, 'name': project})
            for suffix in ('empty', 'used'):
                self.store.register_experiment({'id': project + '-' + suffix, 'project_id': project, 'name': suffix})
        for i in range(12):
            self.store.register_run({'id': 'run-' + str(i), 'name': 'Run ' + str(i), 'experiment_id': 'a-used', 'base_model': '4B' if i % 2 else '8B'})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_model_counts_and_empty_experiments_are_preserved(self):
        projects = {p['id']: p for p in self.store.projects()}
        self.assertEqual(projects['a']['models'], ['4B', '8B'])
        self.assertEqual(projects['a']['experiment_count'], 2)
        self.assertEqual(projects['a']['run_count'], 12)
        self.assertEqual(projects['b']['run_count'], 0)
        self.assertEqual([e['run_count'] for e in self.store.experiments('a')], [0, 12])
        self.assertEqual([e['run_count'] for e in self.store.experiments('a', '4B')], [6])
        self.assertEqual(self.store.experiments('a', 'absent-model'), [])

    def test_request_cache_is_copy_isolated_and_next_request_sees_import(self):
        with read_scope():
            runs = self.store.runs()
            runs[0]['name'] = 'Changed only in this caller'
            self.assertNotEqual(self.store.runs()[0]['name'], runs[0]['name'])
            self.assertEqual(self.store.projects()[0]['run_count'], 12)
        with Store(self.tmp.name) as writer:
            writer.register_run({'id': 'new-run', 'name': 'Imported', 'experiment_id': 'a-used'})
        with read_scope():
            self.assertEqual(len(self.store.runs()), 13)
            self.assertEqual(self.store.projects()[0]['run_count'], 13)

    def test_run_ownership_validation_is_preserved(self):
        with self.assertRaises(ValidationError):
            self.store.runs('b', 'a-used')
        with self.assertRaises(NotFoundError):
            self.store.experiments('missing')
        self.assertTrue(all(r['project_id'] == 'a' and r['experiment_id'] == 'a-used' for r in self.store.runs('a')))


class MethodIdentityReadTests(unittest.TestCase):
    def test_evaluation_fingerprint_does_not_expand_full_skill_bank(self):
        with tempfile.TemporaryDirectory() as tmp, Store(tmp) as store:
            seed_minimal(store)
            with MethodRepository(tmp, store) as repo:
                repo.import_bundle({'method_schema': SCHEMA, 'scope': {'run_id': 'run'}, 'demo': True, 'objects': [
                    {'id': 'bank', 'kind': 'skill_bank', 'data': {'general_skills': [{'skill_id': 's1', 'title': 'Test', 'content': 'x' * 262144}]}},
                    {'id': 'binding', 'kind': 'evaluation_binding', 'data': {'evaluation_id': 'eval0', 'bank_id': 'bank', 'binding_origin': 'retrospective', 'state_complete': True, 'retrieval_config': {'version': 'v1'}}},
                ]})
                bank = repo.object('bank')
                expected = digest({'bank_content_hash': bank['content_hash'], 'retrieval_config': {'version': 'v1'}, 'extensions': {}})
                original = repo.object
                def object_without_bank(oid):
                    if oid == 'bank':
                        raise AssertionError('Evaluation metadata must not decode all bank content')
                    return original(oid)
                with patch.object(repo, 'object', side_effect=object_without_bank):
                    state = repo.evaluation_state('eval0')
                self.assertEqual(state['status'], 'recorded')
                self.assertEqual(state['fingerprint'], expected)
                self.assertEqual(state['bank_id'], 'bank')
                self.assertEqual(repo.object('bank')['data'], bank['data'])


if __name__ == '__main__':
    unittest.main()
