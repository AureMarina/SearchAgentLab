"""Evidence separation and controlled comparisons, using local synthetic data."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from searchagent_lab.core import ValidationError, NotFoundError, digest, dumps
from searchagent_lab.io import import_bundle
from searchagent_lab.server import LabServer
from searchagent_lab.studio.sdk import StudioRecorder


PROTOCOL = json.loads((Path(__file__).resolve().parents[1] / "examples/protocol_v2.synthetic.json").read_text())


class EvaluationDecouplingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = StudioRecorder(self.tmp.name)
        self.s, self.r = self.rec.store, self.rec.repo
        self.s.register_project({"id": "p", "name": "Synthetic test"})
        self.s.register_experiment({"id": "e", "name": "Synthetic test", "project_id": "p"})
        self.r.register_profile({"experiment_id": "e", "track": "main"})
        self.s.register_panel({"id": "panel", "name": "Frozen diagnostic", "split": "probe", "demo": True,
                               "samples": [{"id": "q1", "dataset": "synthetic", "question": "Fixture?", "answers": ["yes"]},
                                           {"id": "q2", "dataset": "synthetic", "question": "Fixture 2?", "answers": ["yes"]}]})
        self.s.register_run({"id": "train", "name": "Synthetic training", "experiment_id": "e", "demo": True,
                             "run_type": "training", "base_model": "fixture", "model_revision": "base-v1"})

    def tearDown(self):
        self.rec.close(); self.tmp.cleanup()

    def evaluation(self, eid, *, weight="a", harness="c", correct=1, purpose="checkpoint_eval", protocol_change=None,
                   allowed=None, weights=True, run="train", kind=None):
        cp = {"id": "cp-" + eid, "run_id": run}
        if kind == "model_snapshot":
            cp["kind"] = kind
        else:
            cp["step"] = len(self.s.conn.execute("SELECT id FROM checkpoints").fetchall())
        if weights:
            cp["weights_sha256"] = weight * 64
        self.s.register_checkpoint(cp)
        protocol = copy.deepcopy(PROTOCOL)
        protocol["harness_version"] = harness * 64
        if protocol_change:
            protocol_change(protocol)
        ev = {"id": eid, "checkpoint_id": cp["id"], "panel_id": "panel", "protocol": protocol}
        if purpose is not None:
            ev["purpose"] = purpose
        if allowed is not None:
            ev["comparison"] = {"allowed_harness_changes": allowed}
        self.s.start_evaluation(ev)
        for i in (1, 2):
            self.s.log_trajectory(eid, {"dataset": "synthetic", "sample_id": "q" + str(i), "scores": {"em": correct},
                "runtime_status": "ok", "termination_reason": "final_answer", "steps": [],
                "usage": {"tool_calls": i, "latency_ms": 10 * i} if i == 1 else {}})
        self.s.finish_evaluation(eid)
        return ev

    def test_independent_agent_without_training_or_fake_step(self):
        protocol = copy.deepcopy(PROTOCOL)
        d = self.rec.register_agent_evaluation(run={"id": "agent", "name": "Agent", "experiment_id": "e"},
                model_snapshot={"id": "agent-model", "weights_sha256": "a" * 64},
                evaluation={"id": "agent-eval", "panel_id": "panel", "protocol": protocol})
        self.assertEqual(d["purpose"], "agent_eval")
        ev = self.s.evaluation(d["evaluation_id"])
        self.assertEqual(ev["run"]["run_type"], "evaluation")
        self.assertIsNone(ev["checkpoint"]["step"])
        self.assertEqual(ev["checkpoint"]["kind"], "model_snapshot")
        summary = self.r.training_summary("agent")
        self.assertFalse(summary["classification"]["has_training_evidence"])
        self.assertEqual(summary["coverage"]["checkpoint_count"], 0)
        self.assertEqual(summary["coverage"]["model_snapshot_count"], 1)
        self.assertEqual(self.s.metrics("agent"), [])

    def test_independent_snapshot_roundtrips_v2_import(self):
        bundle = {"schema_version": 2, "runs": [{"id": "agent", "name": "Agent", "experiment_id": "e", "run_type": "evaluation"}],
                  "checkpoints": [{"id": "snapshot", "run_id": "agent", "kind": "model_snapshot", "step": None}]}
        import_bundle(self.s, bundle)
        import_bundle(self.s, bundle)
        self.assertIsNone(self.s._get("checkpoints", "snapshot")["step"])

    def test_independent_registration_rolls_back_on_bad_protocol(self):
        with self.assertRaises(ValidationError):
            self.rec.register_agent_evaluation(run={"id": "bad", "name": "Bad", "experiment_id": "e"},
                model_snapshot={"id": "bad-model"}, evaluation={"id": "bad-eval", "panel_id": "panel", "protocol": {}})
        self.assertIsNone(self.s.conn.execute("SELECT id FROM runs WHERE id='bad'").fetchone())

    def test_model_snapshot_rejects_optimizer_step(self):
        with self.assertRaises(ValidationError):
            self.s.register_model_snapshot({"id": "bad", "run_id": "train", "step": 0})

    def test_evaluation_run_rejects_training_batch_and_checkpoint(self):
        self.rec.register_evaluation_run(id="agent", name="Agent", experiment_id="e")
        with self.assertRaises(ValidationError):
            self.r.record_batch({"id": "b", "run_id": "agent", "step": 1, "groups": []})
        with self.assertRaises(ValidationError):
            self.s.register_checkpoint({"id": "cp", "run_id": "agent", "step": 0})

    def test_training_origin_cannot_enter_evaluation(self):
        self.s.register_checkpoint({"id": "cp", "run_id": "train", "step": 0})
        for marker in ({"purpose": "train_rollout"}, {"origin": "train_rollout"},
                       {"record_purpose": {"value": "train_rollout"}}, {"metadata": {"purpose": "train_rollout"}}):
            with self.subTest(marker=marker), self.assertRaises(ValidationError):
                self.s.start_evaluation({"id": "bad", "checkpoint_id": "cp", "panel_id": "panel", "protocol": PROTOCOL, **marker})

    def test_training_origin_cannot_enter_trajectory(self):
        self.s.register_checkpoint({"id": "cp", "run_id": "train", "step": 0})
        self.s.start_evaluation({"id": "ev", "checkpoint_id": "cp", "panel_id": "panel", "protocol": PROTOCOL})
        with self.assertRaises(ValidationError):
            self.s.log_trajectory("ev", {"dataset": "synthetic", "sample_id": "q1", "purpose": "train_rollout", "scores": {"em": 1}})
        self.assertEqual(self.s.evaluation("ev")["stats"]["recorded"], 0)

    def test_explicit_eval_cannot_expose_gold_v1(self):
        self.s.register_checkpoint({"id": "cp", "run_id": "train", "step": 0})
        p = copy.deepcopy(PROTOCOL); p.pop("protocol_version"); p["groundtruth_to_agent"] = True
        with self.assertRaises(ValidationError):
            self.s.start_evaluation({"id": "ev", "purpose": "agent_eval", "checkpoint_id": "cp", "panel_id": "panel", "protocol": p})

    def test_legacy_purpose_is_unknown_and_scores_unchanged(self):
        self.evaluation("old", purpose=None)
        original = self.s.export_evaluation("old")
        summary = self.r.evaluation_summary("train", "old")
        self.assertEqual(summary["purpose"]["value"], "unknown")
        self.assertEqual(summary["outcomes"]["answer_correct"], 2)
        self.assertEqual(self.s.export_evaluation("old")["trajectories"], original["trajectories"])

    def test_usage_coverage_preserves_missing_and_search_not_guessed(self):
        self.evaluation("ev")
        summary = self.r.evaluation_summary("train", "ev")
        self.assertEqual(summary["usage"]["tool_calls"]["count"], 1)
        self.assertEqual(summary["usage"]["tool_calls"]["missing"], 1)
        self.assertEqual(summary["usage"]["tool_calls"]["mean"], 1)
        self.assertIsNone(summary["usage"]["search_calls"]["mean"])
        self.assertIsNone(summary["usage"]["output_tokens"]["total"])
        self.assertFalse(summary["context"]["panel"]["generalization_eligible"])

    def test_format_error_is_symptom_not_harness_blame(self):
        self.s.register_checkpoint({"id": "cp", "run_id": "train", "step": 0})
        self.s.start_evaluation({"id": "ev", "checkpoint_id": "cp", "panel_id": "panel", "protocol": PROTOCOL})
        self.s.log_trajectory("ev", {"dataset": "synthetic", "sample_id": "q1", "runtime_status": "ok", "termination_reason": "format_error", "scores": {"em": 0}})
        summary = self.r.evaluation_summary("train", "ev")
        self.assertEqual(summary["outcomes"]["symptoms"], [{"name": "format_error", "count": 1}])
        self.assertIn("不", summary["outcomes"]["attribution"])
        self.assertNotIn("failure_owner", summary)
        self.assertEqual(summary["coverage"]["recorded"], 1)
        self.assertEqual(summary["coverage"]["expected"], 2)

    def test_harness_comparison_fixed_weights_declared_change(self):
        self.evaluation("a", harness="c", correct=0, purpose="agent_eval")
        self.evaluation("b", harness="d", correct=1, purpose="agent_eval", allowed=["/harness_version"])
        self.assertFalse(self.r.pair("a", "b")["compatible"])
        pair = self.r.evaluation_pair("a", "b", "harness")
        self.assertTrue(pair["compatible"], pair["reasons"])
        self.assertEqual(pair["groups"]["rescued"], 2)
        self.assertEqual(pair["macro_delta"], 1)
        self.assertFalse(pair["rl_attribution"])
        self.assertEqual(pair["items"][0]["sample"]["id"], "q1")

    def test_harness_mixed_model_change_blocked(self):
        self.evaluation("a", harness="c", purpose="agent_eval")
        self.evaluation("b", harness="d", weight="b", purpose="agent_eval", allowed=["/harness_version"])
        pair = self.r.evaluation_pair("a", "b", "harness")
        self.assertFalse(pair["compatible"])
        self.assertIsNone(pair["macro_delta"])
        self.assertEqual(pair["items"][0]["group"], "unpaired")

    def test_harness_budget_change_cannot_be_whitelisted(self):
        self.evaluation("a", purpose="agent_eval")
        self.evaluation("b", harness="d", purpose="agent_eval", allowed=["/harness_version", "/budget"],
                        protocol_change=lambda p: p["budget"].update(max_tool_calls=99))
        self.assertFalse(self.r.evaluation_pair("a", "b", "harness")["compatible"])

    def test_harness_undeclared_parser_change_blocked(self):
        self.evaluation("a", purpose="agent_eval")
        self.evaluation("b", harness="d", purpose="agent_eval", allowed=["/harness_version"],
                        protocol_change=lambda p: p["parser"].update(version="new"))
        self.assertFalse(self.r.evaluation_pair("a", "b", "harness")["compatible"])

    def test_checkpoint_comparison_keeps_harness_fixed(self):
        self.evaluation("a", correct=0)
        self.evaluation("b", weight="b", correct=1)
        pair = self.r.evaluation_pair("a", "b", "checkpoint")
        self.assertTrue(pair["compatible"], pair["reasons"])
        self.assertEqual(pair["net_correct_change"], 2)
        self.evaluation("c", weight="b", harness="d")
        self.assertFalse(self.r.evaluation_pair("a", "c", "checkpoint")["compatible"])

    def test_unknown_weights_never_green(self):
        self.evaluation("a", weights=False)
        self.evaluation("b", weight="b")
        pair = self.r.evaluation_pair("a", "b", "checkpoint")
        self.assertFalse(pair["compatible"])
        self.assertTrue(any(c["status"] == "missing" for c in pair["controls"]))

    def test_legacy_compare_does_not_gain_new_attribution(self):
        self.evaluation("a", purpose=None)
        self.evaluation("b", purpose=None, weight="b")
        self.assertTrue(self.r.pair("a", "b")["compatible"])
        self.assertFalse(self.r.evaluation_pair("a", "b")["compatible"])

    def test_training_summary_no_panel_needed_and_no_invented_loss(self):
        self.s.log_metrics("train", 1, {"actor/pg_loss": -0.2, "train/reward": 0.3, "actor/kl": None})
        self.r.record_batch({"id": "batch", "run_id": "train", "step": 1, "groups": [
            {"id": "g1", "samples": [{"reward": 0, "advantage": 0}, {"reward": 1, "advantage": 0.5}]},
            {"id": "g2", "samples": [{"reward": 0}, {"reward": 0}]}]})
        d = self.r.training_summary("train")
        self.assertEqual(d["metrics"]["series"][0]["latest"]["value"], -0.2)
        self.assertNotIn("actor/kl", d["metrics"]["recorded_keys"])
        self.assertEqual(d["batch_summary"]["advantage"]["nonzero_fraction"], 0.5)
        self.assertEqual(d["batch_summary"]["advantage"]["missing"], 2)
        self.assertEqual(d["batch_summary"]["groups"]["mixed_reward"], 1)
        self.assertEqual(d["batch_summary"]["groups"]["uniform_reward"], 1)
        self.assertEqual(d["coverage"]["evaluation_count"], 0)
        self.assertNotIn("loss", d["batch_summary"])

    def test_evaluation_only_metric_rows_are_not_training_evidence(self):
        self.s.register_run({"id": "native", "name": "Native", "experiment_id": "e"})
        self.s.log_metrics("native", 0, {"eval/NQ/em": 0.5, "val/reward": 0.2})
        d = self.r.training_summary("native")
        self.assertFalse(d["classification"]["has_training_evidence"])
        self.assertEqual(d["classification"]["training_metric_rows"], 0)
        self.assertEqual(d["classification"]["logged_evaluation_rows"], 1)
        self.assertTrue(all(s["scope"] == "logged_evaluation" for s in d["metrics"]["series"]))

    def test_verified_dev_isolation_can_be_certified_without_changing_score(self):
        from searchagent_lab.isolation import audit_panel
        sample = {"id": "official-dev-1", "dataset": "synthetic", "question": "Identify the capital of Norway?", "answers": ["Oslo"],
                  "metadata": {"source_id": "official-dev-1", "source_revision": "fixture-v1", "source_split": "dev"}}
        self.s.register_panel({"id": "dev", "name": "Realistic dev fixture", "split": "dev", "samples": [sample]})
        root = Path(self.tmp.name)
        entries = []
        for role in ("train", "test"):
            file = root / (role + ".json")
            file.write_text(json.dumps([{"id": role + "-1", "dataset": "synthetic", "question": "What color is a ruby?"}]))
            entries.append({"project_id": "p", "role": role, "path": file.name, "revision": "v1", "description": "Complete synthetic scope", "declared_complete": True})
        report = audit_panel(self.s.panel("dev"), {"projects": ["p"], "entries": entries}, root, ["p"])
        self.s.record_audit("dev", report)
        self.s.register_checkpoint({"id": "cp-dev", "run_id": "train", "step": 1})
        self.s.start_evaluation({"id": "ev-dev", "checkpoint_id": "cp-dev", "panel_id": "dev", "protocol": PROTOCOL, "purpose": "checkpoint_eval"})
        panel = self.r.evaluation_summary("train", "ev-dev")["context"]["panel"]
        self.assertTrue(panel["generalization_eligible"], panel["eligibility_reasons"])
        self.assertEqual(panel["classification"], "isolated_dev")
        self.assertEqual(self.s.evaluation("ev-dev")["stats"]["recorded"], 0)

    def test_training_rollout_inline_and_scope_enforced(self):
        sample = {"reward": 0, "messages": [{"role": "assistant", "content": "original"}]}
        self.r.record_batch({"id": "batch", "run_id": "train", "step": 4, "groups": [{"id": "g", "samples": [sample]}]})
        d = self.r.training_rollout("train", "batch", "g", "0")
        self.assertEqual(d["sample"], sample)
        self.assertEqual(d["document"]["raw"], sample)
        self.assertEqual(d["document"]["view"]["record_purpose"]["value"], "train_rollout")
        self.assertEqual(d["document"]["view"]["trajectories"][0]["step_count"], 1)
        with self.assertRaises(NotFoundError):
            self.r.training_rollout("train", "batch", "missing", "0")
        self.s.register_run({"id": "other", "name": "Other", "experiment_id": "e"})
        with self.assertRaises(NotFoundError):
            self.r.training_rollout("other", "batch", "g", "0")

    def test_harness_checks_execute_without_model_or_em(self):
        self.rec.register_evaluation_run(id="agent", name="Agent", experiment_id="e")
        called = []
        def parser(text):
            called.append(text)
            return text.removeprefix("<answer>").removesuffix("</answer>")
        d = self.rec.check_harness(id="check", run_id="agent", harness_version="fixture",
            fixtures=[{"id": "parser-1", "handler": "parser", "input": "<answer>yes</answer>", "expected": "yes"},
                      {"id": "parser-2", "handler": "parser", "input": "bad", "expected": "no"}], handlers={"parser": parser})
        self.assertEqual(called, ["<answer>yes</answer>", "bad"])
        self.assertEqual((d["passed"], d["total"], d["status"]), (1, 2, "failed"))
        self.assertEqual(self.s.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0], 0)
        self.assertEqual(self.r.evaluation_summary("agent")["checks"][0]["id"], "check")
        self.assertEqual(self.rec.log_harness_check(d), d)

    def test_harness_check_rejects_immutable_overwrite(self):
        d = {"id": "check", "run_id": "train", "checks": [{"name": "fixture", "passed": True}]}
        self.rec.log_harness_check(d)
        d["checks"][0]["passed"] = False
        with self.assertRaises(ValidationError): self.rec.log_harness_check(d)

    def test_saved_report_freezes_axis_and_conditions(self):
        self.evaluation("a", harness="c", correct=0, purpose="agent_eval")
        self.evaluation("b", harness="d", purpose="agent_eval", allowed=["/harness_version"])
        d = self.r.save_report({"id": "saved", "name": "Harness change", "a": "a", "b": "b", "axis": "harness"})
        self.assertEqual(d["snapshot"]["axis"], "harness")
        self.assertEqual(d["snapshot"]["pair"]["groups"]["rescued"], 2)

    def test_omitted_axis_cannot_bypass_new_report_guard(self):
        self.evaluation("a", purpose=None)
        self.evaluation("b", purpose=None, weight="b")
        self.assertTrue(self.r.pair("a", "b")["compatible"])
        with self.assertRaises(ValidationError):
            self.r.save_report({"id": "bad", "name": "Missing declarations", "a": "a", "b": "b"})

    def test_preexisting_legacy_report_remains_byte_faithful(self):
        self.evaluation("a", purpose=None)
        self.evaluation("b", purpose=None, weight="b")
        snapshot = self.r.report("b", "a")
        legacy = {"id": "old-report", "name": "Old frozen snapshot", "snapshot": snapshot, "snapshot_hash": digest(snapshot)}
        encoded = dumps(legacy)
        with self.r.lock, self.r.conn:
            self.r.conn.execute("INSERT INTO saved_reports VALUES (?,?,?,?)", ("old-report", "p", "main", encoded))
        self.assertEqual(dumps(self.r.saved_report("old-report")), encoded)

    def test_training_batch_rejects_explicit_evaluation_and_cross_run_ref(self):
        self.evaluation("formal")
        trace_id = self.s.trajectories("formal")["items"][0]["id"]
        with self.assertRaises(ValidationError):
            self.r.record_batch({"id": "bad", "run_id": "train", "step": 1,
                                 "groups": [{"id": "g", "samples": [{"trace_ref": {"trajectory": trace_id}, "reward": 1}]}]})
        self.s.register_run({"id": "other", "name": "Other", "experiment_id": "e"})
        self.rec.methods.import_bundle({"method_schema": "method-evidence-v1", "scope": {"run_id": "other"},
            "objects": [{"id": "other-trace", "kind": "trace", "usage": "training", "data": {"steps": []}}]})
        with self.assertRaises(ValidationError):
            self.r.record_batch({"id": "bad-other", "run_id": "train", "step": 1,
                                 "groups": [{"id": "g", "samples": [{"trace_ref": {"object_id": "other-trace"}, "reward": 1}]}]})

    def test_legacy_reference_remains_evidence_only(self):
        self.evaluation("legacy", purpose=None)
        trace_id = self.s.trajectories("legacy")["items"][0]["id"]
        self.r.record_batch({"id": "batch", "run_id": "train", "step": 1,
                             "groups": [{"id": "g", "samples": [{"trace_ref": {"trajectory": trace_id}, "reward": 1}]}]})
        d = self.r.training_rollout("train", "batch", "g", sample_index=0)
        self.assertEqual(d["trace_role"], "evidence_only")
        self.assertIsNone(d["document"])
        self.assertEqual(d["trace_ref"]["trajectory"], trace_id)
        self.assertNotIn("purpose", self.s.trajectory(trace_id))

    def test_inline_eval_purpose_cannot_be_forced_into_training(self):
        with self.assertRaises(ValidationError):
            self.r.record_batch({"id": "bad", "run_id": "train", "step": 1,
                "groups": [{"id": "g", "samples": [{"purpose": "agent_eval", "steps": [], "reward": 1}]}]})

    def test_explicit_sample_index_disambiguates_numeric_sample_id(self):
        samples = [{"id": "1", "reward": 1, "messages": [{"role": "assistant", "content": "A"}]},
                   {"reward": 0, "messages": [{"role": "assistant", "content": "B"}]}]
        self.r.record_batch({"id": "batch", "run_id": "train", "step": 1, "groups": [{"id": "g", "samples": samples}]})
        d = self.r.training_rollout("train", "batch", "g", "1", sample_index=1)
        self.assertEqual(d["sample"]["reward"], 0)
        self.assertEqual(d["document"]["raw"]["messages"][0]["content"], "B")
        with self.assertRaises(ValidationError): self.r.training_rollout("train", "batch", "g", "1")

    def test_team_roundtrip_retains_model_snapshot_and_harness_check(self):
        from searchagent_lab.team.package import init_source, export_run, read_package
        from searchagent_lab.team.hub import Hub, gid
        self.rec.register_agent_evaluation(run={"id": "agent", "name": "Agent", "experiment_id": "e"},
            model_snapshot={"id": "agent-model", "weights_sha256": "a" * 64},
            evaluation={"id": "agent-eval", "panel_id": "panel", "protocol": PROTOCOL})
        self.rec.check_harness(id="check", run_id="agent", evaluation_id="agent-eval", harness_version="v1",
            handlers={"echo": lambda x: x}, fixtures=[{"id": "echo-1", "handler": "echo", "input": "fixture", "expected": "fixture"}])
        source = init_source(self.tmp.name, "tester", "cpu-only")
        package = export_run(self.tmp.name, "agent")
        payload = read_package(package["path"])[1]
        self.assertEqual(payload["run"]["run_type"], "evaluation")
        self.assertEqual(len(payload["harness_checks"]), 1)
        with tempfile.TemporaryDirectory() as center, Hub(center) as hub:
            receipt = hub.ingest(package["path"], allow_definitions=True)
            run_id = receipt["run_id"]
            self.assertEqual(hub.store.run(run_id)["run_type"], "evaluation")
            ev = hub.store.evaluation(gid(source["workspace_id"], "evaluation", "agent-eval"))
            self.assertEqual(ev["purpose"], "agent_eval")
            self.assertIsNone(ev["checkpoint"]["step"])
            self.assertEqual(ev["checkpoint"]["kind"], "model_snapshot")
            check = hub.studio.evaluation_summary(run_id, ev["id"])["checks"][0]
            self.assertEqual(check["passed"], 1)
            self.assertEqual(check["evaluation_id"], ev["id"])
            self.assertEqual(check["source_content_hash"], payload["harness_checks"][0]["content_hash"])
            self.assertEqual(hub.ingest(package["path"], allow_definitions=True)["delivery_status"], "duplicate")

    def test_harness_run_has_no_model_or_evaluation_dependency(self):
        self.rec.register_harness_run(id="checks", name="Local harness checks", experiment_id="e")
        self.rec.check_harness(id="check", run_id="checks", harness_version="v1", handlers={"echo": lambda x: x},
                              fixtures=[{"id": "echo", "handler": "echo", "input": "yes", "expected": "yes"}])
        d = self.r.evaluation_summary("checks")
        self.assertEqual(d["evaluations"], [])
        self.assertEqual(d["checks"][0]["purpose"], "harness_check")
        self.assertEqual(self.s.conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)


class EvaluationHTTPTests(unittest.TestCase):
    def test_summary_and_rollout_routes(self):
        with tempfile.TemporaryDirectory() as folder, StudioRecorder(folder) as rec:
            rec.store.register_run({"id": "run", "name": "Fixture"})
            rec.log_training_batch({"id": "b", "run_id": "run", "step": 1,
                                    "groups": [{"id": "g", "samples": [{"id": "s", "reward": 0}]}]})
            server = LabServer(("127.0.0.1", 0), rec.store)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = "http://127.0.0.1:" + str(server.server_address[1])
            try:
                for path in ("/api/studio/training-summary?run=run", "/api/studio/evaluation-summary?run=run",
                             "/api/studio/training-rollout?run=run&batch=b&group=g&sample=s"):
                    with urllib.request.urlopen(base + path) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.load(response)["run_id"], "run")
                request = urllib.request.Request(base + "/api/studio/harness-check", data=b"{}", headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__": unittest.main()
