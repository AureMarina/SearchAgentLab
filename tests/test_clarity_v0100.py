"""Organization/learning-evidence regressions. Fixtures never enter shipped data."""
from copy import deepcopy
import tempfile
import unittest
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.studio.training_stage import stage_of
from searchagent_lab.inspection.summary import problem_summary
from searchagent_lab.inspection.routes import summaries
from searchagent_lab.core import ValidationError
from helpers import seed_minimal,trajectory

class ClarityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.rec=StudioRecorder(self.tmp.name)
        self.s,self.r=self.rec.store,self.rec.repo;seed_minimal(self.s)
    def tearDown(self):
        self.rec.close();self.tmp.cleanup()
    def test_unknown_stage_not_guessed_from_names_or_metrics(self):
        self.assertEqual(stage_of({'name':'SFT GRPO SkillRL','algorithm':'RL'})['value'],'unknown')
    def test_explicit_stage_and_conflicts(self):
        self.assertEqual(stage_of({'training_stage':'sft'})['value'],'sft')
        self.assertEqual(stage_of({},['rl'])['source'],'explicit')
        self.assertEqual(stage_of({'training_stage':'sft'},['rl'])['value'],'mixed')
    def test_method_is_not_training_stage(self):
        with self.assertRaises(ValidationError):self.s.register_run({'id':'x','name':'test','training_stage':'opd'})
    def test_sft_roundtrip_literal_supervision(self):
        rows=[{'id':'demo','model_input':[{'role':'user','content':'training fixture'}],
               'teacher_output':'original','supervision_target':'target','input_ids':[2,3,4],
               'labels':[-100,3,4],'loss_mask':[0,1,1],'loss':0.2}]
        before=deepcopy(rows)
        result=self.rec.log_sft_batch(run_id='run',batch_id='sft',step=5,samples=rows,dataset_version='train-v1')
        self.assertEqual(rows,before)
        self.assertEqual(result,self.rec.log_sft_batch(run_id='run',batch_id='sft',step=5,samples=rows,dataset_version='train-v1'))
        sample=self.r.batches('run',5)['batches'][0]['groups'][0]['samples'][0]
        self.assertEqual(sample['labels'],[-100,3,4]);self.assertNotIn('reward',sample)
        summary=self.r.training_summary('run');self.assertEqual(summary['training_stage']['value'],'sft')
        self.assertIsNone(summary['batch_summary']['reward']);self.assertEqual(summary['batch_summary']['sample_count'],1)
    def test_sft_missing_arrays_stay_missing(self):
        d=self.rec.log_sft_batch(run_id='run',batch_id='sft',step=0,samples=[{'id':'partial','supervision_target':'literal'}])
        s=d['groups'][0]['samples'][0];self.assertNotIn('loss_mask',s);self.assertNotIn('input_ids',s)
    def test_sft_misaligned_arrays_rejected(self):
        with self.assertRaises(ValidationError):self.rec.log_sft_batch(run_id='run',batch_id='sft',step=0,samples=[{'input_ids':[1,2],'labels':[1]}])
    def test_sft_invalid_mask_rejected(self):
        with self.assertRaises(ValidationError):self.rec.log_sft_batch(run_id='run',batch_id='sft',step=0,samples=[{'loss_mask':[True]}])
    def test_sdk_rejects_eval_alias_even_when_record_purpose_absent(self):
        for marker in [{'purpose':'agent_eval'},{'metadata':{'origin':'checkpoint_eval'}},{'record_purpose':'train_rollout'}]:
            with self.subTest(marker=marker),self.assertRaises(ValidationError):
                self.rec.log_sft_batch(run_id='run',batch_id='sft',step=0,samples=[marker])
    def test_sft_cannot_enter_official_scores(self):
        with self.assertRaises(ValidationError):self.s.log_trajectory('eval0',trajectory(purpose='sft_demonstration'))
        self.assertEqual(self.s.evaluation('eval0')['stats']['recorded'],0)
    def test_mixed_sample_purpose_is_rejected(self):
        with self.assertRaises(ValidationError):self.r.record_batch({'id':'x','run_id':'run','step':0,'groups':[{'id':'g','samples':[{'record_purpose':'sft_demonstration','purpose':'train_rollout'}]}]})
    def test_summary_guard_and_no_score_changes(self):
        t=self.s.log_trajectory('eval0',trajectory(em=0,parsed_answer='',raw_final_output='some final output'))
        other=self.s.log_trajectory('eval1',trajectory())
        before=self.s.export_evaluation('eval0');before.pop('exported_at',None)
        result=summaries(self.rec.inspections,self.s,'eval0',[t]);self.assertEqual(len(result['items']),1)
        self.assertEqual(result['items'][0]['cause_status'],'unknown')
        after=self.s.export_evaluation('eval0');after.pop('exported_at',None);self.assertEqual(before,after)
        with self.assertRaises(ValidationError):summaries(self.rec.inspections,self.s,'eval0',[other])
        with self.assertRaises(ValidationError):summaries(self.rec.inspections,self.s,'eval0',[t]*51)
    def test_confirm_and_reject_use_same_hash_bound_sidecar(self):
        t=self.s.log_trajectory('eval0',trajectory(em=0,parsed_answer='',raw_final_output='final fixture'))
        doc=self.rec.inspections.evaluation_record(self.s.trajectory(t));summary=problem_summary(doc)
        self.assertEqual(summary['label'],'自动线索')
        review={'finding_id':summary['finding_id'],'pointer':summary['pointer'],'status':'confirmed','note':'Synthetic review only','source_hash':doc['source_hash']}
        self.rec.inspections.append_review(doc,review)
        doc=self.rec.inspections.evaluation_record(self.s.trajectory(t));self.assertEqual(problem_summary(doc)['title'],'Synthetic review only')
        self.rec.inspections.append_review(doc,{**review,'status':'rejected','note':'Synthetic retraction'})
        fresh=self.rec.inspections.evaluation_record(self.s.trajectory(t));self.assertNotEqual(problem_summary(fresh)['title'],'Synthetic review only')
        self.assertNotEqual(problem_summary(fresh).get('finding_id'),summary['finding_id'])
    def test_review_source_hash_guard(self):
        t=self.s.log_trajectory('eval0',trajectory());doc=self.rec.inspections.evaluation_record(self.s.trajectory(t))
        with self.assertRaises(ValidationError):self.rec.inspections.append_review(doc,{'pointer':'','status':'confirmed','note':'fixture','source_hash':'stale'})

if __name__=='__main__':unittest.main()
