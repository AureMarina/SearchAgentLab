"""Method evidence semantics, missing denominators and run isolation.

Every record in this module is synthetic. No training/model execution occurs.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from searchagent_lab.core import ValidationError
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.methods import views
from searchagent_lab.methods.schema import validate_object


class MethodAssessmentTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.rec=MethodRecorder(self.tmp.name);self.r=self.rec.repo
    s=self.rec.store;s.register_project({'id':'p','name':'Synthetic methods'})
    s.register_experiment({'id':'e','project_id':'p','name':'Synthetic experiment'})
    for rid in ('a','b'):s.register_run({'id':rid,'experiment_id':'e','name':rid,'demo':True})
 def tearDown(self):self.rec.close();self.tmp.cleanup()
 def obj(self,oid,kind,**data):return {'id':oid,'kind':kind,'demo':True,'data':data}
 def put(self,*rows,run='a'):return self.r.import_bundle({'method_schema':'method-evidence-v1','objects':list(rows)},scope={'run_id':run})
 def assess(self,family,run='a',**kwargs):return self.r.method_assessment('e',run,family,**kwargs)
 def metric(self,family,mid,**kwargs):return next(m for m in self.assess(family,**kwargs)['metrics'] if m['id']==mid)
 def bank(self):self.put(self.obj('bank','skill_bank',general_skills=[{'skill_id':'s','name':'Synthetic skill','principle':'Look up the primary source'}]))
 def failure(self,oid='f',budget=2,**kw):
    return self.obj(oid,'opd_failure',sample_id=oid,student_checkpoint_id='external-student',state={'status':'captured'},eligible=True,attempt_budget=budget,**kw)
 def teacher(self,oid,failure='f',index=1,passed=True,**kw):
    d={'failure_id':failure,'teacher_id':'synthetic-teacher','mode':'continuation','context':{'added_information':['synthetic hint']},
       'status':'completed','attempt_index':index,'state_alignment':'restored',
       'verification':{'status':'passed' if passed else 'failed','method':'environment','verifier':'synthetic-verifier-v1'}}
    d.update(kw);return self.obj(oid,'opd_teacher',**d)
 def supervision(self,oid='s',failure='f',teachers=None,**kw):
    d={'failure_id':failure,'teacher_ids':teachers or ['t'],'objective':'distillation','status':'accepted','loss_mask_tokens':12,'loss_weight':0.2}
    d.update(kw);return self.obj(oid,'opd_supervision',**d)
 def consume(self,oid,artifact,**kw):return self.obj(oid,'training_use',artifact_ids=[artifact],status='sampled',batch_id='synthetic-batch',**kw)
 def update(self,oid,*uses,**kw):return self.obj(oid,'training_update',consumption_ids=list(uses),**kw)
 def evaluation(self,eid,run='a',family='skill',purpose='checkpoint_eval',bind=True):
    s=self.rec.store
    if not s.conn.execute('SELECT id FROM panels WHERE id="panel"').fetchone():
        s.register_panel({'id':'panel','name':'Synthetic uneven panel','split':'probe','demo':True,
          'samples':[{'id':'q1','dataset':'a','question':'Synthetic A1?','answers':['yes']},
                     {'id':'q2','dataset':'a','question':'Synthetic A2?','answers':['yes']},
                     {'id':'q3','dataset':'b','question':'Synthetic B1?','answers':['yes']}]})
    s.register_checkpoint({'id':'cp-'+eid,'run_id':run,'step':1})
    protocol=json.loads((Path(__file__).resolve().parents[1]/'examples/protocol_v2.synthetic.json').read_text())
    ev={'id':eid,'checkpoint_id':'cp-'+eid,'panel_id':'panel','protocol':protocol}
    if purpose is not None:ev['purpose']=purpose
    s.start_evaluation(ev)
    for i,ds in enumerate(('a','a','b'),1):
        s.log_trajectory(eid,{'sample_id':'q'+str(i),'dataset':ds,'scores':{'em':int(i!=2)},'runtime_status':'ok',
          'termination_reason':'final_answer','steps':[],'usage':{'input_tokens':0,'output_tokens':0} if i==1 else {}})
    s.finish_evaluation(eid)
    if bind:self.put(self.obj('bind-'+eid,'evaluation_binding',evaluation_id=eid,binding_origin='recorded_execution',
        assessment_families=[family],independent=True,autonomous=True),run=run)

 def test_unknown_without_records_is_not_zero(self):
    for family in ('skill','self_play','opd'):
        data=self.assess(family)
        self.assertEqual(len(data['metrics']),3)
        self.assertTrue(all(m['value'] is None for m in data['metrics']))
        self.assertEqual(data['evidence']['total'],0)
 def test_typed_opd_activates_only_its_run(self):
    self.put(self.failure())
    self.assertEqual([f['id'] for f in views.experiment_overview(self.r,'e','a')['families']],['opd'])
    self.assertEqual(views.experiment_overview(self.r,'e','b')['families'],[])
    self.assertEqual(self.assess('opd',run='b')['counts']['failure_states'],0)
    self.assertEqual(self.r.method_assessment('e','','opd')['counts']['failure_states'],0)
 def test_cross_run_teacher_link_rejected_atomically(self):
    self.put(self.failure())
    with self.assertRaisesRegex(ValidationError,'same run'):self.put(self.teacher('t'),run='b')
    self.assertEqual(self.r.catalog({'run_id':'b'})['total'],0)
 def test_rescore_cannot_claim_environment_repair(self):
    self.put(self.failure())
    row=self.teacher('t',mode='rescore')
    with self.assertRaisesRegex(ValidationError,'rescore'):self.put(row)
 def test_duplicate_attempt_index_rejected(self):
    self.put(self.failure(),self.teacher('t'))
    with self.assertRaisesRegex(ValidationError,'Duplicate continuation'):self.put(self.teacher('t2'))
    with self.assertRaisesRegex(ValidationError,'Duplicate continuation'):self.put(self.failure(),self.teacher('t3'))
 def test_attempt_beyond_k_rejected(self):
    self.put(self.failure(budget=1))
    with self.assertRaisesRegex(ValidationError,'attempt_budget'):self.put(self.teacher('t',index=2))
 def test_repair_denominator_keeps_errors_and_untested_unknown(self):
    self.put(self.failure('f1'),self.failure('f2'),self.failure('f3'),self.failure('f4'))
    self.put(self.teacher('t1','f1'),self.teacher('t21','f2',1,False),self.teacher('t22','f2',2,False),
             self.teacher('t31','f3',status='error',verification={'status':'error','method':'environment','reason':'synthetic outage'}))
    m=self.metric('opd','teacher_repair')
    self.assertEqual((m['value'],m['numerator'],m['denominator'],m['unknown']),(0.5,1,2,2))
    self.assertEqual((m['attempt_budget'],m['eligible'],m['attempted']),(2,4,3))
    self.assertEqual(m['status'],'partial')
    rows=self.assess('opd')['evidence']['items'];self.assertEqual(len(rows),4)
    f3=next(r for r in rows if r['id']=='f3')['assessment'];self.assertEqual(f3['verification_errors'],1);self.assertIsNone(f3['repair_success'])
 def test_all_k_verified_failures_is_measured_zero(self):
    self.put(self.failure(),self.teacher('t1',passed=False),self.teacher('t2',index=2,passed=False))
    m=self.metric('opd','teacher_repair');self.assertEqual(m['value'],0);self.assertEqual(m['denominator'],1)
 def test_different_k_are_not_pooled(self):
    self.put(self.failure('f1',1),self.failure('f2',2),self.teacher('t1','f1'),self.teacher('t2','f2'))
    data=self.assess('opd');m=data['metrics'][0]
    self.assertIsNone(m['value']);self.assertEqual(m['status'],'selection_required');self.assertEqual(len(data['budget_groups']),2)
 def test_text_only_state_is_not_environment_restoration(self):
    f=self.failure();f['data']['state']['status']='text_only'
    self.put(f,self.teacher('t'))
    self.assertIsNone(self.metric('opd','teacher_repair')['value'])
 def test_task_scorer_can_verify_actual_search_continuation(self):
    self.put(self.failure(),self.teacher('t',verification={'status':'passed','method':'scorer','verifier':{'name':'strict-em','version':'fixture-v1'}}))
    self.assertEqual(self.metric('opd','teacher_repair')['value'],1)
 def test_model_judge_not_promoted_to_deterministic_repair(self):
    self.put(self.failure(),self.teacher('t',verification={'status':'passed','method':'model_judge','verifier':{'name':'judge','version':'fixture-v1'}}))
    self.assertIsNone(self.metric('opd','teacher_repair')['value'])
 def test_rescore_only_repair_is_not_applicable(self):
    self.put(self.failure(),self.obj('t','opd_teacher',failure_id='f',teacher_id='teacher',mode='rescore',context={},status='completed'))
    self.assertEqual(self.metric('opd','teacher_repair')['status'],'not_applicable')
 def test_actual_supervision_requires_sampled_and_completed(self):
    self.put(self.failure(),self.teacher('t'),self.supervision())
    self.assertIsNone(self.metric('opd','supervision_coverage')['value'])
    self.put(self.consume('u','s'),self.update('pending','u'))
    self.assertIsNone(self.metric('opd','supervision_coverage')['value'])
    self.put(self.update('done','u',status='completed'))
    m=self.metric('opd','supervision_coverage');self.assertEqual((m['value'],m['numerator'],m['denominator']),(1,1,1))
 def test_zero_advantage_reweight_not_rescued_signal(self):
    self.put(self.failure(),self.teacher('t'),self.supervision(objective='advantage_reweight',original_advantage=0,effective_advantage=0),self.consume('u','s'),self.update('done','u',status='completed'))
    m=self.metric('opd','supervision_coverage');self.assertEqual(m['value'],0)
    detail=self.r.object('s')['data'];self.assertEqual(detail['original_advantage'],0)
    with self.assertRaisesRegex(ValidationError,'zero original'):self.put(self.supervision('invalid',objective='advantage_reweight',original_advantage=0,effective_advantage=1))
 def test_auxiliary_distillation_can_activate_zero_advantage_sample(self):
    self.put(self.failure(original_advantage=0),self.teacher('t'),self.supervision(original_advantage=0),self.consume('u','s'),self.update('done','u',status='completed'))
    self.assertEqual(self.metric('opd','supervision_coverage')['value'],1)
 def test_missing_mask_is_unknown_even_if_weight_exists(self):
    self.put(self.failure(),self.teacher('t'),self.supervision(loss_mask_tokens=None),self.consume('u','s'),self.update('done','u',status='completed'))
    m=self.metric('opd','supervision_coverage');self.assertIsNone(m['value']);self.assertEqual(m['unknown'],1)
 def test_rejected_supervision_retained_without_counting_active(self):
    self.put(self.failure(),self.teacher('t'),self.supervision(status='rejected'))
    data=self.assess('opd');self.assertEqual(data['metrics'][1]['value'],0)
    self.assertEqual(data['evidence']['items'][0]['assessment']['supervision_count'],1)
    self.assertEqual(data['evidence']['items'][0]['assessment']['active_supervision_count'],0)
 def test_skill_selected_is_not_injected_and_incomplete_is_unknown(self):
    self.bank()
    self.put(self.obj('r1','skill_retrieval',bank_id='bank',trajectory_id='one',selected=['s'],injections=[],injections_complete=True,trajectory_complete=True,final_input={'messages':[{'role':'user','content':'question only'}]}),
      self.obj('r2','skill_retrieval',bank_id='bank',trajectory_id='two',selected=['s'],injections=[]))
    m=self.metric('skill','injection_coverage');self.assertEqual((m['value'],m['denominator'],m['unknown']),(0,1,1))
 def test_skill_injection_coverage_deduplicates_trajectory(self):
    self.bank()
    base={'bank_id':'bank','trajectory_id':'one','injections':[{'member':'s','rendered_text':'Look up the primary source'}],
          'injections_complete':True,'trajectory_complete':True,'final_input':{'input':'Look up the primary source'}}
    self.put(self.obj('r1','skill_retrieval',**base),self.obj('r2','skill_retrieval',**base))
    m=self.metric('skill','injection_coverage');self.assertEqual((m['value'],m['numerator'],m['denominator']),(1,1,1))
 def test_missing_injection_text_not_treated_as_absent(self):
    self.bank()
    self.put(self.obj('request','trace',model_request={'input':'Look up the primary source'}))
    self.put(self.obj('r','skill_retrieval',bank_id='bank',trajectory_id='one',injections=[{'member':'s'}],
        injections_complete=True,trajectory_complete=True,request_ref={'object_id':'request','pointer':'/model_request'}))
    m=self.metric('skill','injection_coverage');self.assertIsNone(m['value']);self.assertEqual(m['unknown'],1)
 def test_sample_id_alone_not_a_unique_trajectory(self):
    self.bank()
    self.put(self.obj('r','skill_retrieval',bank_id='bank',sample_id='shared-id',injections=[],
        injections_complete=True,trajectory_complete=True,final_input='question'))
    m=self.metric('skill','injection_coverage');self.assertIsNone(m['value']);self.assertEqual(m['missing_identity_records'],1)
 def test_skill_assessment_never_loads_bank_body(self):
    self.bank()
    with patch.object(self.r,'object',side_effect=AssertionError('No full body read')):
        self.assertEqual(self.assess('skill')['counts']['banks'],1)
 def test_selfplay_unique_supply_needs_complete_recorded_chain(self):
    self.put(self.obj('cycle','selfplay_cycle',participants=[]),self.obj('later','selfplay_cycle',participants=[]))
    self.put(self.obj('q','generated_question',question='Synthetic question?',stable_question_id='unique-q',first_generated_cycle_id='cycle',deduplication={'status':'unique'}),
      self.obj('q-copy','generated_question',question='Synthetic question?',stable_question_id='unique-q',first_generated_cycle_id='cycle',deduplication={'status':'unique'}),
      self.obj('bad','generated_question',question='Invalid?',stable_question_id='bad-q',first_generated_cycle_id='cycle',deduplication={'status':'unique'}))
    self.put(self.obj('review','question_review',question_id='q',status='accepted',verification={'status':'passed','verifier':'fixture-v1','method':'scorer'}),
      self.obj('bad-review','question_review',question_id='bad',status='rejected'),
      self.consume('u','q',cycle_id='cycle',signal={'objective':'policy_gradient','effective_advantage':0.5,'loss_mask_tokens':20,'loss_weight':1}),
      self.consume('reuse','q',cycle_id='later'),self.update('done','u','reuse',status='completed'))
    data=self.assess('self_play');self.assertEqual(data['counts']['new_verified_consumed_questions'],1)
    self.assertEqual(data['counts']['reused_questions'],1)
    self.assertEqual(data['counts']['generated_questions'],3)
    self.assertEqual(data['metrics'][1]['value'],1)
    self.assertEqual(data['metrics'][1]['unknown'],1)
 def test_legacy_accepted_review_without_verified_answer_not_verification(self):
    self.put(self.obj('c','selfplay_cycle',participants=[]),self.obj('q','generated_question',question='Synthetic?',stable_question_id='q',first_generated_cycle_id='c',deduplication={'status':'unique'}),
      self.obj('r','question_review',question_id='q',status='accepted'),self.consume('u','q'),self.update('done','u',status='completed'))
    data=self.assess('self_play');self.assertIsNone(data['evidence']['items'][0]['assessment']['verified']);self.assertIsNone(data['metrics'][0]['value'])
 def test_legacy_verified_answer_remains_supported(self):
    self.put(self.obj('c','selfplay_cycle',participants=[]),self.obj('q','generated_question',question='Synthetic?',stable_question_id='q',first_generated_cycle_id='c',deduplication={'status':'unique'}),
      self.obj('r','question_review',question_id='q',status='accepted',verified_answer='synthetic',reviewer='fixture'),self.consume('u','q'),self.update('done','u',status='completed'))
    self.assertEqual(self.metric('self_play','new_verified_consumed')['value'],1)
 def test_sibling_reviews_not_leaked_into_current_run(self):
    self.put(self.obj('q','generated_question',question='Synthetic?'))
    self.put(self.obj('r','question_review',question_id='q',status='rejected'),run='b')
    self.assertEqual(self.assess('self_play')['evidence']['items'][0]['assessment']['review_status'],'unreviewed')
 def test_selfplay_absent_sampling_log_stays_unknown(self):
    self.put(self.obj('cycle','selfplay_cycle',participants=[]),self.obj('q','generated_question',question='Synthetic?',first_generated_cycle_id='cycle'))
    self.assertIsNone(self.assess('self_play')['evidence']['items'][0]['assessment']['consumed'])
 def test_selfplay_complete_consumption_without_sampled_is_false(self):
    self.put(self.obj('cycle','selfplay_cycle',participants=[],coverage={'consumption_complete':True}),self.obj('q','generated_question',question='Synthetic?',first_generated_cycle_id='cycle'))
    self.assertIs(self.assess('self_play')['evidence']['items'][0]['assessment']['consumed'],False)
    self.assertEqual(self.metric('self_play','new_verified_consumed')['value'],0)
 def test_selfplay_sampling_and_possible_later_reuse_preserve_three_states(self):
    self.put(self.obj('cycle','selfplay_cycle',participants=[],coverage={'consumption_complete':True}),self.obj('later','selfplay_cycle',participants=[]),
        self.obj('q','generated_question',question='Synthetic?',first_generated_cycle_id='cycle'))
    self.assertIsNone(self.assess('self_play')['evidence']['items'][0]['assessment']['consumed'])
    self.put(self.consume('u','q',cycle_id='later'))
    self.assertIs(self.assess('self_play')['evidence']['items'][0]['assessment']['consumed'],True)
 def test_unknown_status_updates_do_not_mean_completion(self):
    self.put(self.failure(),self.teacher('t'),self.supervision(),self.consume('u','s'),self.update('update','u'))
    self.assertIsNone(self.assess('opd')['evidence']['items'][0]['assessment']['completed_update'])
 def test_old_collector_update_status_is_retained_as_unknown(self):
    self.put(self.failure(),self.teacher('t'),self.supervision(),self.consume('u','s'),self.update('update','u',status='optimizer_seen'))
    self.assertIsNone(self.assess('opd')['evidence']['items'][0]['assessment']['completed_update'])
    self.assertEqual(self.r.object('update')['data']['status'],'optimizer_seen')
 def test_evidence_pagination_is_bounded_and_raw_free(self):
    self.put(*[self.failure('f'+str(i)) for i in range(5)])
    a=self.assess('opd',limit=2);b=self.assess('opd',offset=2,limit=2)
    self.assertTrue(a['evidence']['has_more']);self.assertTrue({r['id'] for r in a['evidence']['items']}.isdisjoint({r['id'] for r in b['evidence']['items']}))
    self.assertTrue(all('data' not in r for r in a['evidence']['items']))
 def test_lazy_projection_upgrade_preserves_immutable_records(self):
    self.put(self.failure())
    before=tuple(self.r.conn.execute('SELECT hash,payload FROM objects WHERE id="f"').fetchone())
    row=json.loads(self.r.conn.execute('SELECT summary FROM method_summaries WHERE id="f"').fetchone()[0]);row.pop('assessment_version');row.pop('assessment')
    self.r.conn.execute('UPDATE method_summaries SET summary=? WHERE id="f"',(json.dumps(row),))
    self.assertEqual(self.assess('opd')['counts']['failure_states'],1)
    self.assertEqual(before,tuple(self.r.conn.execute('SELECT hash,payload FROM objects WHERE id="f"').fetchone()))
 def test_incompatible_supervision_teacher_failure_rejected(self):
    self.put(self.failure('f1'),self.failure('f2'),self.teacher('t','f1'))
    with self.assertRaisesRegex(ValidationError,'this failure'):self.put(self.supervision(failure='f2'))
 def test_external_macro_em_is_not_micro_ratio(self):
    self.evaluation('one')
    m=self.metric('skill','external_em');self.assertEqual(m['value'],0.75);self.assertIsNone(m['numerator']);self.assertEqual(m['denominator'],3)
    cost=self.metric('skill','tokens_per_task');self.assertEqual(cost['value'],0);self.assertEqual(cost['denominator'],1);self.assertEqual(cost['unknown'],2)
 def test_multiple_external_checkpoints_require_explicit_selection(self):
    self.evaluation('one');self.evaluation('two')
    data=self.assess('skill');self.assertEqual(len(data['evaluation_options']),2)
    self.assertEqual(data['metrics'][0]['status'],'selection_required');self.assertIsNone(data['metrics'][0]['value'])
    selected=self.assess('skill',evaluation_id='two');self.assertEqual(selected['metrics'][0]['value'],0.75);self.assertEqual(selected['evaluation']['evaluation_id'],'two')
 def test_foreign_or_undeclared_external_evaluation_not_silently_used(self):
    self.evaluation('other',run='b');self.evaluation('unbound',bind=False)
    for eid in ('other','unbound','unknown'):
        with self.subTest(eid=eid),self.assertRaisesRegex(ValidationError,'not a declared'):self.assess('skill',evaluation_id=eid)
 def test_legacy_evaluation_is_not_promoted_by_new_binding(self):
    self.evaluation('legacy',purpose=None)
    data=self.assess('skill');self.assertIsNone(data['metrics'][0]['value']);self.assertEqual(data['evaluation_options'],[])
    self.assertEqual(data['evaluation']['excluded'][0]['evaluation_id'],'legacy')
 def test_mask_token_count_must_match_saved_mask(self):
    self.put(self.failure(),self.teacher('t'))
    with self.assertRaisesRegex(ValidationError,'loss_mask_tokens'):self.put(self.supervision(loss_mask=[0,0,0],loss_mask_tokens=3))


if __name__=='__main__':unittest.main()
