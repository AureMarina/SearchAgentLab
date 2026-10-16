import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from searchagent_lab.store import Store, ValidationError, NotFoundError, LEGACY_PROJECT
from searchagent_lab.core import Store as OldCore
from searchagent_lab.demo import PROTOCOL_V2
from searchagent_lab.io import import_bundle
from helpers import trajectory


def research_fixture(s):
    for p in ('latent','self-play'):
        s.register_project({'id':p,'name':p,'metric_definitions':{'diagnostic':{'definition':p+' meaning'}}})
        s.register_experiment({'id':p+'-e','project_id':p,'name':p+' experiment'})
        s.register_run({'id':p+'-r','experiment_id':p+'-e','name':p+' run', 'base_model':'test-model', 'model_revision':'revision-1','seed':42,'demo':True})
        for i in (0,1):s.register_checkpoint({'id':p+f'-cp{i}','run_id':p+'-r','step':i*100,'weights_sha256':'test-cp'+str(i)})
    s.register_panel({'id':'gt','name':'GT','demo':True,'samples':[{'id':f'q{i}','dataset':'HotpotQA','question':f'Question HotpotQA {i}?','answers':[f'gold-{i}'],'metadata':{'source_id':f's{i}','source_revision':'r1'}} for i in range(2)]})
    for p in ('latent','self-play'):
        for i in (0,1):
            evid=p+f'-ev{i}'
            s.start_evaluation({'id':evid,'checkpoint_id':p+f'-cp{i}','panel_id':'gt','protocol':deepcopy(PROTOCOL_V2)})
            for j in (0,1):s.log_trajectory(evid,trajectory(j,em=(i+j)%2))
            s.finish_evaluation(evid)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);research_fixture(self.s)
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def test_hierarchy_filter_is_not_a_label(self):
        d=self.s.catalog('latent');self.assertEqual([r['id'] for r in d['runs']],['latent-r'])
        self.assertEqual(d['experiments'][0]['id'],'latent-e')
    def test_overview_blocks_foreign_project(self):
        with self.assertRaises(ValidationError):self.s.overview('self-play-r','gt','latent')
    def test_matrix_blocks_foreign_project(self):
        with self.assertRaises(ValidationError):self.s.matrix('self-play-r','gt',project_id='latent')
    def test_project_label_without_experiment_rejected(self):
        with self.assertRaises(ValidationError):self.s.register_run({'id':'bad','name':'bad','project_id':'latent'})
    def test_mismatched_run_parent_rejected(self):
        with self.assertRaises(ValidationError):self.s.register_run({'id':'bad','name':'bad','project_id':'self-play','experiment_id':'latent-e'})
    def test_checkpoint_lineage_claim_rejected(self):
        with self.assertRaises(ValidationError):self.s.register_checkpoint({'id':'bad','run_id':'latent-r','step':99,'project_id':'self-play'})
    def test_evaluation_lineage_claim_rejected(self):
        with self.assertRaises(ValidationError):self.s.start_evaluation({'id':'bad','checkpoint_id':'latent-cp0','panel_id':'gt','protocol':PROTOCOL_V2,'run_id':'self-play-r'})
    def test_no_silent_relabel(self):
        r=self.s.run('latent-r');r['experiment_id']='self-play-e';r.pop('project_id')
        with self.assertRaises(ValidationError):self.s.register_run(r)
    def test_v2_requires_experiment_id_and_transactional_import(self):
        with self.assertRaises(ValidationError):import_bundle(self.s,{'schema_version':2,'projects':[{'id':'discarded','name':'x'}],'runs':[{'id':'bad','name':'bad'}]})
        self.assertFalse(any(p['id']=='discarded' for p in self.s.projects()))
    def test_metric_scope_definitions(self):
        self.s.log_metrics('latent-r',10,{'diagnostic':3})
        self.s.log_metrics('self-play-r',10,{'diagnostic':999})
        d=self.s.overview('latent-r','gt');self.assertEqual(d['metrics'][0]['diagnostic'],3)
        self.assertEqual(d['metric_definitions']['diagnostic']['definition'],'latent meaning')
    def test_global_gt_shared_not_results(self):
        a=self.s.overview('latent-r','gt');b=self.s.overview('self-play-r','gt')
        self.assertEqual(a['panel']['fingerprint'],b['panel']['fingerprint'])
        self.assertTrue(all(c['run_id']=='latent-r' for c in a['checkpoints']))
    def test_full_gt_export_reimports_without_changing(self):
        p=self.s.panel('gt');self.assertEqual(self.s.register_panel(p),'gt')
    def test_gt_declared_hash_tampering_rejected(self):
        p=self.s.panel('gt');p['id']='newgt';p['samples'][0]['question']='tampered'
        with self.assertRaises(ValidationError):self.s.register_panel(p)
    def test_gt_alias_change_same_version_rejected(self):
        p=self.s.panel('gt');p.pop('fingerprint');p['samples'][0]['answers'].append('another alias')
        with self.assertRaises(ValidationError):self.s.register_panel(p)
    def test_draft_not_treated_as_real_gt(self):
        p=self.s.panel('gt');p['id']='draft';p['selection_status']='DRAFT_NOT_APPROVED'
        with self.assertRaises(ValidationError):self.s.register_panel(p)
    def test_cross_project_requires_opt_in(self):
        d=self.s.compare('latent-ev0','self-play-ev0');self.assertFalse(d['compatible']);self.assertNotIn('groups',d)
        self.assertTrue(self.s.compare('latent-ev0','self-play-ev0',allow_cross_project=True)['compatible'])
    def test_cross_run_model_mismatch_blocks(self):
        r=self.s._get('runs','self-play-r');r['base_model']='other-model';self.s.conn.execute('UPDATE runs SET data=? WHERE id=?',(json.dumps(r),r['id']))
        self.assertFalse(self.s.compare('latent-ev0','self-play-ev0',allow_cross_project=True)['compatible'])
    def test_matrix_shows_same_question_across_checkpoints(self):
        d=self.s.matrix('latent-r','gt','HotpotQA');self.assertEqual(len(d['rows']),2)
        self.assertEqual(d['rows'][0]['cells'][1]['change'],'rescued')
        self.assertEqual(d['rows'][1]['cells'][1]['change'],'harmed')
    def test_missing_checkpoint_eval_is_none(self):
        self.s.register_checkpoint({'id':'empty','run_id':'latent-r','step':200})
        d=self.s.matrix('latent-r','gt');self.assertIsNone(d['rows'][0]['cells'][-1])
    def test_unknown_matrix_dataset_rejected(self):
        with self.assertRaises(ValidationError):self.s.matrix('latent-r','gt','NQ')
    def test_foreign_matrix_eval_choice_rejected(self):
        with self.assertRaises(ValidationError):self.s.matrix('latent-r','gt',selections={'latent-cp0':'self-play-ev0'})
    def test_unknown_checkpoint_selection_rejected(self):
        with self.assertRaises(ValidationError):self.s.matrix('latent-r','gt',selections={'outside':'latent-ev0'})
    def test_repeat_eval_keeps_all_records_and_explicit_choice(self):
        self._repeat('repeat')
        d=self.s.overview('latent-r','gt');self.assertEqual(len(d['checkpoints'][0]['evaluations']),2)
        matrix=self.s.matrix('latent-r','gt',selections={'latent-cp0':'latent-ev0'})
        self.assertEqual(matrix['columns'][0]['evaluation']['id'],'latent-ev0')
    def _repeat(self,name,change=None,protocol=None,steps=True):
        self.s.start_evaluation({'id':name,'checkpoint_id':'latent-cp0','panel_id':'gt','protocol':protocol or deepcopy(PROTOCOL_V2)})
        for j in (0,1):
            t=trajectory(j,em=j)
            if not steps:t['steps']=[]
            if change:change(t,j)
            self.s.log_trajectory(name,t)
        self.s.finish_evaluation(name)
    def test_repro_matching_semantic_trace_ignores_latency(self):
        self._repeat('repeat',lambda t,j:t['steps'][0].update(latency_ms=300))
        d=self.s.reproducibility('latent-ev0','repeat');self.assertTrue(d['all_recorded_evidence_matches'])
    def test_repro_query_change_found(self):
        self._repeat('repeat',lambda t,j:t['steps'][1]['tool_call']['arguments'].update(query='different') if j==0 else None)
        d=self.s.reproducibility('latent-ev0','repeat');self.assertEqual(d['counts']['different'],1)
        self.assertEqual(d['items'][0]['first_different_step_index'],1)
        self.assertTrue(d['items'][0]['answer_equal'])
    def test_repro_missing_steps_not_match(self):
        self._repeat('repeat',steps=False)
        d=self.s.reproducibility('latent-ev0','repeat');self.assertEqual(d['counts']['insufficient'],2);self.assertFalse(d['all_recorded_evidence_matches'])
    def test_repro_same_record_rejected(self):
        self.assertFalse(self.s.reproducibility('latent-ev0','latent-ev0')['eligible'])
    def test_repro_different_checkpoint_rejected(self):
        self.assertFalse(self.s.reproducibility('latent-ev0','latent-ev1')['eligible'])
    def test_protocol_unknown_not_complete(self):
        p=deepcopy(PROTOCOL_V2);p['runtime']['hardware']='unknown';self._repeat('repeat',protocol=p)
        self.assertFalse(self.s.reproducibility('latent-ev0','repeat')['eligible'])
    def test_protocol_state_must_reset(self):
        p=deepcopy(PROTOCOL_V2);p['state_reset']['per_question']=False
        with self.assertRaises(ValidationError):self._repeat('repeat',protocol=p)
    def test_gt_must_not_be_model_input(self):
        p=deepcopy(PROTOCOL_V2);p['groundtruth_to_agent']=True
        with self.assertRaises(ValidationError):self._repeat('repeat',protocol=p)
    def test_no_training_during_validation(self):
        p=deepcopy(PROTOCOL_V2);p['mode']='training'
        with self.assertRaises(ValidationError):self._repeat('repeat',protocol=p)
    def test_note_append_only(self):
        for x in ('positive','negative'):self.s.note_experiment('latent-e',{'kind':x,'note':x,'evidence':[{'type':'evaluation','id':'latent-ev0'}]})
        self.assertEqual(len(self.s.experiment('latent-e')['notes']),2)
        self.assertEqual(len(self.s.experiment('self-play-e')['notes']),0)
    def test_reference_does_not_copy_foreign_evidence(self):
        before=self.s.health();self.s.add_reference('latent-e','self-play-ev0','existing baseline, do not rerun')
        self.assertEqual(before['trajectories'],self.s.health()['trajectories'])
        self.assertEqual(len(self.s.overview('latent-r','gt')['checkpoints']),2)
        self.assertEqual(self.s.experiment('latent-e')['references'][0]['source_project'],'self-play')
    def test_export_carries_whole_ownership(self):
        d=self.s.export_evaluation('latent-ev0');self.assertEqual(d['ownership']['project']['id'],'latent')
        self.assertEqual(len(d['groundtruth']['samples']),2)
    def test_no_audit_not_claimed_isolated(self):
        self.assertEqual(self.s.panel('gt')['isolation']['status'],'not_checked')
    def test_doctor_schema2(self):self.assertEqual(self.s.health()['schema_version'],2)


class MigrationTests(unittest.TestCase):
    def test_v1_backup_unassigned_and_explicit_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            with OldCore(tmp) as old:
                old.register_run({'id':'v1-run','name':'ambiguous latent-looking name'})
                old.log_metrics('v1-run',5,{'loss':.4})
                old.conn.execute('PRAGMA user_version=1')
            with Store(tmp) as new:
                self.assertEqual(new.run('v1-run')['project_id'],LEGACY_PROJECT)
                self.assertEqual(len(list(Path(tmp).glob('before-v2-*.sqlite3'))),1)
                new.register_project({'id':'latent','name':'Latent'})
                new.register_experiment({'id':'e','project_id':'latent','name':'E01'})
                new.assign_legacy_run('v1-run','e','explicit owner confirmation')
                self.assertEqual(new.run('v1-run')['project_id'],'latent')
                self.assertEqual(new.metrics('v1-run')[0]['loss'],.4)
                with self.assertRaises(ValidationError):new.assign_legacy_run('v1-run','e','cannot classify twice')
            with Store(tmp) as again:self.assertEqual(len(list(Path(tmp).glob('before-v2-*.sqlite3'))),1)

if __name__=='__main__':unittest.main()
