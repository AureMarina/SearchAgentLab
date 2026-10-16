"""Synthetic CPU tests for frozen versions, evidence levels and scope.

These fixtures never run a model, optimizer, script or real evaluation task.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from searchagent_lab.core import ValidationError
from searchagent_lab.inspection.repository import digest
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.methods import views


class SkillTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.rec=MethodRecorder(self.tmp.name);self.r=self.rec.repo;self.s=self.rec.store
        self.s.register_project({'id':'p','name':'Synthetic skills'})
        self.s.register_experiment({'id':'e','project_id':'p','name':'Synthetic experiment'})
        self.s.register_experiment({'id':'other','project_id':'p','name':'Other experiment'})
        for rid in ('a','b'):self.s.register_run({'id':rid,'name':rid,'experiment_id':'e','demo':True})
        self.s.register_run({'id':'outside','name':'outside','experiment_id':'other','demo':True})
    def tearDown(self):self.rec.close();self.tmp.cleanup()
    def obj(self,oid,kind,**data):return {'id':oid,'kind':kind,'demo':True,'data':data}
    def put(self,*rows,run='a'):
        return self.r.import_bundle({'method_schema':'method-evidence-v1','objects':list(rows)},scope={'run_id':run} if run else {'experiment_id':'e'})
    def asset(self,oid='asset-v1',version='v1',body='Check the primary source',sid='verify-source'):
        package={'files':{'SKILL.md':body,'scripts/check.py':'print("fixture")'}}
        return self.obj(oid,'skill_asset',skill_id=sid,version=version,package=package,package_hash=digest(package))
    def instance(self,oid='i',**data):
        d={'asset_id':'asset-v1','selection':{'source':'retriever'},'step':2};d.update(data)
        return self.obj(oid,'skill_instance',**d)
    def inline(self,oid='inline',**data):
        d={'inline_skill':{'text':'Verify both hops','content_hash':digest('Verify both hops')},'selection':{'source':'model'}};d.update(data)
        return self.obj(oid,'skill_instance',**d)
    def evaluation(self,eid='eval',run='a'):
        if not self.s.conn.execute('SELECT id FROM panels WHERE id="panel"').fetchone():
            self.s.register_panel({'id':'panel','name':'Synthetic one case','split':'probe','demo':True,
                                   'samples':[{'id':'q','dataset':'fixture','question':'Synthetic?','answers':['yes']}]})
        self.s.register_checkpoint({'id':'cp-'+eid,'run_id':run,'step':1})
        protocol=json.loads((Path(__file__).parents[1]/'examples/protocol_v2.synthetic.json').read_text())
        self.s.start_evaluation({'id':eid,'checkpoint_id':'cp-'+eid,'panel_id':'panel','protocol':protocol,'purpose':'checkpoint_eval'})
        self.s.log_trajectory(eid,{'sample_id':'q','dataset':'fixture','scores':{'em':1},'steps':[],'runtime_status':'ok','termination_reason':'final_answer'})
        self.s.finish_evaluation(eid)
        return self.s.conn.execute('SELECT id FROM trajectories WHERE evaluation_id=?',(eid,)).fetchone()[0]

    def test_asset_package_hash_covers_resources_and_immutable_version(self):
        self.put(self.asset())
        bad=self.asset('same-version-new-bytes',body='Changed')
        with self.assertRaisesRegex(ValidationError,'different package bytes'):self.put(bad)
        bad=self.asset('asset-v2','v2');bad['data']['package']['files']['scripts/check.py']='other'
        with self.assertRaisesRegex(ValidationError,'package_hash'):self.put(bad)
        self.assertEqual(self.r.object('asset-v1')['data']['package']['files']['scripts/check.py'],'print("fixture")')
    def test_binary_resource_hash_and_empty_file_are_supported(self):
        a=self.asset();a['data']['package']['files']['resource.bin']={'encoding':'base64','content':'AAEC','sha256':'ae4b3280e56e2faf83f414a6e3dabe9d5fbe18976544c05fed121accb85b53fc'}
        a['data']['package']['files']['empty']={'encoding':'utf-8','content':''};a['data']['package_hash']=digest(a['data']['package'])
        self.put(a)
        self.assertEqual(self.r.skill_assets('e','a')['items'][0]['file_count'],4)
    def test_asset_rejects_hash_only_file_and_path_escape(self):
        for value in ({'resources/x':{'sha256':'a'*64}},{'../outside':'x'}):
            a=self.asset();a['data']['package']['files']=value;a['data']['package_hash']=digest(a['data']['package'])
            with self.assertRaises(ValidationError):self.put(a)
    def test_historical_instance_keeps_exact_version(self):
        self.put(self.asset(),self.instance())
        self.put(self.asset('asset-v2','v2','New text'))
        got=self.r.skill_instance('i')
        self.assertEqual(got['asset_ref']['version'],'v1')
        self.assertEqual(got['frozen_content']['content']['files']['SKILL.md'],'Check the primary source')
    def test_inline_credit_needs_no_external_bank(self):
        self.put(self.inline(),self.obj('use','training_use',artifact_ids=['inline'],status='sampled',batch_id='batch',
            credit_assignment={'instance_id':'inline','skill_decision':{'reward':0.3,'advantage':-0.2,'mask':[1,1],'source':'semantic segment return'},
                               'action_decision':{'reward':0,'advantage':0,'mask':[0,0],'source':'outcome'}}))
        got=self.r.skill_instance('inline');self.assertEqual(got['asset_ref']['type'],'inline');self.assertEqual(got['frozen_content']['content'],'Verify both hops')
        credit=self.r.skill_lifecycle('e','a')['credits']['items'][0]
        self.assertTrue(credit['model_selection_credit']['value']);self.assertEqual(credit['action_decision']['advantage'],0)
        self.assertFalse(credit['update_completed'])
    def test_credit_selection_source_is_not_inferred_from_skill_name(self):
        self.put(self.asset(),self.instance(selection={'source':'rule'}),self.obj('use','training_use',artifact_ids=['i'],status='sampled',batch_id='b',
            credit_assignment={'instance_id':'i','skill_decision':{'advantage':1,'source':'logged'},'action_decision':{'advantage':2,'source':'logged'}}))
        got=self.r.skill_lifecycle('e','a')['credits']['items'][0]
        self.assertFalse(got['model_selection_credit']['value']);self.assertEqual(got['action_decision']['advantage'],2)
    def test_credit_keeps_training_consumption_and_completed_update_separate(self):
        self.put(self.inline(),self.obj('u','training_use',artifact_ids=['inline'],status='sampled',batch_id='b',credit_assignment={'instance_id':'inline','action_decision':{'advantage':0,'mask':[0],'source':'fixture'}}))
        self.put(self.obj('pending','training_update',consumption_ids=['u'],status='pending'))
        self.assertFalse(self.r.skill_lifecycle('e','a')['credits']['items'][0]['update_completed'])
        self.put(self.obj('done','training_update',consumption_ids=['u'],status='completed'))
        self.assertTrue(self.r.skill_lifecycle('e','a')['credits']['items'][0]['update_completed'])
    def test_cross_run_instance_credit_rejected(self):
        self.put(self.inline())
        with self.assertRaisesRegex(ValidationError,'another run scope'):
            self.put(self.obj('u','training_use',artifact_ids=['inline'],status='sampled',batch_id='b',credit_assignment={'instance_id':'inline'}),run='b')
    def test_credit_instance_requires_explicit_artifact_link(self):
        self.put(self.inline())
        with self.assertRaisesRegex(ValidationError,'artifact_ids'):
            self.put(self.obj('u','training_use',artifact_ids=['inline'],status='sampled',batch_id='b',credit_assignment={'instance_id':'different'}))
    def test_loading_requires_actual_model_input(self):
        self.put(self.asset(),self.instance(loading={'rendered_text':'Check the primary source'}))
        self.assertIsNone(self.r.skill_runtime('e','a')['items'][0]['observations']['loaded']['value'])
    def test_multiline_quoted_skill_matches_real_prompt_content(self):
        body='## Verify\nRead the "primary" source.\nThen answer.'
        self.put(self.asset(body=body),self.instance(loading={'rendered_text':body,'final_input':{'messages':[{'role':'system','content':'Before\n'+body+'\nAfter'}]},'complete':True}))
        self.assertTrue(self.r.skill_runtime('e','a')['items'][0]['observations']['loaded']['value'])
    def test_input_field_names_and_adjacent_parts_do_not_form_false_match(self):
        self.put(self.asset(),self.instance(loading={'rendered_text':'Check the primary source','final_input':{'messages':[{'role':'system','content':['Check the ', 'primary source']}],'Check the primary source':'metadata field'},'complete':True}))
        self.assertFalse(self.r.skill_runtime('e','a')['items'][0]['observations']['loaded']['value'])
    def test_old_multiline_injection_is_preserved(self):
        body='line 1\nline "2"';raw={'skill_id':'s','principle':body}
        self.put(self.obj('bank','skill_bank',general_skills=[raw]),self.obj('r','skill_retrieval',bank_id='bank',trajectory_id='legacy-external',
            injections=[{'member':'s','rendered_text':body}],final_input={'messages':[{'role':'user','content':body}]},injections_complete=True,trajectory_complete=True))
        self.assertTrue(self.r.skill_runtime('e','a')['items'][0]['observations']['loaded']['value'])
    def test_visible_and_loaded_are_distinct(self):
        self.put(self.asset(),self.instance(visibility={'entry_text':'verify-source: check facts','final_input':'Skills: verify-source: check facts'},
            loading={'rendered_text':'Check the primary source','final_input':'Skills: verify-source: check facts','complete':True}))
        obs=self.r.skill_runtime('e','a')['items'][0]['observations']
        self.assertTrue(obs['visible']['value']);self.assertFalse(obs['loaded']['value']);self.assertIsNone(obs['execution']['value'])
    def test_verified_loading_implies_visibility_but_not_execution(self):
        self.put(self.asset(),self.instance(loading={'rendered_text':'Check the primary source','final_input':'Check the primary source'},related_actions=[{'step':3,'tool_call_id':'search-1'}]))
        got=self.r.skill_runtime('e','a')['items'][0]
        self.assertTrue(got['observations']['visible']['value']);self.assertTrue(got['observations']['loaded']['value'])
        self.assertIsNone(got['observations']['execution']['value']);self.assertIsNone(got['observations']['gain']['value'])
        self.assertNotIn('end_step',got)
    def test_execution_needs_trace_evidence_not_only_label(self):
        self.put(self.asset(),self.instance(execution={'status':'observed','kind':'script'}))
        self.assertIsNone(self.r.skill_runtime('e','a')['items'][0]['observations']['execution']['value'])
    def test_script_and_verified_step_evidence(self):
        self.put(self.asset(),self.obj('trace','trace',steps=[{'tool_call':'check.py','exit_code':0}]),
            self.instance(execution={'status':'observed','kind':'script','evidence_ref':{'object_id':'trace','pointer':'/steps/0'}}))
        self.assertTrue(self.r.skill_runtime('e','a')['items'][0]['observations']['execution']['value'])
    def test_metadata_text_is_not_script_execution(self):
        self.put(self.asset(),self.obj('trace','trace',note='Ran the script successfully'),
            self.instance(execution={'status':'observed','kind':'script','evidence_ref':{'object_id':'trace','pointer':'/note'}}))
        self.assertIsNone(self.r.skill_runtime('e','a')['items'][0]['observations']['execution']['value'])
    def test_runtime_evidence_cannot_borrow_another_run_input(self):
        self.put(self.asset());self.put(self.obj('other-input','trace',final_input='Check the primary source'),run='b')
        with self.assertRaisesRegex(ValidationError,'another run scope'):
            self.put(self.instance(loading={'rendered_text':'Check the primary source','request_ref':{'object_id':'other-input','pointer':'/final_input'}}))
    def test_old_candidates_do_not_become_model_visible(self):
        self.put(self.obj('bank','skill_bank',general_skills=[{'skill_id':'s','principle':'Do it'}]),
                 self.obj('retrieval','skill_retrieval',bank_id='bank',candidates=['s'],selected=['s']))
        item=self.r.skill_runtime('e','a')['items'][0]
        self.assertEqual(item['record_type'],'legacy_retrieval');self.assertIsNone(item['observations']['visible']['value'])
        self.assertEqual(self.r.skill_lifecycle('e','a')['counts']['events'],0)
        self.assertEqual(self.r.skill_instance('retrieval')['frozen_content']['kind'],'bank_retrieval')
    def test_old_frozen_bank_member_pin_is_checked(self):
        raw={'skill_id':'s','principle':'Frozen'}
        self.put(self.obj('bank','skill_bank',general_skills=[raw]))
        self.put(self.obj('i','skill_instance',bank_id='bank',member='s',member_content_hash=digest(raw)))
        self.assertEqual(self.r.skill_instance('i')['frozen_content']['content'],raw)
        with self.assertRaisesRegex(ValidationError,'frozen bank member'):
            self.put(self.obj('bad','skill_instance',bank_id='bank',member='s',member_content_hash='0'*64))
    def test_new_bank_pins_assets_without_copying_package_text(self):
        self.put(self.asset(),self.obj('bank','skill_bank',asset_ids=['asset-v1']))
        bank=self.r.skill_bank('bank');self.assertEqual(bank['skill_count'],1)
        listed=views.members(self.r,'bank');self.assertEqual(listed['total'],1)
        detail=views.skill_detail(self.r,'bank','assets/verify-source')
        self.assertEqual(detail['member']['content']['package']['files']['SKILL.md'],'Check the primary source')
        usage=self.r.skill_usage('bank','/asset_ids/0')
        self.assertEqual(usage['member']['asset_id'],'asset-v1');self.assertEqual(usage['uses'],[])
        self.put(self.obj('asset-retrieval','skill_retrieval',bank_id='bank',selected=['/asset_ids/0']))
        self.assertEqual(self.r.details('asset-retrieval')['object']['data']['selected'],['/asset_ids/0'])
    def test_retirement_does_not_mutate_asset_or_other_run(self):
        self.put(self.asset(),self.instance())
        before=self.r.object('asset-v1')
        self.put(self.obj('retire','skill_evolution',asset_id='asset-v1',event='retired',reason='Observed regression',effective_scope={'project_id':'p','experiment_id':'e','run_id':'a'}))
        self.assertEqual(self.r.object('asset-v1'),before);self.assertEqual(self.r.skill_lifecycle('e','b')['counts']['events'],0)
        self.assertEqual(self.r.skill_instance('i')['asset_ref']['asset_id'],'asset-v1')
    def test_lifecycle_does_not_let_retirement_escape_scope(self):
        self.put(self.asset())
        with self.assertRaisesRegex(ValidationError,'effective_scope'):
            self.put(self.obj('retire','skill_evolution',asset_id='asset-v1',event='retired',effective_scope={'experiment_id':'other'}))
    def test_mode_is_explicit_and_run_profile_overrides_parent(self):
        self.put(self.obj('profile','method_profile',families=['skill'],method_name='Name does not determine mode',skill_mode='fixed_policy_skill_iteration'),run='')
        self.assertEqual(self.r.skill_runtime('e','a')['mode']['value'],'fixed_policy_skill_iteration')
        self.put(self.obj('override','method_profile',families=['skill'],method_name='SkillRL',skill_mode='fixed_skills_rl'))
        self.assertEqual(self.r.skill_runtime('e','a')['mode']['value'],'fixed_skills_rl')
        with self.assertRaisesRegex(ValidationError,'Conflicting skill_mode'):
            self.put(self.obj('bad','method_profile',families=['skill'],method_name='x',skill_mode='fixed_skills_rl',configuration={'skill_mode':'joint_evolution'}))
    def test_new_instance_activates_skill_without_unrelated_families(self):
        self.put(self.inline())
        self.assertEqual([f['id'] for f in views.experiment_overview(self.r,'e','a')['families']],['skill'])
        self.assertEqual(views.experiment_overview(self.r,'e','b')['families'],[])
    def test_unknown_denominator_and_measured_zero_remain_distinct(self):
        self.put(self.asset(),self.instance('unknown'),self.instance('absent',loading={'rendered_text':'Check the primary source','final_input':'Question only','complete':True}))
        counts=self.r.skill_runtime('e','a')['counts']['loaded']
        self.assertEqual(counts,{'observed':0,'not_observed':1,'unknown':1,'denominator':2,'known_denominator':1,'value':0})
    def test_step_filters_do_not_invent_temporal_extent(self):
        self.put(self.asset(),self.instance(related_actions=[{'step':7,'tool_call_id':'explicit'}]))
        self.assertEqual(self.r.skill_runtime('e','a',step=7)['total'],1)
        self.assertEqual(self.r.skill_runtime('e','a',step=3)['total'],0)
        self.assertEqual(self.r.skill_runtime('e','a',step=100)['total'],0)
    def test_formal_runtime_binding_and_episode_filter(self):
        tid=self.evaluation()
        self.put(self.asset(),self.instance(evaluation_id='eval',trajectory_id=tid))
        self.assertEqual(self.r.skill_runtime('e','a',evaluation_id='eval',trajectory_id=tid)['total'],1)
        with self.assertRaisesRegex(ValidationError,'outside selected'):self.r.skill_runtime('e','b',evaluation_id='eval')
        with self.assertRaisesRegex(ValidationError,'another experiment/run'):
            self.put(self.instance('bad',evaluation_id='eval',trajectory_id=tid),run='b')
    def test_runtime_overview_does_not_load_asset_or_instance_bodies(self):
        self.put(self.asset(body='X'*100000),self.instance())
        with patch.object(self.r,'object',side_effect=AssertionError('detail body read')):
            got=self.r.skill_runtime('e','a');self.assertEqual(got['total'],1)
            self.assertNotIn('package',json.dumps(got))
    def test_pagination_scope_and_unknown_mode(self):
        self.put(self.asset(),*[self.instance('i'+str(i)) for i in range(4)])
        page=self.r.skill_runtime('e','a',offset=2,limit=1)
        self.assertEqual((page['total'],len(page['items']),page['has_more']),(4,1,True))
        self.assertEqual(page['mode']['value'],'unknown');self.assertEqual(self.r.skill_runtime('e','')['total'],0)
    def test_reported_utility_is_not_verified_gain(self):
        self.put(self.asset(),self.obj('audit','skill_evolution',asset_id='asset-v1',event='audit',reported_utility={'utility':0.9}))
        event=self.r.skill_lifecycle('e','a')['events']['items'][0]
        self.assertFalse(event['gain_verified']);self.assertEqual(event['reported_utility'],{'utility':0.9})
    def test_ambiguous_instance_reference_rejected(self):
        self.put(self.asset())
        with self.assertRaisesRegex(ValidationError,'exactly one'):
            self.put(self.instance(inline_skill={'text':'x','content_hash':digest('x')}))


if __name__=='__main__':unittest.main()
