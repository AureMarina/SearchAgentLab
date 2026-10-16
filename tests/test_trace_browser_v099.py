"""Read-only trace browser guards. All test data are explicitly synthetic."""
import json
import sys
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'examples')]
from seed_studio_demo import seed
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.studio.trace_browser import trace_browser
from searchagent_lab.core import ValidationError
from searchagent_lab.server import LabServer


class TraceBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); seed(cls.tmp.name)
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
    def setUp(self):
        self.rec=StudioRecorder(self.tmp.name);self.r=self.rec.repo;self.s=self.rec.store
    def tearDown(self):
        self.rec.close()
    def test_default_latest_is_not_selected_by_score(self):
        d=trace_browser(self.r,'studio-run')
        self.assertEqual(d['report']['b']['id'],'studio-run-eval200')
        self.assertEqual(d['selected_a'],'')
        self.assertIsNone(d['report']['a'])
    def test_keeps_actual_panel_size(self):
        d=trace_browser(self.r,'studio-run')
        self.assertEqual(len(d['report']['items']),11)
        self.assertEqual(d['panel']['size'],11)
        self.assertNotIn('samples',d['panel'])
    def test_current_evaluation_must_belong_to_current_run(self):
        with self.assertRaises(ValidationError):trace_browser(self.r,'studio-run',b_id='studio-repro-run-eval100')
    def test_same_evaluation_not_compared_with_itself(self):
        with self.assertRaises(ValidationError):trace_browser(self.r,'studio-run',b_id='studio-run-eval100',a_id='studio-run-eval100')
    def test_unknown_a_not_silently_replaced(self):
        with self.assertRaises(ValidationError):trace_browser(self.r,'studio-run',a_id='missing')
    def test_axis_must_be_known(self):
        with self.assertRaises(ValidationError):trace_browser(self.r,'studio-run',axis='whatever')
    def test_same_run_candidates_first(self):
        d=trace_browser(self.r,'studio-run');self.assertTrue(d['candidates'][0]['same_run'])
        self.assertNotIn(d['report']['b']['id'],[c['evaluation']['id'] for c in d['candidates']])
    def test_cross_track_can_browse_but_not_claim_training_gain(self):
        d=trace_browser(self.r,'studio-run',b_id='studio-run-eval100',a_id='studio-repro-run-eval100')
        self.assertEqual(d['report']['a']['id'],'studio-repro-run-eval100')
        self.assertFalse(d['report']['pair']['compatible'])
        self.assertTrue(d['report']['pair']['reasons'])
        self.assertIsNone(d['report']['pair'].get('macro_delta'))
    def test_existing_control_guards_are_unchanged(self):
        d=trace_browser(self.r,'studio-run',b_id='studio-run-eval100',a_id='studio-run-eval50')
        original=self.r.evaluation_pair('studio-run-eval50','studio-run-eval100','checkpoint')
        self.assertEqual(d['report']['pair']['compatible'],original['compatible'])
        self.assertEqual(d['report']['pair']['reasons'],original['reasons'])
    def test_pair_uses_dataset_and_sample_id(self):
        d=trace_browser(self.r,'studio-run',b_id='studio-run-eval100',a_id='studio-run-eval50')
        for row in d['report']['items']:
            for s in ('a','b'):
                if row[s]:
                    self.assertEqual(row[s]['dataset'],row['sample']['dataset'])
                    self.assertEqual(row[s]['sample_id'],row['sample']['id'])
    def test_data_and_scores_are_not_rewritten(self):
        before=self.s.export_evaluation('studio-run-eval100')
        trace_browser(self.r,'studio-run',b_id='studio-run-eval100',a_id='studio-repro-run-eval100')
        self.assertEqual(self.s.export_evaluation('studio-run-eval100'),before)
    def test_different_text_is_unpaired_not_rejected_or_scored_wrong(self):
        pid='trace-v099-other-panel';eid='trace-v099-other-eval'
        self.s.register_panel({'id':pid,'name':'Separate synthetic panel','version':'1','demo':True,'samples':[{'id':'SYN-01','dataset':'TriviaQA','question':'Different text','answers':['X']}]})
        if not self.s.conn.execute('SELECT 1 FROM evaluations WHERE id=?',(eid,)).fetchone():
            self.s.start_evaluation({'id':eid,'checkpoint_id':'studio-repro-run-cp100','panel_id':pid,'protocol':self.s.evaluation('studio-run-eval100')['protocol']})
        d=trace_browser(self.r,'studio-run',b_id='studio-run-eval100',a_id=eid)
        self.assertFalse(d['report']['pair']['compatible'])
        self.assertTrue(all(row['a'] is None or row['b'] is None for row in d['report']['items']))
        self.assertTrue(all(row['group']=='missing' for row in d['report']['items']))
    def test_no_evaluation_is_honest_empty_state(self):
        self.s.register_run({'id':'trace-v099-empty-run','name':'Synthetic empty run','experiment_id':'studio-e03','demo':True})
        d=trace_browser(self.r,'trace-v099-empty-run')
        self.assertIsNone(d['report']);self.assertEqual(d['candidates'],[])
    def test_http_endpoint_is_read_only_and_validates_ownership(self):
        with LabServer(('127.0.0.1',0),self.s) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_address[1])
            try:
                with urllib.request.urlopen(base+'/api/studio/trace-browser?run=studio-run') as response:
                    self.assertEqual(json.load(response)['browser_version'],2)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+'/api/studio/trace-browser?run=studio-run&b=studio-repro-run-eval100')
                self.assertEqual(error.exception.code,400)
            finally:
                server.shutdown();thread.join()

if __name__=='__main__':unittest.main()
