import math
import tempfile
import threading
import unittest
from copy import deepcopy
from searchagent_lab.store import Store, ValidationError, NotFoundError
from searchagent_lab.demo import PROTOCOL, seed_demo
from helpers import seed_minimal, trajectory


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.s=Store(self.tmp.name); self.samples=seed_minimal(self.s)
    def tearDown(self):
        self.s.close();self.tmp.cleanup()
    def test_metrics_missing_not_zero(self):
        self.s.log_metrics("run",0,{"loss":None,"reward":.5})
        self.assertIsNone(self.s.metrics("run")[0]["loss"])
    def test_metrics_merge_and_idempotency(self):
        self.s.log_metrics("run",1,{"loss":.3});self.s.log_metrics("run",1,{"reward":.5});self.s.log_metrics("run",1,{"loss":.3})
        self.assertEqual(self.s.metrics("run"),[{"step":1,"loss":.3,"reward":.5}])
    def test_metrics_different_values_rejected(self):
        self.s.log_metrics("run",1,{"loss":.3})
        with self.assertRaises(ValidationError):self.s.log_metrics("run",1,{"loss":.4})
    def test_nonfinite_and_bool_rejected(self):
        for value in (float('nan'),float('inf'),True):
            with self.assertRaises(ValidationError):self.s.log_metrics("run",1,{"loss":value})
    def test_bad_step_rejected(self):
        for step in (-1,True,1.3):
            with self.assertRaises(ValidationError):self.s.log_metrics("run",step,{"loss":1})
    def test_missing_parent_rejected(self):
        with self.assertRaises(NotFoundError):self.s.log_metrics("absent",0,{"loss":1})
    def test_manifest_duplicate_rejected(self):
        with self.assertRaises(ValidationError):self.s.register_panel({"id":"bad","name":"bad","samples":[self.samples[0],self.samples[0]]})
    def test_panel_immutable(self):
        with self.assertRaises(ValidationError):self.s.register_panel({"id":"panel","name":"Fixed test panel","samples":[self.samples[0]]})
    def test_manifest_order_independent_hash(self):
        self.s.register_panel({"id":"reversed","name":"same samples","samples":self.samples[::-1]})
        self.assertEqual(self.s.panel("panel")["fingerprint"],self.s.panel("reversed")["fingerprint"])
    def test_unknown_sample_rejected(self):
        with self.assertRaises(ValidationError):self.s.log_trajectory("eval0",trajectory(999))
    def test_question_and_gold_cannot_be_overwritten(self):
        for kw in ({"question":"Changed"},{"gold_answers":["Changed"]}):
            with self.assertRaises(ValidationError):self.s.log_trajectory("eval0",trajectory(**kw))
    def test_duplicate_trajectory_same_is_idempotent(self):
        a=self.s.log_trajectory("eval0",trajectory());b=self.s.log_trajectory("eval0",trajectory());self.assertEqual(a,b)
        self.assertEqual(self.s.trajectories("eval0")["total"],1)
    def test_duplicate_trajectory_different_rejected(self):
        self.s.log_trajectory("eval0",trajectory())
        with self.assertRaises(ValidationError):self.s.log_trajectory("eval0",trajectory(em=0))
    def test_original_unknown_fields_preserved(self):
        tid=self.s.log_trajectory("eval0",trajectory(original_tokens=[17,22],masks=[1,1]))
        self.assertEqual(self.s.trajectory(tid)["original_tokens"],[17,22])
    def test_raw_and_visible_separate(self):
        tid=self.s.log_trajectory("eval0",trajectory());s=self.s.trajectory(tid)["steps"][1]
        self.assertNotEqual(s["tool_response"]["text"],s["model_visible"])
    def test_runtime_is_separate_from_answer_score(self):
        tid=self.s.log_trajectory("eval0",trajectory(em=1,runtime_status="timeout"))
        t=self.s.trajectory(tid);self.assertEqual(t["outcome"],"error");self.assertEqual(t["scores"]["em"],1)
    def test_missing_runtime_is_not_assumed_normal(self):
        record=trajectory();del record["runtime_status"]
        tid=self.s.log_trajectory("eval0",record)
        self.assertEqual(self.s.trajectory(tid)["runtime_status"],"unknown")
        self.assertEqual(self.s.trajectory(tid)["outcome"],"correct")
    def test_missing_em_does_not_become_zero(self):
        self.s.log_trajectory("eval0",trajectory(em=None))
        stats=self.s.evaluation("eval0")["stats"]
        self.assertEqual(stats["scored"],0);self.assertIsNone(stats["macro_em"])
    def test_partial_macro_not_reported(self):
        self.s.log_trajectory("eval0",trajectory())
        self.assertIsNone(self.s.evaluation("eval0")["stats"]["macro_em"])
    def test_completion_requires_all_fixed_samples(self):
        self.s.log_trajectory("eval0",trajectory())
        with self.assertRaises(ValidationError):self.s.finish_evaluation("eval0")
        self.s.finish_evaluation("eval0","failed")
    def test_final_eval_immutable(self):
        for i in (0,1):self.s.log_trajectory("eval0",trajectory(i))
        self.s.finish_evaluation("eval0")
        with self.assertRaises(ValidationError):self.s.finish_evaluation("eval0","failed")
    def test_no_macro_when_completed_but_unscored(self):
        self.s.log_trajectory("eval0",trajectory(0,em=None));self.s.log_trajectory("eval0",trajectory(1));self.s.finish_evaluation("eval0")
        self.assertIsNone(self.s.evaluation("eval0")["stats"]["macro_em"])
    def test_macro_is_dataset_average(self):
        panel={"id":"unequal","name":"unequal","samples":[{"id":"q0","dataset":"A","question":"a","answers":["a"]}]+[{"id":f"q{i}","dataset":"B","question":f"b{i}","answers":["b"]} for i in range(3)]}
        self.s.register_panel(panel);self.s.start_evaluation({"id":"unequal","checkpoint_id":"cp0","panel_id":"unequal","protocol":PROTOCOL})
        self.s.log_trajectory("unequal",trajectory(dataset="A",steps=[],em=1))
        for i in range(3):self.s.log_trajectory("unequal",trajectory(i,dataset="B",steps=[],em=0))
        self.assertEqual(self.s.evaluation("unequal")["stats"]["macro_em"],.5)
    def test_pair_rescued_and_harmed(self):
        for i in (0,1):
            self.s.log_trajectory("eval0",trajectory(i,em=i));self.s.log_trajectory("eval1",trajectory(i,em=1-i))
        for ev in ("eval0","eval1"):self.s.finish_evaluation(ev)
        d=self.s.compare("eval0","eval1");self.assertTrue(d["full"]);self.assertEqual(d["groups"]["rescued"],1);self.assertEqual(d["groups"]["harmed"],1);self.assertEqual(d["macro_delta"],0)
    def test_protocol_mismatch_blocks_pair_scores(self):
        protocol=deepcopy(PROTOCOL);protocol["budget"]["max_tool_calls"]=99
        self.s.start_evaluation({"id":"other","checkpoint_id":"cp1","panel_id":"panel","protocol":protocol})
        d=self.s.compare("eval0","other");self.assertFalse(d["compatible"]);self.assertNotIn("groups",d)
    def test_unknown_protocol_cannot_claim_parity(self):
        protocol=deepcopy(PROTOCOL);protocol["retriever"]="unknown"
        for ev in ('unknown1','unknown2'):self.s.start_evaluation({"id":ev,"checkpoint_id":"cp1","panel_id":"panel","protocol":protocol})
        self.assertFalse(self.s.compare('unknown1','unknown2')["compatible"])
    def test_missing_protocol_key_rejected(self):
        protocol=deepcopy(PROTOCOL);del protocol["parser"]
        with self.assertRaises(ValidationError):self.s.start_evaluation({"id":"bad","checkpoint_id":"cp1","panel_id":"panel","protocol":protocol})
    def test_search_filter_pagination(self):
        for i in (0,1):self.s.log_trajectory("eval0",trajectory(i,em=i))
        self.assertEqual(self.s.trajectories("eval0",outcome="correct")["total"],1)
        self.assertEqual(self.s.trajectories("eval0",query="q1")["total"],1)
        self.assertEqual(len(self.s.trajectories("eval0",limit=1,offset=1)["items"]),1)
        self.assertEqual(self.s.trajectories("eval0",query="%'")["total"],0)
    def test_annotation_append_only_and_evidence_validation(self):
        tid=self.s.log_trajectory("eval0",trajectory());orig=self.s.trajectory(tid)["steps"]
        for i in range(2):self.s.annotate(tid,{"category":"other","note":str(i),"step_index":1})
        t=self.s.trajectory(tid);self.assertEqual(len(t["annotations"]),2);self.assertEqual(t["steps"],orig)
        for invalid in (9,True):
            with self.assertRaises(ValidationError):self.s.annotate(tid,{"category":"other","note":"bad","step_index":invalid})
    def test_multiple_connections_writer_and_reader(self):
        other=Store(self.tmp.name)
        try:
            for i in range(10):other.log_metrics("run",i,{"loss":i/10})
            self.assertEqual(len(self.s.metrics("run")),10)
        finally:other.close()
    def test_demo_refuses_real_workspace(self):
        with self.assertRaises(ValidationError):seed_demo(self.s)

if __name__=='__main__':unittest.main()
