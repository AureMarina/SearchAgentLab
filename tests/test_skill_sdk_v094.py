"""Skill SDK/native report integration checks in fresh synthetic workspaces."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from searchagent_lab.core import ValidationError
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.methods.skill_adapters import adapt_slim_lifecycle, adapt_skill_creator_results
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub, gid
from searchagent_lab.team.package import read_package, source


ROOT = Path(__file__).resolve().parents[1]


def _register(path):
    with StudioRecorder(path) as studio:
        studio.store.register_project({"id": "p", "name": "Synthetic", "demo": True})
        studio.store.register_experiment({"id": "e", "project_id": "p", "name": "Synthetic", "demo": True})
        studio.register_experiment_profile(experiment_id="e", track="main")
        studio.store.register_run({"id": "r", "experiment_id": "e", "name": "Synthetic", "demo": True, "run_type": "training"})
        studio.store.register_checkpoint({"id": "cp", "run_id": "r", "step": 1, "demo": True})


class SkillSdkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "db"
        _register(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def logger(self, journal=False):
        sink = {"journal_path": self.root / "events.jsonl"} if journal else {"data_dir": self.db}
        return MethodEvidenceRecorder(scope={"run_id": "r"}, demo=True, **sink)

    def asset(self, log, asset_id="a", version="v1"):
        return log.skill_asset(asset_id, {"skill_id": "verify-source", "version": version,
            "package": {"files": {"SKILL.md": "核对来源", "scripts/check.py": "print('fixture')"}}})

    def test_package_hash_is_computed_from_complete_frozen_content(self):
        data = {"skill_id": "verify-source", "version": "v1",
                "package": {"files": {"SKILL.md": "核对来源", "resources/zero.bin":
                    {"encoding": "base64", "content": "AA==", "sha256": hashlib.sha256(b"\x00").hexdigest()}}}}
        with self.logger() as log:
            event = log.skill_asset("asset", data)
            self.assertNotIn("package_hash", data)
            self.assertEqual(event["data"]["package_hash"], log.content_sha256(data["package"]))
            changed = copy.deepcopy(data)
            changed["package"]["files"]["SKILL.md"] = "different"
            changed["package_hash"] = event["data"]["package_hash"]
            with self.assertRaisesRegex(ValidationError, "package hash"):
                log.skill_asset("bad", changed)
        with StudioRecorder(self.db) as studio:
            self.assertEqual(studio.methods.object("asset")["data"]["package"]["files"]["SKILL.md"], "核对来源")

    def test_inline_instance_journal_roundtrip_requires_no_bank(self):
        with self.logger(journal=True) as log:
            event = log.skill_instance("inline", {"inline_skill": {"text": "先生成候选再验证。"},
                "selection": {"source": "model", "step": 1},
                "loading": {"rendered_text": "先生成候选再验证。", "final_input": [{"role": "assistant", "content": "先生成候选再验证。"}]}})
            self.assertEqual(event["data"]["inline_skill"]["content_hash"], log.content_sha256("先生成候选再验证。"))
            log.training_use("use", {"artifact_ids": ["inline"], "status": "sampled", "batch_id": "batch",
                "credit_assignment": {"instance_id": "inline", "skill_decision": {"reward": 0.5, "advantage": -0.1,
                    "mask": [1, 1], "source": "semantic return"}, "action_decision": {"reward": 0, "advantage": 0,
                    "mask": [0, 0], "source": "task outcome"}}})
        with StudioRecorder(self.db) as studio:
            studio.import_method_evidence(self.root / "events.jsonl")
            self.assertEqual(studio.methods.objects(kind="skill_bank"), [])
            got = studio.methods.skill_instance("inline")
            self.assertEqual(got["frozen_content"]["content"], "先生成候选再验证。")
            credit = studio.methods.skill_lifecycle("e", "r")["credits"]["items"][0]
            self.assertTrue(credit["model_selection_credit"]["value"])
            self.assertEqual(credit["action_decision"]["advantage"], 0)
            self.assertFalse(credit["update_completed"])

    def test_inline_hash_conflict_is_rejected_before_append(self):
        with self.logger(journal=True) as log:
            with self.assertRaisesRegex(ValidationError, "Inline skill hash"):
                log.skill_instance("wrong", {"inline_skill": {"text": "x", "content_hash": "0" * 64}})
        self.assertFalse((self.root / "events.jsonl").exists())

    def test_helper_does_not_infer_execution_or_selection(self):
        with self.logger() as log:
            self.asset(log)
            log.skill_instance("i", {"asset_id": "a", "loading": {"rendered_text": "核对来源"}, "task_result": {"em": 1}})
        with StudioRecorder(self.db) as studio:
            item = studio.methods.skill_runtime("e", "r")["items"][0]
            self.assertEqual(item["selection_source"], "unknown")
            self.assertIsNone(item["observations"]["loaded"]["value"])
            self.assertIsNone(item["observations"]["execution"]["value"])
            self.assertIsNone(item["observations"]["gain"]["value"])

    def test_explicit_modes_preserve_legacy_profile_call(self):
        with self.logger(journal=True) as log:
            old = log.method_profile("legacy-profile", method_name="SkillRL", families=["skill"])
            new = log.method_profile("fixed", method_name="SkillCredit", families=["skill"], skill_mode="fixed_skills_rl")
            self.assertNotIn("skill_mode", old["data"])
            self.assertEqual(new["data"]["skill_mode"], "fixed_skills_rl")
            with self.assertRaisesRegex(ValidationError, "Conflicting skill_mode"):
                log.method_profile("conflict", method_name="x", families=["skill"], skill_mode="joint_evolution",
                                   configuration={"skill_mode": "fixed_policy_skill_iteration"})

    def _slim(self):
        audit = {"skill_id": "source-skill", "perf_with": 0.5, "perf_without": 0.5,
                 "utility": 0.0, "usage_count": 10, "decision": "retain", "status": "active"}
        return {"global_step": 10, "general_group": None, "audited_task_specific_skills": [audit],
                "audited_skills": [copy.deepcopy(audit)], "expanded_skills": ["new-unmapped-skill"],
                "freeform": "逐字原文 <script>literal</script>\\n"}

    def test_slim_native_aliases_are_not_double_counted_or_zero_filled(self):
        data = self._slim()
        bundle = adapt_slim_lifecycle(data, scope={"run_id": "r"}, subject_map={"source-skill": {"asset_id": "a"}}, demo=True)
        self.assertEqual(bundle["objects"][0]["data"], data)
        rows = [o for o in bundle["objects"] if o["kind"] == "skill_evolution"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"]["reported_utility"]["utility"], 0)
        self.assertNotIn("utility_ema", rows[0]["data"]["reported_utility"])
        self.assertNotIn("checkpoint_id", rows[0]["data"])
        self.assertEqual(rows[0]["data"]["comparison_status"], "reported_only")
        self.assertEqual(rows[0]["data"]["source_refs"][0]["pointer"], "/audited_task_specific_skills/0")

    def test_slim_missing_mapping_fails_instead_of_inventing_asset(self):
        with self.assertRaisesRegex(ValidationError, "Missing explicit frozen identity"):
            adapt_slim_lifecycle(self._slim(), scope={"run_id": "r"}, subject_map={}, demo=True)

    def test_slim_legacy_alias_and_general_group_keep_distinct_units(self):
        data = self._slim()
        data.pop("audited_task_specific_skills")
        data["general_group"] = {"skill_id": "general-group", "utility": 0.1, "decision": "retire"}
        bundle = adapt_slim_lifecycle(data, scope={"run_id": "r"},
            subject_map={"source-skill": {"asset_id": "a"}, "general-group": {"bank_id": "general-bank"}}, demo=True)
        rows = bundle["objects"][1:]
        self.assertEqual(rows[0]["data"]["audit_scope"], "general_group")
        self.assertEqual(rows[0]["data"]["bank_id"], "general-bank")
        self.assertNotIn("asset_id", rows[0]["data"])
        self.assertEqual(rows[1]["data"]["source_refs"][0]["pointer"], "/audited_skills/0")

    def test_slim_facade_preserves_original_file_bytes_and_reported_gain(self):
        with self.logger() as log:
            self.asset(log)
        raw = (json.dumps(self._slim(), ensure_ascii=False, indent=3) + "\n\n").encode("utf-8")
        path = self.root / "slim_lifecycle_step10.json"
        path.write_bytes(raw)
        with StudioRecorder(self.db) as studio:
            imported = studio.import_slim_lifecycle(path, scope={"run_id": "r"},
                subject_map={"source-skill": {"asset_id": "a"}}, checkpoint_id="cp", record_prefix="slim-native", demo=True)
            stored_raw = studio.methods.conn.execute("SELECT content FROM imports WHERE id=?", (imported["import_id"],)).fetchone()[0]
            self.assertEqual(stored_raw, raw)
            row = studio.methods.skill_lifecycle("e", "r")["events"]["items"][0]
            self.assertFalse(row["gain_verified"])
            self.assertEqual(row["checkpoint_id"], "cp")
            ref = studio.methods.object("slim-native-audit-0")["data"]["source_refs"][0]
            self.assertTrue(studio.methods.resolve_ref(ref)["verified"])
            self.assertEqual(studio.store.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0], 0)

    def test_slim_bad_native_shapes_do_not_produce_a_bundle(self):
        for value in ({"global_step": "10", "audited_skills": []},
                      {"global_step": 10, "audited_skills": {}},
                      {"global_step": 10, "audited_skills": ["s"]}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                adapt_slim_lifecycle(value, scope={"run_id": "r"}, subject_map={}, demo=True)

    def test_creator_preserves_reported_token_uncertainty_and_score_definition(self):
        benchmark = {"metadata": {"skill_name": "source-name"}, "runs": [{"eval_id": 1,
            "configuration": "with_skill", "run_number": 1, "result": {"pass_rate": 0.5, "tokens": 1200}}],
            "run_summary": {"with_skill": {"pass_rate": {"mean": 0.5}}, "delta": {"pass_rate": "+0.1"}}}
        grading = {"expectations": [], "summary": {"passed": 0, "total": 0}, "execution_metrics": {"output_chars": 1200}}
        bundle = adapt_skill_creator_results({"benchmark.json": benchmark, "grading.json": grading},
            scope={"run_id": "r"}, subject={"asset_id": "a"}, demo=True)
        data = bundle["objects"][-1]["data"]
        self.assertEqual(data["comparison_status"], "reported_only")
        self.assertIn("output_chars", data["reported_results"]["benchmark_token_source"])
        self.assertNotIn("usage", data)
        self.assertNotIn("scores", data)
        self.assertIn("not end-to-end task EM", data["reported_results"]["score_definition"])

    def test_creator_multiple_original_files_are_preserved_inside_source_archive(self):
        with self.logger() as log:
            self.asset(log)
        files = {"grading.json": b'{"summary":{"pass_rate":1},"expectations":[]}\n\n',
                 "timing.json": b'{ "total_tokens": 42, "total_duration_seconds": 1.25 }\n'}
        paths = []
        for name, raw in files.items():
            path = self.root / name
            path.write_bytes(raw)
            paths.append(path)
        with StudioRecorder(self.db) as studio:
            imported = studio.import_skill_creator_results(paths, scope={"run_id": "r"}, subject={"asset_id": "a"},
                record_prefix="creator", checkpoint_id="cp", feedback_role="frozen_test", demo=True)
            raw = studio.methods.conn.execute("SELECT content FROM imports WHERE id=?", (imported["import_id"],)).fetchone()[0]
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                self.assertEqual({name: archive.read(name) for name in archive.namelist()}, files)
            report = studio.methods.object("creator-report")
            self.assertEqual(report["usage"], "test")
            self.assertEqual(report["data"]["event"], "benchmark_report")
            self.assertNotIn("decision", report["data"])
            self.assertEqual(report["data"]["reported_results"]["timing"]["total_tokens"], 42)

    def test_creator_rejects_ambiguous_paths_and_wrong_nested_schema(self):
        with StudioRecorder(self.db) as studio:
            with self.assertRaises(ValidationError):
                studio.import_skill_creator_results([], scope={"run_id": "r"}, subject={"asset_id": "a"})
        with self.assertRaisesRegex(ValidationError, "configuration and nested result"):
            adapt_skill_creator_results({"benchmark.json": {"runs": [{"config": "with_skill", "pass_rate": 1}], "run_summary": {}}},
                scope={"run_id": "r"}, subject={"asset_id": "a"})


class SkillExampleTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        spec = importlib.util.spec_from_file_location("skill_tracking_example", ROOT / "examples/skill_tracking_example.py")
        cls.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.example)
        cls.result = cls.example.build_example(cls.root / "demo")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_example_covers_modes_and_never_modifies_an_existing_destination(self):
        self.assertEqual(set(self.result["modes"].values()), {"fixed_skills_rl", "fixed_policy_skill_iteration", "joint_evolution"})
        self.assertTrue(self.result["synthetic_only"])
        with self.assertRaises(FileExistsError):
            self.example.build_example(self.root / "demo")

    def test_example_matrix_and_ablation_are_explicit_and_partial_cells_remain_unknown(self):
        from searchagent_lab.methods.skill_comparison import skill_comparison
        with StudioRecorder(self.root / "demo") as studio:
            full = skill_comparison(studio.methods, "joint-matrix")
            self.assertEqual(len(full["matrix"]["cells"]), 6)
            self.assertTrue(all(c["status"] == "measured" for c in full["matrix"]["cells"]))
            partial = skill_comparison(studio.methods, "joint-matrix-partial")
            missing = [c for c in partial["matrix"]["cells"] if c["status"] == "unmeasured"]
            self.assertEqual(len(missing), 4)
            self.assertTrue(all(c["value"] is None for c in missing))
            ablation = skill_comparison(studio.methods, "joint-ablation")
            self.assertEqual(ablation["status"], "measured")
            self.assertEqual(ablation["utility"], 0.5)
            self.assertFalse(ablation["causal_instance_credit"])

    def test_example_separates_runtime_gain_and_model_selection_credit(self):
        with StudioRecorder(self.root / "demo") as studio:
            runtime = studio.methods.skill_runtime("joint-skill-experiment", "joint-skill-run",
                evaluation_id="joint-eval-current-current-bank")
            instance = runtime["items"][0]
            for level in ("visible", "loaded", "execution", "gain"):
                self.assertTrue(instance["observations"][level]["value"], level)
            credits = studio.methods.skill_lifecycle("fixed-skill-experiment", "fixed-skill-run")["credits"]["items"]
            routed = next(c for c in credits if c["instance_id"] == "fixed-retrieved-instance")
            self.assertFalse(routed["model_selection_credit"]["value"])
            self.assertEqual(routed["skill_decision"]["advantage"], 0.2)
            inline = studio.methods.skill_instance("inline-training-instance")
            self.assertEqual(inline["asset_ref"]["type"], "inline")
            self.assertNotIn("bank_id", studio.methods.object("inline-training-instance")["data"])

    def test_collector_exports_new_skill_kinds_and_exact_report_sources(self):
        seen = set()
        for path in self.result["packages"]:
            _, payload, blobs = read_package(path)
            seen.update(o["kind"] for o in payload["methods"])
            self.assertTrue(all(o["demo"] for o in payload["methods"]))
            self.assertTrue(payload["method_sources"])
            for entry in payload["method_sources"]:
                self.assertIn(entry["path"], blobs)
        self.assertTrue({"skill_asset", "skill_instance", "skill_evolution", "skill_comparison"} <= seen)

    def test_hub_namespace_normalization_keeps_skill_instances_credit_and_comparison_links(self):
        origin = source(self.root / "demo")["workspace_id"]
        with Hub(self.root / "center") as hub:
            for path in self.result["packages"]:
                hub.ingest(path, allow_definitions=True)
            method = lambda value: gid(origin, "method", value)
            instance = hub.methods.object(method("joint-evaluation-instance"))
            self.assertEqual(instance["data"]["asset_id"], method("joint-asset-v2"))
            self.assertEqual(instance["data"]["comparison_ids"], [method("joint-ablation")])
            credit = hub.methods.object(method("inline-use"))["data"]["credit_assignment"][0]
            self.assertEqual(credit["instance_id"], method("inline-training-instance"))
            self.assertEqual(credit["skill_decision"]["source"], "synthetic semantic-unit reward")
            matrix = hub.methods.object(method("joint-matrix"))["data"]
            self.assertNotEqual(matrix["initial_checkpoint_id"], "joint-cp-0")
            self.assertNotEqual(matrix["cells"][0]["evaluation_id"], "joint-eval-initial-off")


if __name__ == "__main__":
    unittest.main()
