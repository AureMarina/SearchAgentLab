"""Regression tests for immutable method provenance and controlled display semantics."""
import copy,json,tempfile,threading,unittest,urllib.request,urllib.error,subprocess,sys
from pathlib import Path
from searchagent_lab.core import ValidationError,NotFoundError
from searchagent_lab.methods import MethodRecorder
from searchagent_lab.methods.schema import SCHEMA
from searchagent_lab.methods.skills import adapt_bank,member,compare_banks
from searchagent_lab.methods.diagnostics import visibility_evidence
from searchagent_lab.inspection.repository import digest
from searchagent_lab.server import LabServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples'))
from seed_methods_demo import seed

def load(name):return json.loads((ROOT/'examples/methods'/name).read_text())
def obj(id='x',kind='trace',**kw):return {'id':id,'kind':kind,'data':kw}
class MethodStoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.rec=MethodRecorder(self.tmp.name);self.r=self.rec.repo
 def tearDown(self):self.rec.close();self.tmp.cleanup()
 def bundle(self,*items,**kw):return self.r.import_bundle({'method_schema':SCHEMA,'objects':list(items),**kw})
 def fixture(self,name='selfplay.synthetic.json'):return self.r.import_bundle(load(name))
 def project(self,p='p'):
  self.rec.store.register_project({'id':p,'name':p});self.rec.store.register_experiment({'id':p+'-e','project_id':p,'name':'E'});self.rec.store.register_run({'id':p+'-r','experiment_id':p+'-e','name':'R'})
 def test_native_bank_preserves_original_bytes(self):
  raw=b'\xef\xbb\xbf'+json.dumps(load('skillbank_v1.synthetic.json'),ensure_ascii=False,indent=3).encode();res=self.r.import_bytes(raw,'bank.json');self.assertEqual(self.r.original(res['import_id'])['content'],raw)
 def test_native_bank_no_runs_or_evaluations_created(self):
  self.r.import_bytes(json.dumps(load('skillbank_v1.synthetic.json')).encode(),'bank.json');self.assertEqual(self.rec.store.runs(),[])
 def test_native_bank_synthetic_marker_retained(self):
  res=self.r.import_bytes(json.dumps(load('skillbank_v1.synthetic.json')).encode(),'bank.json');self.assertTrue(self.r.object(res['snapshot_id'])['demo'])
 def test_native_fields_and_unknown_data_not_discarded(self):
  b=load('skillbank_v1.synthetic.json');v=adapt_bank(b);self.assertEqual(v['skill_count'],3);self.assertEqual(v['mistake_count'],1);self.assertIn('custom_extension',b)
 def test_search_query_type_group_supported(self):self.assertEqual(member(load('skillbank_v1.synthetic.json'),'syn_rel')['group'],'query_type_skills')
 def test_task_specific_group_supported(self):
  b={'task_specific_skills':{'cat':[{'skill_id':'q','title':'q'}]}};self.assertEqual(member(b,'q')['category'],'cat')
 def test_no_id_not_invented(self):
  m=adapt_bank({'general_skills':[{'title':'a'}]})['members'][0];self.assertIsNone(m['source_id']);self.assertEqual(m['pointer'],'/general_skills/0')
 def test_repeated_import_idempotent(self):
  a=self.fixture();b=self.fixture();self.assertEqual(a['import_id'],b['import_id']);self.assertEqual(len(self.r.objects()),20)
 def test_same_id_changed_text_rejected(self):
  self.bundle(obj(a=1));
  with self.assertRaises(ValidationError):self.bundle(obj(a=2))
  self.assertEqual(self.r.object('x')['data'],{'a':1})
 def test_invalid_bundle_rolls_back_every_object(self):
  b=load('selfplay.synthetic.json');b['objects'][-1]['data']['consumption_ids']=['missing']
  with self.assertRaises(NotFoundError):self.r.import_bundle(b)
  self.assertEqual(len(self.r.objects()),0)
 def test_forward_references_are_resolved(self):
  b=load('selfplay.synthetic.json');b['objects'].reverse();self.r.import_bundle(b);self.assertEqual(self.r.cycle('sp-round-003')['counts']['attempts'],3)
 def test_duplicate_object_id_rejected(self):
  with self.assertRaises(ValidationError):self.bundle(obj(),obj())
 def test_jsonl_line_error_not_silently_dropped(self):
  raw=(json.dumps({'method_schema':SCHEMA,'object':obj()})+'\n{broken').encode()
  with self.assertRaisesRegex(ValidationError,'line 2'):self.r.import_bytes(raw,'events.jsonl')
  self.assertFalse(self.r.objects())
 def test_plain_trace_not_guessed_as_method_event(self):
  with self.assertRaisesRegex(ValidationError,'Plain traces'):self.r.import_bytes(b'{"steps": []}\n','traces.jsonl')
 def test_duplicate_json_keys_rejected(self):
  with self.assertRaises(ValidationError):self.r.import_bytes(b'{"general_skills":[],"general_skills":[]}','bank.json')
 def test_declared_scope_not_erased_by_empty_ui_selection(self):
  self.project();b={'method_schema':SCHEMA,'scope':{'run_id':'p-r'},'objects':[obj()]};self.r.import_bundle(b,scope={});self.assertEqual(self.r.object('x')['scope']['project_id'],'p')
 def test_declared_conflicting_scope_rejected(self):
  self.project();self.project('q');b={'method_schema':SCHEMA,'scope':{'run_id':'p-r'},'objects':[obj()]}
  with self.assertRaises(ValidationError):self.r.import_bundle(b,scope={'project_id':'q'})
 def test_nonexistent_scope_rejected(self):
  with self.assertRaises(ValidationError):self.r.import_bundle({'method_schema':SCHEMA,'scope':{'project_id':'missing'},'objects':[obj()]})
 def test_project_catalog_excludes_other_projects_and_standalone(self):
  self.project();self.project('q');self.bundle(obj());self.r.import_bundle({'method_schema':SCHEMA,'scope':{'run_id':'p-r'},'objects':[obj('p1')]});self.r.import_bundle({'method_schema':SCHEMA,'scope':{'run_id':'q-r'},'objects':[obj('q1')]})
  self.assertEqual([x['id'] for x in self.r.catalog()['objects']],['x']);self.assertEqual([x['id'] for x in self.r.catalog({'project_id':'p'})['objects']],['p1'])
 def test_cross_project_sources_must_be_shareable(self):
  self.project();self.project('q');self.bundle({**obj('q1'),'scope':{'run_id':'q-r'}})
  with self.assertRaises(ValidationError):self.bundle({**obj('p1'),'scope':{'run_id':'p-r'},'derived_from':['q1']})
 def test_explicit_shared_source_does_not_merge_results(self):
  self.project();self.project('q');self.bundle({**obj('q1'),'scope':{'run_id':'q-r'},'shareable':True});self.bundle({**obj('p1'),'scope':{'run_id':'p-r'},'derived_from':['q1']});self.assertEqual(len(self.r.catalog({'project_id':'p'})['objects']),1)
 def test_bad_evidence_hash_rejected(self):
  self.bundle(obj('source',a=1))
  with self.assertRaisesRegex(ValidationError,'hash mismatch'):self.bundle({**obj(), 'evidence_refs':[{'object_id':'source','sha256':'f'*64}]})
 def test_bad_evidence_pointer_rejected(self):
  self.bundle(obj('source',a=1))
  with self.assertRaises(ValidationError):self.r.resolve_ref({'object_id':'source','pointer':'/absent'})
 def test_evidence_pointer_special_characters(self):
  self.bundle(obj(**{'a/b':{'x~y':'yes'}}));self.assertEqual(self.r.resolve_ref({'object_id':'x','pointer':'/a~1b/x~0y'})['value'],'yes')
 def test_ambiguous_evidence_ref_rejected(self):
  with self.assertRaises(ValidationError):self.r.resolve_ref({'object_id':'x','trajectory':'y'})
 def test_unknown_namespaced_extension_preserved(self):
  self.bundle(obj(kind='extension.entropy.v1',mask=[1,0],definition={'unit':'nats'}));self.assertEqual(self.r.object('x')['data']['mask'],[1,0])
 def test_unknown_plain_kind_rejected(self):
  with self.assertRaises(ValidationError):self.bundle(obj(kind='whatever'))
 def test_cycle_has_both_actor_directions(self):
  self.fixture();c=self.r.cycle('sp-round-003');self.assertEqual({q['proposals'][0]['data']['actor_id'] for q in c['questions']},{'agent-a','agent-b'})
 def test_answers_proposed_verified_and_grading_separate(self):
  self.fixture();c=self.r.cycle('sp-round-003');bad=c['questions'][1];self.assertEqual(bad['review_status'],'rejected');self.assertEqual(bad['question']['data']['proposed_answer'],'1978');self.assertEqual(bad['attempts'][0]['data']['scores']['em'],0)
 def test_selected_and_sampled_counts_not_added_as_two_questions(self):
  self.fixture();c=self.r.cycle('sp-round-003')['counts'];self.assertEqual(c['sampled_questions'],1);self.assertEqual(c['selected_questions'],1);self.assertEqual(c['rejected'],1)
 def test_updates_link_sampled_batches_and_checkpoint(self):
  self.fixture();u=self.r.cycle('sp-round-003')['updates'][0];self.assertEqual(u['data']['output_checkpoint_id'],'sp-cp-100');self.assertEqual(u['data']['consumption_ids'],['sp-use-017-sampled'])
 def test_actor_must_belong_to_cycle(self):
  b=load('selfplay.synthetic.json');next(o for o in b['objects'] if o['kind']=='solver_attempt')['data']['actor_id']='invented'
  with self.assertRaises(ValidationError):self.r.import_bundle(b)
 def test_verified_answer_requires_review_identity(self):
  with self.assertRaises(ValidationError):self.bundle(obj(kind='question_review',question_id='q',status='accepted',verified_answer='x'))
 def test_review_supersedes_cycle_rejected(self):
  self.bundle(obj('q','generated_question',question='q'))
  with self.assertRaises(ValidationError):self.bundle(obj('a','question_review',question_id='q',status='needs_review',supersedes='b'),obj('b','question_review',question_id='q',status='needs_review',supersedes='a'))
 def test_missing_skill_ids_do_not_positionally_pair(self):
  self.assertFalse(compare_banks({'general_skills':[{'title':'A'}]},{'general_skills':[{'title':'B'}]})['comparable'])
 def test_reward_not_converted_to_em(self):
  self.fixture();a=self.r.object('sp-attempt-017-1');a={k:v for k,v in a.items() if k not in ('hash','content_hash','imported_at')};a['id']='attempt-new';a['data'].pop('scores');a['data']['reward']=1;self.bundle(a);self.assertNotIn('scores',self.r.object('attempt-new')['data'])
 def test_sampled_needs_batch(self):
  with self.assertRaises(ValidationError):self.bundle(obj(kind='training_use',artifact_ids=['x'],status='sampled'))
 def test_lineage_cycles_rejected_atomically(self):
  with self.assertRaises(ValidationError):self.bundle({**obj('a'),'derived_from':['b']},{**obj('b'),'derived_from':['a']})
  self.assertFalse(self.r.objects())
 def test_validation_source_traced_through_derived_skill(self):
  self.bundle({**obj('gt'),'usage':'validation'},{**obj('derived','skill_bank',general_skills=[]),'derived_from':['gt'],'usage':'evolution'},obj('use','training_use',artifact_ids=['derived'],status='sampled',batch_id='b'))
  self.assertEqual(self.r.lineage('derived')['restricted'][0]['id'],'gt');self.assertIn('restricted_source',[x['rule'] for x in self.r.diagnostics('use')['findings']])
 def test_unknown_source_usage_not_certified_clean(self):
  self.bundle(obj());self.assertFalse(self.r.lineage('x')['training_export_ready'])
 def test_bank_snapshots_distinct_and_diff_exact(self):
  self.fixture('skillrl.synthetic.json');d=self.r.diff('skill-bank-v1','skill-bank-v2');self.assertEqual(d['counts'],{'added':1,'removed':1,'modified':1,'unchanged':2})
 def test_duplicate_skill_ids_not_arbitrarily_resolved(self):
  b={'general_skills':[{'skill_id':'a','title':'x'},{'skill_id':'a','title':'y'}]}
  with self.assertRaises(ValidationError):member(b,'a')
  self.assertFalse(compare_banks(b,b)['comparable'])
 def test_literal_injection_matches_actual_input_only(self):
  self.fixture('skillrl.synthetic.json');u=self.r.skill_usage('skill-bank-v1','syn_chain');self.assertEqual(u['counts']['fragment_verified'],1);self.assertEqual(u['uses'][0]['effect'],'requires_controlled_evaluation')
 def test_selected_is_not_injected(self):
  self.fixture('skillrl.synthetic.json');u=self.r.skill_usage('skill-bank-v1','syn_stop');self.assertEqual(u['counts']['selected'],1);self.assertEqual(u['counts']['injection_recorded'],0)
 def test_missing_fragment_is_candidate_not_root(self):
  self.fixture('skillrl.synthetic.json');f=[f for f in self.r.diagnostics('skill-retrieval-missing')['findings'] if f['rule']=='injection_fragment_missing'];self.assertEqual(f[0]['basis'],'candidate')
 def test_output_text_not_accepted_as_model_input(self):
  self.assertFalse(visibility_evidence({'pointer':'/steps/0/output'}));self.assertFalse(visibility_evidence({'pointer':'/steps/1/tool_response/text'}))
 def test_skill_hash_mismatch_rejected(self):
  b=load('skillrl.synthetic.json');next(o for o in b['objects'] if o['id']=='skill-retrieval-01')['data']['injections'][0]['content_hash']='f'*64
  with self.assertRaises(ValidationError):self.r.import_bundle(b)
 def test_ref_to_skill_absent_from_frozen_bank_rejected(self):
  b=load('skillrl.synthetic.json');next(o for o in b['objects'] if o['id']=='skill-retrieval-01')['data']['selected']=['missing']
  with self.assertRaises(ValidationError):self.r.import_bundle(b)
 def test_source_trace_is_openable_in_existing_inspector(self):
  self.fixture();d=self.r.document('sp-solve-b-1');self.assertEqual(d['source']['object_kind'],'trace');self.assertEqual(d['raw']['scores']['em'],0);self.assertTrue(d['view'])
 def test_review_history_does_not_mutate_source(self):
  self.bundle(obj(a=1));h=self.r.object('x')['content_hash'];self.r.append_review('x',h,'pending','Check');self.r.append_review('x',h,'rejected','Not supported');self.assertEqual(len(self.r.reviews('x')),2);self.assertEqual(self.r.object('x')['content_hash'],h)
 def test_stale_review_rejected(self):
  self.bundle(obj());
  with self.assertRaises(ValidationError):self.r.append_review('x','a'*64,'confirmed','x')
 def test_export_dependencies_roundtrip(self):
  self.fixture('skillrl.synthetic.json');export=self.r.export('skill-retrieval-01')
  with tempfile.TemporaryDirectory() as p,MethodRecorder(p) as r:
   r.repo.import_bundle(export);self.assertEqual(r.repo.object('skill-bank-v1')['content_hash'],self.r.object('skill-bank-v1')['content_hash'])
 def test_export_does_not_include_unrelated_other_direction(self):
  self.fixture();e=self.r.export('sp-attempt-017-1');self.assertNotIn('sp-q-018-v1',[x['id'] for x in e['objects']])
 def test_markup_stays_data(self):
  self.bundle(obj(question='<script>bad()</script>'));self.assertEqual(self.r.document('x')['raw']['question'],'<script>bad()</script>')

class MethodEvaluationsTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();seed(self.tmp.name);self.rec=MethodRecorder(self.tmp.name);self.r=self.rec.repo
 def tearDown(self):self.rec.close();self.tmp.cleanup()
 def test_bank_changes_block_policy_only_comparison(self):
  self.assertFalse(self.r.pair_state('sk-eval-0-skill-bank-v1','sk-eval-0-skill-bank-v2')['compatible'])
 def test_same_bank_allows_checkpoint_comparison(self):
  self.assertTrue(self.r.pair_state('sk-eval-0-skill-bank-v1','sk-eval-100-skill-bank-v1')['compatible'])
 def test_core_compare_respects_external_state(self):
  self.assertFalse(self.rec.store.compare('sk-eval-0-skill-bank-v1','sk-eval-0-skill-bank-v2')['compatible'])
 def test_formal_trace_shows_its_evaluation_bank_binding(self):
  t=self.rec.store.export_evaluation('sk-eval-0-skill-bank-v1')['trajectories'][0]
  result=self.r.evidence_for({'trajectory':t['id']})
  self.assertEqual(result['objects'][0]['data']['bank_id'],'skill-bank-v1')
 def test_matrix_retains_four_distinct_cells(self):
  g=self.r.evaluation_matrix('skill-bank-v1')['groups'];self.assertEqual(len(g),1);self.assertEqual(len(g[0]['cells']),4);self.assertFalse(g[0]['causal_claim'])
 def test_binding_cannot_overwrite_existing_evaluation_state(self):
  with self.assertRaises(ValidationError):self.rec.record({'id':'other','kind':'evaluation_binding','scope':{'run_id':'demo-skillrl-s42'},'data':{'evaluation_id':'sk-eval-0-skill-bank-v1','binding_origin':'retrospective','bank_id':'skill-bank-v2'}})
 def test_empty_binding_scope_rejected(self):
  with self.assertRaises(ValidationError):self.rec.record({'id':'other','kind':'evaluation_binding','data':{'evaluation_id':'sk-eval-0-skill-bank-v1','binding_origin':'retrospective','bank_id':'skill-bank-v2'}})
 def test_method_read_does_not_change_original_grades(self):
  before=self.rec.store.evaluation('sk-eval-0-skill-bank-v1')['stats'];self.r.evaluation_matrix('skill-bank-v1');self.r.diagnostics('skill-retrieval-01');self.assertEqual(before,self.rec.store.evaluation('sk-eval-0-skill-bank-v1')['stats'])

class MethodsAPITests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();seed(cls.tmp.name);cls.rec=MethodRecorder(cls.tmp.name);cls.server=LabServer(('127.0.0.1',0),cls.rec.store);cls.t=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.t.start();cls.base=f'http://127.0.0.1:{cls.server.server_address[1]}'
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.rec.close();cls.tmp.cleanup()
 def request(self,path,data=None,headers=None):
  try:r=urllib.request.urlopen(urllib.request.Request(self.base+path,data=data,headers=headers or {}))
  except urllib.error.HTTPError as e:r=e
  return r.status,r.read()
 def token(self):return json.loads(self.request('/api/session')[1])['csrf_token']
 def post(self,path,value):return self.request(path,json.dumps(value).encode(),{'Content-Type':'application/json','X-Local-Token':self.token()})
 def test_default_catalog_unassigned_only(self):self.assertEqual(json.loads(self.request('/api/methods/catalog')[1])['objects'],[])
 def test_scoped_catalog_returns_selfplay_not_skillrl(self):
  d=json.loads(self.request('/api/methods/catalog?project_id=demo-selfplay')[1]);self.assertTrue(all(x['scope']['project_id']=='demo-selfplay' for x in d['objects']))
 def test_method_routes_read_actual_data(self):self.assertEqual(json.loads(self.request('/api/methods/cycle?id=sp-round-003')[1])['counts']['attempts'],3)
 def test_native_skill_upload_uses_local_csrf(self):
  code,_=self.request('/api/methods/upload?name=b.json',json.dumps(load('skillbank_v1.synthetic.json')).encode(),{'Content-Type':'application/octet-stream'});self.assertEqual(code,403)
 def test_static_modules_included(self):
  for path in ['methods/workspace.js','methods/evidence-panel.js','methods/common.js','methods/methods.css']:
   with self.subTest(path=path):self.assertEqual(self.request('/'+path)[0],200)
 def test_method_source_opens_in_existing_inspection_api(self):
  code,d=self.request('/api/inspection/record?method=sp-solve-b-1');self.assertEqual(code,200);self.assertEqual(json.loads(d)['source']['object_id'],'sp-solve-b-1')
 def test_combined_case_is_retrospective_and_hash_bound(self):
  code,d=self.post('/api/methods/diagnostic-case',{'scope':{'project_id':'demo-selfplay'},'evidence':[{'object_id':'sp-q-017-v1','pointer':'/question'},{'object_id':'sp-solve-b-1','pointer':''}]});self.assertEqual(code,201);oid=json.loads(d)['case_id'];o=self.server.methods.object(oid);self.assertEqual(o['data']['mode'],'retrospective');self.assertTrue(o['demo']);self.assertTrue(all(len(x['ref']['sha256'])==64 for x in o['data']['evidence']))
 def test_cross_project_case_cannot_silently_join(self):
  code,_=self.post('/api/methods/diagnostic-case',{'scope':{'project_id':'demo-selfplay'},'evidence':[{'object_id':'skill-bank-v1'}]});self.assertEqual(code,400)

class MethodCLITests(unittest.TestCase):
 def test_public_sdk_import(self):
  from searchagent_lab import MethodRecorder as PublicRecorder
  self.assertIs(PublicRecorder,MethodRecorder)

 def test_import_and_export_cli(self):
  with tempfile.TemporaryDirectory() as d:
   path=ROOT/'examples/methods/skillbank_v1.synthetic.json'
   r=subprocess.run([sys.executable,'-m','searchagent_lab','import-methods',str(path),'--snapshot-id','b1','--data-dir',d],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
   out=Path(d)/'out.json';r=subprocess.run([sys.executable,'-m','searchagent_lab','export-method','--id','b1','--out',str(out),'--data-dir',d],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(out.read_text())['method_schema'],SCHEMA)
 def test_methods_no_serve_offline(self):
  with tempfile.TemporaryDirectory() as d:
   r=subprocess.run([sys.executable,'-m','searchagent_lab','methods',str(ROOT/'examples/methods/selfplay.synthetic.json'),'--no-serve','--data-dir',d],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)

if __name__=='__main__':unittest.main()
