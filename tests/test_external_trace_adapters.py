"""Synthetic compatibility fixtures, NOT benchmark results or real agent runs.

Shapes follow the public sources listed in inspection/adapters.py. The fixtures
exercise provenance and ambiguous/missing evidence without either dependency.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest

from searchagent_lab.core import ValidationError
from searchagent_lab.inspection.adapters import normalize_record, resolve
from searchagent_lab.inspection.repository import InspectionRepository


def evalscope_fixture():
    return {
        'sample_id': 'synthetic-evalscope-1',
        'sample': {'input': 'Synthetic question', 'target': '42'},
        'messages': [
            {'id': 'm0', 'role': 'user', 'content': 'Synthetic question'},
            {'id': 'm1', 'role': 'assistant', 'content': 'Search.',
             'tool_calls': [{'id': 'c1', 'function': {'name': 'search', 'arguments': {'query': 'synthetic'}}}]},
            {'id': 'm2', 'role': 'tool', 'tool_call_id': 'c1', 'function': 'search', 'content': 'Recorded result 42'},
        ],
        'agent_trace': {
            'framework': 'native', 'strategy': 'function_calling', 'environment': 'docker',
            'events': [
                {'step': 0, 'type': 'model_generate', 'message_id': 'm1', 'latency_ms': 10,
                 'payload': {'stop_reason': 'tool_calls'}},
                {'step': 0, 'type': 'tool_call', 'payload': {'id': 'c1', 'name': 'search', 'arguments': {'query': 'synthetic'}}},
                {'step': 0, 'type': 'tool_result', 'message_id': 'm2',
                 'payload': {'id': 'c1', 'preview': 'Recorded result'}},
                {'step': 1, 'type': 'submit', 'payload': {'final_answer_preview': '42'}},
            ],
        },
        'sample_score': {'sample_id': 'synthetic-evalscope-1', 'score': {
            'value': {'acc': 1.0}, 'extracted_prediction': '42', 'prediction': 'ANSWER: 42',
            'explanation': 'Synthetic exact-answer grading evidence.', 'metadata': {'synthetic': True},
        }},
    }


def inspect_fixture():
    message = {'role': 'assistant', 'content': '42'}
    output = {'model': 'synthetic-model', 'choices': [{'message': message, 'stop_reason': 'stop'}]}
    return {
        'id': 'synthetic-inspect-1', 'epoch': 1, 'input': 'Synthetic question', 'target': '42',
        'messages': [{'role': 'user', 'content': 'Synthetic question'}, message],
        'output': output,
        'scores': {'custom/scorer~1': {'value': 'C', 'answer': '42', 'reason': 'synthetic_check',
                                     'explanation': 'Expected and extracted answers match.'}},
        'events': [
            {'event': 'model', 'model': 'synthetic-model', 'input': [{'role': 'user', 'content': 'Synthetic question'}],
             'output': output, 'tools': [], 'timestamp': '2026-10-09T00:00:00Z'},
            {'event': 'tool', 'type': 'function', 'id': 'c1', 'function': 'search',
             'arguments': {'query': 'synthetic'}, 'result': 'Recorded tool result', 'error': None},
            {'event': 'score', 'scorer': 'custom/scorer~1', 'score': {'value': 'C', 'answer': '42'}},
        ],
        'store': {'synthetic_final_state': {'verified': True}}, 'metadata': {'synthetic': True},
    }


class ExternalAdapterTests(unittest.TestCase):
    def assert_field_pointers(self, raw, view):
        for trajectory in view['trajectories']:
            for step in trajectory['steps']:
                resolve(raw, step['pointer'])
                for field in step['fields']:
                    self.assertEqual(field['value'], resolve(raw, field['pointer']))
        for score in view['scorer_results']:
            resolve(raw, score['pointer'])
            for name, path in score['pointers'].items():
                value = resolve(raw, path)
                if name in ('value', 'answer', 'reason', 'explanation'):
                    self.assertEqual(score[name], value)

    def test_evalscope_links_actual_message_and_preserves_metric_name(self):
        raw = evalscope_fixture(); before = copy.deepcopy(raw)
        view = normalize_record(raw)
        self.assertEqual(view['adapter'], 'evalscope-agent-trace')
        fields = view['trajectories'][0]['steps'][2]['fields']
        returned = next(f for f in fields if f['name'] == 'tool_response')
        self.assertEqual(returned['pointer'], '/messages/2/content')
        self.assertEqual(returned['value'], 'Recorded result 42')
        self.assertEqual(view['scorer_results'][0]['name'], 'acc')
        self.assertEqual(view['scorer_results'][0]['value'], 1.0)
        self.assertEqual(view['parsed_answer'], '42')
        self.assertEqual(view['scores'], {'acc': 1.0})
        self.assertNotIn('em', view['scores'])
        self.assertIsNone(view['termination_reason'])
        self.assertEqual(raw, before)
        self.assert_field_pointers(raw, view)

    def test_evalscope_missing_or_duplicate_message_id_does_not_invent_result(self):
        for duplicate in (False, True):
            raw = evalscope_fixture()
            if duplicate: raw['messages'].append(copy.deepcopy(raw['messages'][2]))
            else: raw['messages'].pop()
            view = normalize_record(raw)
            fields = view['trajectories'][0]['steps'][2]['fields']
            self.assertFalse(any(f['name'] == 'tool_response' for f in fields))
            self.assertTrue(any('message_id' in warning for warning in view['warnings']))

    def test_evalscope_preview_and_submit_never_become_final_answer(self):
        raw = evalscope_fixture(); del raw['sample_score']
        view = normalize_record(raw)
        self.assertIsNone(view['raw_final_output'])
        self.assertIsNone(view['parsed_answer'])
        self.assertEqual(view['scorer_results'], [])

    def test_inspect_single_sample_keeps_model_stop_separate_and_score_evidence(self):
        raw = inspect_fixture(); before = copy.deepcopy(raw)
        view = normalize_record(raw)
        self.assertEqual(view['adapter'], 'inspect-json-sample')
        self.assertEqual(view['raw_final_output'], '42')
        self.assertEqual(view['model_stop_reason'], 'stop')
        self.assertIsNone(view['termination_reason'])
        self.assertIsNone(view['runtime_status'])
        self.assertEqual(view['scorer_results'][0]['name'], 'custom/scorer~1')
        self.assertEqual(view['scorer_results'][0]['pointer'], '/scores/custom~1scorer~01')
        self.assertNotIn('em', view['scores'])
        self.assertEqual(view['outcome_evidence'], raw['store'])
        self.assertEqual(view['record_purpose']['value'], 'unknown')
        self.assertEqual(raw, before)
        self.assert_field_pointers(raw, view)

    def test_inspect_limit_is_sample_termination_evidence(self):
        raw = inspect_fixture()
        raw['limit'] = {'type': 'token', 'limit': 300, 'reason': 'Sample token budget exhausted'}
        view = normalize_record(raw)
        self.assertEqual(view['termination_reason'], 'Sample token budget exhausted')
        self.assertEqual(view['pointers']['termination_reason'], '/limit/reason')

    def test_inspect_grader_role_is_not_hidden_as_an_agent_call(self):
        raw = inspect_fixture()
        raw['events'][0]['role'] = 'grader'
        view = normalize_record(raw)
        step = view['trajectories'][0]['steps'][0]
        self.assertEqual(step['title'], '评分模型调用')
        self.assertTrue(any(f['name'] == 'role' and f['value'] == 'grader' for f in step['fields']))
        self.assert_field_pointers(raw, view)

    def test_inspect_multiple_scorers_do_not_pick_an_answer_or_infer_em(self):
        raw = inspect_fixture()
        raw['scores']['other'] = {'value': None, 'answer': 'forty-two', 'reason': 'grader_failed'}
        view = normalize_record(raw)
        self.assertEqual(len(view['scorer_results']), 2)
        self.assertIsNone(view['parsed_answer'])
        self.assertNotIn('em', view['scores'])

    def test_inspect_condensed_input_remains_a_reference(self):
        raw = inspect_fixture()
        del raw['events'][0]['input']
        raw['events'][0]['input_refs'] = [[0, 1]]
        raw['events_data'] = {'messages': [{'role': 'user', 'content': 'Pooled input'}]}
        raw['attachments'] = {'a': 'Preserved attachment'}
        view = normalize_record(raw)
        fields = view['trajectories'][0]['steps'][0]['fields']
        self.assertFalse(any(f['name'] == 'input' for f in fields))
        self.assertTrue(any('共享引用' in warning for warning in view['warnings']))
        self.assert_field_pointers(raw, view)

    def test_inspect_single_log_wrapper_retains_root_pointers(self):
        raw = {'version': 2, 'status': 'success', 'eval': {'task': 'synthetic', 'model': 'synthetic-model'},
               'samples': [inspect_fixture()], 'metadata': {'purpose': 'checkpoint_eval'}}
        view = normalize_record(raw)
        self.assertEqual(view['pointers']['sample_id'], '/samples/0/id')
        self.assertEqual(view['record_purpose']['value'], 'checkpoint_eval')
        self.assertEqual(view['record_purpose']['pointer'], '/metadata/purpose')
        self.assertIsNone(view['runtime_status'])  # EvalLog.status is not task correctness.
        self.assert_field_pointers(raw, view)
        raw['samples'].append(inspect_fixture())
        multi = normalize_record(raw)
        self.assertEqual(multi['adapter'], 'raw')
        self.assertEqual(multi['trajectories'], [])

    def test_explicit_training_purpose_and_values_keep_provenance(self):
        raw = {'episode': {'trajectories': [{'steps': []}], 'metadata': {
            'record_purpose': 'train_rollout', 'global_step': 0, 'advantage': 0, 'reward': 1}}}
        view = normalize_record(raw)
        self.assertEqual(view['adapter'], 'rllm-episode')
        self.assertEqual(view['record_purpose']['value'], 'train_rollout')
        self.assertEqual(view['record_purpose']['pointer'], '/episode/metadata/record_purpose')
        self.assertEqual(next(f['value'] for f in view['training_fields'] if f['name'] == 'global_step'), 0)
        self.assertEqual(view['scores'], {})
        self.assertEqual(normalize_record({'reward': 1, 'epoch': 4})['record_purpose']['value'], 'unknown')
        raw['purpose'] = 'agent_eval'
        conflict = normalize_record(raw)['record_purpose']
        self.assertEqual(conflict['value'], 'unknown')
        self.assertEqual(conflict['source'], 'conflicting')

    def test_private_events_are_not_guessed_as_an_external_trace(self):
        view = normalize_record({'events': [{'event': 'model', 'input': 'untyped private data'}]})
        self.assertEqual(view['adapter'], 'raw')
        self.assertEqual(view['trajectories'], [])

    def test_repository_original_bytes_and_other_scores_are_separate_from_em(self):
        with tempfile.TemporaryDirectory() as tmp, InspectionRepository(tmp) as repo:
            content = (json.dumps(evalscope_fixture(), ensure_ascii=False) + '\n').encode()
            info = repo.import_bytes(content, 'synthetic-evalscope.jsonl')
            self.assertEqual(repo.original(info['id']), content)
            row = repo.list_records(info['id'])['items'][0]
            self.assertTrue(row['has_recorded_scores'])
            self.assertEqual(row['outcome'], 'unscored')
            self.assertIsNone(row['score_label'])
            self.assertEqual(repo.record(info['id'], 1)['raw'], evalscope_fixture())

    def test_inspect_binary_rejected_with_json_export_guidance(self):
        with tempfile.TemporaryDirectory() as tmp, InspectionRepository(tmp) as repo:
            with self.assertRaisesRegex(ValidationError, r'\.eval binary.*JSON'):
                repo.import_bytes(b'PK\x03\x04synthetic', 'sample.eval')


if __name__ == '__main__':
    unittest.main()
