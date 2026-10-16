"""v0.8.1: explicit experiment/run context without cross-method or cross-run mixing."""
import json,tempfile,threading,unittest,urllib.request,urllib.error
from unittest.mock import patch
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.methods import views
from searchagent_lab.methods.routes import scope_from
from searchagent_lab.core import ValidationError,NotFoundError
from searchagent_lab.server import LabServer
from test_method_data import definition

class ExperimentMethodContextTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.rec=MethodRecorder(self.tmp.name);self.repo=self.rec.repo
  s=self.rec.store;s.register_project({'id':'p','name':'Self-play Skill Entropy Latent'})
  for eid in ('e','other'):
   s.register_experiment({'id':eid,'name':'Self-play Skill Entropy Latent','project_id':'p'})
  for rid,eid in [('a','e'),('b','e'),('c','other')]:s.register_run({'id':rid,'name':'Synthetic '+rid,'experiment_id':eid,'demo':True})
 def tearDown(self):self.rec.close();self.tmp.cleanup()
 def obj(self,oid,kind,data,scope):
  return self.repo.import_bundle({'method_schema':'method-evidence-v1','objects':[{'id':oid,'kind':kind,'demo':True,'data':data}]},scope=scope)
 def bank(self,oid='bank-a',scope=None):
  return self.obj(oid,'skill_bank',{'version':'synthetic','general_skills':[{'id':'s1','name':'Synthetic skill','principle':'Example content','when_to_apply':'UI testing only'}]},scope or {'run_id':'a'})
 def profile(self,oid,families,scope):return self.obj(oid,'method_profile',{'method_name':'Synthetic recipe','families':families},scope)
 def test_explicit_experiment_required(self):
  with self.assertRaises(ValidationError):views.experiment_overview(self.repo,'')
 def test_names_never_create_method_tabs(self):self.assertEqual(views.experiment_overview(self.repo,'e','a')['families'],[])
 def test_actual_bank_without_profile_activates_skill(self):
  self.bank();d=views.experiment_overview(self.repo,'e','a');self.assertEqual([x['id'] for x in d['families']],['skill']);self.assertEqual(d['profiles'],[])
 def test_sibling_run_not_combined(self):
  self.bank();self.rec.entropy_definition('h',definition(),{'run_id':'b'});self.assertEqual([x['id'] for x in views.experiment_overview(self.repo,'e','a')['families']],['skill']);self.assertEqual([x['id'] for x in views.experiment_overview(self.repo,'e','b')['families']],['entropy'])
 def test_cross_experiment_run_rejected(self):
  with self.assertRaises(ValidationError):views.experiment_overview(self.repo,'e','c')
 def test_unknown_run_rejected(self):
  with self.assertRaises(NotFoundError):views.experiment_overview(self.repo,'e','missing')
 def test_unknown_experiment_rejected(self):
  with self.assertRaises(NotFoundError):views.experiment_overview(self.repo,'missing')
 def test_no_run_means_unbound_not_all_runs(self):
  self.bank();d=views.experiment_overview(self.repo,'e');self.assertEqual(d['scope']['run_id'],'');self.assertEqual(d['counts'],{})
 def test_experiment_level_bank_not_injected_into_run(self):
  self.bank('experiment-bank',{'experiment_id':'e'});self.assertEqual(views.experiment_overview(self.repo,'e','a')['families'],[]);self.assertEqual([x['id'] for x in views.experiment_overview(self.repo,'e')['families']],['skill'])
 def test_parent_profile_inherited_as_configuration_not_evidence(self):
  self.profile('p-exp',['skill'],{'experiment_id':'e'});d=views.experiment_overview(self.repo,'e','a');self.assertEqual(d['profile_origin'],'experiment');self.assertFalse(d['families'][0]['recorded']);self.assertEqual(d['families'][0]['state'],'declared_only')
 def test_run_profile_overrides_parent_family_declarations(self):
  self.profile('p-exp',['skill'],{'experiment_id':'e'});self.profile('p-run',['entropy'],{'run_id':'a'});d=views.experiment_overview(self.repo,'e','a');self.assertEqual([x['id'] for x in d['families']],['entropy']);self.assertEqual(d['profile_origin'],'run');self.assertEqual(len(d['inherited_profiles']),1)
 def test_actual_records_not_hidden_by_profile(self):
  self.profile('p-run',['entropy'],{'run_id':'a'});self.bank();self.assertEqual([x['id'] for x in views.experiment_overview(self.repo,'e','a')['families']],['skill','entropy'])
 def test_generic_training_use_does_not_infer_self_play(self):
  with patch.object(views,'overview',return_value={'scope':{},'profiles':[],'counts':{'training_use':3},'families':[{'id':'self_play'}]}):self.assertEqual(views.experiment_overview(self.repo,'e','a')['families'],[])
 def test_overview_does_not_load_skill_payload(self):
  self.bank()
  with patch.object(self.repo,'object',side_effect=AssertionError('no full object loads')):self.assertEqual(views.experiment_overview(self.repo,'e','a')['counts']['skill_bank'],1)
 def test_exact_unbound_filter(self):self.assertEqual(scope_from({'experiment_id':'e','unbound':'1'}),{'experiment_id':'e','run_id':''})
 def test_ambiguous_scope_rejected(self):
  with self.assertRaises(ValidationError):scope_from({'run_id':'a','unbound':'1'})
 def test_context_does_not_mutate_records(self):
  self.bank();before=self.repo.object('bank-a');views.experiment_overview(self.repo,'e','a');self.assertEqual(self.repo.object('bank-a'),before)
 def test_http_context_and_scope_validation(self):
  self.bank()
  with LabServer(('127.0.0.1',0),self.rec.store) as server:
   th=threading.Thread(target=server.serve_forever,daemon=True);th.start();base='http://127.0.0.1:'+str(server.server_address[1])
   try:
    with urllib.request.urlopen(base+'/api/methods/experiment-overview?experiment_id=e&run_id=a') as r:d=json.load(r)
    self.assertEqual([x['id'] for x in d['families']],['skill'])
    with self.assertRaises(urllib.error.HTTPError) as c:urllib.request.urlopen(base+'/api/methods/experiment-overview?experiment_id=other&run_id=a')
    self.assertEqual(c.exception.code,400)
    with urllib.request.urlopen(base+'/api/methods/catalog?experiment_id=e&unbound=1&kind=skill_bank') as r:d=json.load(r)
    self.assertEqual(d['total'],0)
   finally:server.shutdown();th.join()

if __name__=='__main__':unittest.main()
