import json
import tempfile
import unittest
from pathlib import Path
from searchagent_lab.isolation import audit_panel, normalize_question
from searchagent_lab.store import ValidationError,digest

class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        sample={'id':'v1','dataset':'HotpotQA','question':'Which town contains the old observatory of Mira Vale?','answers':['Northmere'], 'metadata':{'source_id':'source-1','source_revision':'v1'}}
        self.panel={'id':'gt','samples':[sample],'fingerprint':digest([sample])}
        self.clean=[{'id':'x1','dataset':'NQ','question':'What is the largest moon of Jupiter?'}]
        self.entries=[]
        for p in ('latent','self-play','entropy'):
            for r in ('train','test'):
                f=f'{p}-{r}.json';(self.root/f).write_text(json.dumps(self.clean))
                self.entries.append({'project_id':p,'role':r,'path':f,'revision':'v1','description':'explicit full inventory','declared_complete':True})
        self.inventory={'projects':['latent','self-play','entropy'],'entries':self.entries}
    def tearDown(self):self.tmp.cleanup()
    def audit(self):return audit_panel(self.panel,self.inventory,self.root,['latent','self-play','entropy'])
    def test_clean_supplied_scope_not_pretraining_claim(self):
        r=self.audit();self.assertEqual(r['status'],'no_detected_overlap_in_supplied_scope');self.assertFalse(r['base_model_pretraining_verified'])
    def test_normalization_punctuation_case(self):self.assertEqual(normalize_question('  Ｗhich, TOWN? '),'which town')
    def test_source_id_collision(self):
        (self.root/'latent-train.json').write_text(json.dumps([{'id':'other','dataset':'HotpotQA','question':'Different wording','metadata':{'source_id':'source-1'}}]))
        self.assertEqual(self.audit()['status'],'blocked_overlap')
    def test_cross_dataset_same_question_detected(self):
        (self.root/'entropy-test.json').write_text(json.dumps([{'id':'other','dataset':'NQ','question':self.panel['samples'][0]['question'].upper()}]))
        r=self.audit();self.assertEqual(r['status'],'blocked_overlap');self.assertEqual(r['exact_conflicts'][0]['source']['project_id'],'entropy')
    def test_near_match_is_review_not_approval(self):
        (self.root/'self-play-train.json').write_text(json.dumps([{'id':'other','dataset':'HotpotQA','question':'Which town contains the old observatory of Mira Vales?'}]))
        self.assertEqual(self.audit()['status'],'manual_review')
    def test_partial_project_list_incomplete(self):
        self.inventory['projects']=['latent'];self.inventory['entries']=self.entries[:2]
        self.assertEqual(self.audit()['status'],'incomplete_scope')
    def test_missing_test_inventory_incomplete(self):
        self.inventory['entries']=self.entries[:-1];self.assertEqual(self.audit()['status'],'incomplete_scope')
    def test_unattested_training_coverage_incomplete(self):
        self.inventory['entries'][0]['declared_complete']=False;self.assertEqual(self.audit()['status'],'incomplete_scope')
    def test_changed_file_checksum_is_rejected(self):
        self.inventory['entries'][0]['sha256']='bad'
        with self.assertRaises(ValidationError):self.audit()
    def test_audit_manifest_hash_changes_on_new_selfplay_data(self):
        before=self.audit()['inventory_hash'];(self.root/'self-play-train.json').write_text(json.dumps(self.clean+[{'id':'y','dataset':'NQ','question':'Who designed this building?'}]))
        self.assertNotEqual(before,self.audit()['inventory_hash'])
    def test_missing_original_text_rejected_not_id_only(self):
        (self.root/'latent-train.json').write_text(json.dumps([{'id':'q','dataset':'NQ'}]))
        with self.assertRaises(ValidationError):self.audit()
    def test_jsonl_source_supported(self):
        f=self.root/'latent-train.jsonl';f.write_text(json.dumps(self.clean[0])+'\n');self.entries[0]['path']=f.name
        self.assertEqual(self.audit()['files'][0]['rows'],1)

if __name__=='__main__':unittest.main()
