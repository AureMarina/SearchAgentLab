"""0.8.0 snapshot lifecycle, typed metadata, read indexes and transport regression.

Actual archive assertions describe the user's saved files, never model outcomes.
Other fixtures are explicitly synthetic.
"""
import os
import copy,gzip,hashlib,http.client,io,json,tempfile,threading,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from searchagent_lab.core import ValidationError,NotFoundError
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.methods.schema import SCHEMA
from searchagent_lab.methods import views
from searchagent_lab.server import LabServer,Handler
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.package import export_run,init_source
from searchagent_lab.team.hub import Hub,gid
from searchagent_lab.team.server import TeamServer
from searchagent_lab.team.auth import Auth
from test_team import seed_source

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=Path(os.environ.get('SEARCHAGENT_LAB_SKILLRL_ARCHIVE', ROOT.parent/'eval-data/reference-assets/skillrl/SkillRL_skills_20261005.zip'))

def mutate_zip(change):
    with zipfile.ZipFile(ARCHIVE) as z:data={x:z.read(x) for x in z.namelist()}
    change(data);out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for name,raw in data.items():z.writestr(name,raw)
    return out.getvalue()

def definition():return {'name':'Synthetic full-vocab','estimator':'full_vocab','unit':'nats',
 'distribution':{'policy_revision':'synthetic','logits_stage':'pre_temperature','temperature':1,'top_k':'disabled','top_p':1},
 'token_scope':{'region':'query','mask_definition':'response_only'},'aggregation':{'level':'decision','weighting':'token_mean'},
 'implementation':{'git_commit':'synthetic','function':'entropy.compute'}}

class MethodDataTests(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.rec=MethodRecorder(self.tmp.name);self.r=self.rec.repo
 def tearDown(self):self.rec.close();self.tmp.cleanup()
 def archive(self,scope=None):
    out=self.rec.import_file(ARCHIVE,scope);self.a=views.archive(self.r,out['archive_id']);return out
 def project(self,p='p',run='r'):
    self.rec.store.register_project({'id':p,'name':'Entropy'})
    self.rec.store.register_experiment({'id':p+'-e','project_id':p,'name':'Synthetic'})
    self.rec.store.register_run({'id':run,'experiment_id':p+'-e','name':'synthetic'})
 def record(self,*rows,scope=None):return self.r.import_bundle({'method_schema':SCHEMA,'objects':list(rows)},scope=scope)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_original_zip_preserved_byte_for_byte(self):
    x=self.archive();self.assertEqual(self.r.original(x['import_id'])['content'],ARCHIVE.read_bytes())
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_import_does_not_create_training_or_evaluations(self):
    self.archive();self.assertEqual(self.rec.store.runs(),[]);self.assertEqual(self.rec.store.conn.execute('SELECT count(*) FROM evaluations').fetchone()[0],0)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_exact_snapshot_count_and_phase_counts(self):
    self.archive();s=self.a['snapshots'];self.assertEqual(len(s),23);self.assertEqual([sum(x['phase']==p for x in s) for p in ('initial','training','evaluation')],[1,21,1])
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_total_not_confused_with_general_skills(self):
    self.archive();self.assertEqual(self.a['initial_counts']['skill_count'],30);self.assertEqual(self.a['last_training_counts']['skill_count'],92);self.assertEqual(self.a['last_training_counts']['general_skills'],72)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_query_type_and_mistakes_separate(self):
    self.archive();self.assertTrue(all(s['counts']['query_type_skills']==20 and s['counts']['mistake_count']==11 for s in self.a['snapshots']))
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_additions_observed_not_invented_modifications(self):
    self.archive();ds=[s['changes'] for s in self.a['snapshots'] if s['phase']=='training'];self.assertEqual(sum(d['added'] for d in ds),62);self.assertEqual(sum(d['modified']+d['removed'] for d in ds),0)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_frozen_bank_matches_step100_not_latest105(self):
    self.archive();f=self.a['snapshots'][-1];same=self.a['snapshots'][f['byte_identical_training_index']];self.assertEqual(same['step'],100);self.assertEqual(f['file_sha256'],same['file_sha256']);self.assertEqual(f['counts']['skill_count'],89);self.assertEqual(self.a['snapshots'][-2]['step'],105)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_late_skill_absent_from_frozen_eval(self):
    x=self.archive();life=views.lifecycle(self.r,x['archive_id'],'general_skills/dyn_060');self.assertEqual(life['first_observed']['step'],105);self.assertEqual(life['timeline'][-1]['event'],'absent_in_frozen_snapshot');self.assertEqual(life['creation_time'],'not_recorded')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_unchanged_original_skill_history(self):
    x=self.archive();life=views.lifecycle(self.r,x['archive_id'],'general_skills/gen_001');self.assertEqual(len({r['content_hash'] for r in life['timeline']}),1);self.assertFalse(life['causal_claim'])
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_no_unlogged_usage_fabrication(self):
    x=self.archive();self.assertEqual(self.a['coverage']['candidate_selection'],'not_recorded');u=self.r.skill_usage(self.a['snapshots'][0]['bank_id'],'gen_001');self.assertEqual(u['uses'],[])
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_archive_repeat_is_idempotent(self):
    a=self.archive();b=self.archive();self.assertEqual(a['archive_id'],b['archive_id']);self.assertEqual(self.r.catalog()['counts'],{'skill_archive':1,'skill_bank':23})
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_same_archive_in_two_runs_stays_separate(self):
    self.project(run='one');self.rec.store.register_run({'id':'two','experiment_id':'p-e','name':'two'})
    a=self.archive({'run_id':'one'});b=self.archive({'run_id':'two'});self.assertNotEqual(a['archive_id'],b['archive_id']);self.assertEqual(self.r.catalog({'run_id':'one'})['total'],24)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_bad_file_hash_rejected_atomically(self):
    raw=mutate_zip(lambda d:d.update({'training/updated_skills_step5.json':b'{}'}))
    with self.assertRaisesRegex(ValidationError,'SHA256'):self.r.import_bytes(raw,'skills.zip')
    self.assertEqual(self.r.catalog()['total'],0)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_missing_manifest_member_rejected(self):
    raw=mutate_zip(lambda d:d.pop('training/updated_skills_step5.json'))
    with self.assertRaisesRegex(ValidationError,'Missing snapshot'):self.r.import_bytes(raw,'skills.zip')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_unlisted_json_evidence_rejected(self):
    raw=mutate_zip(lambda d:d.update({'training/extra.json':b'{}'}))
    with self.assertRaisesRegex(ValidationError,'Unlisted'):self.r.import_bytes(raw,'skills.zip')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_zip_traversal_rejected_without_extraction(self):
    raw=mutate_zip(lambda d:d.update({'../bad.json':b'{}'}))
    with self.assertRaisesRegex(ValidationError,'Unsafe'):self.r.import_bytes(raw,'skills.zip')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_duplicate_zip_entry_rejected(self):
    buf=io.BytesIO(ARCHIVE.read_bytes())
    import warnings
    with warnings.catch_warnings():
     warnings.simplefilter('ignore')
     with zipfile.ZipFile(buf,'a') as z:z.writestr('manifest.json','{}')
    with self.assertRaisesRegex(ValidationError,'duplicate'):self.r.import_bytes(buf.getvalue(),'skills.zip')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_archive_identity_not_user_overridden(self):
    with self.assertRaises(ValidationError):self.r.import_bytes(ARCHIVE.read_bytes(),'skills.zip',snapshot_id='pretend')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_skill_listing_is_paged_and_body_free(self):
    self.archive();bank=self.a['snapshots'][-1]['bank_id'];a=views.members(self.r,bank,limit=25);b=views.members(self.r,bank,offset=25,limit=25)
    self.assertEqual(a['total'],100);self.assertTrue(a['has_more']);self.assertFalse(any('content' in m for m in a['items']));self.assertTrue({m['pointer'] for m in a['items']}.isdisjoint(m['pointer'] for m in b['items']))
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_search_entire_content_not_only_first_page(self):
    self.archive();bank=self.a['snapshots'][-2]['bank_id'];q=views.members(self.r,bank,query='Title-To-Creator');self.assertEqual(q['total'],1);self.assertEqual(q['items'][0]['source_id'],'dyn_062')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_group_filter_is_explicit(self):
    self.archive();b=self.a['snapshots'][-2]['bank_id'];self.assertEqual(views.members(self.r,b,group='general_skills')['total'],72);self.assertEqual(views.members(self.r,b,group='common_mistakes')['total'],11)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_invalid_page_limit_rejected(self):
    self.archive()
    with self.assertRaises(ValidationError):views.members(self.r,self.a['snapshots'][0]['bank_id'],limit=201)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_member_diff_added_and_unchanged(self):
    self.archive();a,b=self.a['snapshots'][-3]['bank_id'],self.a['snapshots'][-2]['bank_id'];self.assertEqual(views.member_diff(self.r,a,b,'general_skills/dyn_060')['change'],'added');self.assertEqual(views.member_diff(self.r,a,b,'general_skills/gen_001')['change'],'unchanged')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_unknown_skill_not_silently_created(self):
    self.archive()
    with self.assertRaises(NotFoundError):views.skill_detail(self.r,self.a['snapshots'][0]['bank_id'],'absent')
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_catalog_does_not_read_payloads(self):
    self.archive();sql=[];self.r.conn.set_trace_callback(sql.append);self.r.catalog();self.r.conn.set_trace_callback(None);self.assertFalse(any('payload' in x for x in sql));self.assertLess(len(sql),8)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_member_page_does_not_fetch_bank_payload(self):
    self.archive();sql=[];self.r.conn.set_trace_callback(sql.append);views.members(self.r,self.a['snapshots'][0]['bank_id']);self.r.conn.set_trace_callback(None);self.assertFalse(any('payload' in x for x in sql))
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_rebuildable_indexes_preserve_original_payload(self):
    self.archive();old=[tuple(r) for r in self.r.conn.execute('SELECT id,hash,payload FROM objects ORDER BY id')];self.r.conn.execute('DELETE FROM method_summaries');self.r.conn.execute('DELETE FROM skill_members');from searchagent_lab.methods.indexes import migrate;migrate(self.r);self.assertEqual(old,[tuple(r) for r in self.r.conn.execute('SELECT id,hash,payload FROM objects ORDER BY id')]);self.assertGreater(self.r.conn.execute('SELECT count(*) FROM skill_members').fetchone()[0],1000)
 def test_index_failure_rolls_back_objects(self):
    with patch('searchagent_lab.methods.indexes.index_object',side_effect=RuntimeError('injected')):
     with self.assertRaises(RuntimeError):self.record({'id':'x','kind':'trace','data':{}})
    self.assertEqual(self.r.objects(),[]);self.assertEqual(self.r.conn.execute('SELECT count(*) FROM method_summaries').fetchone()[0],0)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_method_family_not_guessed_from_project_name(self):
    self.project();self.archive({'run_id':'r'});ov=views.overview(self.r,{'run_id':'r'});self.assertTrue(next(x for x in ov['families'] if x['id']=='skill')['recorded']);self.assertFalse(next(x for x in ov['families'] if x['id']=='entropy')['recorded'])
 def test_multiple_explicit_families_allowed(self):
    self.rec.method_profile('p','Synthetic',['skill','entropy'],{},{});o=views.overview(self.r);self.assertEqual({f['id'] for f in o['families'] if f['declared']},{'skill','entropy'})
 def test_unknown_family_not_project_label(self):
    with self.assertRaises(ValidationError):self.rec.method_profile('p','Synthetic',['my-project'],{}, {})
 def test_entropy_definition_required_fields(self):
    d=definition();d.pop('distribution')
    with self.assertRaises(ValidationError):self.rec.entropy_definition('h',d,{})
 def test_entropy_value_and_null_missingness(self):
    self.rec.entropy_definition('h',definition(),{});self.rec.entropy_observation('v',{'definition_id':'h','value':None,'missing_reason':'not_collected','context':'unknown'},{});d=views.entropy_observations(self.r,'h');self.assertIsNone(d['observations'][0]['data']['value'])
 def test_entropy_negative_rejected(self):
    self.rec.entropy_definition('h',definition(),{})
    with self.assertRaises(ValidationError):self.rec.entropy_observation('v',{'definition_id':'h','value':-1,'context':'unknown'},{})
 def test_entropy_nonfinite_rejected(self):
    self.rec.entropy_definition('h',definition(),{})
    with self.assertRaises(ValidationError):self.rec.entropy_observation('v',{'definition_id':'h','value':float('nan'),'context':'unknown'},{})
 def test_entropy_null_needs_reason(self):
    self.rec.entropy_definition('h',definition(),{})
    with self.assertRaises(ValidationError):self.rec.entropy_observation('v',{'definition_id':'h','value':None,'context':'unknown'},{})
 def test_fixed_prefix_probe_needs_actual_hash(self):
    self.rec.entropy_definition('h',definition(),{})
    with self.assertRaises(ValidationError):self.rec.entropy_observation('v',{'definition_id':'h','value':1,'context':'fixed_prefix_probe','prefix_hash':'unknown'},{})
 def test_entropy_different_definitions_not_mixed(self):
    for id in ['h1','h2']:
     self.rec.entropy_definition(id,definition(),{});self.rec.entropy_observation('v'+id,{'definition_id':id,'value':1,'context':'training_on_policy'},{})
    self.assertEqual(views.entropy_observations(self.r,'h1')['total'],1)
 def test_entropy_context_filter(self):
    self.rec.entropy_definition('h',definition(),{})
    for context in ['training_on_policy','fixed_prefix_probe']:
     self.rec.entropy_observation(context,{'definition_id':'h','value':1,'context':context,'prefix_hash':'a'*64},{})
    self.assertEqual(views.entropy_observations(self.r,'h',context='fixed_prefix_probe')['total'],1)
 def test_entropy_definition_immutable(self):
    d=definition();self.rec.entropy_definition('h',d,{});d['unit']='bits'
    with self.assertRaises(ValidationError):self.rec.entropy_definition('h',d,{})

class MetadataTransportTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_center_roundtrip_lifecycle_and_original_zip(self):
    local=self.path/'local';seed_source(local);raw=ARCHIVE.read_bytes()
    with MethodRecorder(local) as r:x=r.import_file(ARCHIVE,{'run_id':'run'})
    package=export_run(local,'run')
    with Hub(self.path/'center') as h:
     out=h.ingest(package['path'],allow_definitions=True);methods=h.methods.catalog({'run_id':out['run_id']})
     aid=next(o['id'] for o in methods['objects'] if o['kind']=='skill_archive');a=views.archive(h.methods,aid)
     self.assertEqual(a['snapshots'][-1]['counts']['skill_count'],89)
     self.assertEqual(views.lifecycle(h.methods,aid,'general_skills/dyn_060')['first_observed']['step'],105)
     originals=[h.methods.original(f['id'])['content'] for f in h.methods.imports_for(aid)];self.assertIn(raw,originals)
     self.assertEqual(h.ingest(package['path'])['delivery_status'],'duplicate')
     self.assertEqual(h.store.conn.execute('SELECT count(*) FROM trajectory_summaries').fetchone()[0],1)
     self.assertEqual(h.store.conn.execute('SELECT count(*) FROM sx.training_batch_summaries').fetchone()[0],1)
 @unittest.skipUnless(ARCHIVE.is_file(), 'Requires external SkillRL reference archive')
 def test_http_gzip_and_static_revalidation(self):
    with MethodRecorder(self.path) as rec:
     x=rec.import_file(ARCHIVE)
     with LabServer(('127.0.0.1',0),rec.store) as s:
      thread=threading.Thread(target=s.serve_forever,daemon=True);thread.start();c=http.client.HTTPConnection(*s.server_address)
      try:
       path='/api/methods/skill-archive?id='+x['archive_id'];c.request('GET',path,headers={'Accept-Encoding':'gzip'});r=c.getresponse();body=r.read();self.assertEqual(r.getheader('Content-Encoding'),'gzip');self.assertEqual(len(json.loads(gzip.decompress(body))['snapshots']),23)
       c.request('GET','/studio/method-data.js');r=c.getresponse();r.read();etag=r.getheader('ETag');self.assertTrue(etag)
       c.request('GET','/studio/method-data.js',headers={'If-None-Match':etag});r=c.getresponse();self.assertEqual(r.status,304);self.assertEqual(r.read(),b'')
       c.request('GET',path,headers={'Accept-Encoding':'gzip;q=0'});r=c.getresponse();r.read();self.assertIsNone(r.getheader('Content-Encoding'))
      finally:c.close();s.shutdown();thread.join()
 def test_center_socket_send_happens_outside_database_lock(self):
    with Hub(self.path/'center') as h:
     token,_=Auth(h.data_dir).issue('owner','viewer',[],1)
     with TeamServer(('127.0.0.1',0),h) as server:
      thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
      original=Handler._send;states=[]
      def checked(handler,*args,**kwargs):
       states.append(h.lock._is_owned());return original(handler,*args,**kwargs)
      c=http.client.HTTPConnection(*server.server_address)
      try:
       with patch.object(Handler,'_send',checked):
        c.request('GET','/api/session',headers={'Authorization':'Bearer '+token});res=c.getresponse();res.read();self.assertEqual(res.status,200)
       self.assertEqual(states,[False])
      finally:c.close();server.shutdown();thread.join()

if __name__=='__main__':unittest.main()
