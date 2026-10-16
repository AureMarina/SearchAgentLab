"""OPD native-format semantics, exact-byte intake and synthetic transport checks."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from searchagent_lab.core import ValidationError
from searchagent_lab.methods.opd_adapters import (
    adapt_agentopsd_debug, adapt_ocsd_diagnostics, adapt_opd_metrics,
    adapt_verl_generations, parse_ocsd_parquet, verl_opd_configuration,
)
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub, gid
from searchagent_lab.team.package import read_package, source


ROOT = Path(__file__).resolve().parents[1]


def _diagnostics(bundle):
    return [obj for obj in bundle["objects"] if obj["kind"] == "opd_diagnostic"]


def _register(path):
    with StudioRecorder(path) as studio:
        studio.store.register_project({"id": "p", "name": "Synthetic", "demo": True})
        studio.store.register_experiment({"id": "e", "project_id": "p", "name": "Synthetic", "demo": True})
        studio.register_experiment_profile(experiment_id="e", track="main")
        studio.store.register_run({"id": "r", "experiment_id": "e", "name": "Synthetic", "demo": True, "run_type": "training"})
        studio.store.register_checkpoint({"id": "cp", "run_id": "r", "step": 1, "demo": True})


class OpdSdkNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "db"
        _register(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _verl(self):
        return {"input": "问题", "output": "<answer>答</answer>", "gts": ["答"], "score": 1.0, "step": 12,
                "reward_extra": {"not_an_EM_contract": True}}

    def _ocsd(self, num_tokens=3):
        return {"global_step": 12, "batch_idx": 0, "traj_uid": "upstream-trajectory", "uid": "upstream-group",
            "step_index": 2, "available_future_len": 1, "future_obs_text_1": "未来观察",
            "num_tokens": num_tokens, "step_selected": True, "is_nll_selected": False,
            "advantage": -1.0, "step_delta_F": 0.8, "step_delta_A": 0.3,
            "step_residual_e": 0.5, "step_q": 0.24, "mean_old_log_prob": -1.8}

    def _agent(self):
        return {"global_step": 12, "sample_idx": 0, "turn_step": 2, "traj_uid": "upstream-trajectory",
            "episode_reward": 0.0, "episode_length": 3.0, "seq_advantage": -1.0,
            "tokens": ["查", "证", "据"], "token_ids": [100, 200, 300], "gaps": [0.5, 0.0, -0.5],
            "teacher_log_probs": [-1.0, -1.0, -2.0], "student_log_probs": [-1.5, -1.0, -1.5],
            "token_advantages": [0.2, 0.0, -0.2]}

    def test_native_config_keeps_two_switches_and_configured_coefficient_distinct(self):
        config = {"distillation": {"enabled": True, "distillation_loss": {
            "use_task_rewards": False, "use_policy_gradient": True, "distillation_loss_coef": 0.2}}}
        got = verl_opd_configuration(config, teacher={"construction": "frozen_external", "revision": "teacher-v1"})
        self.assertFalse(got["use_task_rewards"])
        self.assertTrue(got["use_policy_gradient"])
        self.assertEqual(got["distillation_loss_coef"], 0.2)
        self.assertEqual(got["source_rule_effective_distillation_coef"], 1.0)
        self.assertTrue(got["configuration_only"])
        self.assertEqual(got["native_config"], config)
        self.assertNotIn("teacher", config)

    def test_native_config_unknown_switches_are_not_filled_with_defaults(self):
        got = verl_opd_configuration({"distillation": {}})
        self.assertNotIn("enabled", got)
        self.assertNotIn("use_task_rewards", got)
        self.assertNotIn("source_rule_effective_distillation_coef", got)
        self.assertEqual(got["source_revision"], "unknown")
        with self.assertRaises(ValidationError):
            verl_opd_configuration({"distillation": {"distillation_loss": {"use_task_rewards": "false"}}})

    def test_reviewed_schema_hash_does_not_invent_the_actual_producer_revision(self):
        unknown = _diagnostics(adapt_ocsd_diagnostics([self._ocsd()], scope={"run_id": "r"}))[0]["data"]
        self.assertEqual(unknown["source_version"], "unknown")
        self.assertEqual(len(unknown["schema_reference_sha256"]), 64)
        explicit = _diagnostics(adapt_ocsd_diagnostics([self._ocsd()], scope={"run_id": "r"},
            producer_revision="fixture-trainer-commit"))[0]["data"]
        self.assertEqual(explicit["source_version"], "fixture-trainer-commit")

    def test_configuration_and_diagnostic_helpers_roundtrip_through_journal(self):
        path = self.root / "opd-events.jsonl"
        with MethodEvidenceRecorder(journal_path=path, scope={"run_id": "r"}, demo=True) as log:
            log.opd_configuration("profile", native_config={"distillation": {"enabled": True,
                "distillation_loss": {"use_task_rewards": True, "use_policy_gradient": False, "distillation_loss_coef": 0.1}}})
            log.opd_diagnostic("diagnostic", {"diagnostic_type": "step", "context": "training_on_policy",
                "signals": {"task": {"original_advantage": 0.0, "effective_advantage": 0.0},
                            "opd": {"raw": [0.5], "effective": [0.0], "loss_active": False}},
                "masks": {"token": [0]}})
        with StudioRecorder(self.db) as studio:
            studio.import_method_evidence(path)
            data = studio.methods.object("diagnostic")["data"]
            self.assertEqual(data["signals"]["opd"]["raw"], [0.5])
            self.assertEqual(data["signals"]["opd"]["effective"], [0.0])
            self.assertEqual(studio.store.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0], 0)

    def test_verl_generation_score_is_reported_not_em_or_optimizer_evidence(self):
        got = _diagnostics(adapt_verl_generations([self._verl()], scope={"run_id": "r"},
            purpose="training_rollout", demo=True))[0]["data"]
        self.assertEqual(got["context"], "training_on_policy")
        self.assertEqual(got["training_step"], 12)
        self.assertNotIn("step", got)
        self.assertNotIn("scores", got)
        self.assertNotIn("tokens", got)
        self.assertEqual(got["generation"], self._verl())
        self.assertIn("not automatically", got["reported_metrics"][0]["definition"])

    def test_verl_evaluation_only_intake_does_not_certify_autonomy(self):
        got = _diagnostics(adapt_verl_generations([self._verl()], scope={"run_id": "r"},
            purpose="evaluation_only", demo=True))[0]["data"]
        self.assertEqual(got["declared_purpose"], "evaluation_only")
        self.assertEqual(got["context"], "evaluation")
        self.assertEqual(got["autonomy_status"], "unknown")
        self.assertNotIn("evaluation_id", got)
        self.assertEqual(got["identity_status"], "unmapped")
        with self.assertRaises(ValidationError):
            adapt_verl_generations([self._verl()], scope={"run_id": "r"}, purpose="test")

    def test_native_source_original_bytes_lines_hashes_and_extra_fields_survive_import(self):
        raw = ("\n" + json.dumps(self._verl(), ensure_ascii=False, separators=(", ", ": ")) + "\n\n").encode()
        path = self.root / "12.jsonl"
        path.write_bytes(raw)
        with StudioRecorder(self.db) as studio:
            result = studio.import_verl_generations(path, scope={"run_id": "r"}, purpose="validation",
                record_prefix="native", student_checkpoint_id="cp", demo=True)
            original = studio.methods.conn.execute("SELECT content FROM imports WHERE id=?", (result["import_id"],)).fetchone()[0]
            self.assertEqual(original, raw)
            source_row = studio.methods.object("native-source-0")
            self.assertEqual(source_row["data"], self._verl())
            self.assertEqual(source_row["provenance"]["source_line"], 2)
            self.assertEqual(source_row["provenance"]["source_file_sha256"], hashlib.sha256(raw).hexdigest())
            ref = studio.methods.object("native-diagnostic-0")["data"]["source_refs"][0]
            self.assertTrue(studio.methods.resolve_ref(ref)["verified"])
            self.assertEqual(studio.store.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0], 0)

    def test_scope_and_formal_identity_are_explicit_and_not_inferred_from_traj_uid(self):
        bundle = adapt_ocsd_diagnostics([self._ocsd()], scope={"run_id": "r"}, demo=True)
        data = _diagnostics(bundle)[0]["data"]
        self.assertEqual(data["external_traj_uid"], "upstream-trajectory")
        self.assertNotIn("trajectory_id", data)
        self.assertNotIn("student_checkpoint_id", data)
        with self.assertRaises(ValidationError):
            adapt_ocsd_diagnostics([self._ocsd()], scope={"experiment_id": "e"})
        with self.assertRaises(ValidationError):
            adapt_ocsd_diagnostics([self._ocsd()], scope={"run_id": "r"},
                identity_map={0: {"student_checkpoint_id": "other"}}, student_checkpoint_id="cp")

    def test_explicit_mapping_invalid_row_duplicate_index_or_unknown_field_is_rejected(self):
        for mapping in ({4: {"student_checkpoint_id": "cp"}}, {0: {}, "0": {}}, {0: {"pretend_eval": "e"}}):
            with self.subTest(mapping=mapping), self.assertRaises(ValidationError):
                adapt_ocsd_diagnostics([self._ocsd()], scope={"run_id": "r"}, identity_map=mapping)

    def test_ocsd_future_observation_and_nll_selection_have_distinct_meanings(self):
        row = self._ocsd()
        bundle = adapt_ocsd_diagnostics([row], scope={"run_id": "r"}, source_format="rows", demo=True)
        data = _diagnostics(bundle)[0]["data"]
        self.assertTrue(data["observation"]["has_future_observation"])
        self.assertFalse(data["observation"]["is_nll_selected"])
        self.assertEqual(data["observation"]["residual"], 0.5)
        self.assertEqual(data["signals"]["task"]["original_advantage"], -1.0)
        self.assertNotIn("effective_advantage", data["signals"]["task"])
        self.assertNotIn("tokens", data)
        self.assertEqual(data["coverage"]["teacher_prompt"], "not recorded")
        row["is_nll_selected"] = None
        self.assertIsNone(_diagnostics(adapt_ocsd_diagnostics([row], scope={"run_id": "r"}))[0]["data"]["observation"]["is_nll_selected"])

    def test_ocsd_zero_token_summaries_preserve_zero_but_mark_no_support(self):
        row = self._ocsd(0)
        for key in ("advantage", "step_delta_F", "step_delta_A", "step_residual_e", "step_q", "mean_old_log_prob"):
            row[key] = 0.0
        bundle = adapt_ocsd_diagnostics([row], scope={"run_id": "r"}, record_prefix="zero", demo=True)
        data = _diagnostics(bundle)[0]["data"]
        self.assertTrue(all(metric["value"] == 0 for metric in data["reported_metrics"]))
        self.assertTrue(all(metric["support"]["count"] == 0 and metric["support"]["available"] is False for metric in data["reported_metrics"]))
        with StudioRecorder(self.db) as studio:
            studio.methods.import_bundle(bundle)
            metrics = studio.methods.opd_record("zero-diagnostic-0")["diagnostic"]["reported_metrics"]
            self.assertTrue(all(m["value"] is None and m["reported_value"] == 0 for m in metrics))

    def test_native_population_coverage_unknown_and_selected_shards_are_declared(self):
        rows = [self._ocsd() for _ in range(3)]
        unknown = adapt_ocsd_diagnostics(rows, scope={"run_id": "r"}, row_offset=1, row_limit=1)
        coverage = unknown["objects"][0]["data"]["coverage"]
        self.assertEqual(coverage["saved_source_rows"], 3)
        self.assertEqual(coverage["selected_row_offset"], 1)
        self.assertFalse(coverage["selected_rows_complete"])
        self.assertIsNone(coverage["source_complete"])
        self.assertEqual(_diagnostics(unknown)[0]["data"]["source_row_index"], 1)
        self.assertIsNone(unknown["objects"][0]["data"]["source_file_sha256"])
        with self.assertRaises(ValidationError):
            adapt_ocsd_diagnostics(rows, scope={"run_id": "r"}, source_population_size=8, source_complete=True)

    def test_ocsd_optional_parquet_dependency_error_has_non_parquet_fallback(self):
        with patch.dict("sys.modules", {"pyarrow": None, "pyarrow.parquet": None}):
            with self.assertRaisesRegex(ValidationError, "viewer needs no pyarrow"):
                parse_ocsd_parquet(b"PAR1synthetic-fixturePAR1")

    def test_agentopsd_all_token_arrays_are_retained_without_fabricating_masks_or_topk(self):
        row = self._agent()
        data = _diagnostics(adapt_agentopsd_debug([row], scope={"run_id": "r"}))[0]["data"]
        for key in ("tokens", "token_ids", "gaps", "teacher_log_probs", "student_log_probs", "token_advantages"):
            self.assertEqual(data["tokens"][key], row[key])
        self.assertNotIn("task_mask", data["tokens"])
        self.assertNotIn("opd_mask", data["tokens"])
        self.assertNotIn("task_advantages", data["tokens"])
        self.assertEqual(data["coverage"]["zero_token_rows"], "omitted upstream")
        self.assertIn("cannot reconstruct", data["coverage"]["topk"])

    def test_agentopsd_mismatched_lengths_missing_arrays_and_boolean_tokenids_fail(self):
        bad1 = self._agent(); bad1["tokens"].pop()
        bad2 = self._agent(); bad2.pop("student_log_probs")
        bad3 = self._agent(); bad3["token_ids"][0] = True
        for row in (bad1, bad2, bad3):
            with self.subTest(row=row), self.assertRaises(ValidationError):
                adapt_agentopsd_debug([row], scope={"run_id": "r"})

    def test_invalid_json_duplicates_nonfinite_and_wrong_native_types_are_rejected(self):
        for raw in (b'{"input":"x","input":"y"}\n', b'{"score":NaN}\n', b'[]\n', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                adapt_verl_generations(raw, scope={"run_id": "r"}, purpose="unknown")
        row = self._ocsd(); row["is_nll_selected"] = "False"
        with self.assertRaises(ValidationError):
            adapt_ocsd_diagnostics([row], scope={"run_id": "r"})
        row = self._agent(); row["gaps"][0] = float("inf")
        with self.assertRaises(ValidationError):
            adapt_agentopsd_debug([row], scope={"run_id": "r"})

    def test_uniopsd_rescue_is_fraction_of_all_credit_rows_not_failed_task_recovery(self):
        bundle = adapt_opd_metrics({"rlsd/opsd_rescue_frac": 0.25, "rlsd/opsd_rescue_ceiling": 0.5},
            scope={"run_id": "r"}, source_family="uniopsd", training_step=12)
        metric = _diagnostics(bundle)[0]["data"]["reported_metrics"][0]
        self.assertEqual(metric["value"], 0.25)
        self.assertIn("ALL rows", metric["definition"])
        self.assertIn("not rescued tasks", metric["definition"])
        self.assertEqual(metric["support"]["kind"], "all_rows")
        self.assertNotIn("total", metric["support"])
        self.assertFalse(any(obj["kind"] == "opd_recovery" for obj in bundle["objects"]))
        with self.assertRaises(ValidationError):
            adapt_opd_metrics({"rlsd/opsd_rescue_frac": 1.1}, scope={"run_id": "r"}, source_family="uniopsd")

    def test_verl_zero_overlap_native_zero_is_preserved_without_alignment_claim(self):
        bundle = adapt_opd_metrics({"actor/distillation/overlap_ratio": 0.0,
            "actor/distillation/overlap_token_advantage": 0.0}, scope={"run_id": "r"},
            source_family="verl", record_prefix="overlap", demo=True)
        metrics = _diagnostics(bundle)[0]["data"]["reported_metrics"]
        self.assertEqual(metrics[1]["value"], 0)
        self.assertFalse(metrics[1]["support"]["available"])
        self.assertEqual(metrics[1]["support"]["count"], 0)
        with StudioRecorder(self.db) as studio:
            studio.methods.import_bundle(bundle)
            projected = studio.methods.opd_record("overlap-diagnostic-0")["diagnostic"]["reported_metrics"][1]
            self.assertEqual(projected["status"], "uninterpretable")
            self.assertIsNone(projected["value"])
            self.assertEqual(projected["reported_value"], 0)

    def test_same_named_metrics_keep_source_specific_definitions_and_unknown_support(self):
        for family in ("verl", "uniopsd"):
            metric = _diagnostics(adapt_opd_metrics({"new/entropy_gap": 0.1}, scope={"run_id": "r"},
                source_family=family))[0]["data"]["reported_metrics"][0]
            self.assertIn("not supplied", metric["definition"])
            self.assertNotIn("count", metric["support"])
            self.assertNotIn("normalization", metric["support"])

    def test_import_is_atomic_when_a_later_native_row_has_misaligned_tokens(self):
        row = self._agent(); row["gaps"].pop()
        path = self.root / "agentopsd.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in (self._agent(), row)) + "\n")
        with StudioRecorder(self.db) as studio:
            with self.assertRaises(ValidationError):
                studio.import_agentopsd_debug(path, scope={"run_id": "r"}, demo=True)
            self.assertEqual(studio.methods.objects(), [])
            self.assertEqual(studio.methods.conn.execute("SELECT count(*) FROM imports").fetchone()[0], 0)


class OpdExampleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        spec = importlib.util.spec_from_file_location("opd_tracking_example", ROOT / "examples/opd_tracking_example.py")
        cls.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.example)
        cls.result = cls.example.build_example(cls.root / "demo")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_example_refuses_existing_destination_and_all_data_is_explicit_demo(self):
        self.assertTrue(self.result["synthetic_only"])
        with self.assertRaises(FileExistsError):
            self.example.build_example(self.root / "demo")
        _, payload, blobs = read_package(self.result["packages"][0])
        self.assertTrue(all(obj["demo"] for obj in payload["methods"]))
        self.assertTrue(all(ev["demo"] for ev in payload["evaluations"]))
        self.assertTrue(payload["method_sources"])
        for entry in payload["method_sources"]:
            self.assertIn(entry["path"], blobs)

    def test_fully_bound_continuation_and_independent_endpoint_transfer_stay_separate(self):
        with StudioRecorder(self.root / "demo") as studio:
            value = studio.methods.opd_recovery("opd-v095-recovery-full")
            self.assertEqual(value["status"], "measured")
            arms = value["cases"][0]["arms"]
            self.assertEqual(arms["student_before"]["value"], 0)
            self.assertEqual(arms["teacher"]["value"], 1)
            self.assertEqual(arms["student_after"]["value"], 1)
            self.assertEqual(value["full_task"]["status"], "measured")
            self.assertEqual(value["full_task"]["rescued"], 2)
            self.assertEqual(value["full_task"]["harmed"], 1)
            self.assertFalse(value["claims"]["causal_training_attribution"])
            self.assertFalse(value["claims"]["final_generalization"])

    def test_rescore_and_missing_runs_do_not_become_zero_or_success(self):
        with StudioRecorder(self.root / "demo") as studio:
            missing = studio.methods.opd_recovery("opd-v095-recovery-missing")
            self.assertEqual(missing["status"], "unmeasured")
            for arm in missing["cases"][0]["arms"].values():
                self.assertIsNone(arm["value"])
            mismatch = studio.methods.opd_recovery("opd-v095-recovery-mismatch")
            self.assertIsNone(mismatch["cases"][0]["arms"]["student_after"]["value"])
            self.assertIsNone(mismatch["cases"][0]["student_delta"]["value"])

    def test_group_zero_advantage_and_gating_stay_separate_from_completed_update(self):
        with StudioRecorder(self.root / "demo") as studio:
            for name, expected in (("all-wrong", "all_wrong"), ("mixed", "mixed"), ("all-correct", "all_correct")):
                group = studio.methods.opd_record("opd-v095-group-" + name)["diagnostic"]["group"]
                self.assertEqual(group["classification"], expected)
            active = studio.methods.opd_record("opd-v095-supervision-active")["training"]
            pending = studio.methods.opd_record("opd-v095-supervision-pending-update")["training"]
            gated = studio.methods.opd_record("opd-v095-supervision-gated")["training"]
            self.assertTrue(active["update_completed"])
            self.assertTrue(pending["sampled"])
            self.assertFalse(pending["update_completed"])
            self.assertFalse(gated["sampled"])
            self.assertFalse(gated["update_completed"])

    def test_runtime_follows_exact_evaluation_trajectory_original_step_and_token_paging(self):
        with StudioRecorder(self.root / "demo") as studio:
            before = studio.methods.opd_runtime("opd-v095-experiment", "opd-v095-run", evaluation_id="opd-v095-eval-before",
                trajectory_id="opd-v095-eval-before-fixture-q1", step=2)
            self.assertEqual([item["id"] for item in before["items"]], ["opd-v095-eval-only-diagnostic"])
            after = studio.methods.opd_runtime("opd-v095-experiment", "opd-v095-run", evaluation_id="opd-v095-eval-after")
            self.assertEqual(after["items"], [])
            self.assertTrue(before["teacher_condition"]["autonomous"])
            detail = studio.methods.opd_record("opd-v095-token-diagnostic")
            self.assertIn("/tokens", detail["omitted_fields"])
            page = studio.methods.opd_tokens("opd-v095-token-diagnostic", offset=50, limit=50)
            self.assertEqual(page["total"], 63)
            self.assertEqual(len(page["items"]), 13)
            self.assertEqual(page["items"][0]["index"], 50)
            self.assertFalse(page["has_more"])

    def test_exact_signal_relation_has_explicit_shared_support_and_no_task_repair_claim(self):
        with StudioRecorder(self.root / "demo") as studio:
            relation = studio.methods.opd_record("opd-v095-signal-relation")["diagnostic"]["relation"]
            self.assertEqual(relation["status"], "measured")
            self.assertEqual(relation["shared_support_count"], 4)
            self.assertEqual(relation["shared_nonzero"], 3)
            self.assertEqual(relation["shared_zero"], 1)
            self.assertEqual(relation["sign_conflict"]["numerator"], 1)
            self.assertEqual(relation["sign_conflict"]["denominator"], 3)
            self.assertAlmostEqual(relation["pearson"]["value"], 0.30151134457776363)
            self.assertFalse(relation["task_repair_claim"])
            native = studio.methods.opd_record("opd-v095-native-agentopsd-diagnostic-0")["diagnostic"]["relation"]
            self.assertEqual(native["status"], "unmeasured")

    def test_hub_collector_keeps_native_bytes_typed_references_and_recovery_evidence(self):
        origin = source(self.root / "demo")["workspace_id"]
        with Hub(self.root / "center") as hub:
            hub.ingest(self.result["packages"][0], allow_definitions=True)
            method = lambda value: gid(origin, "method", value)
            record = hub.methods.object(method("opd-v095-eval-only-diagnostic"))["data"]
            self.assertEqual(record["evaluation_id"], gid(origin, "evaluation", "opd-v095-eval-before"))
            self.assertNotEqual(record["trajectory_id"], "opd-v095-eval-before-fixture-q1")
            native = hub.methods.object(method("opd-v095-native-agentopsd-diagnostic-0"))["data"]
            self.assertEqual(native["failure_id"], method("opd-v095-failure-rescore"))
            self.assertEqual(native["external_traj_uid"], "external-training-trajectory")
            recovered = hub.methods.opd_recovery(method("opd-v095-recovery-full"))
            self.assertEqual(recovered["status"], "measured")
            self.assertEqual(recovered["full_task"]["rescued"], 2)
            _, payload, blobs = read_package(self.result["packages"][0])
            archived = [row[0] for row in hub.methods.conn.execute("SELECT content FROM imports")]
            for entry in payload["method_sources"]:
                self.assertIn(blobs[entry["path"]], archived)


if __name__ == "__main__":
    unittest.main()
