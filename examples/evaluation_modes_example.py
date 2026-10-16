"""Synthetic, local SDK example: no model, network, GPU, or training call.

Run from the source directory:
    python examples/evaluation_modes_example.py --data-dir /tmp/my-new-eval-fixture

The destination must not exist. All generated evidence is marked demo=True.
Fixture hashes identify literal synthetic bytes, NOT actual trained weights.
Importing this module does not create a workspace; call build_example explicitly.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from searchagent_lab.studio.sdk import StudioRecorder


def fixture_hash(label: str) -> str:
    """Only an identity for this demo's synthetic bytes, never a weight claim."""
    return hashlib.sha256(("synthetic-fixture:" + label).encode()).hexdigest()


def parse_final(payload: dict) -> str:
    """A real local function exercised by check_harness; no model is involved."""
    match = re.fullmatch(r"<answer>(.*?)</answer>", payload["text"].strip(), re.S)
    if not match or not match.group(1).strip():
        raise ValueError("Missing nonempty answer tag")
    return match.group(1).strip()


def build_example(data_dir: str | Path) -> dict:
    """Write and verify the demo only in a caller-selected, new directory."""
    destination = Path(data_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Choose a NEW directory; refusing to modify: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    protocol = json.loads((SOURCE_ROOT / "examples/protocol_v2.synthetic.json").read_text())
    protocol.update(harness_version="fixture-h1", harness_fingerprint=fixture_hash("h1"),
                    panel_scope="Synthetic diagnostic fixture; isolation not checked; no model executed")
    samples = [
        {"id": "q1", "dataset": "synthetic", "question": "[Synthetic] Is this a fixture?", "answers": ["yes"]},
        {"id": "q2", "dataset": "synthetic", "question": "[Synthetic] Which color is the fixture?", "answers": ["blue"]},
    ]

    with StudioRecorder(destination) as rec:
        store = rec.store
        store.register_project({"id": "fixture-project", "name": "合成接入示例", "demo": True})
        store.register_experiment({"id": "fixture-experiment", "project_id": "fixture-project",
                                   "name": "评测解耦 · 无实际训练", "demo": True})
        rec.register_experiment_profile(experiment_id="fixture-experiment", track="main",
            kind="agent_iteration", binding_origin="prospective",
            question="验证日志接入与控变量门槛；不是研究结论", change="合成记录",
            constants="固定合成题单、协议、底座、方法状态")
        run_metadata = {"experiment_id": "fixture-experiment", "base_model": "synthetic-no-model",
                        "model_revision": "synthetic-base-v1", "seed": 42, "demo": True,
                        "status": "completed"}

        # RL evidence works before any panel or checkpoint evaluation exists.
        store.register_run({"id": "fixture-training", "name": "合成训练日志", "run_type": "training",
                            "algorithm": "fixture only; no optimizer executed", **run_metadata})
        store.log_metrics("fixture-training", 1,
                          {"train/reward_mean": 0.5, "actor/pg_loss": -0.02, "val/success": 0.75})
        rec.log_training_batch({"id": "fixture-batch", "run_id": "fixture-training", "step": 1,
            "demo": True, "behavior_policy": "synthetic-m0", "groups": [
                {"id": "mixed-group", "samples": [
                    {"id": "sample-positive", "reward": 1, "advantage": 0.5,
                     "token_ids": [11, 12, 13], "response_mask": [0, 1, 1],
                     "messages": [{"role": "user", "content": "[Synthetic training] Say yes."},
                                  {"role": "assistant", "content": "yes"}]},
                    {"id": "sample-negative", "reward": 0, "advantage": -0.5,
                     "steps": [{"index": 0, "kind": "final", "output": "no"}]},
                ]},
                {"id": "missing-fields", "samples": [
                    {"id": "sample-unrecorded", "reward": 0},
                ]},
            ]})
        early = rec.repo.training_summary("fixture-training")
        assert early["coverage"]["evaluation_count"] == 0
        assert early["batch_summary"]["sample_count"] == 3
        assert early["batch_summary"]["advantage"]["missing"] == 1
        assert next(x for x in early["metrics"]["series"] if x["key"] == "val/success")["scope"] == "logged_evaluation"

        store.register_panel({"id": "fixture-panel", "name": "两题合成诊断", "version": "fixture-v1",
                              "split": "probe", "demo": True, "samples": samples})

        def log_fixture_results(evaluation_id: str, correct_count: int) -> None:
            # These are constructed test records, not outputs of search_agent.
            for index, sample in enumerate(samples):
                answer = sample["answers"][0] if index < correct_count else "synthetic-wrong"
                store.log_trajectory(evaluation_id, {"dataset": sample["dataset"], "sample_id": sample["id"],
                    "demo": True, "scores": {"em": int(answer == sample["answers"][0])},
                    "runtime_status": "ok", "termination_reason": "final_answer",
                    "raw_final_output": f"<answer>{answer}</answer>", "parsed_answer": answer,
                    "steps": [{"index": 0, "kind": "final", "output": f"<answer>{answer}</answer>"}]})
            rec.finish_evaluation(evaluation_id)

        # Independent Agent evals have model snapshots with step=None, no fake training run.
        for suffix in ("a", "b"):
            selected_protocol = copy.deepcopy(protocol)
            evaluation = {"id": f"agent-{suffix}", "panel_id": "fixture-panel",
                          "purpose": "agent_eval", "protocol": selected_protocol, "demo": True}
            if suffix == "b":
                selected_protocol["harness_version"] = "fixture-h2"
                selected_protocol["harness_fingerprint"] = fixture_hash("h2")
                selected_protocol["parser"]["version"] = "fixture-parser-v2"
                evaluation["comparison"] = {"allowed_harness_changes": [
                    "/harness_version", "/harness_fingerprint", "/parser/version"]}
            rec.register_agent_evaluation(
                run={"id": f"agent-run-{suffix}", "name": f"合成 Agent {suffix}", **run_metadata},
                model_snapshot={"id": f"model-{suffix}", "weights_sha256": fixture_hash("m0"),
                                "demo": True, "artifact_uri": "synthetic://not-real-weights/m0"},
                evaluation=evaluation)
            log_fixture_results(f"agent-{suffix}", 1 if suffix == "a" else 2)
        independent = store.evaluation("agent-a")
        assert independent["checkpoint"]["kind"] == "model_snapshot"
        assert independent["checkpoint"]["step"] is None
        assert rec.repo.training_summary("agent-run-a")["classification"]["has_training_evidence"] is False

        # Fixed-protocol checkpoint evals belong to an ordinary training run.
        for step in (0, 1):
            store.register_checkpoint({"id": f"cp-{step}", "run_id": "fixture-training", "step": step,
                "weights_sha256": fixture_hash(f"m{step}"), "demo": True,
                "artifact_uri": f"synthetic://not-real-weights/m{step}"})
            rec.start_evaluation(id=f"checkpoint-eval-{step}", checkpoint_id=f"cp-{step}",
                panel_id="fixture-panel", purpose="checkpoint_eval", protocol=protocol, demo=True)
            log_fixture_results(f"checkpoint-eval-{step}", step + 1)

        # check_harness really invokes these pure handlers. Importing assertion labels
        # through log_harness_check alone would not execute a check.
        rec.register_harness_run(id="fixture-checks", name="本地 parser 合约检查", **run_metadata)
        checks = rec.check_harness(id="fixture-check-result", run_id="fixture-checks",
            harness_version="fixture-h1", harness_fingerprint=fixture_hash("h1"),
            handlers={"parse_final": parse_final}, fixtures=[
                {"id": "valid-answer", "handler": "parse_final", "input": {"text": "<answer> yes </answer>"}, "expected": "yes"},
                {"id": "missing-tag", "handler": "parse_final", "input": {"text": "yes"}, "expected_error": "ValueError"},
            ])
        assert checks["status"] == "passed" and checks["total"] == 2

        harness_pair = rec.repo.evaluation_pair("agent-a", "agent-b", axis="harness")
        checkpoint_pair = rec.repo.evaluation_pair("checkpoint-eval-0", "checkpoint-eval-1", axis="checkpoint")
        for pair in (harness_pair, checkpoint_pair):
            assert pair["compatible"], pair["reasons"]
            assert pair["full"] and pair["rl_attribution"] is False
        assert rec.repo.evaluation_pair("agent-a", "agent-b", axis="checkpoint")["compatible"] is False
        rollout = rec.repo.training_rollout("fixture-training", "fixture-batch", "mixed-group", "sample-positive")
        assert rollout["sample"]["advantage"] == 0.5 and rollout["document"] is not None
        assert rec.repo.evaluation_summary(evaluation_id="agent-a")["context"]["panel"]["generalization_eligible"] is False
        result = {"verified": True, "synthetic_only": True, "data_dir": str(destination),
            "independent_agent_has_no_training_step": True, "training_visible_without_panel": True,
            "harness_fixture_checks_passed": checks["passed"],
            "harness_comparison_compatible": harness_pair["compatible"],
            "checkpoint_comparison_compatible": checkpoint_pair["compatible"],
            "notice": "验证成功：仅合成日志与本地 parser fixture；没有模型、网络或训练调用，不是实际评测成绩。"}
        (destination / "example-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="必须显式指定尚不存在的新目录；不会覆盖现有工作区")
    args = parser.parse_args()
    try:
        result = build_example(args.data_dir)
    except FileExistsError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
