"""Controlled method interventions on synthetic, frozen task executions."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request

from searchagent_lab.server import LabServer
from searchagent_lab.studio.sdk import StudioRecorder


PROTOCOL = json.loads((Path(__file__).resolve().parents[1] / "examples/protocol_v2.synthetic.json").read_text())


class MethodStateComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = StudioRecorder(self.tmp.name)
        self.s, self.r = self.rec.store, self.rec.repo
        self.s.register_project({"id": "p", "name": "Synthetic"})
        self.s.register_experiment({"id": "e", "project_id": "p", "name": "Synthetic method test"})
        self.r.register_profile({"experiment_id": "e", "track": "main"})
        self.s.register_run({"id": "run", "experiment_id": "e", "name": "Synthetic", "demo": True,
                             "base_model": "synthetic", "model_revision": "fixture-v1"})
        self.s.register_panel({"id": "panel", "name": "Synthetic frozen tasks", "split": "probe", "demo": True,
            "samples": [{"id": "q1", "dataset": "synthetic", "question": "Fixture 1?", "answers": ["yes"]},
                        {"id": "q2", "dataset": "synthetic", "question": "Fixture 2?", "answers": ["yes"]}]})
        for bank in ("old", "new"):
            self.rec.methods.import_bundle({"method_schema": "method-evidence-v1", "objects": [{
                "id": bank, "kind": "skill_bank", "demo": True, "data": {"general_skills": [
                    {"skill_id": "s1", "name": "Synthetic skill", "principle": bank, "when_to_apply": "Test only"}]}}]},
                scope={"run_id": "run"})

    def tearDown(self):
        self.rec.close()
        self.tmp.cleanup()

    def evaluation(self, eid, bank, *, allowed=None, origin="recorded_execution", weight="a", protocol_change=None,
                   retrieval=None, extensions=None, state_complete=True, no_binding=False, disabled=None):
        self.s.register_checkpoint({"id": "cp-" + eid, "run_id": "run", "step": 1, "weights_sha256": weight * 64})
        protocol = copy.deepcopy(PROTOCOL)
        protocol["harness_version"] = "c" * 64
        if protocol_change: protocol_change(protocol)
        self.s.start_evaluation({"id": eid, "checkpoint_id": "cp-" + eid, "panel_id": "panel", "protocol": protocol,
            "purpose": "checkpoint_eval", "comparison": {"allowed_method_changes": allowed if allowed is not None else ["/bank_content_hash"]}})
        for i in (1, 2):
            self.s.log_trajectory(eid, {"dataset": "synthetic", "sample_id": "q" + str(i),
                "scores": {"em": 0 if eid == "a" else 1}, "steps": [], "runtime_status": "ok", "termination_reason": "final_answer",
                "usage": {"input_tokens": 10 if eid == "a" else 15, **({"output_tokens": 5} if i == 1 else {})}})
        self.s.finish_evaluation(eid)
        if no_binding: return
        data = {"evaluation_id": eid, "binding_origin": origin, "state_complete": state_complete,
                "retrieval_config": retrieval or {"mode": "keyword", "top_k": 2}, "extensions": extensions or {}}
        if bank is not None: data["bank_id"] = bank
        else: data["skills_disabled"] = True
        if disabled is not None: data["skills_disabled"] = disabled
        self.rec.methods.import_bundle({"method_schema": "method-evidence-v1", "objects": [{
            "id": "bind-" + eid, "kind": "evaluation_binding", "demo": True, "data": data}]}, scope={"run_id": "run"})

    def pair(self):
        return self.r.evaluation_pair("a", "b", "method_state")

    def test_bank_version_comparison_and_other_axes_stay_strict(self):
        self.evaluation("a", "old"); self.evaluation("b", "new")
        result = self.pair()
        self.assertTrue(result["compatible"], result["reasons"])
        self.assertEqual(result["macro_delta"], 1)
        self.assertEqual(result["groups"]["rescued"], 2)
        self.assertFalse(result["rl_attribution"])
        self.assertFalse(self.r.evaluation_pair("a", "b", "checkpoint")["compatible"])
        self.assertFalse(self.r.evaluation_pair("a", "b", "harness")["compatible"])

    def test_explicit_bank_off_is_known_not_missing(self):
        self.evaluation("a", None); self.evaluation("b", "new")
        result = self.pair()
        self.assertTrue(result["compatible"], result["reasons"])
        self.assertEqual(result["method_states"]["a"]["runtime"]["bank_content_hash"], None)
        self.assertEqual(result["method_state_differences"][0]["path"], "/bank_content_hash")

    def test_retrieval_changes_need_exact_leaf_declaration(self):
        self.evaluation("a", "old")
        self.evaluation("b", "old", retrieval={"mode": "keyword", "top_k": 3}, allowed=["/retrieval_config/top_k"])
        self.assertTrue(self.pair()["compatible"], self.pair()["reasons"])

    def test_broad_exemption_does_not_hide_retrieval_changes(self):
        self.evaluation("a", "old")
        self.evaluation("b", "new", retrieval={"mode": "keyword", "top_k": 3}, allowed=["/bank_content_hash", "/retrieval_config"])
        self.assertFalse(self.pair()["compatible"])
        self.assertIsNone(self.pair()["macro_delta"])

    def test_weight_change_blocks_method_attribution(self):
        self.evaluation("a", "old"); self.evaluation("b", "new", weight="b")
        result = self.pair()
        self.assertFalse(result["compatible"])
        self.assertIsNone(result["paired_usage"])
        self.assertTrue(any("模型权重" in reason for reason in result["reasons"]))

    def test_budget_change_blocks_method_attribution(self):
        self.evaluation("a", "old")
        self.evaluation("b", "new", protocol_change=lambda p: p["budget"].update(max_tool_calls=99))
        self.assertFalse(self.pair()["compatible"])

    def test_retrospective_binding_is_not_execution_evidence(self):
        self.evaluation("a", "old"); self.evaluation("b", "new", origin="retrospective")
        result = self.pair()
        self.assertFalse(result["compatible"])
        self.assertEqual(result["items"][0]["group"], "unpaired")

    def test_missing_binding_is_not_bank_off(self):
        self.evaluation("a", None, no_binding=True); self.evaluation("b", "new")
        self.assertFalse(self.pair()["compatible"])

    def test_incomplete_state_and_no_change_are_blocked(self):
        self.evaluation("a", "old"); self.evaluation("b", "old", state_complete=False)
        result = self.pair()
        self.assertFalse(result["compatible"])
        self.assertTrue(any("没有登记" in reason for reason in result["reasons"]))

    def test_other_method_extensions_remain_fixed(self):
        self.evaluation("a", "old", extensions={"latent": "reset"})
        self.evaluation("b", "new", extensions={"latent": "carry"})
        self.assertFalse(self.pair()["compatible"])

    def test_contradictory_disabled_bank_is_not_accepted(self):
        self.evaluation("a", "old"); self.evaluation("b", "new", disabled=True)
        self.assertFalse(self.pair()["compatible"])

    def test_paired_usage_keeps_missing_fields_out_of_denominator(self):
        self.evaluation("a", "old"); self.evaluation("b", "new")
        usage = self.pair()["paired_usage"]
        self.assertEqual(usage["input_tokens"]["paired"], 2)
        self.assertEqual(usage["input_tokens"]["mean_delta"], 5)
        self.assertEqual(usage["total_tokens"], {"paired": 1, "missing": 1, "mean_a": 15, "mean_b": 20, "mean_delta": 5})
        self.assertIsNone(usage["search_calls"]["mean_delta"])

    def test_frozen_report_preserves_axis_and_original_outcomes(self):
        self.evaluation("a", "old"); self.evaluation("b", "new")
        before = self.s.export_evaluation("b")
        saved = self.r.save_report({"id": "report", "name": "Synthetic bank comparison", "a": "a", "b": "b", "axis": "method_state"})
        self.assertEqual(saved["snapshot"]["pair"]["axis"], "method_state")
        after = self.s.export_evaluation("b")
        # Each export generates its own timestamp. Compare all saved evidence,
        # without requiring the two reads to happen in the same wall-clock second.
        before.pop("exported_at")
        after.pop("exported_at")
        self.assertEqual(after, before)

    def test_http_axis_route(self):
        self.evaluation("a", "old"); self.evaluation("b", "new")
        with LabServer(("127.0.0.1", 0), self.s) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with urllib.request.urlopen("http://127.0.0.1:" + str(server.server_address[1]) + "/api/studio/evaluation-pair?a=a&b=b&axis=method_state") as response:
                    result = json.load(response)
                self.assertTrue(result["compatible"], result["reasons"])
            finally:
                server.shutdown(); thread.join()


if __name__ == "__main__":
    unittest.main()
