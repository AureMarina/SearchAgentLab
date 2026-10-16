"""Controlled matrix/ablation tests; fixtures never become research results."""
import copy
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from searchagent_lab.core import ValidationError
from searchagent_lab.inspection.repository import digest
from searchagent_lab.methods.skill_comparison import checked_skill_gain
from searchagent_lab.server import LabServer
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub, gid
from searchagent_lab.team.package import init_source, export_run

PROTOCOL = json.loads((Path(__file__).resolve().parents[1]/'examples/protocol_v2.synthetic.json').read_text())


class SkillComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'local'
        self.rec = StudioRecorder(self.path)
        self.store, self.methods = self.rec.store, self.rec.methods
        self.store.register_project({'id':'p','name':'Synthetic'})
        self.store.register_experiment({'id':'e','project_id':'p','name':'Synthetic Skill controls'})
        self.rec.repo.register_profile({'experiment_id':'e','track':'main'})
        self.store.register_run({'id':'run','experiment_id':'e','name':'Synthetic','demo':True,'base_model':'fixture','model_revision':'base-v1'})
        self.store.register_panel({'id':'panel','name':'Synthetic frozen tasks','split':'probe','demo':True,
            'samples':[{'id':'q1','dataset':'fixture','question':'One?','answers':['yes']},
                       {'id':'q2','dataset':'fixture','question':'Two?','answers':['yes']}]})
        for cid, step, weight in [('cp0',0,'a'),('cpt',10,'b')]:
            self.store.register_checkpoint({'id':cid,'run_id':'run','step':step,'weights_sha256':weight*64})
        package = {'files':{'SKILL.md':'Synthetic checking skill','scripts/check.py':'print("fixture")'}}
        self.emit('s1','skill_asset',{'skill_id':'check','version':'1','package':package,'package_hash':digest(package)})
        package2 = {'files':{'SKILL.md':'Synthetic checking skill, revised','scripts/check.py':'print("fixture v2")'}}
        self.emit('s2','skill_asset',{'skill_id':'check','version':'2','package':package2,'package_hash':digest(package2)})
        self.emit('b0','skill_bank',{'asset_ids':['s1']})
        self.emit('bt','skill_bank',{'asset_ids':['s2']})
        self.emit('empty','skill_bank',{'asset_ids':[]})

    def tearDown(self):
        self.rec.close();self.tmp.cleanup()

    def emit(self, oid, kind, data, scope=None):
        return self.methods.import_bundle({'method_schema':'method-evidence-v1','objects':[
            {'id':oid,'kind':kind,'demo':True,'data':data}]},scope=scope or {'run_id':'run'})

    def evaluate(self, eid, cp, bank, correct=1, *, retrieval=None, change=None, origin='recorded_execution',
                 no_binding=False, autonomous=True, finish=True, purpose='checkpoint_eval'):
        protocol = copy.deepcopy(PROTOCOL);protocol['harness_version']='c'*64
        if change:change(protocol)
        self.store.start_evaluation({'id':eid,'checkpoint_id':cp,'panel_id':'panel','protocol':protocol,
            'purpose':purpose,'comparison':{'allowed_method_changes':['/bank_content_hash','/retrieval_config/top_k']}})
        for i in (1,2):
            self.store.log_trajectory(eid,{'id':eid+'-q'+str(i),'dataset':'fixture','sample_id':'q'+str(i),
                'scores':{'em':int(i<=correct)},'steps':[],'runtime_status':'ok','termination_reason':'final_answer',
                'usage':{'input_tokens':10,'output_tokens':5} if i==1 else {}})
        if finish:self.store.finish_evaluation(eid)
        if not no_binding:
            binding={'evaluation_id':eid,'binding_origin':origin,'state_complete':True,'independent':True,
                     'autonomous':autonomous,'retrieval_config':retrieval or {'mode':'fixed','top_k':2}}
            binding.update({'bank_id':bank} if bank else {'skills_disabled':True})
            self.emit('bind-'+eid,'evaluation_binding',binding)

    def matrix_data(self, cells):
        return {'comparison_type':'cross_matrix','initial_checkpoint_id':'cp0','current_checkpoint_id':'cpt',
                'initial_bank_id':'b0','current_bank_id':'bt','cells':cells}

    def full_matrix(self):
        cells=[]
        values={('initial','off'):0,('initial','initial_bank'):1,('initial','current_bank'):1,
                ('current','off'):1,('current','initial_bank'):1,('current','current_bank'):2}
        for row, cp in [('initial','cp0'),('current','cpt')]:
            for column, bank in [('off',None),('initial_bank','b0'),('current_bank','bt')]:
                eid=row+'-'+column
                self.evaluate(eid,cp,bank,values[row,column])
                cells.append({'row_id':row,'column_id':column,'evaluation_id':eid})
        self.emit('matrix','skill_comparison',self.matrix_data(cells))
        return self.methods.skill_comparison('matrix')

    def ablation(self, *, without='empty', changed=None, target='s2'):
        self.evaluate('with','cpt','bt',2)
        self.evaluate('without','cpt',without,1,change=changed)
        self.emit('ablation','skill_comparison',{'comparison_type':'skill_ablation','checkpoint_id':'cpt',
            'skill_asset_id':target,'with_evaluation_id':'with','without_evaluation_id':'without'})
        return self.methods.skill_comparison('ablation')

    def test_complete_matrix_separates_policy_bank_and_interaction(self):
        result=self.full_matrix();effects={e['id']:e for e in result['effects']}
        self.assertEqual(result['status'],'measured')
        self.assertEqual(len(result['matrix']['cells']),6)
        self.assertEqual(effects['policy_off']['value'],.5)
        self.assertEqual(effects['bank_initial']['value'],0)
        self.assertEqual(effects['bank_current']['value'],.5)
        self.assertEqual(effects['interaction']['value'],.5)
        self.assertFalse(result['causal_instance_credit'])

    def test_empty_matrix_stays_unmeasured_despite_existing_evaluations(self):
        self.evaluate('latest-good','cpt','bt',2)
        self.emit('empty-matrix','skill_comparison',self.matrix_data([]))
        result=self.methods.skill_comparison('empty-matrix')
        self.assertTrue(all(c['value'] is None and c['status']=='unmeasured' for c in result['matrix']['cells']))

    def test_duplicate_cell_and_duplicate_evaluation_rejected(self):
        self.evaluate('ev','cp0','b0')
        cell={'row_id':'initial','column_id':'initial_bank','evaluation_id':'ev'}
        for cells in ([cell,cell],[cell,{**cell,'column_id':'current_bank'}]):
            with self.assertRaises(ValidationError):self.emit('duplicate','skill_comparison',self.matrix_data(cells))

    def test_wrong_bank_or_checkpoint_is_visible_as_incomparable(self):
        self.evaluate('wrong','cpt','bt')
        self.emit('m','skill_comparison',self.matrix_data([{'row_id':'initial','column_id':'initial_bank','evaluation_id':'wrong'}]))
        cell=self.methods.skill_comparison('m')['matrix']['cells'][1]
        self.assertEqual(cell['status'],'incomparable');self.assertIsNone(cell['value']);self.assertEqual(cell['raw_value'],.5)
        self.assertEqual(len([r for r in cell['reasons'] if 'checkpoint' in r or '冻结版本' in r]),2)

    def test_unbound_state_never_implies_off(self):
        self.evaluate('unbound','cp0',None,no_binding=True)
        self.emit('m','skill_comparison',self.matrix_data([{'row_id':'initial','column_id':'off','evaluation_id':'unbound'}]))
        self.assertEqual(self.methods.skill_comparison('m')['matrix']['cells'][0]['status'],'incomparable')

    def test_budget_difference_blocks_bank_effect(self):
        self.evaluate('old','cp0','b0')
        self.evaluate('new','cp0','bt',2,change=lambda p:p['budget'].update(max_tool_calls=9))
        self.emit('m','skill_comparison',self.matrix_data([
            {'row_id':'initial','column_id':'initial_bank','evaluation_id':'old'},
            {'row_id':'initial','column_id':'current_bank','evaluation_id':'new'}]))
        effect=next(e for e in self.methods.skill_comparison('m')['effects'] if e['id']=='bank_initial')
        self.assertIsNone(effect['value']);self.assertIn('预算',effect['reason'])

    def test_declared_routing_change_still_is_not_a_pure_bank_effect(self):
        self.evaluate('old','cp0','b0')
        self.evaluate('new','cp0','bt',2,retrieval={'mode':'fixed','top_k':3})
        self.assertTrue(self.rec.repo.evaluation_pair('old','new','method_state')['compatible'])
        self.emit('m','skill_comparison',self.matrix_data([
            {'row_id':'initial','column_id':'initial_bank','evaluation_id':'old'},
            {'row_id':'initial','column_id':'current_bank','evaluation_id':'new'}]))
        effect=next(e for e in self.methods.skill_comparison('m')['effects'] if e['id']=='bank_initial')
        self.assertIsNone(effect['value']);self.assertIn('路由',effect['reason'])

    def test_retrospective_and_non_autonomous_results_are_not_qualified(self):
        self.evaluate('ev','cp0','b0',origin='retrospective',autonomous=False)
        self.emit('m','skill_comparison',self.matrix_data([{'row_id':'initial','column_id':'initial_bank','evaluation_id':'ev'}]))
        cell=self.methods.skill_comparison('m')['matrix']['cells'][1]
        self.assertEqual(cell['status'],'incomparable');self.assertIsNone(cell['value'])

    def test_incomplete_execution_is_not_zero(self):
        self.evaluate('unfinished','cp0','b0',finish=False)
        self.emit('m','skill_comparison',self.matrix_data([{'row_id':'initial','column_id':'initial_bank','evaluation_id':'unfinished'}]))
        cell=self.methods.skill_comparison('m')['matrix']['cells'][1]
        self.assertIsNone(cell['value']);self.assertIsNone(cell['raw_value'])

    def test_exact_asset_removal_and_paired_cost_denominators(self):
        result=self.ablation()
        self.assertEqual(result['utility'],.5)
        self.assertEqual(result['removal_check']['status'],'verified')
        self.assertEqual(result['effects'][0]['paired_usage']['total_tokens']['paired'],1)
        self.assertEqual(result['effects'][0]['paired_usage']['total_tokens']['missing'],1)

    def test_removing_last_asset_may_explicitly_use_off(self):
        result=self.ablation(without=None)
        self.assertEqual(result['utility'],.5)

    def test_swap_is_not_single_skill_removal(self):
        result=self.ablation(without='b0')
        self.assertEqual(result['status'],'incomparable');self.assertIsNone(result['utility'])

    def test_ablation_requires_fixed_budget_and_target(self):
        result=self.ablation(changed=lambda p:p['budget'].update(max_tool_calls=10))
        self.assertIsNone(result['utility']);self.assertIn('预算',result['reason'])

    def test_cross_matrix_is_not_individual_credit(self):
        self.full_matrix()
        result=checked_skill_gain(self.methods,'matrix')
        self.assertIsNone(result['value']);self.assertFalse(result['causal_instance_credit'])

    def test_matching_instance_external_gain_and_wrong_version_remain_distinct(self):
        self.ablation()
        self.emit('use','skill_instance',{'asset_id':'s2','evaluation_id':'with','trajectory_id':'with-q1','selection':{'source':'model'},'comparison_ids':['ablation']})
        result=self.methods.skill_runtime('e','run','with')['items'][0]
        self.assertEqual(result['observations']['gain']['effect'],.5)
        self.assertFalse(result['observations']['gain']['causal_instance_credit'])
        wrong={**self.methods.object('use'),'data':{**self.methods.object('use')['data'],'asset_id':'s1'}}
        self.assertIsNone(checked_skill_gain(self.methods,'ablation',wrong)['value'])

    def test_cross_run_comparison_is_rejected(self):
        self.store.register_run({'id':'other','experiment_id':'e','name':'Other','demo':True})
        self.store.register_checkpoint({'id':'other-cp','run_id':'other','step':0})
        with self.assertRaises(ValidationError):
            self.emit('outside','skill_comparison',{**self.matrix_data([]),'current_checkpoint_id':'other-cp'})

    def test_http_matrix_and_runtime_routes(self):
        self.full_matrix()
        with LabServer(('127.0.0.1',0),self.store) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base='http://127.0.0.1:'+str(server.server_address[1])
                for endpoint in ('/api/methods/skill-comparison?id=matrix','/api/methods/skill-runtime?experiment_id=e&run_id=run',
                                 '/api/methods/skill-lifecycle?experiment_id=e&run_id=run','/api/methods/skill-assets?experiment_id=e&run_id=run'):
                    with urllib.request.urlopen(base+endpoint) as response:self.assertIn('scope',json.load(response))
            finally:server.shutdown();thread.join()

    def test_team_roundtrip_rewrites_typed_ids_keeps_frozen_package_and_credit(self):
        self.full_matrix()
        self.emit('inline','skill_instance',{'inline_skill':{'text':'literal cp0 matrix IDs','content_hash':digest('literal cp0 matrix IDs')},
            'evaluation_id':'current-off','trajectory_id':'current-off-q1','selection':{'source':'model','step':0}})
        self.emit('credit','training_use',{'artifact_ids':['inline'],'status':'sampled','batch_id':'external-batch',
            'credit_assignment':{'instance_id':'inline','skill_decision':{'reward':1,'advantage':.2,'mask':1,'source':'fixture'}}})
        source=init_source(self.path,'fixture','v094');package=export_run(self.path,'run')
        with Hub(Path(self.tmp.name)/'center') as hub:
            hub.ingest(package['path'],allow_definitions=True)
            mid=lambda value:gid(source['workspace_id'],'method',value)
            matrix=hub.methods.object(mid('matrix'))
            self.assertEqual(matrix['data']['initial_checkpoint_id'],gid(source['workspace_id'],'checkpoint','cp0'))
            self.assertEqual(matrix['data']['cells'][0]['evaluation_id'],gid(source['workspace_id'],'evaluation','initial-off'))
            self.assertEqual(hub.methods.object(mid('b0'))['data']['asset_ids'],[mid('s1')])
            self.assertEqual(hub.methods.object(mid('inline'))['data']['inline_skill']['text'],'literal cp0 matrix IDs')
            self.assertEqual(hub.methods.object(mid('credit'))['data']['credit_assignment']['instance_id'],mid('inline'))
            self.assertEqual(hub.methods.skill_comparison(mid('matrix'))['status'],'measured')

    def test_asset_bank_member_selectors_roundtrip_without_rewriting_names_or_pointers(self):
        asset_hash=self.methods.object('s1')['data']['package_hash']
        for index,selector in enumerate(('s1','assets/check','/asset_ids/0')):
            self.emit('member-'+str(index),'skill_instance',{'bank_id':'b0','member':selector,'member_content_hash':asset_hash,
                'selection':{'source':'model'},'note':'s1 is literal source text'})
        self.emit('asset-retrieval','skill_retrieval',{'bank_id':'b0','candidates':['s1','assets/check'],
            'selected':[{'member':'s1','content_hash':asset_hash}],
            'injections':[{'member':'s1','rendered_text':'s1 is literal source text'}]})
        source=init_source(self.path,'fixture','v094');package=export_run(self.path,'run')
        with Hub(Path(self.tmp.name)/'center') as hub:
            hub.ingest(package['path'],allow_definitions=True)
            mid=lambda value:gid(source['workspace_id'],'method',value)
            expected=(mid('s1'),'assets/check','/asset_ids/0')
            for index,selector in enumerate(expected):
                item=hub.methods.skill_instance(mid('member-'+str(index)))
                self.assertEqual(item['object']['data']['member'],selector)
                self.assertEqual(item['frozen_content']['content_hash'],asset_hash)
                self.assertEqual(item['object']['data']['note'],'s1 is literal source text')
            retrieval=hub.methods.object(mid('asset-retrieval'))['data']
            self.assertEqual(retrieval['candidates'],[mid('s1'),'assets/check'])
            self.assertEqual(retrieval['selected'][0]['member'],mid('s1'))
            self.assertEqual(retrieval['injections'][0]['member'],mid('s1'))
            self.assertEqual(retrieval['injections'][0]['rendered_text'],'s1 is literal source text')


if __name__=='__main__':unittest.main()
