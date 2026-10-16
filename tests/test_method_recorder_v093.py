"""Method logging/transport checks, using temporary synthetic workspaces only."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from searchagent_lab.core import NotFoundError, ValidationError
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.methods.sdk import MethodRecorder
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub, gid
from searchagent_lab.team.package import read_package, source


ROOT = Path(__file__).resolve().parents[1]


def _registered(path):
    with StudioRecorder(path) as rec:
        rec.store.register_project({"id": "p", "name": "Synthetic project", "demo": True})
        rec.store.register_experiment({"id": "e", "project_id": "p", "name": "Synthetic experiment", "demo": True})
        rec.register_experiment_profile(experiment_id="e", track="main")
        rec.store.register_run({"id": "r", "experiment_id": "e", "demo": True, "name": "Synthetic run", "run_type": "training"})


class MethodJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.journal = self.root / "events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def logger(self, **kwargs):
        return MethodEvidenceRecorder(journal_path=self.journal, scope={"run_id": "r"}, demo=True, **kwargs)

    def test_native_jsonl_roundtrip_retains_raw_text_and_reference_hashes(self):
        raw = {"question": "逐字保留：<answer>北京</answer>\n\\n", "steps": [{"input": "literal r/id", "output": "<script>text</script>"}]}
        with self.logger() as log:
            event = log.trace("raw", raw, usage="unknown")
            log.record("derived", "trace", {"original": raw}, derived_from=[event["id"]],
                       evidence_refs=[log.evidence_ref(event, "/steps/0/output")])
            exported = log.export_bundle(self.root / "bundle.json")
        _registered(self.root / "db")
        with StudioRecorder(self.root / "db") as rec:
            imported = rec.import_method_evidence(self.journal)
            self.assertEqual(imported["count"], 2)
            stored = rec.methods.object("raw")
            self.assertEqual(stored["data"], raw)
            self.assertEqual(stored["content_hash"], MethodEvidenceRecorder.content_sha256(raw))
            self.assertEqual(rec.methods.resolve_ref(exported["objects"][1]["evidence_refs"][0])["value"], "<script>text</script>")
            self.assertEqual(rec.store.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0], 0)

    def test_idempotent_retry_does_not_append_duplicate_line(self):
        with self.logger() as log:
            log.trace("r-event", {"answer": None})
            first = self.journal.read_bytes()
            log.trace("r-event", {"answer": None})
            self.assertEqual(self.journal.read_bytes(), first)
        with self.logger() as reopened:
            reopened.trace("r-event", {"answer": None})
            self.assertEqual(self.journal.read_bytes(), first)
            self.assertEqual(len(reopened.export_bundle()["objects"]), 1)

    def test_conflicting_immutable_id_keeps_original_bytes(self):
        with self.logger() as log:
            log.trace("r-event", {"answer": "original"})
            before = self.journal.read_bytes()
            with self.assertRaises(ValidationError):
                log.trace("r-event", {"answer": "changed"})
            self.assertEqual(self.journal.read_bytes(), before)

    def test_input_and_returned_objects_cannot_mutate_recorded_contents(self):
        value = {"nested": {"text": "original"}}
        with self.logger() as log:
            returned = log.trace("r-event", value)
            value["nested"]["text"] = "caller mutation"
            returned["data"]["nested"]["text"] = "return mutation"
            self.assertEqual(log.export_bundle()["objects"][0]["data"]["nested"]["text"], "original")

    def test_bad_batch_has_no_partial_append(self):
        with self.logger() as log:
            log.trace("old", {})
            before = self.journal.read_bytes()
            with self.assertRaises(ValidationError):
                log.record_batch([{"id": "good", "kind": "trace", "data": {}},
                                  {"id": "bad", "kind": "training_use", "data": {"status": "sampled", "artifact_ids": ["good"]}}])
            self.assertEqual(self.journal.read_bytes(), before)

    def test_offline_reference_resolution_is_checked_atomically_on_import(self):
        with self.logger() as log:
            log.trace("good", {})
            log.record("missing-source", "trace", {"original": "retained"}, derived_from=["absent"])
        _registered(self.root / "db")
        with StudioRecorder(self.root / "db") as rec:
            with self.assertRaises((ValidationError, NotFoundError)):
                rec.import_method_evidence(self.journal)
            self.assertEqual(rec.methods.conn.execute("SELECT count(*) FROM objects").fetchone()[0], 0)

    def test_incomplete_journal_is_not_silently_repaired(self):
        self.journal.write_bytes(b'{"method_schema":"method-evidence-v1"')
        before = self.journal.read_bytes()
        with self.assertRaises(ValidationError):
            self.logger()
        self.assertEqual(self.journal.read_bytes(), before)

    def test_nan_and_scope_mutation_are_rejected_before_write(self):
        with self.logger() as log:
            with self.assertRaises(ValidationError):
                log.trace("nan-event", {"value": float("nan")})
            with self.assertRaises(ValidationError):
                log.record_batch([{"id": "wrong-scope", "kind": "trace", "scope": {"run_id": "different"}, "data": {}}])
            self.assertFalse(self.journal.exists())

    def test_one_journal_has_one_writer(self):
        with self.logger() as first:
            first.trace("first", {})
            with self.assertRaises(ValidationError):
                self.logger()
            first.trace("second", {})
        self.assertEqual(len(self.journal.read_text().splitlines()), 2)

    def test_export_refuses_to_overwrite_existing_file(self):
        target = self.root / "bundle.json"
        target.write_text("preserve me", encoding="utf-8")
        with self.logger() as log:
            log.trace("r-event", {})
            with self.assertRaises(FileExistsError):
                log.export_bundle(target)
        self.assertEqual(target.read_text(), "preserve me")

    def test_final_input_is_hashed_after_assembly_and_unknown_is_preserved(self):
        final = [{"role": "system", "content": "actual skill fragment"}]
        with self.logger() as log:
            event = log.skill_retrieval("injected", {"bank_id": "future-bank", "injections_complete": True,
                "injections": [{"member": "skill", "rendered_text": "actual skill fragment"}]}, final_input=final)
            unknown = log.skill_retrieval("unlogged", {"bank_id": "future-bank", "injections_complete": False})
            self.assertEqual(event["data"]["final_input_sha256"], log.content_sha256(final))
            self.assertNotIn("injections", unknown["data"])
            self.assertNotIn("final_input", unknown["data"])
            with self.assertRaises(ValidationError):
                log.skill_retrieval("bad-hash", {"bank_id": "future-bank", "final_input_sha256": "0" * 64}, final_input=final)

    def test_zero_advantage_reweight_cannot_be_called_active_signal(self):
        with self.logger() as log:
            with self.assertRaises(ValidationError):
                log.opd_supervision("false-active", {"failure_id": "failure", "teacher_ids": ["teacher"],
                    "objective": "advantage_reweight", "status": "accepted", "original_advantage": 0.0,
                    "effective_advantage": 0.0, "loss_mask_tokens": 4, "loss_weight": 1.0, "loss_active": True})

    def test_live_sink_registers_with_existing_repository_and_checks_dependencies(self):
        path = self.root / "db"
        _registered(path)
        with MethodEvidenceRecorder(data_dir=path, scope={"run_id": "r"}, demo=True) as log:
            event = log.trace("live", {"text": "literal"})
            with self.assertRaises((ValidationError, NotFoundError)):
                log.record("bad-link", "trace", {}, derived_from=["absent"])
            with MethodRecorder(path) as rec:
                self.assertEqual(rec.repo.object(event["id"])["scope"], {"project_id": "p", "experiment_id": "e", "run_id": "r"})
                self.assertEqual(rec.repo.catalog({"run_id": "r"})["total"], 1)


class MethodExampleTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        spec = importlib.util.spec_from_file_location("method_evidence_example", ROOT / "examples/method_evidence_example.py")
        cls.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.example)
        cls.result = cls.example.build_example(cls.root / "demo")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_example_is_fresh_only_and_has_all_three_conditional_profiles(self):
        self.assertTrue(self.result["synthetic_only"])
        self.assertEqual(set(self.result["events"]), {"skill", "self_play", "opd"})
        with self.assertRaises(FileExistsError):
            self.example.build_example(self.root / "demo")
        with StudioRecorder(self.root / "demo") as rec:
            profiles = rec.methods.objects(kind="method_profile")
            self.assertEqual({tuple(p["data"]["families"]) for p in profiles}, {("skill",), ("self_play",), ("opd",)})

    def test_existing_collector_carries_new_evidence_and_original_journal_bytes(self):
        for path in self.result["packages"]:
            _, payload, blobs = read_package(path)
            self.assertTrue(payload["methods"])
            self.assertTrue(all(event["demo"] is True for event in payload["methods"]))
            self.assertTrue(payload["batches"])
            self.assertTrue(any(entry["name"].endswith("-method-events.jsonl") for entry in payload["method_sources"]))
            for entry in payload["method_sources"]:
                self.assertIn(entry["path"], blobs)

    def test_new_typed_links_survive_center_namespace_normalization(self):
        origin = source(self.root / "demo")["workspace_id"]
        with Hub(self.root / "center") as hub:
            for path in self.result["packages"]:
                hub.ingest(path, allow_definitions=True)
            method_id = lambda local_id: gid(origin, "method", local_id)
            teacher = hub.methods.object(method_id("opd-teacher-2"))
            self.assertEqual(teacher["data"]["failure_id"], method_id("opd-failure-repair"))
            supervision = hub.methods.object(method_id("opd-supervision-active"))
            self.assertEqual(supervision["data"]["teacher_ids"], [method_id("opd-teacher-1"), method_id("opd-teacher-2")])
            question = hub.methods.object(method_id("sp-q-duplicate"))
            self.assertEqual(question["data"]["first_generated_cycle_id"], method_id("sp-cycle-1"))
            self.assertEqual(question["data"]["duplicate_of"], method_id("sp-q-valid"))
            retrieved = hub.methods.object(method_id("sk-retrieval-used"))
            self.assertEqual(retrieved["data"]["final_input_sha256"], MethodEvidenceRecorder.content_sha256(retrieved["data"]["final_input"]))
            self.assertTrue(hub.methods.resolve_ref(retrieved["data"]["trace_ref"])["verified"])
            self.assertIn("没有调用模型", teacher["data"]["verification"]["notice"])

    def test_method_comparison_requires_method_axis_not_checkpoint_axis(self):
        self.assertTrue(self.result["method_comparison"]["compatible"])
        self.assertEqual(self.result["method_comparison"]["axis"], "method_state")
        with StudioRecorder(self.root / "demo") as rec:
            self.assertFalse(rec.repo.evaluation_pair("sk-eval-old-bank", "sk-eval-new-bank", axis="checkpoint")["compatible"])

    def test_sdk_module_import_has_no_training_dependency_or_workspace_side_effect(self):
        result = subprocess.run([sys.executable, "-c",
            "import sys; from searchagent_lab.methods.recorder import MethodEvidenceRecorder; "
            "assert not any(n in sys.modules for n in ('torch', 'verl', 'ray', 'vllm')); print('ok')"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
