"""Self-play SDK journal and collector integration; every fixture is synthetic."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from searchagent_lab.core import ValidationError
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.hub import Hub, gid
from searchagent_lab.team.package import read_package, source

ROOT = Path(__file__).resolve().parents[1]


class SelfplaySdkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="selfplay-sdk-v098-")
        cls.root = Path(cls.temp.name)
        spec = importlib.util.spec_from_file_location("selfplay_example_v098", ROOT / "examples/selfplay_tracking_example.py")
        cls.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.example)
        cls.result = cls.example.build_example(cls.root / "source")
        cls.origin = source(cls.root / "source")["workspace_id"]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_synthetic_example_refuses_existing_directory_without_changing_a_byte(self):
        path = self.root / "source" / "example-verification.json"
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.example.build_example(self.root / "source")
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.result["synthetic_only"])
        self.assertTrue(all(hasattr(MethodEvidenceRecorder, key) for key in (
            "selfplay_cycle", "selfplay_reference", "selfplay_pool_event", "selfplay_comparison")))

    def test_journal_reimport_is_idempotent_and_source_bytes_stay_exact(self):
        item = self.result["runs"]["fresh"]
        raw = Path(item["journal"]).read_bytes()
        with StudioRecorder(self.root / "source") as studio:
            count = len(studio.methods.objects())
            imported = studio.import_method_evidence(item["journal"])
            self.assertEqual(len(studio.methods.objects()), count)
            saved = studio.methods.conn.execute("SELECT content FROM imports WHERE id=?", (imported["import_id"],)).fetchone()[0]
            self.assertEqual(saved, raw)
            self.assertEqual(hashlib.sha256(saved).hexdigest(), hashlib.sha256(raw).hexdigest())

    def test_supply_signal_and_external_results_are_distinct(self):
        fresh = self.result["runs"]["fresh"]
        with StudioRecorder(self.root / "source") as studio:
            round1 = studio.methods.selfplay_training(self.example.EXPERIMENT, fresh["run_id"],
                cycle_id=fresh["cycle_ids"][1], evaluation_id=fresh["evaluation_ids"][2])
            self.assertEqual(round1["counts"]["generated_questions"], 7)
            self.assertEqual(round1["counts"]["new_verified_consumed_questions"], 2)
            self.assertEqual(round1["counts"]["validation_error"], 1)
            self.assertEqual(round1["task_signal"]["total"], 4)
            self.assertEqual(round1["task_signal"]["active"], 2)
            self.assertEqual(round1["opd_signal"]["active"], 1)
            self.assertEqual(round1["difficulty"]["groups"], {"all_failed":1, "partial_success":1, "all_succeeded":1, "incomplete":4})
            round2 = studio.methods.selfplay_training(self.example.EXPERIMENT, fresh["run_id"], cycle_id=fresh["cycle_ids"][2])
            self.assertIsNone(round2["task_signal"]["value"])
            self.assertEqual((round2["task_signal"]["active"],round2["task_signal"]["unknown"],round2["task_signal"]["total"]), (1,1,4))
            self.assertEqual(round2["counts"]["old_first_consumed_questions"], 1)
            self.assertEqual(round2["counts"]["reused_questions"], 1)
            self.assertEqual(round2["metrics"][2]["status"], "selection_required")
            self.assertEqual(round2["difficulty"]["groups"]["incomplete"], 1)

    def test_rejected_error_and_unconsumed_questions_have_readable_chain(self):
        with StudioRecorder(self.root / "source") as studio:
            for suffix, expected in (("rejected","failed"),("error","error"),("pending","unknown")):
                item = studio.methods.selfplay_question("selfplay-v098-fresh-q-" + suffix)
                self.assertEqual(item["assessment"]["validation_status"], expected)
                self.assertTrue(item["reviews"])
                self.assertEqual(item["question"]["data"]["proposed_answer"], "银湾")
            unused = studio.methods.selfplay_question("selfplay-v098-fresh-q-unused")
            self.assertTrue(unused["pool_events"])
            self.assertFalse(unused["updates"])

    def test_run_packages_preserve_native_sources_and_do_not_smuggle_cross_run_comparison(self):
        for package in self.result["packages"]:
            _, payload, blobs = read_package(package)
            ids = {obj["id"] for obj in payload["methods"]}
            self.assertFalse(ids.intersection(self.result["comparison_ids"]))
            self.assertTrue(all(obj["demo"] for obj in payload["methods"]))
            for src in payload["method_sources"]:
                self.assertEqual(hashlib.sha256(blobs[src["path"]]).hexdigest(), src["sha256"])
            self.assertTrue(any(obj["kind"] == "selfplay_reference" for obj in payload["methods"]))
            self.assertTrue(any(obj["kind"] == "selfplay_pool_event" for obj in payload["methods"]))

    def test_collector_roundtrip_namespaces_typed_links_preserves_identity_and_qualification(self):
        with Hub(self.root / "center") as hub:
            for package in self.result["packages"]:
                hub.ingest(package, allow_definitions=True)
            statuses = self.example.register_center_comparisons(hub, self.result["experiment_comparison_file"], self.origin)
            method = lambda oid: gid(self.origin, "method", oid)
            good = statuses[method("selfplay-v098-compare-supply")]
            self.assertTrue(good["comparison_qualified"], good["qualification_reasons"])
            bad = statuses[method("selfplay-v098-compare-budget-missing")]
            self.assertFalse(bad["comparison_qualified"])
            self.assertIsNone(bad["delta"]["value"])
            self.assertIsNotNone(bad["delta"]["raw_value"])
            account = hub.methods.object(method("selfplay-v098-fresh-total-cost"))["data"]
            self.assertEqual(account["run_id"], gid(self.origin, "run", "selfplay-v098-fresh-run"))
            self.assertEqual(account["cycle_ids"], [method("selfplay-v098-fresh-round-1"), method("selfplay-v098-fresh-round-2")])
            self.assertEqual(account["output_checkpoint_id"], gid(self.origin,"checkpoint","selfplay-v098-fresh-cp-2"))
            q = hub.methods.object(method("selfplay-v098-fresh-q-good"))["data"]
            self.assertEqual(q["stable_question_id"], "fixture-question-good")
            attempt = hub.methods.object(method("selfplay-v098-fresh-r1-sample-0-attempt"))["data"]
            self.assertEqual(attempt["solver_checkpoint_id"], gid(self.origin,"checkpoint","selfplay-v098-fresh-cp-0"))
            update = hub.methods.object(method("selfplay-v098-fresh-update-1"))["data"]
            self.assertEqual(update["batch_id"], gid(self.origin,"batch","selfplay-v098-fresh-batch-1"))
            center = hub.methods.selfplay_training(self.example.EXPERIMENT, gid(self.origin,"run","selfplay-v098-fresh-run"),
                cycle_id=method("selfplay-v098-fresh-round-1"), evaluation_id=gid(self.origin,"evaluation","selfplay-v098-fresh-external-2"))
            self.assertEqual(center["task_signal"]["value"], 0.5)
            self.assertEqual(center["difficulty"]["groups"]["partial_success"], 1)
            count = len(hub.methods.objects())
            self.example.register_center_comparisons(hub, self.result["experiment_comparison_file"], self.origin)
            self.assertEqual(len(hub.methods.objects()), count)
            raw = Path(self.result["experiment_comparison_file"]).read_bytes()
            self.assertTrue(any(row[0] == raw for row in hub.methods.conn.execute("SELECT content FROM imports")))

    def test_cross_run_selectors_do_not_widen_the_requested_run(self):
        with StudioRecorder(self.root / "source") as studio:
            with self.assertRaises(ValidationError):
                studio.methods.selfplay_training(self.example.EXPERIMENT,"selfplay-v098-fixed-run",cycle_id="selfplay-v098-fresh-round-1")
            with self.assertRaises(ValidationError):
                studio.methods.selfplay_training(self.example.EXPERIMENT,"selfplay-v098-fixed-run",reference_id="selfplay-v098-fresh-reference")
            with self.assertRaises(ValidationError):
                studio.methods.selfplay_runtime(self.example.EXPERIMENT,"selfplay-v098-fixed-run",evaluation_id="selfplay-v098-fresh-external-2")


if __name__ == "__main__":
    unittest.main()
