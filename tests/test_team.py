"""0.7.0 team protocol, provenance, atomicity, auth and transport regressions.
Fixtures are synthetic and are never presented as research results.
"""
import copy
import http.client
import json
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch
from searchagent_lab.core import ValidationError, digest, dumps
from searchagent_lab.store import Store
from searchagent_lab.demo import PROTOCOL
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.package import init_source,export_run,read_package,add_artifact
from searchagent_lab.team.hub import Hub,gid,ConflictError,PermissionError
from searchagent_lab.team.auth import Auth
from searchagent_lab.team.client import Outbox,upload,DeliveryError,sync_once,endpoint
from searchagent_lab.team.server import TeamServer
from searchagent_lab.team.util import sha,json_bytes


def plan(project='latent',exp='e1',track='main'):
    return {'plan_schema':'searchagent-lab.plan/v1','project':{'id':project,'name':project},
            'experiment':{'id':exp,'project_id':project,'name':'Synthetic '+exp},
            'profile':{'experiment_id':exp,'track':track,**({'reproduction':{'method_name':'Synthetic reproduction'}} if track=='reproduction' else {})},
            'panels':[{'id':'p','name':'Synthetic fixed panel','demo':True,'samples':[{'id':'q','dataset':'NQ','question':'Synthetic Q?','answers':['A']}]}]}


def seed_source(path,project='latent',exp='e1',track='main',method=True):
    p=plan(project,exp,track)
    with StudioRecorder(path) as r:
        s=r.store;s.register_project(p['project']);s.register_experiment(p['experiment']);r.repo.register_profile(p['profile'])
        s.register_run({'id':'run','name':'seed42','project_id':project,'experiment_id':exp,'seed':42,'demo':True,'base_model':'synthetic-model','config':{'lr':1e-5,'precision':'bf16','git_commit':'test-only'}})
        s.log_metrics('run',1,{'loss':.7,'reward':.5,'entropy/raw':1.2})
        s.register_panel(p['panels'][0]);s.register_checkpoint({'id':'c','run_id':'run','step':1})
        s.start_evaluation({'id':'ev','checkpoint_id':'c','panel_id':'p','protocol':PROTOCOL})
        s.log_trajectory('ev',{'id':'t','dataset':'NQ','sample_id':'q','scores':{'em':1},'steps':[{'index':0,'kind':'model','model_output':'literal run/c/m IDs in raw content must not be replaced'}]})
        s.finish_evaluation('ev')
        if method:
            f=r.inspections.import_bytes(b'{"question":"Synthetic training Q","steps":[{"query":"hello"}]}\n','train.jsonl')
            r.methods.import_bundle({'method_schema':'method-evidence-v1','scope':{'run_id':'run'},'objects':[{'id':'m','kind':'trace','data':{'raw':'m run c'},'evidence_refs':[{'file':f['id'],'record':1}]}]})
            r.repo.record_batch({'id':'b','run_id':'run','step':1,'groups':[{'id':'g','samples':[{'reward':1,'advantage':.2,'trace_ref':{'object_id':'m'}}]}]})
    return init_source(path,'student','platform-A')


def rewrite(path,out,change=None,manifest_change=None):
    m,p,b=read_package(path)
    if change:change(p)
    b['records.json']=json_bytes(p)
    m['files']=[{'path':k,'sha256':sha(v),'size':len(v)} for k,v in sorted(b.items())]
    if manifest_change:manifest_change(m)
    m['state_sha256']=digest({'files':m['files'],'source':m['source']})
    m.pop('package_id',None);m['package_id']=digest(m)
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',json_bytes(m))
        for k,v in b.items():z.writestr(k,v)
    return out


class TeamCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.local=self.root/'local';self.center=self.root/'center'
        self.source=seed_source(self.local);self.package=export_run(self.local,'run')
    def tearDown(self):self.tmp.cleanup()
    def hub(self):return Hub(self.center)
    def modified(self,change=None,rev=2,manifest_change=None):
        def update(m):
            m['revision']=rev
            if manifest_change:manifest_change(m)
        return rewrite(self.package['path'],self.root/'mutated.zip',change,update)
    def test_package_is_json_and_bytes_not_database(self):
        m,p,b=read_package(self.package['path']);self.assertEqual(m['format'],'searchagent-lab.run/v1');self.assertTrue(all(not k.endswith('.sqlite3') for k in b));self.assertEqual(p['panels'][0]['samples'][0]['id'],'q')
    def test_export_unchanged_is_stable(self):self.assertEqual(export_run(self.local,'run')['package_id'],self.package['package_id'])
    def test_source_identity_stable(self):self.assertEqual(init_source(self.local,'student','platform-A'),self.source)
    def test_source_relabel_rejected(self):
        with self.assertRaises(ValidationError):init_source(self.local,'another','platform-A')
    def test_roundtrip_all_record_families(self):
        with self.hub() as h:
            result=h.ingest(self.package['path'],allow_definitions=True);rid=result['run_id']
            self.assertEqual(h.store.run(rid)['config']['lr'],1e-5)
            self.assertEqual(h.store.conn.execute('SELECT count(*) FROM metrics').fetchone()[0],1)
            self.assertEqual(h.store.conn.execute('SELECT count(*) FROM trajectories').fetchone()[0],1)
            self.assertEqual(h.store.conn.execute('SELECT count(*) FROM mx.objects').fetchone()[0],1)
            self.assertEqual(h.store.conn.execute('SELECT count(*) FROM ix.files').fetchone()[0],1)
            self.assertEqual(h.store.conn.execute('SELECT count(*) FROM sx.training_batches').fetchone()[0],1)
            b=h.studio.batches(rid,1)['batches'][0];ref=b['groups'][0]['samples'][0]['trace_ref']
            self.assertEqual(ref['object_id'],gid(self.source['workspace_id'],'method','m'))
            self.assertEqual(h.methods.resolve_ref(ref)['verified'],True)
    def test_raw_model_content_not_rewritten(self):
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True)
            tr=h.store._get('trajectories',gid(self.source['workspace_id'],'trajectory','t'))
            self.assertIn('run/c/m',tr['steps'][0]['model_output'])
    def test_offline_and_online_identity_dedup(self):
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True,submitter_owner='student-a')
            principal={'owner':'student-a','role':'uploader','experiments':['e1']}
            self.assertEqual(h.ingest(self.package['path'],principal)['delivery_status'],'duplicate')
            self.assertEqual(len(h.runs()),1)
    def test_independent_sources_same_seed_and_name_stay_separate(self):
        other=self.root/'other';seed_source(other);p=export_run(other,'run')
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True);h.ingest(p['path'],allow_definitions=True)
            self.assertEqual(len(h.runs()),2);self.assertEqual(h.store.conn.execute('SELECT count(*) FROM trajectories').fetchone()[0],2)
    def test_revision_appends_metrics(self):
        with Store(self.local) as s:s.log_metrics('run',2,{'loss':.5})
        second=export_run(self.local,'run')
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True);r=h.ingest(second['path'])
            self.assertEqual(r['revision'],2);self.assertEqual(h.runs()[0]['last_metric_step'],2)
    def test_late_older_snapshot_does_not_regress(self):
        with Store(self.local) as s:s.log_metrics('run',2,{'loss':.5})
        second=export_run(self.local,'run')
        with self.hub() as h:
            h.ingest(second['path'],allow_definitions=True)
            self.assertEqual(h.ingest(self.package['path'])['delivery_status'],'superseded');self.assertEqual(h.runs()[0]['revision'],2)
    def test_same_revision_different_content_conflict(self):
        p=self.modified(lambda p:p['metrics'][0]['metrics'].update(loss=.1),1)
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True)
            with self.assertRaises(ConflictError):h.ingest(p)
            self.assertEqual(len(h.receipts()['conflicts']),1)
    def test_new_revision_metric_rewrite_conflict(self):
        p=self.modified(lambda p:p['metrics'][0]['metrics'].update(loss=.1))
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True)
            with self.assertRaises(ConflictError):h.ingest(p)
    def test_missing_trajectory_not_silently_dropped(self):
        p=self.modified(lambda p:p.update(trajectories=[]))
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True)
            with self.assertRaises(ConflictError):h.ingest(p)
    def test_terminal_run_cannot_be_reopened(self):
        with Store(self.local) as s:s.set_run_status('run','failed')
        failed=export_run(self.local,'run')
        p=rewrite(failed['path'],self.root/'reopen.zip',lambda p:p['run'].update(status='running'),lambda m:m.update(revision=3))
        with self.hub() as h:
            h.ingest(failed['path'],allow_definitions=True)
            with self.assertRaises(ConflictError):h.ingest(p)
    def test_scoped_uploader_cannot_create_definitions(self):
        with self.hub() as h:
            with self.assertRaises(PermissionError):h.ingest(self.package['path'],{'owner':'a','role':'uploader','experiments':['e1']},True)
    def test_scoped_uploader_cannot_submit_other_experiment(self):
        with self.hub() as h:
            h.apply_plan(plan())
            with self.assertRaises(PermissionError):h.ingest(self.package['path'],{'owner':'a','role':'uploader','experiments':['other']})
    def test_scoped_uploader_can_submit_approved_plan(self):
        with self.hub() as h:
            h.apply_plan(plan());self.assertEqual(h.ingest(self.package['path'],{'owner':'a','role':'uploader','experiments':['e1']})['delivery_status'],'accepted')
    def test_source_owner_cannot_be_stolen(self):
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True,submitter_owner='a')
            with self.assertRaises(PermissionError):h.ingest(self.package['path'],{'owner':'b','role':'uploader','experiments':['e1']})
    def test_uploader_cannot_import_on_behalf(self):
        with self.hub() as h:
            with self.assertRaises(PermissionError):h.ingest(self.package['path'],{'owner':'a','role':'uploader','experiments':['e1']},submitter_owner='b')
    def test_groundtruth_cannot_change(self):
        p=self.modified(lambda p:p['panels'][0]['samples'][0].update(answers=['B']))
        with self.hub() as h:
            h.apply_plan(plan())
            with self.assertRaises(ConflictError):h.ingest(p)
    def test_main_reproduction_not_mixed(self):
        other=self.root/'repro';seed_source(other,exp='paper1',track='reproduction');p=export_run(other,'run')
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True);h.ingest(p['path'],allow_definitions=True)
            self.assertEqual(len(h.runs('latent','main')),1);self.assertEqual(len(h.runs('latent','reproduction')),1)
            self.assertEqual(h.studio.catalog('latent','reproduction')['main_iteration_count'],0)
    def test_tampered_source_labels_rejected(self):
        p=self.modified(manifest_change=lambda m:m['source'].update(platform='fake'))
        with self.hub() as h:
            h.ingest(self.package['path'],allow_definitions=True)
            with self.assertRaises(ConflictError):h.ingest(p)
    def test_late_failure_rolls_back_all_attached_databases(self):
        def bad(p):
            b=p['batches'][0];b['groups'][0]['samples'][0]['trace_ref']={'file':'no-such-file','record':1};b.pop('content_hash');b['content_hash']=digest(b)
        p=self.modified(bad)
        with self.hub() as h:
            with self.assertRaises((ValidationError,KeyError)):h.ingest(p,allow_definitions=True)
            for table in ('runs','projects','evaluations','trajectories','mx.objects','mx.imports','ix.files','ix.records','sx.profiles','sx.training_batches','team_receipts'):
                self.assertEqual(h.store.conn.execute('SELECT count(*) FROM '+table).fetchone()[0],0,table)
    def test_center_old_local_writer_refused(self):
        with self.hub() as h:
            with self.assertRaises(ValidationError):Store(self.center)
    def test_single_center_writer_lock(self):
        with self.hub():
            with self.assertRaises(ValidationError):Hub(self.center)
    def test_source_cannot_be_center(self):
        with self.assertRaises(ValidationError):Hub(self.local)
        self.assertFalse((self.local/'.team/center.json').exists())
    def test_center_uses_transactional_delete_journals(self):
        with self.hub() as h:
            for schema in ('main','sx','mx','ix','lx'):self.assertEqual(h.store.conn.execute('PRAGMA '+schema+'.journal_mode').fetchone()[0],'delete')
    def test_required_external_artifact_not_claimed_archived(self):
        add_artifact(self.local,'run','weights',uri='s3://durable-bucket/ckpt-1',required=True)
        p=export_run(self.local,'run')
        with self.hub() as h:
            r=h.ingest(p['path'],allow_definitions=True);self.assertEqual(r['evidence_status'],'required_artifacts_pending');self.assertEqual(h.run(r['run_id'])['artifacts'][0]['availability'],'external_unverified')
    def test_artifact_bytes_survive_missing_source(self):
        file=self.root/'evidence.txt';file.write_text('actual evidence');add_artifact(self.local,'run','evidence',path=file,required=True)
        p=export_run(self.local,'run')
        with self.hub() as h:
            r=h.ingest(p['path'],allow_definitions=True);file.unlink();p2=export_run(self.local,'run');h.ingest(p2['path']);d=h.run(r['run_id']);self.assertEqual(d['artifacts'][0]['availability'],'archived_present');self.assertEqual(d['required_missing'],[])
    def test_missing_center_artifact_visible(self):
        file=self.root/'evidence.txt';file.write_text('x');a=add_artifact(self.local,'run','evidence',path=file,required=True);p=export_run(self.local,'run')
        with self.hub() as h:
            r=h.ingest(p['path'],allow_definitions=True);(self.center/'.team/blobs'/a['sha256']).unlink();self.assertEqual(h.run(r['run_id'])['evidence_status'],'archived_files_missing')
    def test_changed_registered_artifact_rejected(self):
        file=self.root/'a';file.write_text('before');add_artifact(self.local,'run','a',path=file);file.write_text('after')
        with self.assertRaises(ValidationError):export_run(self.local,'run')
    def test_external_uri_cannot_embed_credential(self):
        with self.assertRaises(ValidationError):add_artifact(self.local,'run','a',uri='https://host/path?token=secret')
    def test_archive_file_hash_mismatch_rejected(self):
        path=self.root/'bad.zip'
        with zipfile.ZipFile(self.package['path']) as old,zipfile.ZipFile(path,'w') as new:
            for n in old.namelist():new.writestr(n,b'{}' if n=='records.json' else old.read(n))
        with self.assertRaises(ValidationError):read_package(path)
    def test_unsafe_archive_names_rejected(self):
        for name in ('../escape','/absolute','C:/escape','a\\b','a/./b'):
            with self.subTest(name=name):
                path=self.root/'unsafe.zip'
                with zipfile.ZipFile(path,'w') as z:z.writestr('manifest.json','{}');z.writestr(name,'x')
                with self.assertRaises(ValidationError):read_package(path)
    def test_duplicate_archive_members_rejected(self):
        path=self.root/'duplicate.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(path,'w') as z:z.writestr('manifest.json','{}');z.writestr('manifest.json','{}')
        with self.assertRaises(ValidationError):read_package(path)
    def test_zip_symlink_rejected(self):
        path=self.root/'link.zip'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('manifest.json','{}');i=zipfile.ZipInfo('link');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(i,'/etc/passwd')
        with self.assertRaises(ValidationError):read_package(path)
    def test_expansion_limit_enforced(self):
        with patch('searchagent_lab.team.package.MAX_EXPANDED',10):
            with self.assertRaises(ValidationError):read_package(self.package['path'])
    def test_duplicate_json_keys_rejected(self):
        path=self.root/'json.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('manifest.json','{"format":1,"format":2}');z.writestr('records.json','{}')
        with self.assertRaises(ValidationError):read_package(path)
    def test_outbox_persists_pending_across_restart(self):
        with Outbox(self.local) as b:b.enqueue(self.package['path'])
        with Outbox(self.local) as b:self.assertEqual(b.status()['counts']['pending'],1)
    def test_outbox_duplicate_enqueue(self):
        with Outbox(self.local) as b:b.enqueue(self.package['path']);b.enqueue(self.package['path']);self.assertEqual(len(b.status()['items']),1)
    def test_outbox_network_error_keeps_exact_package(self):
        with Outbox(self.local) as b:
            b.enqueue(self.package['path'])
            with patch('searchagent_lab.team.client.upload',side_effect=DeliveryError('offline')):r=b.flush('http://127.0.0.1:1','token')
            self.assertEqual(r['counts']['pending'],1);self.assertEqual(r['items'][0]['attempts'],1);self.assertTrue(Path(r['items'][0]['path']).is_file())
    def test_conflict_blocked_until_explicit_retry(self):
        with Outbox(self.local) as b:
            b.enqueue(self.package['path'])
            with patch('searchagent_lab.team.client.upload',side_effect=DeliveryError('conflict',False,409)):r=b.flush('http://127.0.0.1:1','token')
            self.assertEqual(r['counts']['blocked'],1);self.assertEqual(b.retry_blocked(),1);self.assertEqual(b.status()['counts']['pending'],1)
    def test_outbox_prune_only_acknowledged(self):
        with Outbox(self.local) as b:
            b.enqueue(self.package['path']);b.prune();self.assertTrue(Path(b.status()['items'][0]['path']).exists())
            with patch('searchagent_lab.team.client.upload',return_value={'delivery_status':'accepted'}):b.flush('http://127.0.0.1:1','token')
            b.prune();self.assertFalse(Path(b.status()['items'][0]['path']).exists());self.assertEqual(b.status()['counts']['acknowledged'],1)
    def test_outbox_tampered_bytes_are_blocked(self):
        with Outbox(self.local) as b:
            q=b.enqueue(self.package['path']);Path(q['path']).write_bytes(b'wrong');r=b.flush('http://127.0.0.1:1','token');self.assertEqual(r['counts']['blocked'],1)
    def test_outbox_capacity_alert_no_success(self):
        with Outbox(self.local,max_bytes=1) as b:
            with self.assertRaises(ValidationError):b.enqueue(self.package['path'])
            self.assertEqual(b.status()['counts']['pending'],0)
    def test_source_logs_unchanged_by_sync_failure(self):
        with patch('searchagent_lab.team.client.upload',side_effect=DeliveryError('offline')):result=sync_once(self.local,['run'],'http://127.0.0.1:1','token')
        self.assertEqual(result['counts']['pending'],1)
        with Store(self.local) as s:self.assertEqual(s.run('run')['status'],'running')
    def test_unsecured_remote_origin_rejected(self):
        for url in ('http://remote.example','https://user:pass@example.org','https://example.org/prefix','https://example.org/?token=x'):
            with self.subTest(url=url),self.assertRaises(ValidationError):endpoint(url)
    def test_token_metadata_never_includes_plaintext(self):
        a=Auth(self.center);token,d=a.issue('a','uploader',['e1']);self.assertNotIn(token,json.dumps(a.list()));self.assertNotIn(token,(self.center/'.team/auth.sqlite3').read_bytes().decode('latin1'));self.assertEqual(a.authenticate(token)['role'],'uploader')
    def test_token_revoke_immediate(self):
        a=Auth(self.center);token,d=a.issue('a','viewer');a.revoke(d['id']);self.assertIsNone(a.authenticate(token));self.assertIsNone(a.active(d['id']))
    def test_token_expiration(self):
        a=Auth(self.center);token,d=a.issue('a','viewer')
        with patch('searchagent_lab.team.auth.time.time',return_value=time.time()+86400*100):self.assertIsNone(a.authenticate(token))
    def test_uploader_requires_assigned_experiment(self):
        with self.assertRaises(ValidationError):Auth(self.center).issue('a','uploader')


class TeamHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.local=self.root/'local';self.center=self.root/'center';seed_source(self.local);self.pkg=export_run(self.local,'run')
        self.h=Hub(self.center);self.h.apply_plan(plan());self.a=Auth(self.center)
        self.admin,self.admin_meta=self.a.issue('admin','admin');self.viewer,self.viewer_meta=self.a.issue('reader','viewer');self.uploader,_=self.a.issue('student','uploader',['e1'])
        self.server=TeamServer(('127.0.0.1',0),self.h);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base=self.server.public_origin
    def tearDown(self):self.server.shutdown();self.thread.join();self.server.server_close();self.h.close();self.tmp.cleanup()
    def req(self,path='/api/session',method='GET',token=None,data=None,headers=None):
        h=headers or {};h=dict(h)
        if token:h['Authorization']='Bearer '+token
        if data is not None:
            if not isinstance(data,bytes):data=json.dumps(data).encode();h.setdefault('Content-Type','application/json')
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=5)
        try:
            conn.request(method,path,data,h);r=conn.getresponse();raw=r.read()
            try:d=json.loads(raw)
            except ValueError:d=raw.decode()
            return r.status,dict(r.headers),d
        finally:conn.close()
    def test_unauthenticated_workspace_is_protected(self):self.assertEqual(self.req()[0],401);self.assertEqual(self.req('/')[0],302)
    def test_login_static_resources_available(self):
        for p in ('/team-login','/studio/team-login.js','/studio/team.css'):self.assertEqual(self.req(p)[0],200)
    def test_viewer_can_read_not_write(self):
        self.assertEqual(self.req(token=self.viewer)[2]['read_only'],True);self.assertEqual(self.req('/api/team/plan','POST',self.viewer,plan())[0],403)
    def test_uploader_cannot_read_shared_workspace(self):self.assertEqual(self.req(token=self.uploader)[0],403)
    def test_admin_cannot_use_legacy_overwrite_route(self):self.assertEqual(self.req('/api/import','POST',self.admin,{'anything':'ignored'})[0],403)
    def test_online_upload_and_duplicate(self):
        first=upload(self.pkg['path'],self.base,self.uploader);second=upload(self.pkg['path'],self.base,self.uploader);self.assertEqual(first['delivery_status'],'accepted');self.assertEqual(second['delivery_status'],'duplicate');self.assertEqual(len(self.h.runs()),1)
    def test_offline_import_followed_by_online_uploader(self):
        self.h.ingest(self.pkg['path'],submitter_owner='student');self.assertEqual(upload(self.pkg['path'],self.base,self.uploader)['delivery_status'],'duplicate')
    def test_login_session_and_csrf(self):
        status,headers,_=self.req('/api/team/login','POST',data={'token':self.admin},headers={'Origin':self.base})
        self.assertEqual(status,200);cookie=headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        cookie=cookie.split(';')[0]
        self.assertEqual(self.req(headers={'Cookie':cookie})[0],200)
        self.assertEqual(self.req('/api/team/plan','POST',data=plan(),headers={'Cookie':cookie,'Origin':self.base})[0],403)
        self.assertEqual(self.req('/api/team/plan','POST',data=plan(),headers={'Cookie':cookie,'Origin':self.base,'X-Local-Token':self.server.csrf_token})[0],201)
    def test_revocation_kills_browser_session(self):
        _,headers,_=self.req('/api/team/login','POST',data={'token':self.viewer},headers={'Origin':self.base});self.a.revoke(self.viewer_meta['id'])
        self.assertEqual(self.req(headers={'Cookie':headers['Set-Cookie'].split(';')[0]})[0],401)
    def test_cross_origin_post_denied(self):self.assertEqual(self.req('/api/team/plan','POST',self.admin,plan(),{'Origin':'https://evil.example'})[0],403)
    def test_host_rebinding_denied(self):self.assertEqual(self.req(token=self.admin,headers={'Host':'evil.example'})[0],403)
    def test_unknown_and_oversize_length_denied(self):self.assertEqual(self.req('/api/team/packages','POST',self.uploader,b'x',{'Content-Type':'application/zip','Content-Length':str(129*1024*1024)})[0],400)
    def test_spoofed_owner_by_uploader_denied(self):
        with open(self.pkg['path'],'rb') as f:raw=f.read()
        self.assertEqual(self.req('/api/team/packages','POST',self.uploader,raw,{'Content-Type':'application/zip','X-Team-Owner':'other'})[0],403)
    def test_archive_download_access_control(self):
        r=upload(self.pkg['path'],self.base,self.uploader);p='/api/team/package?id='+r['package_id']
        self.assertEqual(self.req(p)[0],401);self.assertEqual(self.req(p,token=self.uploader)[0],403)
    def test_real_network_queue_ack(self):
        with Outbox(self.local) as b:
            b.enqueue(self.pkg['path']);result=b.flush(self.base,self.uploader);self.assertEqual(result['counts']['acknowledged'],1);self.assertEqual(result['items'][0]['receipt']['package_id'],self.pkg['package_id'])
    def test_lost_ack_retries_same_real_accepted_package(self):
        actual_upload=upload
        def lose_ack(*args,**kwargs):
            actual_upload(*args,**kwargs)
            raise DeliveryError('Simulated loss after server committed')
        with Outbox(self.local) as b:
            b.enqueue(self.pkg['path'])
            with patch('searchagent_lab.team.client.upload',side_effect=lose_ack):first=b.flush(self.base,self.uploader)
            self.assertEqual(first['counts']['pending'],1);self.assertEqual(len(self.h.runs()),1)
            second=b.flush(self.base,self.uploader,force=True)
            self.assertEqual(second['counts']['acknowledged'],1);self.assertEqual(second['items'][0]['receipt']['delivery_status'],'duplicate');self.assertEqual(len(self.h.runs()),1)
    def test_concurrent_duplicate_uploads_share_one_run(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            results=list(ex.map(lambda _:upload(self.pkg['path'],self.base,self.uploader),range(2)))
        self.assertEqual({r['delivery_status'] for r in results},{'accepted','duplicate'});self.assertEqual(len(self.h.runs()),1)
    def test_malformed_record_shape_returns_client_error(self):
        path=rewrite(self.pkg['path'],self.root/'invalid.zip',lambda p:p.update(profile=None))
        status,_,_=self.req('/api/team/packages','POST',self.uploader,path.read_bytes(),{'Content-Type':'application/zip'})
        self.assertEqual(status,400)
    def test_bad_receipt_not_marked_ack(self):
        class Response:
            status=200
            def read(self,n):return b'{"package_id":"wrong","revision":1,"delivery_status":"accepted"}'
        with patch('searchagent_lab.team.client.http.client.HTTPConnection') as cls:
            cls.return_value.getresponse.return_value=Response()
            with self.assertRaises(DeliveryError):upload(self.pkg['path'],self.base,self.uploader)
    def test_redirect_not_followed_with_token(self):
        class Response:
            status=302
            def read(self,n):return b'{"error":"redirect"}'
        with patch('searchagent_lab.team.client.http.client.HTTPConnection') as cls:
            cls.return_value.getresponse.return_value=Response()
            with self.assertRaises(DeliveryError) as e:upload(self.pkg['path'],self.base,self.uploader)
            self.assertFalse(e.exception.retryable);self.assertEqual(cls.call_count,1)

class TeamSDKAndCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.local=self.root/'local';seed_source(self.local)
    def tearDown(self):self.tmp.cleanup()
    def test_run_registration_uses_unique_id_and_runtime_snapshot(self):
        from searchagent_lab.team.sdk import register_team_run
        a=register_team_run(self.local,'e1','same',seed=42,config={'precision':'bf16'})
        b=register_team_run(self.local,'e1','same',seed=42)
        self.assertNotEqual(a,b)
        with Store(self.local) as s:
            r=s.run(a);self.assertEqual(r['provenance']['registered_before_training'],True);self.assertIn('python',r['runtime_snapshot']);self.assertEqual(r['config']['precision'],'bf16')
    def test_runtime_does_not_dump_environment_or_remotes(self):
        from searchagent_lab.team.sdk import runtime_snapshot
        with patch.dict('os.environ',{'PRIVATE_API_TOKEN':'do-not-export-this-value'}):d=runtime_snapshot(self.root)
        self.assertNotIn('do-not-export-this-value',json.dumps(d));self.assertNotIn('git_remote',d.get('code',{}))
    def test_reserved_provenance_cannot_be_overwritten(self):
        from searchagent_lab.team.sdk import register_team_run
        with self.assertRaises(ValidationError):register_team_run(self.local,'e1','bad',extra={'id':'other'})
    def cli(self,*args):
        import subprocess,sys
        return subprocess.run([sys.executable,'-m','searchagent_lab',*map(str,args)],text=True,capture_output=True,timeout=15)
    def test_cli_export_plan_existing_experiment_and_offline_import(self):
        out=self.root/'plan.json';result=self.cli('team-export-plan','--data-dir',self.local,'--experiment','e1','--out',out);self.assertEqual(result.returncode,0,result.stderr)
        center=self.root/'center';result=self.cli('team-plan',out,'--center','--data-dir',center);self.assertEqual(result.returncode,0,result.stderr)
        pkg=self.root/'run.zip';result=self.cli('team-export','--data-dir',self.local,'--run','run','--out',pkg);self.assertEqual(result.returncode,0,result.stderr)
        result=self.cli('team-import',pkg,'--data-dir',center,'--owner','student');self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['delivery_status'],'accepted')
    def test_cli_token_secret_file_permissions(self):
        file=self.root/'upload-token';result=self.cli('team-token','--data-dir',self.root/'center','--owner','student','--role','uploader','--experiment','e1','--out',file);self.assertEqual(result.returncode,0,result.stderr)
        secret=file.read_text().strip();self.assertNotIn(secret,result.stdout);self.assertTrue(secret.startswith('key-'));self.assertEqual(file.stat().st_mode&0o777,0o600)
    def test_cli_offline_requires_owner_for_future_dedup(self):
        p=export_run(self.local,'run');result=self.cli('team-import',p['path'],'--data-dir',self.root/'center');self.assertNotEqual(result.returncode,0);self.assertIn('--owner',result.stderr)
    def test_cli_local_invalid_plan_has_no_partial_project(self):
        p=plan();p['profile']['experiment_id']='different';file=self.root/'bad-plan.json';file.write_text(json.dumps(p));local=self.root/'empty'
        result=self.cli('team-plan',file,'--data-dir',local);self.assertNotEqual(result.returncode,0);self.assertFalse((local/'searchagent_lab.sqlite3').exists())
