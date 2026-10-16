"""Regression cases for full evaluations, partial pairing and diagnostic separation."""
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'examples')]
from seed_studio_demo import seed
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.studio.trace_browser import trace_browser


class EvaluationFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();seed(self.tmp.name)
        self.rec=StudioRecorder(self.tmp.name);self.s=self.rec.store;self.r=self.rec.repo
        self.protocol=self.s.evaluation('studio-run-eval100')['protocol']

    def tearDown(self):
        self.rec.close();self.tmp.cleanup()

    def full(self,ident='full-b',split='test',count=65,offset=0):
        samples=[{'id':str(i),'dataset':'Example','question':f'Synthetic question {i}', 'answers':['yes']} for i in range(offset,offset+count)]
        pid=ident+'-questions'
        self.s.register_panel({'id':pid,'name':'Synthetic full test','split':split,'samples':samples,'demo':True})
        self.s.start_evaluation({'id':ident,'checkpoint_id':'studio-run-cp100','panel_id':pid,'scope':'full','protocol':self.protocol})
        for i in range(offset,offset+count-1):
            self.s.log_trajectory(ident,{'dataset':'Example','sample_id':str(i),'parsed_answer':'yes' if i%2 else 'no',
                'scores':{'em':i%2},'steps':[],'runtime_status':'error' if i==2 else 'ok'})
        return ident

    def test_full_evaluation_default_not_old_eleven(self):
        self.full();d=trace_browser(self.r,'studio-run')
        self.assertEqual(d['selected_evaluation']['id'],'full-b')
        self.assertEqual(d['page']['total'],65);self.assertEqual(len(d['report']['items']),30)
        self.assertEqual(d['availability'],'results_only')
        self.assertTrue(any(e['scope']=='diagnostic' for e in d['evaluations']))

    def test_explicit_evaluation_wins_over_stale_panel(self):
        self.full();d=trace_browser(self.r,'studio-run',panel_id='studio-panel',b_id='full-b')
        self.assertEqual(d['page']['total'],65)

    def test_filters_count_missing_separately(self):
        self.full();d=trace_browser(self.r,'studio-run',outcome='incorrect')
        self.assertEqual(d['page']['filtered'],32)
        self.assertEqual(d['page']['counts']['missing'],1)
        self.assertEqual(d['page']['counts']['error'],1)
        self.assertTrue(all(r['b']['scores']['em']==0 for r in d['report']['items']))

    def test_deep_link_locates_question_beyond_first_page(self):
        self.full();d=trace_browser(self.r,'studio-run',sample_id='63',dataset='Example')
        self.assertEqual(d['page']['offset'],60)
        self.assertTrue(any(r['sample']['id']=='63' for r in d['report']['items']))

    def test_diagnostic_scope_excludes_full_evaluation(self):
        self.full();d=trace_browser(self.r,'studio-run',scope='diagnostic')
        self.assertEqual(d['page']['total'],11)
        self.assertEqual(d['selected_evaluation']['scope'],'diagnostic')
        d=self.r.results('studio-run',scope='diagnostic')
        self.assertTrue(all(e['id']!='full-b' for e in d['evaluations']))

    def test_no_full_evaluation_does_not_fall_back_to_eleven(self):
        d=trace_browser(self.r,'studio-run',scope='full')
        self.assertIsNone(d['report']);self.assertEqual(d['page']['total'],0)

    def test_overlap_pairing_and_a_only_questions(self):
        self.full('full-b',count=4);self.full('full-a',count=4,offset=2)
        d=trace_browser(self.r,'studio-run',b_id='full-b',a_id='full-a')
        self.assertEqual(d['page']['total'],6)
        self.assertEqual(d['page']['counts']['both_wrong'],1)
        self.assertEqual(d['page']['counts']['missing'],5)
        self.assertFalse(d['report']['pair']['compatible'])

    def test_different_splits_never_pair_identical_ids(self):
        self.full('full-b',count=4);self.full('full-a',split='dev',count=4)
        d=trace_browser(self.r,'studio-run',b_id='full-b',a_id='full-a')
        self.assertEqual(d['page']['counts']['missing'],8)
        self.assertEqual(len({r['key'] for r in d['report']['items']}),8)

    def test_aggregate_is_selectable_without_inventing_rows(self):
        self.s.log_metrics('studio-run',300,{'eval/nq/em_pct':50,'eval/nq/sample_count':5000})
        d=trace_browser(self.r,'studio-run')
        self.assertEqual(d['selected_evaluation']['record_type'],'aggregate')
        self.assertEqual(d['selected_evaluation']['question_count'],5000)
        self.assertIsNone(d['report']);self.assertEqual(d['page']['total'],0)
        self.assertEqual(d['availability'],'not_connected')

    def test_both_correct_filter_is_available(self):
        self.full('full-b',count=5);self.full('full-a',count=5)
        d=trace_browser(self.r,'studio-run',b_id='full-b',a_id='full-a',outcome='both_correct')
        self.assertEqual(d['page']['filtered'],2)
        self.assertTrue(all(r['a']['scores']['em']==r['b']['scores']['em']==1 for r in d['report']['items']))


if __name__=='__main__':unittest.main()
