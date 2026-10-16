"""Entropy saved-source boundaries, provenance, adapters and transport checks."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from searchagent_lab.core import ValidationError
from searchagent_lab.methods.entropy_adapters import (
    SOURCES, adapt_entropy_metrics, adapt_entropy_rows, adapt_seal_scores,
    native_entropy_definition, verl_entropy_configuration,
)
from searchagent_lab.methods.entropy_tracking import definition_semantics
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub, gid
from searchagent_lab.team.package import read_package, source


ROOT = Path(__file__).resolve().parents[1]


def _records(bundle):
    return [obj for obj in bundle["objects"] if obj["kind"] in {"entropy_observation", "entropy_probe"}]


def _register(path):
    with StudioRecorder(path) as studio:
        studio.store.register_project({"id": "p", "name": "Synthetic", "demo": True})
        studio.store.register_experiment({"id": "e", "project_id": "p", "name": "Synthetic", "demo": True})
        studio.register_experiment_profile(experiment_id="e", track="main")
        studio.store.register_run({"id": "r", "experiment_id": "e", "name": "Synthetic", "demo": True, "run_type": "training"})
        studio.store.register_checkpoint({"id": "cp", "run_id": "r", "step": 1, "demo": True})
    with MethodEvidenceRecorder(data_dir=path, scope={"run_id": "r"}, demo=True) as log:
        log.entropy_definition("d", native_entropy_definition("seal"))


class EntropySdkNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "db"
        _register(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _seal(self):
        return {"input_ids": [1, 2, 3], "token_texts": ["查", "证", "答"],
            "obs_logprob": [-1, -2, -1], "obs_prob": [0.3, 0.1, 0.3], "entropy": [1.0, 2.0, 3.0]}

    def test_veRL_configuration_keeps_native_values_but_missing_defaults_unknown(self):
        config = {"actor_rollout_ref": {"actor": {"entropy_coeff": 0.0, "calculate_entropy": True},
            "rollout": {"temperature": 0.8, "top_p": 0.95}}}
        got = verl_entropy_configuration(config)
        self.assertEqual(got["native_config"], config)
        self.assertTrue(got["configuration_only"])
        self.assertEqual(got["entropy_coeff"], 0)
        self.assertEqual(got["sampling_temperature"], 0.8)
        self.assertNotIn("scoring_temperature", got)
        self.assertNotIn("loss_agg_mode", got)
        self.assertEqual(got["source_revision"], "unknown")
        with self.assertRaises(ValidationError):
            verl_entropy_configuration({"actor_rollout_ref": {"actor": {"calculate_entropy": "false"}}})

    def test_reviewed_source_hash_never_becomes_actual_producer_revision(self):
        bundle = adapt_entropy_metrics({"actor/entropy": 1.2}, scope={"run_id": "r"}, source_family="verl")
        definition = next(obj for obj in bundle["objects"] if obj["kind"] == "entropy_definition")
        self.assertEqual(len(definition["data"]["schema_reference_sha256"]), 64)
        self.assertEqual(definition["data"]["implementation"]["producer_revision"], "unknown")
        self.assertEqual(_records(bundle)[0]["data"]["source_version"], "unknown")
        self.assertFalse(definition_semantics(definition)["complete"])
        self.assertIn("scoring_temperature", definition_semantics(definition)["missing_fields"])
        explicit = _records(adapt_entropy_metrics({"actor/entropy": 1.2}, scope={"run_id": "r"},
            source_family="verl", producer_revision="actual-training-commit"))[0]
        self.assertEqual(explicit["data"]["source_version"], "actual-training-commit")

    def test_original_file_bytes_blank_lines_extra_fields_and_zero_survive_import(self):
        raw = b'\n{"step":7,"metrics":{"actor/entropy":0.0,"extra/private_key_name":"kept"}}\n\n'
        path = self.root / "entropy.jsonl"
        path.write_bytes(raw)
        with StudioRecorder(self.db) as studio:
            result = studio.import_entropy_metrics(path, scope={"run_id": "r"}, source_family="verl",
                source_format="jsonl", checkpoint_id="cp", record_prefix="native", demo=True)
            self.assertEqual(studio.methods.conn.execute("SELECT content FROM imports WHERE id=?",
                (result["import_id"],)).fetchone()[0], raw)
            records = [obj for obj in studio.methods.objects() if obj["kind"] == "entropy_observation"]
            data = records[0]["data"]
            self.assertEqual(data["value"], 0)
            self.assertEqual(data["training_step"], 7)
            self.assertEqual(data["native_source"]["line_number"], 2)
            self.assertEqual(data["native_source"]["source_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(data["native_source"]["row"]["metrics"]["extra/private_key_name"], "kept")
            self.assertNotIn("tokens", data)
            self.assertNotIn("prefix_hash", data)
            self.assertIsNone(result["coverage"]["source_complete"])

    def test_repeat_import_is_idempotent_and_new_id_needed_for_different_source(self):
        path = self.root / "entropy.json"
        path.write_text('{"actor/entropy":1.2}\n')
        with StudioRecorder(self.db) as studio:
            options = {"scope": {"run_id": "r"}, "source_family": "verl", "record_prefix": "native", "demo": True}
            studio.import_entropy_metrics(path, **options)
            count = len(studio.methods.objects())
            studio.import_entropy_metrics(path, **options)
            self.assertEqual(len(studio.methods.objects()), count)
            path.write_text('{"actor/entropy":1.3}\n')
            with self.assertRaises(ValidationError):
                studio.import_entropy_metrics(path, **options)

    def test_jsonl_scope_and_explicit_identity_mapping_do_not_use_raw_uid(self):
        row = {"step": 2, "metrics": {"actor/entropy": 0.4}, "traj_uid": "not-a-Lab-trajectory"}
        data = _records(adapt_entropy_metrics([row], scope={"run_id": "r"}, source_family="verl", source_format="rows",
            identity_map={0: {"checkpoint_id": "cp", "sample_id": "explicit-sample"}}))[0]["data"]
        self.assertEqual(data["checkpoint_id"], "cp")
        self.assertEqual(data["sample_id"], "explicit-sample")
        self.assertNotIn("trajectory_id", data)
        self.assertEqual(data["native_source"]["row"]["traj_uid"], "not-a-Lab-trajectory")
        self.assertIn("canonical_json", data["native_source"]["hash_basis"])
        with self.assertRaises(ValidationError):
            adapt_entropy_metrics([row], scope={"run_id": "r"}, source_family="verl", source_format="rows",
                checkpoint_id="cp", identity_map={0: {"checkpoint_id": "other"}})

    def test_ragen_signed_mi_and_sampling_namespaces_remain_distinct(self):
        bundle = adapt_entropy_metrics({"collapse_first_turn_sample/mi_estimate": -0.2,
            "collapse_trajectory_sample/mi_estimate": 0.3}, scope={"run_id": "r"}, source_family="ragen")
        records = _records(bundle)
        self.assertEqual([obj["data"]["value"] for obj in records], [-0.2, 0.3])
        self.assertTrue(all(obj["kind"] == "entropy_probe" for obj in records))
        self.assertNotEqual(records[0]["data"]["definition_id"], records[1]["data"]["definition_id"])
        definitions = [obj["data"] for obj in bundle["objects"] if obj["kind"] == "entropy_definition"]
        self.assertEqual(definitions[0]["quantity_kind"], "mi_proxy")
        self.assertIn("not strict sequence", definitions[0]["source_rule"])

    def test_ragen_retrieval_means_prompt_identification_and_fraction_has_range(self):
        bundle = adapt_entropy_metrics({"collapse_first_turn_sample/retrieval_accuracy": 0.5},
            scope={"run_id": "r"}, source_family="ragen")
        definition = next(obj["data"] for obj in bundle["objects"] if obj["kind"] == "entropy_definition")
        self.assertEqual(definition["estimator"], "prompt_identification_accuracy")
        self.assertIn("not Search", definition["source_rule"])
        for value in (-0.1, 1.1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                adapt_entropy_metrics({"collapse_first_turn_sample/retrieval_accuracy": value},
                    scope={"run_id": "r"}, source_family="ragen")

    def test_unknown_metric_names_are_not_silently_called_entropy(self):
        for data, family in (({"my/entropy": 1.2}, "verl"), ({"search/retrieval_accuracy": 1}, "ragen")):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                adapt_entropy_metrics(data, scope={"run_id": "r"}, source_family=family)

    def test_native_scalar_cannot_claim_a_fixed_prefix_from_a_context_label(self):
        with self.assertRaises(ValidationError):
            adapt_entropy_metrics({"actor/entropy": 1.2}, scope={"run_id": "r"}, source_family="verl",
                context="fixed_prefix_probe")

    def test_arpo_and_ssrl_definition_units_support_and_window_are_not_full_vocab(self):
        arpo = native_entropy_definition("arpo")
        ssrl = native_entropy_definition("ssrl")
        self.assertEqual(arpo["quantity_kind"], "proxy")
        self.assertEqual(arpo["support"]["kind"], "returned_candidates")
        self.assertEqual(arpo["window"]["max_positions"], 20)
        self.assertEqual(arpo["window"]["aggregation"], "sum_not_token_mean")
        self.assertEqual(arpo["unit"], "dimensionless")
        self.assertEqual(ssrl["unit"], "bits")
        self.assertEqual(ssrl["support"]["kind"], "sampled_tokens_only")
        self.assertIn("p_sample * log2", ssrl["formula"])
        self.assertNotEqual(definition_semantics(arpo)["compatibility_key"], definition_semantics(ssrl)["compatibility_key"])

    def test_seal_actual_return_arrays_are_kept_but_no_mask_means_no_scalar_mean(self):
        obj = _records(adapt_seal_scores(self._seal(), scope={"run_id": "r"}, definition_id="d",
            common={"context": "unknown", "checkpoint_id": "cp"}))[0]
        self.assertEqual(obj["data"]["tokens"]["entropy"], [1, 2, 3])
        self.assertEqual(obj["data"]["tokens"]["token_ids"], [1, 2, 3])
        self.assertNotIn("mask", obj["data"]["tokens"])
        self.assertIsNone(obj["data"]["value"])
        self.assertNotIn("prefix_hash", obj["data"])
        self.assertEqual(obj["data"]["native_source"]["row"], self._seal())

    def test_seal_masked_mean_excludes_observation_and_handles_zero_support(self):
        obj = _records(adapt_seal_scores(self._seal(), scope={"run_id": "r"}, definition_id="d",
            common={"context": "unknown"}, token_mask=[1, 0, 1]))[0]
        self.assertEqual(obj["data"]["value"], 2)
        self.assertEqual(obj["data"]["derived_value"]["selected_tokens"], 2)
        missing = _records(adapt_seal_scores(self._seal(), scope={"run_id": "r"}, definition_id="d",
            common={"context": "unknown"}, token_mask=[0, 0, 0]))[0]
        self.assertIsNone(missing["data"]["value"])
        self.assertIn("No supported", missing["data"]["missing_reason"])

    def test_seal_missing_selected_entropy_is_unknown_not_dropped_from_denominator(self):
        row = self._seal(); row["entropy"][1] = None
        obj = _records(adapt_seal_scores(row, scope={"run_id": "r"}, definition_id="d",
            common={"context": "unknown"}, token_mask=[1, 1, 1]))[0]
        self.assertIsNone(obj["data"]["value"])
        self.assertEqual(obj["data"]["tokens"]["entropy"], [1, None, 3])

    def test_seal_original_positions_and_segment_types_require_explicit_aligned_inputs(self):
        obj = _records(adapt_seal_scores(self._seal(), scope={"run_id": "r"}, definition_id="d",
            common={"context": "unknown"}, token_mask=[1, 0, 1], token_positions=[100, 101, 102],
            segment_types=["query", "observation", "answer"]))[0]
        self.assertEqual(obj["data"]["tokens"]["positions"], [100, 101, 102])
        self.assertEqual(obj["data"]["tokens"]["segment_types"], ["query", "observation", "answer"])
        for options in ({"token_positions": [0, 0, 1]}, {"segment_types": ["query"]},
                        {"common": {"context": "unknown", "tokens": {"positions": [1, 2, 3]}}}):
            options.setdefault("common", {"context": "unknown"})
            with self.subTest(options=options), self.assertRaises(ValidationError):
                adapt_seal_scores(self._seal(), scope={"run_id": "r"}, definition_id="d", **options)

    def test_seal_misaligned_arrays_invalid_probabilities_and_masks_are_errors(self):
        bad = []
        row = self._seal(); row["token_texts"].pop(); bad.append(row)
        row = self._seal(); row["obs_prob"][0] = 1.2; bad.append(row)
        row = self._seal(); row["obs_logprob"][0] = 0.1; bad.append(row)
        for row in bad:
            with self.subTest(row=row), self.assertRaises(ValidationError):
                adapt_seal_scores(row, scope={"run_id": "r"}, definition_id="d", common={"context": "unknown"})
        with self.assertRaises(ValidationError):
            adapt_seal_scores(self._seal(), scope={"run_id": "r"}, definition_id="d", common={"context": "unknown"}, token_mask=[1])

    def test_normalized_rows_are_explicit_source_hook_and_keep_missing_evidence(self):
        row = {"context": "training_on_policy", "value": None, "missing_reason": "no query support",
            "reported_native_value": 0, "measurement_support": {"available": False, "count": 0},
            "checkpoint_id": "cp", "tokens": {"tokens": [], "entropy": []}}
        bundle = adapt_entropy_rows([row], scope={"run_id": "r"}, definition_id="d", source_format="rows", source_family="ssrl-explicit-hook")
        self.assertEqual(_records(bundle)[0]["data"]["reported_native_value"], 0)
        self.assertIsNone(_records(bundle)[0]["data"]["value"])
        self.assertEqual(bundle["objects"][0]["data"]["source_family"], "ssrl-explicit-hook")

    def test_normalized_row_conflicting_definitions_context_or_identities_are_rejected(self):
        base = {"context": "unknown", "value": 1}
        cases = (({**base, "definition_id": "other"}, {}), (base, {"context": "evaluation"}),
            ({**base, "checkpoint_id": "other"}, {"identity_map": {0: {"checkpoint_id": "cp"}}}))
        for row, options in cases:
            with self.subTest(row=row), self.assertRaises(ValidationError):
                adapt_entropy_rows([row], scope={"run_id": "r"}, definition_id="d", source_format="rows", **options)

    def test_invalid_source_json_nan_boolean_and_negative_entropy_are_rejected(self):
        for data in (b'{"actor/entropy":NaN}', b'{"actor/entropy":1,"actor/entropy":2}',
                     {"actor/entropy": True}, {"actor/entropy": -0.1}, {"actor/entropy": float("inf")}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                adapt_entropy_metrics(data, scope={"run_id": "r"}, source_family="verl")

    def test_complete_population_claim_requires_matching_row_count(self):
        rows = [{"context": "unknown", "value": 1}]
        with self.assertRaises(ValidationError):
            adapt_entropy_rows(rows, scope={"run_id": "r"}, definition_id="d", source_format="rows",
                source_complete=True, source_population_size=4)
        bundle = adapt_entropy_rows(rows, scope={"run_id": "r"}, definition_id="d", source_format="rows",
            source_complete=False, source_population_size=4)
        self.assertFalse(bundle["objects"][0]["data"]["coverage"]["source_complete"])
        self.assertEqual(bundle["objects"][0]["data"]["coverage"]["source_population_size"], 4)

    def test_later_bad_token_row_rejects_whole_studio_import(self):
        rows = [{"context": "unknown", "value": 1, "tokens": {"tokens": ["x"], "entropy": [1]}},
            {"context": "unknown", "value": 1, "tokens": {"tokens": ["x"], "entropy": [1, 2]}}]
        path = self.root / "rows.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with StudioRecorder(self.db) as studio:
            original = len(studio.methods.objects())
            original_imports = studio.methods.conn.execute("SELECT count(*) FROM imports").fetchone()[0]
            with self.assertRaises(ValidationError):
                studio.import_entropy_rows(path, scope={"run_id": "r"}, definition_id="d", demo=True)
            self.assertEqual(len(studio.methods.objects()), original)
            self.assertEqual(studio.methods.conn.execute("SELECT count(*) FROM imports").fetchone()[0], original_imports)

    def test_source_audit_hashes_match_packaged_adapter_references(self):
        audit = json.loads((ROOT / "docs/v096/ENTROPY_SOURCE_AUDIT.json").read_text())
        for key, source_entry in SOURCES.items():
            self.assertEqual(audit["sources"][key]["sha256"], source_entry["sha256"])
            self.assertEqual(audit["sources"][key]["status"], "read")
        self.assertEqual(audit["sources"]["verl_actor"]["status"], "unavailable")
        self.assertIn("Tied scores", audit["sources"]["polygraph_risk"]["findings"][0])

    def test_recorder_helpers_preserve_selection_signal_and_update_as_separate_events(self):
        journal = self.root / "events.jsonl"
        with MethodEvidenceRecorder(journal_path=journal, scope={"run_id": "r"}, demo=True) as log:
            log.entropy_configuration("profile", native_config={"actor_rollout_ref": {"actor": {"entropy_coeff": 0.1}}})
            log.entropy_definition("d", native_entropy_definition("verl"))
            log.entropy_observation("obs", {"definition_id": "d", "context": "training_on_policy", "value": 1.0})
            log.entropy_probe("probe", {"definition_id": "d", "probe_type": "input_dependence", "context": "unknown", "value": -0.1})
            log.entropy_intervention("choice", {"definition_id": "d", "observation_ids": ["obs"],
                "mode": "training", "intervention_type": "token_selection", "selection": {"selected": True}})
            bundle = log.export_bundle()
        kinds = [obj["kind"] for obj in bundle["objects"]]
        self.assertIn("entropy_intervention", kinds)
        self.assertNotIn("training_use", kinds)
        self.assertNotIn("training_update", kinds)


class EntropyExampleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        spec = importlib.util.spec_from_file_location("entropy_tracking_example", ROOT / "examples/entropy_tracking_example.py")
        cls.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.example)
        cls.result = cls.example.build_example(cls.root / "demo")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fixture_refuses_existing_destination_and_all_packages_are_marked_demo(self):
        self.assertTrue(self.result["synthetic_only"])
        with self.assertRaises(FileExistsError):
            self.example.build_example(self.root / "demo")
        self.assertEqual(len(self.result["packages"]), 3)
        for path in self.result["packages"]:
            _, payload, blobs = read_package(path)
            self.assertTrue(all(obj["demo"] for obj in payload["methods"]))
            self.assertTrue(all(ev["demo"] for ev in payload["evaluations"]))
            for entry in payload["method_sources"]:
                self.assertIn(entry["path"], blobs)

    def test_exact_runtime_binding_and_token_paging_survive_example(self):
        with StudioRecorder(self.root / "demo") as studio:
            before = studio.methods.entropy_runtime("entropy-v096-experiment", "entropy-v096-run", evaluation_id="entropy-v096-eval-before",
                trajectory_id="entropy-v096-eval-before-fixture-q1", step=2)
            self.assertEqual(before["items"], [])
            after = studio.methods.entropy_runtime("entropy-v096-experiment", "entropy-v096-run", evaluation_id="entropy-v096-eval-after",
                trajectory_id="entropy-v096-eval-after-fixture-q1", step=2)
            self.assertIn("entropy-v096-runtime-observation", [obj["id"] for obj in after["items"]])
            page = studio.methods.entropy_tokens("entropy-v096-fixed-before", offset=50, limit=50)
            self.assertEqual(page["total"], 63)
            self.assertEqual(len(page["items"]), 13)
            self.assertEqual(page["items"][0]["index"], 50)
            self.assertFalse(page["has_more"])

    def test_separate_training_runs_and_autonomous_bindings_are_retained(self):
        with StudioRecorder(self.root / "demo") as studio:
            self.assertEqual(len(set(self.result["runs"].values())), 3)
            workspace = studio.repo.workspace()
            experiment = next(entry for entry in workspace["experiments"] if entry["experiment"]["id"] == "entropy-v096-experiment")
            self.assertEqual(experiment["family"], "entropy")
            for eid in self.result["evaluations"].values():
                data = studio.methods.object("bind-" + eid)["data"]
                self.assertEqual(data["entropy_context"]["runtime_intervention"], "none")
                self.assertTrue(data["entropy_context"]["training_only_guidance_removed"])
                self.assertFalse(any(data["entropy_context"]["extra_resources"].values()))
            self.assertEqual(studio.store.evaluation("entropy-v096-eval-after")["protocol"]["decoding"]["temperature"], 0)
            self.assertEqual(studio.methods.object("entropy-v096-def-token")["data"]["scoring_temperature"], 1)

    def test_actual_effective_signal_mask_and_update_remain_distinct(self):
        with StudioRecorder(self.root / "demo") as studio:
            records = {key: studio.methods.entropy_record("entropy-v096-intervention-" + key)
                for key in ("active", "masked", "pending")}
            self.assertIs(records["active"]["diagnostic"]["signals"]["entropy"]["active"], True)
            self.assertIs(records["masked"]["diagnostic"]["signals"]["entropy"]["active"], False)
            self.assertIsNone(records["pending"]["diagnostic"]["signals"]["entropy"]["active"])
            self.assertTrue(records["active"]["training"]["update_completed"])
            self.assertTrue(records["masked"]["training"]["update_completed"])
            self.assertIsNone(records["pending"]["training"]["update_completed"])

    def test_budget_comparison_matches_training_controls_and_completed_outputs(self):
        with StudioRecorder(self.root / "demo") as studio:
            result = studio.methods.entropy_comparison("entropy-v096-compare-budget")
            self.assertEqual(result["status"], "measured")
            controls = [arm["training_controls"] for arm in result["arms"]]
            self.assertTrue(all(value == controls[0] for value in controls))
            self.assertEqual(controls[0]["initial_weights_sha256"], self.example.H(self.example.CP0))
            for arm in result["arms"]:
                if arm["id"] == "baseline":
                    continue
                self.assertTrue(arm["update_evidence"]["completed"])
                self.assertTrue(arm["update_evidence"]["sampled_use_ids"])
                for update_id in arm["update_evidence"]["completed_update_ids"]:
                    data = studio.methods.object(update_id)["data"]
                    self.assertEqual(data["output_checkpoint_id"], arm["checkpoint_id"])

    def test_uncertainty_common_support_ties_and_probabilities_remain_explicit(self):
        with StudioRecorder(self.root / "demo") as studio:
            full = studio.methods.entropy_comparison("entropy-v096-compare-estimators")
            self.assertTrue(full["comparison_qualified"])
            self.assertEqual(full["common_count"], 6)
            self.assertEqual(full["ood_status"], "not_measured")
            values = {item["id"]: item for item in full["estimators"]}
            self.assertAlmostEqual(values["token"]["auroc"]["value"], 0.75)
            self.assertAlmostEqual(values["new"]["aurc"]["value"], (1/5 + 2/6)/6)
            self.assertEqual(values["new"]["calibration"]["status"], "not_calibrated")
            missing = studio.methods.entropy_comparison("entropy-v096-compare-estimators-missing")
            self.assertEqual(missing["common_count"], 5)
            self.assertFalse(missing["comparison_qualified"])
            self.assertTrue(missing["measurement_budget"]["matched"])

    def test_run_package_roundtrip_preserves_typed_score_refs_and_source_bytes(self):
        origin = source(self.root / "demo")["workspace_id"]
        with Hub(self.root / "center") as hub:
            for path in self.result["packages"]:
                hub.ingest(path, allow_definitions=True)
            method = lambda value: gid(origin, "method", value)
            record = hub.methods.object(method("entropy-v096-score-new-0"))["data"]
            self.assertEqual(record["evaluation_id"], gid(origin, "evaluation", "entropy-v096-eval-after"))
            self.assertEqual(record["trajectory_id"], gid(origin, "trajectory", "entropy-v096-eval-after-fixture-q1"))
            _, payload, blobs = read_package(self.result["packages"][0])
            originals = [row[0] for row in hub.methods.conn.execute("SELECT content FROM imports")]
            for entry in payload["method_sources"]:
                self.assertIn(blobs[entry["path"]], originals)
            comparison = hub.methods.entropy_comparison(method("entropy-v096-compare-estimators"))
            self.assertEqual(comparison["status"], "measured")
            self.assertTrue(comparison["comparison_qualified"])
            data = hub.methods.object(method("entropy-v096-compare-estimators"))["data"]
            source_tids = ["entropy-v096-eval-after-" + sample["id"] for sample in self.example.SAMPLES]
            for estimator in data["estimators"]:
                self.assertEqual(estimator["source_sample_set_hash"], self.example.H(sorted(source_tids)))
                self.assertEqual(estimator["probe_protocol"]["sample_set_hash"],
                    self.example.H(sorted(data["trajectory_ids"])))

    def test_cross_run_comparison_rebinds_actual_center_cost_hashes_after_all_packages(self):
        origin = source(self.root / "demo")["workspace_id"]
        with Hub(self.root / "center-comparison") as hub:
            for path in self.result["packages"]:
                hub.ingest(path, allow_definitions=True)
            statuses = self.example.register_center_comparisons(hub, self.result["experiment_comparison_file"], origin)
            full = gid(origin, "method", "entropy-v096-compare-budget")
            mismatch = gid(origin, "method", "entropy-v096-compare-budget-mismatch")
            self.assertEqual(statuses[full], "measured")
            self.assertEqual(statuses[mismatch], "incomparable")
            self.assertEqual(self.example.register_center_comparisons(hub, self.result["experiment_comparison_file"], origin), statuses)
            data = hub.methods.object(full)["data"]
            for arm in data["arms"]:
                target = hub.methods.object(arm["cost_ref"]["object_id"])
                self.assertEqual(arm["cost_ref"]["sha256"], MethodEvidenceRecorder.content_sha256(target["data"]))
                self.assertEqual(target["data"]["checkpoint_id"], arm["checkpoint_id"])
                self.assertEqual(target["data"]["evaluation_id"], arm["evaluation_id"])
            raw = Path(self.result["experiment_comparison_file"]).read_bytes()
            self.assertIn(raw, [row[0] for row in hub.methods.conn.execute("SELECT content FROM imports")])


if __name__ == "__main__":
    unittest.main()
