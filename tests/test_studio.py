"""Purpose separation, dynamics, fixed reports and immutable original evidence."""
import json,tempfile,unittest,sys,threading,urllib.request,urllib.error,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'examples')]
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.store import ValidationError
from searchagent_lab.server import LabServer
from searchagent_lab.core import digest
from seed_studio_demo import seed

class StudioTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();seed(cls.tmp.name);cls.directory=cls.tmp.name
 def setUp(self):self.rec=StudioRecorder(self.directory);self.r=self.rec.repo;self.s=self.rec.store
 def tearDown(self):self.rec.close()
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def test_main_catalog_excludes_reproduction(self):
  c=self.r.catalog('studio-selfplay','main');self.assertEqual({e['experiment']['id'] for e in c['experiments']},{'studio-e01','studio-e03'})
 def test_reproduction_catalog_excludes_main(self):
  c=self.r.catalog('studio-selfplay','reproduction');self.assertIn('studio-drzero',[e['experiment']['id'] for e in c['experiments']]);self.assertTrue(all(e['profile']['track']=='reproduction' for e in c['experiments']));self.assertEqual(c['main_iteration_count'],0)
 def test_old_record_not_guessed_from_name(self):
  self.s.register_experiment({'id':'unknown_drzero','project_id':'studio-selfplay','name':'Dr.Zero / Self-play'});self.assertEqual(self.r.profile('unknown_drzero')['track'],'unassigned')
 def test_profile_immutable_track(self):
  p=self.r.profile('studio-drzero');p['track']='main';p['reproduction']={}
  with self.assertRaises(ValidationError):self.r.register_profile(p)
 def test_profile_cannot_be_self_parent(self):
  p={'experiment_id':'studio-e03','track':'main','parent_experiment_id':'studio-e03'}
  with self.assertRaises(ValidationError):self.r.register_profile(p)
 def test_reproduction_cannot_parent_main(self):
  with self.assertRaises(ValidationError):self.r.create_experiment({'experiment':{'id':'main_child_bad','name':'bad','project_id':'studio-selfplay'},'profile':{'track':'main','parent_experiment_id':'studio-drzero'}})
  self.assertFalse(self.s.conn.execute("SELECT 1 FROM experiments WHERE id='main_child_bad'").fetchone())
 def test_repro_cannot_join_lineage(self):
  with self.assertRaises(ValidationError):self.r.create_experiment({'experiment':{'id':'repro_parent_bad','name':'bad','project_id':'studio-selfplay'},'profile':{'track':'reproduction','reproduction':{'method_name':'X'},'parent_experiment_id':'studio-e03'}})
 def test_main_repro_pair_no_delta(self):
  d=self.r.pair('studio-repro-run-eval100','studio-run-eval100');self.assertFalse(d['compatible']);self.assertIsNone(d['macro_delta']);self.assertFalse(d['included_in_main_iteration'])
 def test_legacy_compare_guard_too(self):
  d=self.s.compare('studio-repro-run-eval100','studio-run-eval100');self.assertFalse(d['compatible'])
 def test_legacy_baseline_reference_guard(self):
  with self.assertRaises(ValidationError):self.s.add_reference('studio-e03','studio-repro-run-eval100','not allowed')
 def test_repro_internally_comparable_not_main(self):
  d=self.r.pair('studio-repro-run-eval0','studio-repro-run-eval100');self.assertTrue(d['compatible']);self.assertFalse(d['included_in_main_iteration']);self.assertEqual(d['delta_kind'],'复现内部 checkpoint 变化')
 def test_main_pair_counts_consistent(self):
  d=self.r.pair('studio-run-eval50','studio-run-eval100');self.assertEqual(d['groups']['rescued'],2);self.assertEqual(d['groups']['harmed'],1);self.assertEqual(d['net_correct_change'],1)
 def test_matrix_counts_match_chart(self):
  d=self.r.dynamics('studio-run','studio-synthetic-11');self.assertEqual([c['evaluation']['stats']['answer_correct'] for c in d['matrix']['columns']],[5,6,7,7]);self.assertEqual(len(d['matrix']['rows']),11)
 def test_initial_vs_previous_reference(self):
  a=self.r.dynamics('studio-run','studio-synthetic-11',baseline='previous');b=self.r.dynamics('studio-run','studio-synthetic-11',baseline='initial');self.assertEqual(a['changes'][2]['reference_id'],'studio-run-eval50');self.assertEqual(b['changes'][2]['reference_id'],'studio-run-eval0')
 def test_invalid_selection_rejected(self):
  with self.assertRaises(ValidationError):self.r.dynamics('studio-run','studio-synthetic-11',{'studio-run-cp100':'studio-repro-run-eval100'})
 def test_negative_loss_preserved(self):
  self.assertTrue(any(m['actor/pg_loss']<0 for m in self.s.metrics('studio-run')))
 def test_batch_step_link_not_guessed(self):
  self.assertEqual(self.r.batches('studio-run',98)['batches'],[]);self.assertEqual(self.r.batches('studio-run',100)['batches'][0]['id'],'studio-b100')
 def test_batch_scope_kept(self):self.assertEqual(self.r.batches('studio-repro-run',100)['batches'],[])
 def test_bad_mask_length_rejected(self):
  with self.assertRaises(ValidationError):self.r.record_batch({'id':'bad_mask','run_id':'studio-run','step':100,'groups':[{'id':'g','samples':[{'token_ids':[1,2],'response_mask':[1]}]}]})
 def test_batch_no_fake_advantages(self):
  d=self.r.record_batch({'id':'no_adv_batch','run_id':'studio-run','step':5,'groups':[{'id':'g','samples':[{'reward':0}]}]});self.assertNotIn('advantage',d['groups'][0]['samples'][0])
 def test_batch_immutable(self):
  b=self.r.batches('studio-run',100)['batches'][0];b['step']=101;b.pop('content_hash')
  with self.assertRaises(ValidationError):self.r.record_batch(b)
 def test_report_list_lane_isolation(self):
  d=self.r.report_catalog('studio-selfplay','main');self.assertTrue(all(x['profile']['track']=='main' for x in d['evaluations']));self.assertFalse(any('repro-run' in x['evaluation']['id'] for x in d['evaluations']))
 def test_report_contains_every_fixed_question(self):
  d=self.r.report('studio-run-eval100','studio-run-eval50');self.assertEqual(len(d['items']),11);self.assertEqual(sum(x['group']=='rescued' for x in d['items']),2)
 def test_report_single_does_not_imply_gain(self):
  d=self.r.report('studio-run-eval100');self.assertIsNone(d['pair']);self.assertTrue(all(x['group']=='unpaired' for x in d['items']))
 def test_save_report_readonly_snapshot(self):
  # This test concerns snapshot immutability. Single-evaluation snapshots do not
  # pretend the legacy fixture has newly registered controlled-comparison hashes.
  d=self.r.save_report({'id':'test_snapshot','name':'UI snapshot','b':'studio-run-eval100'});stored=self.r.saved_report('test_snapshot');self.assertEqual(digest(stored['snapshot']),stored['snapshot_hash'])
  with self.assertRaises(ValidationError):self.r.save_report({'id':'test_snapshot','name':'overwrite','b':'studio-run-eval200'})
 def test_bad_report_not_persisted(self):
  with self.assertRaises(ValidationError):self.r.save_report({'id':'bad_cross','name':'bad','a':'studio-repro-run-eval100','b':'studio-run-eval100'})
 def test_published_reference_not_local_metric(self):
  p=self.r.profile('studio-drzero');self.assertIsNone(p['reproduction']['published_reference']['value']);self.assertNotIn('published_reference',self.s.evaluation('studio-repro-run-eval100')['stats'])
 def test_original_scores_unchanged_after_reports(self):
  a=self.s.export_evaluation('studio-run-eval100');self.r.report('studio-run-eval100','studio-run-eval50');b=self.s.export_evaluation('studio-run-eval100');self.assertEqual(a['trajectories'],b['trajectories'])
 def test_profile_rejects_invalid_mode(self):
  with self.assertRaises(ValidationError):self.r.register_profile({'experiment_id':'studio-drzero','track':'reproduction','reproduction':{'method_name':'X','protocol_mode':'pretend'}})
 def test_unknown_kind_rejected(self):
  with self.assertRaises(ValidationError):self.r.register_profile({'experiment_id':'studio-e03','track':'main','kind':'wrong'})
 def test_dynamic_method_summary_scoped(self):
  self.assertEqual(self.r.method_summary('studio-run')['counts']['selfplay_cycle'],1);self.assertEqual(self.r.method_summary('studio-repro-run')['counts'],{})
 def test_explicit_old_classification_preserves_raw(self):
  self.s.register_experiment({'id':'old_preserve','project_id':'studio-selfplay','name':'Original'});before=self.s._get('experiments','old_preserve');self.r.register_profile({'experiment_id':'old_preserve','track':'reproduction','reproduction':{'method_name':'X'}});self.assertEqual(before,self.s._get('experiments','old_preserve'))
 def test_batch_return_roundtrip_idempotent(self):
  b=self.r.batches('studio-run',100)['batches'][0];self.assertEqual(self.r.record_batch(b),b)
 def test_bad_batch_hash_rejected(self):
  b=self.r.batches('studio-run',100)['batches'][0];b['content_hash']='0'*64
  with self.assertRaises(ValidationError):self.r.record_batch(b)
 def test_group_not_dict_rejected(self):
  with self.assertRaises(ValidationError):self.r.record_batch({'id':'invalid_g','run_id':'studio-run','step':2,'groups':[None]})
 def test_duplicate_group_rejected(self):
  with self.assertRaises(ValidationError):self.r.record_batch({'id':'duplicate_g','run_id':'studio-run','step':2,'groups':[{'id':'g','samples':[]},{'id':'g','samples':[]}]})
 def test_tokens_not_list_rejected(self):
  with self.assertRaises(ValidationError):self.r.record_batch({'id':'invalid_tokens','run_id':'studio-run','step':2,'groups':[{'id':'g','samples':[{'token_ids':12}]}]})
 def test_batch_nonnumeric_reward_rejected(self):
  with self.assertRaises(ValidationError):self.r.record_batch({'id':'invalid_r','run_id':'studio-run','step':2,'groups':[{'id':'g','samples':[{'reward':'good'}]}]})
 def test_empty_project_catalog(self):self.assertEqual(self.r.catalog('studio-entropy','main')['experiments'],[])
 def test_missing_metric_not_zero(self):
  d=self.r.dynamics('studio-base','studio-synthetic-11');self.assertEqual(d['overview']['metrics'],[])

class StudioHTTPTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();seed(cls.tmp.name);cls.rec=StudioRecorder(cls.tmp.name);cls.server=LabServer(('127.0.0.1',0),cls.rec.store);threading.Thread(target=cls.server.serve_forever,daemon=True).start();cls.base='http://127.0.0.1:'+str(cls.server.server_address[1])
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.rec.close();cls.tmp.cleanup()
 def request(self,path,data=None,token=True):
  headers={'Content-Type':'application/json'}
  if token:headers['X-Local-Token']=self.server.csrf_token
  try:r=urllib.request.urlopen(urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers))
  except urllib.error.HTTPError as e:r=e
  return r.status,r.read()
 def test_root_loads_shipped_workspace_resources(self):
  status,raw=self.request('/');self.assertEqual(status,200)
  scripts=re.findall(rb'<script type="module" src="([^"]+)"',raw)
  styles=re.findall(rb'<link rel="stylesheet" href="([^"]+)"',raw)
  self.assertEqual(len(scripts),1);self.assertTrue(styles)
  for asset in scripts+styles:
   self.assertTrue(asset.startswith(b'/assets/'))
   status,body=self.request(asset.decode());self.assertEqual(status,200);self.assertGreater(len(body),100)
 def test_legacy_page_retained(self):self.assertEqual(self.request('/legacy.html')[0],200)
 def test_studio_assets_served(self):self.assertEqual(self.request('/studio/replay.js')[0],200)
 def test_csrf_guard(self):self.assertEqual(self.request('/api/studio/profile',{},False)[0],403)
 def test_malformed_profile_is400(self):self.assertEqual(self.request('/api/studio/profile',{'experiment_id':'studio-e03','track':'bad'})[0],400)
 def test_arrays_not_accepted(self):self.assertEqual(self.request('/api/studio/profile',[])[0],400)
 def test_repro_catalog_actual_http(self):
  status,raw=self.request('/api/studio/catalog?project=studio-selfplay&track=reproduction');self.assertEqual(status,200);self.assertEqual(json.loads(raw)['main_iteration_count'],0)
 def test_cross_scope_report_forbidden_save(self):
  status,_=self.request('/api/studio/report',{'id':'bad','name':'bad','a':'studio-repro-run-eval100','b':'studio-run-eval100'});self.assertEqual(status,400)
 def test_all_urls_loopback_only(self):
  self.assertEqual(self.server.server_address[0],'127.0.0.1')

if __name__=='__main__':unittest.main()
