import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

class CLITests(unittest.TestCase):
 def test_doctor_on_new_workspace(self):
  with tempfile.TemporaryDirectory() as d:
   r=subprocess.run([sys.executable,'-m','searchagent_lab','doctor','--data-dir',d],capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(json.loads(r.stdout)['ok'])
 def test_bad_file_nonzero(self):
  with tempfile.TemporaryDirectory() as d:
   r=subprocess.run([sys.executable,'-m','searchagent_lab','import','missing.json','--data-dir',d],capture_output=True,text=True)
   self.assertEqual(r.returncode,2);self.assertIn('does not exist',r.stderr)
 def test_help(self):
  r=subprocess.run([sys.executable,'-m','searchagent_lab','--help'],capture_output=True,text=True)
  self.assertEqual(r.returncode,0);self.assertIn('import-metrics',r.stdout)

if __name__=='__main__':unittest.main()
