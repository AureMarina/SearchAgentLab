"""Independent acceptance tests for the new 0.4 diagnosis/verification layer."""
import unittest,tempfile,json,copy,threading,urllib.request,urllib.error
from pathlib import Path
from searchagent_lab.inspection.repository import InspectionRepository,digest
from searchagent_lab.diagnostic_loop.repository import LoopRepository
from searchagent_lab.diagnostic_loop.contracts import contract_template,schema_check
from searchagent_lab.diagnostic_loop.evidence import compare_context,context_of,lineage
from searchagent_lab.diagnostic_loop.scorecards import scorecard
from searchagent_lab.diagnostic_loop.verification import component,regression,reliability,validate_external_result
from searchagent_lab.diagnostic_loop.judge import make_packet,validate_response
from searchagent_lab.diagnostic_loop.benchmark import assess
from searchagent_lab.core import ValidationError
from searchagent_lab.store import Store
from searchagent_lab.server import LabServer


def ctx():
 return {'checkpoint_id':'fixture-cp','weights_sha256':'a'*64,'base_model':'synthetic','model_revision':'fixture-v1',
  'panel_hash':'b'*64,'prompt':{'version':'p1'},'chat_template':{'version':'t1'},'tokenizer':{'version':'t1'},
  'tool_schema':{'version':'s1'},'retriever':{'snapshot':'synthetic'},'context_builder':{'version':'v1'},
  'parser':{'version':'p1'},'scorer':{'version':'s1'},'budget':{'max_search':4},'decoding':{'temperature':0},
  'seed':42,'runtime':{'version':'fixture-runtime'},'state_reset':{'per_question':True},'harness_version':'fixture'}

def raw():
 return {'id':'fixture-1','dataset':'SYNTHETIC','question':'Synthetic fictional workplace?', 'gold_answers':['Copper Harbor'],
  'demo':True,'trace_complete':True,'scores':{'em':0},'raw_final_output':'<answer>Copper Harbor</answer>',
  'parsed_answer':'Other Place','runtime_status':'ok','execution_context':ctx(),
  'steps':[{'index':0,'kind':'model','input':[{'role':'user','content':'Synthetic task, do a search.'}],'output':'Search for fictional place.'},
    {'index':1,'kind':'tool','tool_call':{'id':'call-1','name':'search','arguments':{'query':'fictional place'}},
     'tool_call_id':'call-1','tool_response':{'documents':[{'text':'Copper Harbor'}]},'model_visible':'No location in this excerpt.',
     'context_transform':{'source_pointer':'/steps/1/tool_response','target_pointer':'/steps/1/model_visible','version':'v1'},'retrieval_ms':27},
    {'index':2,'kind':'model','input':[{'role':'tool','content':'No location in this excerpt.'}], 'output':'<answer>Copper Harbor</answer>'}]}

class Base(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.repo=InspectionRepository(self.tmp.name);self.loop=LoopRepository(self.tmp.name)
  self.original=(json.dumps(raw())+'\n').encode();self.file=self.repo.import_bytes(self.original,'fixture.jsonl');self.doc=self.repo.record(self.file['id'],1);self.hash=self.doc['source_hash']
 def tearDown(self):self.repo.close();self.loop.close();self.tmp.cleanup()
 def h(self):return self.loop.add_hypothesis(self.doc,{'source_hash':self.hash,'hypothesis':{'title':'Parser may be wrong','layer':'Outcome','component':'parser','observation':'Raw answer and parsed answer differ.','evidence':['/raw_final_output','/parsed_answer'],'alternatives':['Scorer or alias mapping could also be wrong.'],'minimal_test':'Hold raw output fixed, compare parsers.'}})
 def task(self,mode='component',allowed=None):
  h=self.h();task={'hypothesis_id':h['id'],'title':'Test parser','mode':mode,'change_candidate':'Use matching output tags.','expected_observation':'Parsed output changes; not a model gain.','allowed_changes':allowed or [],'required_paths':[]}
  if mode=='component':task['component']={'operation':'answer_parser','input_pointer':'/raw_final_output','baseline':{'mode':'strip'},'candidate':{'mode':'answer_tag','tag':'answer'}}
  else:
   task.update(allowed_changes=allowed or ['/context_builder'],required_paths=['/checkpoint_id','/panel_hash','/prompt','/retriever','/parser','/scorer','/budget','/runtime'],expected_cases=[{'dataset':'SYNTHETIC','sample_id':'fixture-1'}])
   if mode=='prefix':task.update(prefix_pointer='/steps/1',restore_mode='approximate_text_reconstruction')
  return self.loop.add_task(self.doc,{'source_hash':self.hash,'task':task})
 def result(self,task):
  a={'evaluation_id':'eval-a','trace_sha256':'a'*64,'context':ctx(),'cases':[{'dataset':'SYNTHETIC','sample_id':'fixture-1','em':0}], 'from_empty_state':True,'groundtruth_assisted':False}
  b=copy.deepcopy(a);b['evaluation_id']='eval-b';b['trace_sha256']='b'*64;b['cases'][0]['em']=1;b['context']['context_builder']['version']='v2'
  return {'task_id':task['id'],'task_hash':task['task_hash'],'execution':'executed_externally','baseline':a,'candidate':b}

class ContractTests(Base):
 def test_layers_and_original_score(self):
  s=scorecard(self.doc);self.assertEqual([x['name'] for x in s['layers']],['Outcome','Decision','Action','Reliability']);self.assertEqual(s['layers'][0]['status'],'fail');self.assertEqual(self.doc['raw']['scores']['em'],0)
 def test_no_mandatory_search_by_default(self):
  s=scorecard(self.doc);self.assertEqual(next(x for x in s['checks'] if x['id']=='required_tools')['status'],'not_applicable')
 def test_missing_score_not_reward(self):
  d=copy.deepcopy(self.doc);d['view']['scores']={};d['view']['reward']=1;s=scorecard(d);self.assertEqual(s['checks'][0]['status'],'insufficient')
 def test_completed_not_ready(self):
  s=scorecard(self.doc);self.assertIsNone(s['gate']['release_ready']);self.assertFalse(s['gate']['enforced'])
 def test_immutable_named_contract_version(self):
  c=contract_template();self.loop.bind_contract(self.doc,{'source_hash':self.hash,'contract':c});c['required_tools']=['search']
  with self.assertRaises(ValidationError):self.loop.bind_contract(self.doc,{'source_hash':self.hash,'contract':c})
 def test_new_contract_version_does_not_erase(self):
  c=contract_template();self.loop.bind_contract(self.doc,{'source_hash':self.hash,'contract':c});c['version']='v2';self.loop.bind_contract(self.doc,{'source_hash':self.hash,'contract':c});self.assertEqual(len(self.loop.workspace(self.doc)['contract_history']),2)
 def test_preexecution_binding_needs_actual_hash(self):
  with self.assertRaises(ValidationError):self.loop.bind_contract(self.doc,{'source_hash':self.hash,'contract':contract_template(),'binding':'recorded_at_execution'})
 def test_no_call_and_incomplete_trace_is_unknown(self):
  d=copy.deepcopy(self.doc);d['raw']['trace_complete']=False;c=contract_template();c['required_tools']=['not_called'];s=scorecard(d,c)
  self.assertEqual(next(x for x in s['checks'] if x['id']=='required_tools:not_called')['status'],'insufficient')
 def test_no_call_and_complete_trace_is_failure(self):
  c=contract_template();c['required_tools']=['not_called'];s=scorecard(self.doc,c);self.assertEqual(next(x for x in s['checks'] if x['id']=='required_tools:not_called')['status'],'fail')
 def test_forbidden_call_is_recorded_violation(self):
  c=contract_template();c['forbidden_tools']=['search'];s=scorecard(self.doc,c);self.assertEqual(next(x for x in s['checks'] if x['id']=='forbidden_tools:search')['status'],'fail')
 def test_missing_assertion_not_auto_fail(self):
  c=contract_template();c['assertions']=[{'id':'missing','layer':'Action','pointer':'/not_saved','op':'exists'}];s=scorecard(self.doc,c);self.assertEqual(next(x for x in s['checks'] if x['id']=='assertion:missing')['status'],'insufficient')
 def test_numeric_assertion(self):
  c=contract_template();c['assertions']=[{'id':'ms','layer':'Action','pointer':'/steps/1/retrieval_ms','op':'number_close','expected':25,'tolerance':1}];s=scorecard(self.doc,c);self.assertEqual(next(x for x in s['checks'] if x['id']=='assertion:ms')['status'],'fail')
 def test_output_tag_contract(self):
  c=contract_template();c['output']={'format':'answer_tag'};s=scorecard(self.doc,c);self.assertEqual(next(x for x in s['checks'] if x['id']=='output_contract')['status'],'pass')
 def test_invalid_json_contract(self):
  c=contract_template();c['output']={'format':'json'};s=scorecard(self.doc,c);self.assertEqual(next(x for x in s['checks'] if x['id']=='output_contract')['status'],'fail')
 def test_unconfigured_gate_not_pass(self):
  c=contract_template();c['hard_gates']=['unknown-rule'];self.assertEqual(scorecard(self.doc,c)['gate']['status'],'insufficient')
 def test_contract_stale_source_rejected(self):
  with self.assertRaises(ValidationError):self.loop.bind_contract(self.doc,{'source_hash':'wrong','contract':contract_template()})
 def test_conflicting_tools_rejected(self):
  c=contract_template();c.update(required_tools=['search'],forbidden_tools=['search'])
  with self.assertRaises(ValidationError):self.loop.bind_contract(self.doc,{'source_hash':self.hash,'contract':c})
 def test_reference_not_agent_failure(self):
  f=self.repo.import_bytes(b'{"question":"reference","answers":["x"]}\n','reference.jsonl');s=scorecard(self.repo.record(f['id'],1));self.assertTrue(all(x['status']=='not_applicable' for x in s['layers']))
 def test_unknown_schema_keyword_does_not_pass(self):self.assertEqual(schema_check('abc',{'type':'string','pattern':'abc'})['status'],'insufficient')
 def test_bool_not_number(self):self.assertEqual(schema_check(True,{'type':'number'})['status'],'fail')
 def test_enum_numeric_equivalence(self):self.assertEqual(schema_check(1,{'enum':[1.0]})['status'],'pass')
 def test_enum_bool_distinct(self):self.assertEqual(schema_check(True,{'enum':[1]})['status'],'fail')
 def test_invalid_bound_unknown(self):self.assertEqual(schema_check('x',{'minLength':-1})['status'],'insufficient')
 def test_schema_additional_fields(self):self.assertEqual(schema_check({'x':1},{'properties':{},'additionalProperties':False})['status'],'fail')
 def test_schema_required_field(self):self.assertEqual(schema_check({}, {'type':'object','required':['query']})['status'],'fail')
 def test_schema_no_remote_ref(self):self.assertEqual(schema_check({}, {'$ref':'https://localhost/private'})['status'],'insufficient')

class HypothesisTests(Base):
 def test_hypothesis_defaults_unverified(self):self.assertEqual(self.h()['state'],'unverified')
 def test_bad_evidence_rejected(self):
  h=self.h();h['evidence']=['/does_not_exist']
  with self.assertRaises(ValidationError):self.loop.add_hypothesis(self.doc,{'source_hash':self.hash,'hypothesis':h})
 def test_competing_explanation_required(self):
  h=self.h();h['alternatives']=[]
  with self.assertRaises(ValidationError):self.loop.add_hypothesis(self.doc,{'source_hash':self.hash,'hypothesis':h})
 def test_rule_becomes_unverified_hypothesis(self):
  f=next(x for x in self.doc['report']['findings'] if x['rule']=='parser_mismatch');h=self.loop.hypothesis_from_rule(self.doc,{'source_hash':self.hash,'finding_id':f['id']});self.assertEqual(h['component'],'parser');self.assertEqual(h['state'],'unverified')
 def test_hypothesis_cannot_confirm_without_result(self):
  h=self.h()
  with self.assertRaises(Exception):self.loop.review_hypothesis(self.doc,{'source_hash':self.hash,'hypothesis_id':h['id'],'status':'supported_by_experiment','note':'guess'})
 def test_hypothesis_review_history(self):
  h=self.h()
  for st in ('inconclusive','unverified'):self.loop.review_hypothesis(self.doc,{'source_hash':self.hash,'hypothesis_id':h['id'],'status':st,'note':'More evidence needed.'})
  self.assertEqual(len(self.loop.events(h['id'])),2);self.assertEqual(self.loop.object(h['id'])['state'],'unverified')
 def test_cross_source_object_rejected(self):
  h=self.h();d=copy.deepcopy(self.doc);d['source_hash']='other'
  with self.assertRaises(ValidationError):self.loop.object(h['id'],d)
 def test_sidecar_persists(self):
  h=self.h();self.loop.close();self.loop=LoopRepository(self.tmp.name);self.assertEqual(self.loop.object(h['id'])['title'],h['title'])
 def test_original_bytes_unchanged(self):
  h=self.h();t=self.task();self.loop.execute(self.doc,{'source_hash':self.hash,'task_id':t['id']});self.assertEqual(self.repo.original(self.file['id']),self.original)

class EvidenceTests(Base):
 def test_lineage_explicit_ids_and_transform(self):
  l=lineage(self.doc);self.assertTrue(any(x['basis']=='explicit_call_id' for x in l['links']));self.assertTrue(any(x['basis']=='explicit_pointer' for x in l['links']))
 def test_time_segments_preserved_not_summed(self):self.assertEqual(lineage(self.doc)['timings'][0]['value_ms'],27)
 def test_absent_versions_marked_missing(self):
  d=copy.deepcopy(self.doc);d['raw'].pop('execution_context');self.assertIn('checkpoint_id',context_of(d)['missing'])
 def test_allowed_change_only(self):
  a=ctx();b=ctx();b['context_builder']['version']='v2';r=compare_context(a,b,['/context_builder']);self.assertTrue(r['eligible']);self.assertTrue(r['expected_change_observed'])
 def test_extra_drift_blocks(self):
  a=ctx();b=ctx();b['budget']['max_search']=7;r=compare_context(a,b,['/context_builder']);self.assertFalse(r['eligible'])
 def test_same_unknown_is_not_comparable(self):
  a=ctx();a['runtime']='unknown';self.assertFalse(compare_context(a,a,[])['eligible'])
 def test_broad_root_change_rejected(self):
  with self.assertRaises(ValidationError):compare_context(ctx(),ctx(),['/'])
 def test_empty_dict_not_version(self):
  a=ctx();a['parser']={};self.assertFalse(compare_context(a,a,[])['eligible'])

class VerificationTests(Base):
 def test_task_planned_until_execute(self):t=self.task();self.assertEqual(t['state'],'planned');self.assertEqual(self.loop.objects(self.doc,'result'),[])
 def test_component_executes_actual_pure_functions(self):
  t=self.task();r=self.loop.execute(self.doc,{'source_hash':self.hash,'task_id':t['id']});self.assertEqual(r['candidate']['parsed'],'Copper Harbor');self.assertTrue(r['changed']);self.assertIsNone(r['autonomous_uplift'])
 def test_component_input_cannot_change(self):
  t=self.task();d=copy.deepcopy(self.doc);d['raw']['raw_final_output']='changed'
  with self.assertRaises(ValidationError):self.loop.execute(d,{'source_hash':self.hash,'task_id':t['id']})
 def test_no_remote_task_execution(self):
  t=self.task('autonomous')
  with self.assertRaises(ValidationError):self.loop.execute(self.doc,{'source_hash':self.hash,'task_id':t['id']})
 def test_unknown_local_operation_rejected(self):
  with self.assertRaises(ValidationError):component('x','shell',{'command':'rm'})
 def test_answer_parser_multiple_tags_fail(self):self.assertEqual(component('<answer>a</answer><answer>b</answer>','answer_parser',{'mode':'answer_tag'})['status'],'fail')
 def test_json_pointer_parser(self):self.assertEqual(component('{"answer":"x"}','answer_parser',{'mode':'json_pointer','pointer':'/answer'})['parsed'],'x')
 def test_renderer_char_budget_explicit(self):
  r=component([{'text':'abc'},{'text':'xyz'}],'context_render',{'max_chars':2,'max_documents':1,'selection':'last'});self.assertEqual(r['rendered'],'xy');self.assertIn('NOT tokens',r['budget_unit'])
 def test_tool_schema_input_checked(self):self.assertEqual(component('{"x":3}','tool_schema',{'schema':{'required':['query']}})['status'],'fail')
 def test_extra_component_config_rejected(self):
  with self.assertRaises(ValidationError):component('x','answer_parser',{'mode':'strip','execute':'cmd'})
 def test_external_hash_mismatch(self):
  t=self.task('autonomous');r=self.result(t);r['task_hash']='bad'
  with self.assertRaises(ValidationError):validate_external_result(t,r)
 def test_valid_external_controlled_change(self):
  t=self.task('autonomous');r=validate_external_result(t,self.result(t));self.assertTrue(r['autonomous_comparison_eligible']);self.assertEqual(r['regression']['counts']['repaired'],1)
 def test_synthetic_results_not_real_uplift(self):
  t=self.task('autonomous');r=self.loop.import_result(self.doc,{'source_hash':self.hash,'task_id':t['id'],'result':self.result(t)});self.assertTrue(r['report']['synthetic']);self.assertFalse(r['report']['autonomous_comparison_eligible'])
 def test_external_assistance_not_autonomous(self):
  t=self.task('autonomous');r=self.result(t);r['candidate']['groundtruth_assisted']=True;self.assertFalse(validate_external_result(t,r)['autonomous_comparison_eligible'])
 def test_prefix_never_autonomous(self):
  t=self.task('prefix');self.assertFalse(validate_external_result(t,self.result(t))['autonomous_comparison_eligible'])
 def test_measurement_change_not_model_gain(self):
  t=self.task('autonomous',['/scorer']);r=self.result(t);r['candidate']['context']=ctx();r['candidate']['context']['scorer']['version']='s2'
  rep=validate_external_result(t,r);self.assertTrue(rep['measurement_changed']);self.assertFalse(rep['autonomous_comparison_eligible'])
 def test_both_arms_drift_from_frozen_baseline(self):
  t=self.task('autonomous');r=self.result(t)
  for name in ('baseline','candidate'):r[name]['context']['prompt']['version']='new'
  rep=validate_external_result(t,r);self.assertFalse(rep['comparison']['eligible'])
 def test_same_eval_record_cannot_compare_itself(self):
  t=self.task('autonomous');r=self.result(t);r['candidate']['evaluation_id']='eval-a'
  with self.assertRaises(ValidationError):validate_external_result(t,r)
 def test_missing_cases_not_zero(self):
  t=self.task('autonomous');r=self.result(t);r['candidate']['cases']=[];rep=validate_external_result(t,r);self.assertFalse(rep['autonomous_comparison_eligible']);self.assertIsNone(rep['regression']['macro_delta'])
 def test_unknown_em_not_zero(self):
  keys=[{'dataset':'d','sample_id':'1'}];r=regression([{**keys[0],'em':None}],[{**keys[0],'em':1}],keys);self.assertEqual(r['counts']['unscored'],1)
 def test_duplicate_cases_rejected(self):
  keys=[{'dataset':'d','sample_id':'1'}]
  with self.assertRaises(ValidationError):regression([{**keys[0],'em':1}]*2,[],keys)
 def test_outside_manifest_rejected(self):
  with self.assertRaises(ValidationError):regression([{'dataset':'d','sample_id':'2','em':1}],[],[{'dataset':'d','sample_id':'1'}])
 def test_macro_not_pooled(self):
  keys=[{'dataset':'a','sample_id':'1'},{'dataset':'b','sample_id':'1'},{'dataset':'b','sample_id':'2'}];a=[{**k,'em':int(k['dataset']=='a')} for k in keys];r=regression(a,a,keys);self.assertEqual(r['macro_baseline'],.5)
 def test_fix_acceptance_needs_result(self):
  t=self.task()
  with self.assertRaises(ValidationError):self.loop.record_fix(self.doc,{'source_hash':self.hash,'task_id':t['id'],'decision':'accept_for_further_evaluation','patch_ref':'p','note':'guess'})
 def test_fix_does_not_apply_or_publish(self):
  t=self.task();r=self.loop.execute(self.doc,{'source_hash':self.hash,'task_id':t['id']});f=self.loop.record_fix(self.doc,{'source_hash':self.hash,'task_id':t['id'],'decision':'accept_for_further_evaluation','patch_ref':'commit-fixture','note':'needs regression','result_id':r['id']});self.assertFalse(f['data']['applied']);self.assertFalse(f['data']['published'])

class JudgeTests(Base):
 def response(self,p):
  return {'packet_hash':p['packet_hash'],'judge':{'model':'fixture judge','revision':'fixture','prompt_version':p['prompt_version']},'judgements':[{'dimension':'evidence_support','verdict':'contradicted','explanation':'Synthetic example','evidence':[{'pointer':'/steps/0/output','quote':'Search for fictional place.'}],'alternatives':['Not yet searched.'],'next_check':'Check returned evidence.'}]}
 def test_prefix_excludes_gt_score_future_and_raw_retrieval(self):
  p=make_packet(self.doc,'decision_prefix','/steps/0');s=json.dumps(p['evidence']);self.assertNotIn('Copper Harbor',s);self.assertNotIn('gold_answers',s);self.assertNotIn('/steps/1',s);self.assertNotIn('scores',s)
 def test_prefix_no_visible_input_rejected(self):
  d=copy.deepcopy(self.doc);d['raw']['steps'][0].pop('input')
  with self.assertRaises(ValidationError):make_packet(d,'decision_prefix','/steps/0')
 def test_retrospective_labels_gt_allowed(self):
  p=make_packet(self.doc,'retrospective');self.assertTrue(any(e['pointer']=='/gold_answers' for e in p['evidence']))
 def test_judge_reference_scope_valid(self):
  p=make_packet(self.doc,'decision_prefix','/steps/0');self.assertFalse(validate_response(p,self.response(p))['causal_claim'])
 def test_judge_future_reference_rejected(self):
  p=make_packet(self.doc,'decision_prefix','/steps/0');r=self.response(p);r['judgements'][0]['evidence'][0]={'pointer':'/steps/2/output','quote':'Copper Harbor'}
  with self.assertRaises(ValidationError):validate_response(p,r)
 def test_judge_fabricated_quote_rejected(self):
  p=make_packet(self.doc,'decision_prefix','/steps/0');r=self.response(p);r['judgements'][0]['evidence'][0]['quote']='invented'
  with self.assertRaises(ValidationError):validate_response(p,r)
 def test_judge_nonabstention_needs_evidence(self):
  p=make_packet(self.doc,'decision_prefix','/steps/0');r=self.response(p);r['judgements'][0]['evidence']=[]
  with self.assertRaises(ValidationError):validate_response(p,r)
 def test_judge_stale_packet(self):
  p=make_packet(self.doc,'decision_prefix','/steps/0');r=self.response(p);r['packet_hash']='wrong'
  with self.assertRaises(ValidationError):validate_response(p,r)
 def test_judge_review_does_not_change_scores(self):
  p=self.loop.add_judge_packet(self.doc,{'source_hash':self.hash,'mode':'decision_prefix','step_pointer':'/steps/0'});r=self.loop.import_judge(self.doc,{'source_hash':self.hash,'packet_id':p['id'],'response':self.response(p)});self.loop.review_judge(self.doc,{'source_hash':self.hash,'judge_result_id':r['id'],'status':'rejected','note':'Synthetic review'});self.assertEqual(self.repo.record(self.file['id'],1)['raw']['scores']['em'],0)

class ReliabilityTests(Base):
 def runs(self,n=3):return [{'run_id':'r'+str(i),'dataset':'SYNTHETIC','sample_id':'fixture-1','context':ctx(),'em':i%2,'independent_reset':True,'latency_ms':100+i*10} for i in range(n)]
 def test_single_is_insufficient(self):self.assertEqual(reliability(self.runs(1),['/runtime'])['status'],'insufficient')
 def test_three_declared_runs_reported(self):self.assertEqual(reliability(self.runs(),['/runtime'])['status'],'reported')
 def test_duplicate_runs_rejected(self):
  r=self.runs();r[1]['run_id']=r[0]['run_id']
  with self.assertRaises(ValidationError):reliability(r,['/runtime'])
 def test_no_reset_insufficient(self):
  r=self.runs();r[1]['independent_reset']=False;self.assertEqual(reliability(r,['/runtime'])['status'],'insufficient')
 def test_different_question_rejected(self):
  r=self.runs();r[1]['sample_id']='other'
  with self.assertRaises(ValidationError):reliability(r,['/runtime'])
 def test_seed_variation_permitted(self):
  r=self.runs();r[1]['context']['seed']=77;self.assertEqual(reliability(r,['/runtime'])['status'],'reported')
 def test_runtime_drift_insufficient(self):
  r=self.runs();r[1]['context']['runtime']['version']='changed';self.assertEqual(reliability(r,['/runtime'])['status'],'insufficient')
 def test_latency_counts_and_p90(self):
  r=reliability(self.runs(),['/runtime']);self.assertEqual(r['latency_n'],3);self.assertEqual(r['p90_ms'],118)
 def test_wrong_selected_case_rejected(self):
  r=self.runs();r[0]['dataset']='other'
  with self.assertRaises(ValidationError):self.loop.add_reliability(self.doc,{'source_hash':self.hash,'runs':r})

class BenchmarkTests(unittest.TestCase):
 def manifest(self):
  return {'ruleset':'trace-evidence-v1','cases':[{'id':'1','rule':'parser_mismatch','label':'positive','label_source':'injected_fault','raw':raw()},
   {'id':'2','rule':'parser_mismatch','label':'negative','label_source':'injected_fault','raw':{**raw(),'parsed_answer':'Copper Harbor'}}]}
 def test_known_fixture_detected(self):
  r=assess(self.manifest());self.assertEqual(r['counts']['tp'],1);self.assertEqual(r['counts']['tn'],1)
 def test_no_self_generated_gold_labels(self):
  m=self.manifest();m['cases'][0]['label_source']='system_confirmation'
  with self.assertRaises(ValidationError):assess(m)
 def test_pin_ruleset(self):
  m=self.manifest();m['ruleset']='latest'
  with self.assertRaises(ValidationError):assess(m)
 def test_gap_reports_abstain(self):
  m=self.manifest();m['cases'][0]['raw']={'parsed_answer':'x'};r=assess(m);self.assertEqual(r['counts']['abstain_positive'],1)

class LoopAPI(Base):
 def setUp(self):
  super().setUp();self.store=Store(self.tmp.name);self.server=LabServer(('127.0.0.1',0),self.store);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_address[1])
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.store.close();super().tearDown()
 def request(self,op,data=None,token=True):
  url=self.base+'/api/loop/'+op
  if data is None:url+='?file='+self.file['id']+'&record=1';body=None;headers={}
  else:body=json.dumps({'file':self.file['id'],'record':1,'source_hash':self.hash,**data}).encode();headers={'Content-Type':'application/json'}
  if token:headers['X-Local-Token']=self.server.csrf_token
  try:r=urllib.request.urlopen(urllib.request.Request(url,data=body,headers=headers))
  except urllib.error.HTTPError as e:r=e
  return r.status,json.loads(r.read())
 def test_workspace_endpoint(self):
  status,data=self.request('workspace');self.assertEqual(status,200);self.assertEqual(len(data['scorecard']['layers']),4)
 def test_write_csrf_protected(self):self.assertEqual(self.request('contract',{'contract':contract_template()},token=False)[0],403)
 def test_bind_then_get(self):
  self.assertEqual(self.request('contract',{'contract':contract_template()})[0],201);self.assertIsNotNone(self.request('workspace')[1]['contract'])
 def test_loop_export_contains_raw_and_no_score_edits(self):self.assertEqual(self.request('export')[1]['raw']['scores']['em'],0)
 def test_bad_rule_400(self):self.assertEqual(self.request('rule-hypothesis',{'finding_id':'not-real'})[0],400)
 def test_module_is_served(self):
  r=urllib.request.urlopen(self.base+'/inspector/loop-panel.js');self.assertEqual(r.status,200);self.assertIn(b'LoopPanel',r.read())

class ExtendedSafetyTests(Base):
 def test_evidence_negative_index_rejected(self):
  with self.assertRaises(ValidationError):self.loop.evidence(self.doc,['/steps/-1/output'])
 def test_component_schema_unsupported_does_not_pass(self):
  self.assertEqual(component({'x':'foo'},'tool_schema',{'schema':{'properties':{'x':{'pattern':'foo'}}}})['status'],'insufficient')
 def test_core_comparison_fields_cannot_be_disabled(self):
  t=self.task('autonomous');self.assertIn('/chat_template',t['required_paths']);self.assertIn('/model_revision',t['required_paths'])
 def test_recorded_contract_hash_binding(self):
  c=contract_template();d=copy.deepcopy(self.doc);d['raw']['execution_context']['contract_hash']=digest(c)
  b=self.loop.bind_contract(d,{'source_hash':self.hash,'contract':c,'binding':'recorded_at_execution'});self.assertEqual(b['binding'],'recorded_at_execution')
 def test_registered_evaluation_context_adapter(self):
  from searchagent_lab.diagnostic_loop.routes import formal_context
  from searchagent_lab.demo import seed_demo
  from searchagent_lab.inspection.routes import document
  from searchagent_lab.diagnostic_loop.evidence import context_of
  with Store(Path(self.tmp.name)/'formal') as store:
   seed_demo(store,1)
   ev=store.evaluation('lt-persistent-s42-step100-eval01');tr=store.trajectories(ev['id'])['items'][0]
   d=document(self.repo,store,{'trajectory':tr['id']});d['formal_context']=formal_context(ev)
   self.assertTrue(d['demo']);self.assertIn('prompt',context_of(d)['values']);self.assertEqual(d['source']['ownership']['project_id'],ev['project_id'])
 def test_confounded_result_cannot_support_hypothesis(self):
  t=self.task('autonomous');r=self.result(t);r['candidate']['context']['budget']['max_search']=9
  saved=self.loop.import_result(self.doc,{'source_hash':self.hash,'task_id':t['id'],'result':r})
  with self.assertRaises(ValidationError):self.loop.review_hypothesis(self.doc,{'source_hash':self.hash,'hypothesis_id':t['hypothesis_id'],'status':'supported_by_experiment','result_id':saved['id'],'note':'Not actually controlled'})
 def test_no_tasks_results_overwrite(self):
  t=self.task();a=self.loop.execute(self.doc,{'source_hash':self.hash,'task_id':t['id']});b=self.loop.execute(self.doc,{'source_hash':self.hash,'task_id':t['id']})
  self.assertNotEqual(a['id'],b['id']);self.assertEqual(len(self.loop.objects(self.doc,'result')),2)
