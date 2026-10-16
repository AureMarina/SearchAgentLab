from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from searchagent_lab.inspection.repository import InspectionRepository,strict_json
from searchagent_lab.inspection.adapters import normalize_record,resolve,pointer
from searchagent_lab.inspection.diagnostics import diagnose
from searchagent_lab.store import Store,ValidationError
from searchagent_lab.server import LabServer

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'examples/inspection_demo.jsonl'

def trace(): return json.loads(FIXTURE.read_text().splitlines()[0])

class AdapterChecks(unittest.TestCase):
    def test_input_never_mutated(self):
        r=trace();before=copy.deepcopy(r);normalize_record(r);diagnose(r);self.assertEqual(r,before)
    def test_steps_and_pointer(self):
        r=trace();v=normalize_record(r);self.assertEqual(len(v['trajectories'][0]['steps']),6)
        f=v['trajectories'][0]['steps'][1]['fields'][1];self.assertEqual(f['value'],resolve(r,f['pointer']))
    def test_rllm_multiple_roles(self):
        r=json.loads(FIXTURE.read_text().splitlines()[1]);v=normalize_record(r);self.assertEqual(v['adapter'],'rllm-episode');self.assertEqual(len(v['trajectories']),2)
    def test_event_wrapper(self):
        r={'type':'trajectory','data':trace()};v=normalize_record(r);self.assertEqual(v['pointers']['scores'],'/data/scores')
    def test_message_no_prompt_reconstruction(self):
        r={'messages':[{'role':'user','content':'Q'},{'role':'assistant','content':'A'}]};v=normalize_record(r)
        self.assertEqual(v['execution_kind'],'recorded_messages');self.assertFalse(any(f['name']=='input' for s in v['trajectories'][0]['steps'] for f in s['fields']))
    def test_dataset_not_rollout(self):
        v=normalize_record({'question':'Q','answers':['A']});self.assertEqual(v['execution_kind'],'reference_only')
    def test_retrieval_not_rollout(self):
        v=normalize_record({'query_kind':'main','query':'Q','documents':[{'text':'A'}]});self.assertEqual(v['execution_kind'],'reference_only')
    def test_private_schema_no_guess(self):
        v=normalize_record({'my_workflow':[1,2]});self.assertEqual(v['adapter'],'raw');self.assertFalse(v['trajectories'])
    def test_scores_not_reward(self):
        self.assertEqual(normalize_record({'reward':1})['scores'],{})
    def test_no_fallback_visible(self):
        v=normalize_record({'steps':[{'kind':'tool','tool_response':'answer'}]});self.assertFalse(any(f['name']=='model_visible' for f in v['trajectories'][0]['steps'][0]['fields']))
    def test_escaped_pointer(self):
        r={'a/b':{'t~k':7}};self.assertEqual(resolve(r,pointer(pointer('','a/b'),'t~k')),7)
    def test_bad_em_warning(self): self.assertTrue(normalize_record({'scores':{'em':True}})['warnings'])

class RuleChecks(unittest.TestCase):
    def rules(self,r):return {f['rule']:f for f in diagnose(r)['findings']}
    def test_repeated_query(self): self.assertIn('repeated_query',self.rules(trace()))
    def test_raw_visible_alias(self): self.assertIn('alias_lost',self.rules(trace()))
    def test_errors(self): self.assertIn('tool_error',self.rules(trace()));self.assertIn('tool_args',self.rules(trace()))
    def test_parser_consistency(self): self.assertIn('parser_mismatch',self.rules(trace()))
    def test_score_consistency_not_rewrite(self):
        r={'answers':['The Harbor'],'parsed_answer':'Harbor','scores':{'em':0}};self.assertIn('score_mismatch',self.rules(r));self.assertEqual(r['scores']['em'],0)
    def test_missing_not_false_zero(self): self.assertIn('unscored',self.rules({'parsed_answer':'x'}))
    def test_empty_output(self): self.assertIn('tool_empty',self.rules({'steps':[{'kind':'tool','tool_response':[]}]}))
    def test_visible_missing_distinct(self): self.assertIn('visible_missing',self.rules({'steps':[{'kind':'tool','tool_response':'A'}]}))
    def test_absent_return_distinct(self): self.assertIn('tool_missing_return',self.rules({'steps':[{'kind':'tool'}]}))
    def test_all_pointers_resolve(self):
        r=trace()
        for f in diagnose(r)['findings']:
            for e in f['evidence']:resolve(r,e['pointer'])
    def test_reference_no_model_failure(self):self.assertEqual(set(self.rules({'question':'Q','answers':['A']})),{'reference_only'})
    def test_valid_tool_string_no_error(self):
        r={'steps':[{'tool_calls':[{'function':{'name':'search','arguments':'{"query":"x"}'}}]}]};self.assertNotIn('tool_args',self.rules(r))
    def test_short_yes_alias_not_retrieval_evidence(self):
        r=trace();r['gold_answers']=['yes'];self.assertNotIn('alias_lost',self.rules(r))
    def test_stable_findings(self): self.assertEqual(diagnose(trace()),diagnose(trace()))
    def test_no_trace_does_not_claim_cause(self): self.assertIn('no_steps',self.rules({'scores':{'em':0}}))

class RepositoryChecks(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.repo=InspectionRepository(self.tmp.name);self.info=self.repo.import_bytes(FIXTURE.read_bytes(),FIXTURE.name)
    def tearDown(self):self.repo.close();self.tmp.cleanup()
    def test_raw_byte_hash(self):self.assertEqual(self.repo.original(self.info['id']),FIXTURE.read_bytes());self.assertEqual(self.info['sha256'],hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
    def test_line_numbers(self):self.assertEqual([r['source_line'] for r in self.repo.list_records(self.info['id'])['items']],[1,2,4,5,6,7])
    def test_malformed_preserved(self):self.assertEqual(self.info['invalid_count'],1);self.assertTrue(self.repo.record(self.info['id'],6)['parse_error'])
    def test_idempotent(self):self.assertTrue(self.repo.import_bytes(FIXTURE.read_bytes(),FIXTURE.name)['already_present']);self.assertEqual(len(self.repo.files()),1)
    def test_file_scoped_search(self):self.assertEqual(self.repo.list_records(self.info['id'],query='hidden_signal_8472')['total'],1)
    def test_hidden_array_search(self):
        d=self.repo.record(self.info['id'],1);self.assertEqual(self.repo.search_record(d,'array_tail_317')['items'][0]['pointer'],'/custom_metadata/nested/large_array/317/value')
    def test_rule_filter(self):self.assertEqual(self.repo.list_records(self.info['id'],rule='parser_mismatch')['total'],1)
    def test_invalid_filter(self):self.assertEqual(self.repo.list_records(self.info['id'],outcome='invalid')['total'],1)
    def test_no_false_boolean_em(self):
        f=self.repo.import_bytes(b'{"scores":{"em":true}}','bool.jsonl');self.assertEqual(self.repo.list_records(f['id'])['items'][0]['outcome'],'unscored')
    def test_json_array(self):
        f=self.repo.import_bytes(b'[{"id":1},{"id":2}]','array.json');self.assertEqual(f['record_count'],2);self.assertEqual(self.repo.record(f['id'],2)['source']['pointer'],'/1')
    def test_json_episode_not_flatten(self):
        f=self.repo.import_bytes(json.dumps({'trajectories':[{'steps':[]},{'steps':[]}]}).encode(),'ep.json');self.assertEqual(f['record_count'],1)
    def test_duplicate_json_keys_retained_error(self):
        f=self.repo.import_bytes(b'{"a":1,"a":2}\n','dup.jsonl');self.assertEqual(f['invalid_count'],1)
    def test_nonfinite_retained_error(self):
        f=self.repo.import_bytes(b'{"a":NaN}\n','nan.jsonl');self.assertEqual(f['invalid_count'],1)
    def test_bom_bytes_preserved(self):
        b=b'\xef\xbb\xbf{"x":1}\r\n';f=self.repo.import_bytes(b,'bom.jsonl');self.assertEqual(self.repo.original(f['id']),b)
    def test_unsupported_encoding_rejected(self):
        with self.assertRaises(ValidationError):self.repo.import_bytes(b'\xff\xfe','bad.jsonl')
    def test_empty_rejected(self):
        with self.assertRaises(ValidationError):self.repo.import_bytes(b'\n','empty.jsonl')
    def test_append_review_persistent(self):
        d=self.repo.record(self.info['id'],1);n={'source_hash':d['source_hash'],'pointer':'/steps/1/tool_response','status':'confirmed','note':'manually checked'}
        a=self.repo.append_review(d,n);self.assertTrue(a['id']);self.assertEqual(len(self.repo.record(self.info['id'],1)['reviews']),1)
        self.assertEqual(d['raw']['scores']['em'],0)
    def test_invalid_review_pointer(self):
        d=self.repo.record(self.info['id'],1)
        with self.assertRaises(ValidationError):self.repo.append_review(d,{'source_hash':d['source_hash'],'pointer':'/not_here','note':'x'})
    def test_stale_hash_rejected(self):
        d=self.repo.record(self.info['id'],1)
        with self.assertRaises(ValidationError):self.repo.append_review(d,{'source_hash':'other','pointer':'','note':'x'})
    def test_invalid_finding_rejected(self):
        d=self.repo.record(self.info['id'],1)
        with self.assertRaises(ValidationError):self.repo.append_review(d,{'source_hash':d['source_hash'],'finding_id':'other','note':'x'})
    def test_reviews_append_not_overwrite(self):
        d=self.repo.record(self.info['id'],1)
        for status in ('pending','rejected'):self.repo.append_review(d,{'source_hash':d['source_hash'],'note':'reason','status':status})
        self.assertEqual(len(self.repo.record(self.info['id'],1)['reviews']),2)
    def test_report_no_score_changes(self):
        d=self.repo.record(self.info['id'],1);r=self.repo.report(d);self.assertEqual(r['raw']['scores']['em'],0);self.assertEqual(r['diagnostics']['ruleset'],'trace-evidence-v1')
    def test_pagination(self):self.assertEqual(len(self.repo.list_records(self.info['id'],limit=2)['items']),2)

class InspectionHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name);self.server=LabServer(('127.0.0.1',0),self.store);threading.Thread(target=self.server.serve_forever,daemon=True).start();self.base=f'http://127.0.0.1:{self.server.server_address[1]}'
    def tearDown(self):self.server.shutdown();self.server.server_close();self.store.close();self.tmp.cleanup()
    def get(self,p):return urllib.request.urlopen(self.base+p)
    def test_deep_link_parent_guard(self):
        from searchagent_lab.demo import seed_demo
        seed_demo(self.store,1)
        evaluation='lt-persistent-s42-step100-eval01'
        row=self.store.trajectories(evaluation)['items'][0]
        data=json.load(self.get('/api/inspection/evaluation-position?evaluation='+evaluation+'&trajectory='+row['id']))
        self.assertEqual(data['offset'],0)
        with self.assertRaises(urllib.error.HTTPError) as cm:self.get('/api/inspection/evaluation-position?evaluation=other&trajectory='+row['id'])
        self.assertEqual(cm.exception.code,400)

    def test_static_modules(self):
        for p in ('/app.js','/inspector/workbench.js','/inspector/json-tree.js','/inspector/inspector.css','/reference_evidence.json'):
            with self.get(p) as r:self.assertEqual(r.status,200)
    def test_upload_without_csrf(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:urllib.request.urlopen(urllib.request.Request(self.base+'/api/inspection/upload?name=t.jsonl',data=b'{}',headers={'Content-Type':'application/octet-stream'}))
        self.assertEqual(cm.exception.code,403)
    def test_binary_upload_and_immutable_export(self):
        token=json.load(self.get('/api/session'))['csrf_token'];content=b'{"steps":[]}\n'
        req=urllib.request.Request(self.base+'/api/inspection/upload?name=t.jsonl',data=content,headers={'Content-Type':'application/octet-stream','X-Local-Token':token})
        f=json.load(urllib.request.urlopen(req));self.assertEqual(self.get('/api/inspection/original?file='+f['id']).read(),content)
    def test_static_traversal_not_served(self):
        with self.assertRaises(urllib.error.HTTPError):self.get('/inspector/../server.py')
    def test_independent_inspection_no_project_results(self):
        self.server.inspections.import_bytes(FIXTURE.read_bytes(),FIXTURE.name);self.assertEqual(self.store.projects(),[]);self.assertEqual(self.store.runs(),[])

if __name__=='__main__':unittest.main()
