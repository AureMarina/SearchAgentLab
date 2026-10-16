"""0.9 result-first endpoint: ownership, nulls, no heavyweight trace reads."""
import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'examples'))
from seed_studio_demo import seed
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.store import ValidationError

class ResultViewTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();seed(cls.tmp.name)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):self.rec=StudioRecorder(self.tmp.name);self.r=self.rec.repo;self.s=self.rec.store
 def tearDown(self):self.rec.close()
 def test_defaults_latest_saved_not_best(self):
  d=self.r.results('studio-run');self.assertEqual(d['report']['b']['id'],'studio-run-eval200');self.assertEqual(d['selection_rule'],'latest_record_not_best')
 def test_explicit_record(self):self.assertEqual(self.r.results('studio-run',b_id='studio-run-eval0')['report']['b']['id'],'studio-run-eval0')
 def test_fixed_denominator_and_macro(self):
  d=self.r.results('studio-run');self.assertEqual(len(d['report']['items']),11);self.assertEqual(d['report']['b']['stats']['answer_correct'],7);self.assertIsNotNone(d['report']['b']['stats']['macro_em'])
 def test_comparison_uses_existing_guard(self):
  d=self.r.results('studio-run',b_id='studio-run-eval100',a_id='studio-run-eval50');self.assertEqual(d['report']['pair']['groups']['rescued'],2);self.assertEqual(d['report']['pair']['groups']['harmed'],1)
 def test_cross_run_b_rejected(self):
  with self.assertRaises(ValidationError):self.r.results('studio-run',b_id='studio-base-eval0')
 def test_cross_run_a_rejected(self):
  with self.assertRaises(ValidationError):self.r.results('studio-run',a_id='studio-repro-run-eval100')
 def test_same_record_rejected(self):
  with self.assertRaises(ValidationError):self.r.results('studio-run',b_id='studio-run-eval0',a_id='studio-run-eval0')
 def test_no_training_overview_read(self):
  with patch.object(self.s,'overview',side_effect=AssertionError('must not load dynamics')):self.assertEqual(len(self.r.results('studio-run')['report']['items']),11)
 def test_summary_fields_match_original_trace(self):
  report=self.r.report('studio-run-eval100');sample=next(x['b'] for x in report['items'] if x['b']);raw=self.s.trajectory(sample['id']);self.assertEqual(sample['scores'],raw['scores']);self.assertEqual(sample['parsed_answer'],raw['parsed_answer']);self.assertNotIn('steps',sample)
 def test_report_only_current_project(self):self.assertEqual(self.r.report_catalog('studio-entropy','main')['evaluations'],[])
 def test_other_panel_no_fabricated_result(self):
  self.s.register_panel({'id':'unused-panel','name':'No executions','version':'v1','split':'probe','samples':[{'id':'Q','dataset':'test','question':'Synthetic?','answers':['yes']}]})
  d=self.r.results('studio-run','unused-panel');self.assertIsNone(d['report']);self.assertEqual(d['evaluations'],[])
  with self.assertRaises(ValidationError):self.r.results('studio-run','unused-panel',b_id='studio-run-eval0')
