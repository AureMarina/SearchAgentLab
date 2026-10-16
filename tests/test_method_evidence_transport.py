"""New typed method evidence remains navigable after collector namespacing."""
import tempfile
from pathlib import Path
import unittest

from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.package import export_run, read_package
from searchagent_lab.team.hub import Hub, gid
from test_team import seed_source


class MethodEvidenceTransportTests(unittest.TestCase):
    def test_opd_links_checkpoint_and_original_teacher_context_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            local, center = Path(tmp) / 'local', Path(tmp) / 'center'
            source = seed_source(local)
            objects = [
                {'id':'failure','kind':'opd_failure','data':{'sample_id':'q','student_checkpoint_id':'c',
                    'state':{'status':'captured','state_id':'original-state'},'failure_step':1,'attempt_budget':1,'eligible':True}},
                {'id':'teacher','kind':'opd_teacher','data':{'failure_id':'failure','teacher_id':'external-teacher-v1',
                    'mode':'continuation','status':'completed','attempt_index':1,'state_alignment':'restored',
                    'context':{'raw_input':'c failure teacher are literal context; keep them'},
                    'verification':{'status':'passed','method':'environment','verifier':'synthetic-v1'}},
                    'trace_ref':None},
                {'id':'supervision','kind':'opd_supervision','data':{'failure_id':'failure','teacher_ids':['teacher'],
                    'objective':'distillation','status':'accepted','loss_active':True,'loss_mask_tokens':3,'loss_weight':1}},
                {'id':'use','kind':'training_use','data':{'artifact_ids':['supervision'],'status':'sampled','batch_id':'b','step':1}},
                {'id':'update','kind':'training_update','data':{'consumption_ids':['use'],'status':'completed',
                    'input_checkpoint_id':'c','output_checkpoint_id':'c'}},
                {'id':'bank','kind':'skill_bank','data':{'general_skills':[{'skill_id':'s1','name':'Synthetic','principle':'Test only'}]}},
                {'id':'retrieval','kind':'skill_retrieval','data':{'bank_id':'bank','trajectory_id':'t',
                    'candidates':['general_skills/s1'],'selected':['general_skills/s1'],'injections':[],'injections_complete':False}},
            ]
            # The separate raw execution ref is explicitly inside data.
            objects[1].pop('trace_ref')
            objects[1]['data']['trace_ref']={'object_id':'m'}
            with StudioRecorder(local) as rec:
                rec.methods.import_bundle({'method_schema':'method-evidence-v1','objects':[
                    {**obj,'demo':True} for obj in objects]},scope={'run_id':'run'})
            package=export_run(local,'run')
            _, records, _=read_package(package['path'])
            original=next(o for o in records['methods'] if o['id']=='teacher')
            self.assertEqual(original['data']['context']['raw_input'],'c failure teacher are literal context; keep them')
            with Hub(center) as hub:
                result=hub.ingest(package['path'],allow_definitions=True)
                mid=lambda oid:gid(source['workspace_id'],'method',oid)
                failure=hub.methods.object(mid('failure'))
                teacher=hub.methods.object(mid('teacher'))
                supervision=hub.methods.object(mid('supervision'))
                self.assertEqual(failure['data']['student_checkpoint_id'],gid(source['workspace_id'],'checkpoint','c'))
                self.assertEqual(teacher['data']['failure_id'],failure['id'])
                self.assertEqual(supervision['data']['teacher_ids'],[teacher['id']])
                self.assertEqual(teacher['data']['context'],original['data']['context'])
                self.assertEqual(teacher['data']['teacher_id'],'external-teacher-v1')
                self.assertTrue(hub.methods.resolve_ref(teacher['data']['trace_ref'])['verified'])
                self.assertEqual(hub.methods.object(mid('retrieval'))['data']['trajectory_id'],gid(source['workspace_id'],'trajectory','t'))
                self.assertEqual(hub.methods.object(mid('update'))['data']['input_checkpoint_id'],gid(source['workspace_id'],'checkpoint','c'))
                self.assertEqual(hub.methods.object(mid('use'))['data']['batch_id'],gid(source['workspace_id'],'batch','b'))
                self.assertEqual(hub.ingest(package['path'])['delivery_status'],'duplicate')
                self.assertEqual(failure['scope']['run_id'],result['run_id'])


if __name__=='__main__':unittest.main()
