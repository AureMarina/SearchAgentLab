import gzip
import json
import tempfile
import unittest
from pathlib import Path
from searchagent_lab.store import Store,ValidationError
from searchagent_lab.io import import_file,import_bundle,import_metrics_csv
from helpers import seed_minimal,trajectory

class ImportTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.s=Store(self.root/'data')
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def test_bundle_transaction_rollback(self):
  bundle={"schema_version":1,"runs":[{"id":"r","name":"R"}],"checkpoints":[{"id":"c","run_id":"missing","step":0}]}
  with self.assertRaises(ValidationError):import_bundle(self.s,bundle)
  self.assertEqual(self.s.runs(),[])
 def test_jsonl_transaction_rollback(self):
  p=self.root/'bad.jsonl';p.write_text(json.dumps({"type":"run","data":{"id":"r","name":"R"}})+'\n{bad json}\n')
  with self.assertRaises(ValidationError):import_file(self.s,p)
  self.assertEqual(self.s.runs(),[])
 def test_jsonl_gzip(self):
  p=self.root/'good.jsonl.gz'
  with gzip.open(p,'wt') as f:f.write(json.dumps({"type":"run","data":{"id":"r","name":"R"}})+'\n')
  self.assertEqual(import_file(self.s,p),{"run":1});self.assertEqual(len(self.s.runs()),1)
 def test_json_gzip(self):
  p=self.root/'good.json.gz'
  with gzip.open(p,'wt') as f:json.dump({"schema_version":1,"runs":[{"id":"r","name":"R"}]},f)
  self.assertEqual(import_file(self.s,p)["runs"],1)
 def test_csv_missing_values_preserved(self):
  seed_minimal(self.s);p=self.root/'metrics.csv';p.write_text('step,actor/pg_loss,reward\n1,,0.3\n')
  import_metrics_csv(self.s,p,'run');self.assertIsNone(self.s.metrics('run')[0]['actor/pg_loss'])
 def test_csv_invalid_value_rolls_back(self):
  seed_minimal(self.s);p=self.root/'metrics.csv';p.write_text('step,loss\n1,0.3\n2,not-a-number\n')
  with self.assertRaises(ValidationError):import_metrics_csv(self.s,p,'run')
  self.assertEqual(self.s.metrics('run'),[])
 def test_nan_json_rejected(self):
  p=self.root/'bad.json';p.write_text('{"schema_version": 1, "description": NaN}')
  with self.assertRaises(ValidationError):import_file(self.s,p)
 def test_unknown_bundle_keys_rejected(self):
  with self.assertRaises(ValidationError):import_bundle(self.s,{"schema_version":1,"fake":[]})
 def test_non_event_lines_not_guessed(self):
  p=self.root/'plain.jsonl';p.write_text('{"question":"Q","answer":"A"}\n')
  with self.assertRaises(ValidationError):import_file(self.s,p)

if __name__=='__main__':unittest.main()
