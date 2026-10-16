import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import subprocess
import sys
from pathlib import Path
from searchagent_lab.store import Store
from searchagent_lab.server import LabServer
from test_research import research_fixture

class ResearchAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.s=Store(cls.tmp.name);research_fixture(cls.s)
        cls.server=LabServer(('127.0.0.1',0),cls.s);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_address[1]}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.s.close();cls.tmp.cleanup()
    def get(self,path):
        try:
            with urllib.request.urlopen(self.url+path) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def test_session_projects(self):self.assertEqual(len(self.get('/api/session')[1]['projects']),2)
    def test_catalog_filters_real_ownership(self):self.assertEqual(self.get('/api/catalog?project=latent')[1]['runs'][0]['id'],'latent-r')
    def test_api_mismatched_scope_returns400(self):self.assertEqual(self.get('/api/overview?project=latent&run=self-play-r&panel=gt')[0],400)
    def test_api_matrix_fixed_samples(self):self.assertEqual(len(self.get('/api/matrix?project=latent&run=latent-r&panel=gt')[1]['rows']),2)
    def test_cross_project_api_opt_in(self):
        self.assertFalse(self.get('/api/compare?a=latent-ev0&b=self-play-ev0')[1]['compatible'])
        self.assertTrue(self.get('/api/compare?a=latent-ev0&b=self-play-ev0&cross_project=1')[1]['compatible'])
    def test_gt_export_is_read_only(self):self.assertTrue(self.get('/api/export/panel?id=gt')[1]['read_only'])
    def test_repro_is_not_ordinary_checkpoint_compare(self):self.assertFalse(self.get('/api/reproducibility?a=latent-ev0&b=latent-ev1')[1]['eligible'])
    def test_notes_require_csrf(self):
        request=urllib.request.Request(self.url+'/api/experiment-notes',data=json.dumps({'experiment_id':'latent-e','note':{'note':'x'}}).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403)
    def test_original_score_plus_ownership_in_export(self):
        d=self.get('/api/export/evaluation?id=latent-ev0')[1];self.assertEqual(d['ownership']['project']['id'],'latent');self.assertEqual(d['trajectories'][0]['scores']['em'],0)

class ResearchCLITests(unittest.TestCase):
    def run_cli(self,*args):return subprocess.run([sys.executable,'-m','searchagent_lab',*args],capture_output=True,text=True)
    def test_template_cannot_be_imported_as_frozen_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            template=str(Path(tmp)/'draft.json')
            r=self.run_cli('validation-template','--data-dir',tmp,'--out',template);self.assertEqual(r.returncode,0,r.stderr)
            r=self.run_cli('freeze-panel',template,'--data-dir',tmp,'--out',str(Path(tmp)/'out.json'));self.assertEqual(r.returncode,2)
    def test_template_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'keep.json';p.write_text('keep')
            self.assertEqual(self.run_cli('validation-template','--data-dir',tmp,'--out',str(p)).returncode,2)
            self.assertEqual(p.read_text(),'keep')
    def test_repro_cli_reports_ineligible_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            with Store(tmp) as s:research_fixture(s)
            p=Path(tmp)/'report.json';r=self.run_cli('repro-check','--data-dir',tmp,'--a','latent-ev0','--b','latent-ev1','--out',str(p))
            self.assertEqual(r.returncode,3);self.assertFalse(json.loads(p.read_text())['eligible'])

if __name__=='__main__':unittest.main()
