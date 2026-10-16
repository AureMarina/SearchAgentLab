import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from searchagent_lab.store import Store,ValidationError
from searchagent_lab.server import LabServer
from helpers import seed_minimal,trajectory

class ServerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(self.tmp.name);seed_minimal(self.s)
  self.tid=self.s.log_trajectory('eval0',trajectory(parsed_answer='=HYPERLINK("bad")'))
  self.server=LabServer(('127.0.0.1',0),self.s)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.url=f'http://127.0.0.1:{self.server.server_address[1]}'
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.s.close();self.tmp.cleanup()
 def request(self,path,**kwargs):
  try:
   with urllib.request.urlopen(urllib.request.Request(self.url+path,**kwargs),timeout=3) as r:return r.status,r.headers,r.read()
  except urllib.error.HTTPError as e:return e.code,e.headers,e.read()
 def test_static_and_csp(self):
  status,headers,body=self.request('/');self.assertEqual(status,200);self.assertIn(b'SearchAgent Lab',body);self.assertIn("default-src 'self'",headers['Content-Security-Policy'])
 def test_health(self):self.assertTrue(json.loads(self.request('/api/health')[2])['ok'])
 def test_host_header_rejected(self):self.assertEqual(self.request('/api/session',headers={'Host':'evil.example'})[0],403)
 def test_path_traversal_not_served(self):self.assertEqual(self.request('/../searchagent_lab.sqlite3')[0],404)
 def test_missing_parameters_are_400(self):self.assertEqual(self.request('/api/overview')[0],400)
 def test_unknown_record_is_404(self):self.assertEqual(self.request('/api/trajectory?id=absent')[0],404)
 def test_overview_api(self):self.assertEqual(len(json.loads(self.request('/api/overview?run=run&panel=panel')[2])['checkpoints']),2)
 def test_csrf_required(self):
  data=json.dumps({'trajectory_id':self.tid,'annotation':{'category':'other','note':'note'}}).encode()
  self.assertEqual(self.request('/api/annotations',data=data,headers={'Content-Type':'application/json'})[0],403)
 def test_annotation_with_token(self):
  session=json.loads(self.request('/api/session')[2]);data=json.dumps({'trajectory_id':self.tid,'annotation':{'category':'other','note':'note'}}).encode()
  status,_,body=self.request('/api/annotations',data=data,headers={'Content-Type':'application/json','X-Local-Token':session['csrf_token']})
  self.assertEqual(status,201);self.assertEqual(json.loads(body)['source'],'manual')
 def test_cross_origin_even_with_token_rejected(self):
  data=json.dumps({'trajectory_id':self.tid,'annotation':{'category':'other','note':'note'}}).encode()
  self.assertEqual(self.request('/api/annotations',data=data,headers={'Content-Type':'application/json','X-Local-Token':self.server.csrf_token,'Origin':'https://evil.example'})[0],403)
 def test_csv_formula_neutralized(self):
  status,headers,body=self.request('/api/export/csv?id=eval0');self.assertEqual(status,200);self.assertIn(b"'=HYPERLINK",body);self.assertIn('attachment',headers['Content-Disposition'])
 def test_full_export_keeps_raw(self):
  data=json.loads(self.request('/api/export/evaluation?id=eval0')[2]);self.assertIn('tool_response',data['trajectories'][0]['steps'][1])
 def test_remote_bind_rejected(self):
  with self.assertRaises(ValidationError):LabServer(('0.0.0.0',0),self.s)

if __name__=='__main__':unittest.main()
