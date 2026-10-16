"""CPU-only Self-play evidence demo; every question, score and cost is synthetic.

    python examples/selfplay_tracking_example.py --data-dir /tmp/new-selfplay-demo

The destination must not exist. No proposer, solver, verifier, teacher or
optimizer is executed. The generated records exercise the saved-evidence UI
and collector without modifying any bundled or existing experiment.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.methods.schema import SCHEMA
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.package import export_run, init_source, read_package

PREFIX = "selfplay-v098"
EXPERIMENT = PREFIX + "-experiment"
NOTICE = "合成接入示例：所有题目、轨迹、得分、成本和更新均为 fixture，没有执行模型或训练。"
H = MethodEvidenceRecorder.content_sha256
SAMPLES = [
    {"id": "external-q1", "dataset": "FixtureExternal", "question": "[合成外部题] 蓝色车站在哪座城？", "answers": ["银湾"]},
    {"id": "external-q2", "dataset": "FixtureExternal", "question": "[合成外部题] 车站的颜色？", "answers": ["蓝色"]},
]


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _trace(question, answer, score):
    return {"question": question, "raw_final_output": "<answer>" + answer + "</answer>",
        "parsed_answer": answer, "scores": {"em": score}, "runtime_status": "ok",
        "termination_reason": "final_answer", "notice": NOTICE,
        "steps": [{"index": 0, "kind": "model", "output": "search 合成档案"},
            {"index": 1, "kind": "tool", "tool_call": {"name": "search", "arguments": {"query": "合成档案"}},
             "tool_response": {"text": "合成资料：林舟出生于银湾。"}, "model_visible": "合成资料：林舟出生于银湾。"},
            {"index": 2, "kind": "model", "output": "<answer>" + answer + "</answer>"}]}


def _budget(generation, validation, solving, training):
    return {"unit": "gpu_seconds", "accounting_protocol_sha256": H("synthetic-complete-accounting-v1"),
        "hardware_sha256": H("synthetic-hardware-no-gpu-run"),
        "components": {"generation": generation, "validation": validation, "solving": solving, "training": training},
        "total": generation + validation + solving + training, "complete": True, "notice": NOTICE}


def _build_run(studio, destination, artifacts, role, protocol):
    prefix = PREFIX + "-" + role
    run = prefix + "-run"
    checkpoints = {i: prefix + "-cp-" + str(i) for i in range(3)}
    cycles = {i: prefix + "-round-" + str(i) for i in range(3)}
    studio.store.register_run({"id": run, "experiment_id": EXPERIMENT, "run_type": "training",
        "name": ("持续新有效题" if role == "fresh" else "固定旧题池") + " · 合成", "demo": True,
        "base_model": "synthetic-solver", "model_revision": "synthetic-initial-v1", "seed": 42,
        "algorithm": "synthetic GRPO + optional OPD; no optimizer executed", "status": "completed"})
    for step, cp in checkpoints.items():
        studio.store.register_checkpoint({"id": cp, "run_id": run, "step": step, "demo": True,
            "artifact_uri": f"synthetic://no-weights/{role}/{step}",
            "weights_sha256": H("same-initial-solver" if step == 0 else f"synthetic-{role}-{step}")})
    journal = artifacts / (role + "-selfplay-events.jsonl")
    batch_records = []
    with MethodEvidenceRecorder(journal_path=journal, scope={"run_id": run}, usage="training", demo=True,
            provenance={"producer": "selfplay_tracking_example.py", "version": "fixture-v1", "notice": NOTICE}) as log:
        log.method_profile(prefix + "-profile", method_name="Self-play 供给、信号与外部结果", families=["self_play"],
            configuration={"algorithm": "GRPO", "pool_policy": "continual_fresh_supply" if role == "fresh" else "fixed_pool",
                "reference_solver": "synthetic-reference-solver-v1", "notice": NOTICE})
        source = log.record(prefix + "-source", "dataset_manifest", {"source": "synthetic source corpus", "usage": "training",
            "documents": [{"id": "doc-1", "text": "[合成] 导演林舟出生于银湾；山城的河流叫青河。"}], "notice": NOTICE})
        ref_id = prefix + "-reference"
        reference = {"solver_revision": H("synthetic-reference-solver-v1"), "solver_weights_sha256": H("synthetic-reference-weights"), "harness_sha256": H("synthetic-harness-v1"),
            "environment": {"retrieval_corpus_sha256": H("synthetic-probe-corpus"), "search_budget": 3, "max_context_tokens": 1024},
            "scoring": {"protocol_sha256": H("fixture-em-v1"), "scorer": {"id": "fixture-em", "version": "1"}},
            "retriever": {"index_sha256": H("synthetic-probe-corpus"), "version": "fixture-v1"},
            "retrieval": {"corpus_sha256": H("synthetic-probe-corpus"), "version": "fixture-v1"},
            "sampling": {"temperature": 0.7, "top_p": 1.0, "max_output_tokens": 256},
            "budget": {"max_tool_calls": 3, "max_output_tokens": 256}, "attempts_per_question": 3,
            "verifier": {"id": "fixture-em", "version": "1"}, "scorer": {"name": "fixture-em", "version": "1"},
            "scoring_protocol_sha256": H("fixture-em-v1"), "seed_policy": {"base_seed": 42, "rule": "base + attempt_index"},
            "frozen": True, "notice": NOTICE}
        log.selfplay_reference(ref_id, reference, usage="validation", title="固定参考 solver · K=3（合成）")
        for index, cycle in cycles.items():
            cost = _budget(0, 0, 0, 0) if index == 0 else (_budget(15, 5, 25, 5) if role == "fresh" else _budget(0, 2, 43, 5))
            log.selfplay_cycle(cycle, {"round_index": index, "phase": "initial_pool" if index == 0 else "training",
                "participants": [{"id": "proposer", "role": "proposer", "version": f"fixture-proposer-{index}"},
                    {"id": "solver", "role": "solver", "version": "fixture-solver-" + str(max(index - 1, 0))},
                    {"id": "reference", "role": "reference_solver", "version": reference["solver_revision"]}],
                "solver_checkpoint_id": checkpoints[max(index - 1, 0)], "reference_id": ref_id,
                "generation_input": {"seed": 42 + index, "source_ids": ["doc-1"], "requested_hops": 2},
                "verifier": {"id": "fixture-verifier", "version": "1"}, "initial_pool_sha256": H(["fixture-question-old"]),
                "coverage": {"generated_questions_complete": True, "reviews_complete": True, "consumption_complete": True,
                    "signals_complete": role == "fixed" or index != 2, "pool_complete": True, "attempts_complete": True, "updates_complete": True}, "budget": cost, "notice": NOTICE},
                evidence_refs=[log.evidence_ref(source)], title="初始题池" if index == 0 else f"第 {index} 轮")

        def question(suffix, first_round, status="passed", dedup="unique", duplicate_of=None):
            qid = prefix + "-q-" + suffix
            data = {"question": f"[合成 {suffix}] 林舟出生在哪里？", "proposed_answer": "银湾",
                "stable_question_id": "fixture-question-" + suffix, "first_generated_cycle_id": cycles[first_round],
                "generation_step": first_round, "deduplication": {"status": dedup, "version": "fixture-normalized-v1"},
                "requested_hops": 2, "verified_hops": None, "notice": NOTICE}
            if duplicate_of:
                data["duplicate_of"] = duplicate_of
                data["stable_question_id"] = "fixture-question-good"
            q = log.generated_question(qid, data, evidence_refs=[log.evidence_ref(source)])
            log.proposal(qid + "-proposal", {"cycle_id": cycles[first_round], "question_id": qid, "actor_id": "proposer",
                "raw_input": {"seed": 42 + first_round, "source": "doc-1"}, "raw_output": {"question": data["question"], "answer": "银湾"},
                "notice": NOTICE})
            review = {"question_id": qid, "status": "accepted" if status == "passed" else "rejected" if status == "failed" else "needs_review",
                "reviewer": {"name": "fixture-verifier", "version": "1"},
                "verification": {"status": status, "verifier": {"name": "fixture-verifier", "version": "1"}},
                "checks": {"format": {"status": "passed", "verifier": {"id": "fixture-format", "version": "1"}},
                    "answerability": {"status": status, "verifier": {"id": "fixture-verifier", "version": "1"}}},
                "reason": {"passed": "合成验证通过", "failed": "合成证据不支持答案", "error": "合成验证器超时", "not_run": "合成验证尚未执行"}[status],
                "notice": NOTICE}
            if status == "passed": review["verified_answer"] = "银湾"
            if status == "error": review["verification"]["error"] = "synthetic timeout"
            log.question_review(qid + "-review", review, evidence_refs=[log.evidence_ref(source)])
            if status == "passed" and dedup == "unique":
                log.selfplay_pool_event(qid + "-admitted", {"question_id": qid, "cycle_id": cycles[first_round],
                    "event": "admitted", "status": "admitted", "pool_id": prefix + "-pool",
                    "occurred_at": f"2026-01-01T00:0{first_round}:00Z", "admitted_at": f"2026-01-01T00:0{first_round}:00Z", "notice": NOTICE})
            return q

        old = question("old", 0)
        fresh = {}
        if role == "fresh":
            for suffix, status in (("good", "passed"), ("all-fail", "passed"), ("unused", "passed"),
                    ("rejected", "failed"), ("error", "error"), ("pending", "not_run")):
                fresh[suffix] = question(suffix, 1, status, dedup="unknown" if status == "not_run" else "unique")
            fresh["duplicate"] = question("duplicate", 1, duplicate_of=fresh["good"]["id"], dedup="duplicate")
            fresh["round2"] = question("round2", 2)
            # Fixed reference attempts are separate from training attempts and
            # use the frozen protocol above. Missing third attempt stays missing.
            for suffix, scores in (("good", [0, 1, 0]), ("all-fail", [0, 0, 0]), ("unused", [1, 1, 1]), ("round2", [0, 1])):
                q = fresh[suffix]
                cycle = cycles[2 if suffix == "round2" else 1]
                for i, score in enumerate(scores):
                    tid = q["id"] + "-probe-trace-" + str(i)
                    trace = log.trace(tid, _trace(q["data"]["question"], "银湾" if score else "海港", score), usage="validation")
                    log.solver_attempt(q["id"] + "-probe-" + str(i), {"cycle_id": cycle, "question_id": q["id"],
                        "actor_id": "reference", "context": "reference", "reference_id": ref_id,
                        "solver_revision": reference["solver_revision"], "attempt_index": i + 1, "status": "completed",
                        "groundtruth_visible": False, "reference_configuration_hash": H(reference),
                        "execution_config": {key: copy.deepcopy(reference[key]) for key in ("solver_weights_sha256", "harness_sha256", "sampling", "environment", "scoring", "verifier", "seed_policy")},
                        "scores": {"em": score}, "trace_ref": log.evidence_ref(trace), "notice": NOTICE}, usage="validation")

        for round_index in (1, 2):
            batch = prefix + "-batch-" + str(round_index)
            group = prefix + "-group-" + str(round_index)
            if role == "fixed":
                specs = [(old, 1, 0.0, "task"), (old, 1, 0.0, "task")]
            elif round_index == 1:
                specs = [(fresh["good"], 0, -1.0, "task"), (fresh["good"], 1, 1.0, "task"),
                    (fresh["all-fail"], 0, 0.0, "opd"), (fresh["all-fail"], 0, 0.0, "task")]
            else:
                specs = [(fresh["round2"], 0, None, "missing"), (fresh["round2"], 1, 0.0, "masked"),
                    (fresh["good"], 1, 0.0, "opd"), (old, 0, -1.0, "task")]
            uses, samples = [], []
            for i, (q, score, advantage, mode) in enumerate(specs):
                stem = prefix + f"-r{round_index}-sample-{i}"
                trace = log.trace(stem + "-trace", _trace(q["data"]["question"], "银湾" if score else "海港", score))
                attempt = log.solver_attempt(stem + "-attempt", {"cycle_id": cycles[round_index], "question_id": q["id"],
                    "actor_id": "solver", "context": "training", "attempt_index": i + 1, "status": "completed",
                    "solver_checkpoint_id": checkpoints[round_index - 1], "scores": {"em": score}, "reward": score,
                    "advantage": advantage, "group_id": group, "groundtruth_visible": False,
                    "trace_ref": log.evidence_ref(trace), "notice": NOTICE})
                task = {"objective": "policy_gradient", "effective_advantage": advantage,
                    "loss_mask": [1, 1, 0], "loss_weight": 1.0}
                if mode == "missing": task.pop("loss_mask")
                if mode == "masked":
                    task.pop("effective_advantage")
                    task["effective_advantages"] = [0.0, 0.0, 2.0]
                data = {"artifact_ids": [q["id"], attempt["id"]], "attempt_id": attempt["id"],
                    "rollout_id": stem, "sample_id": stem, "cycle_id": cycles[round_index], "status": "sampled", "selfplay_rollouts_complete": True,
                    "batch_id": batch, "group_id": group, "step": round_index, "task_signal": task, "notice": NOTICE}
                data["opd_signal"] = {"objective": "distillation", "loss_mask": [1, 1, 0] if mode == "opd" else [0, 0, 0],
                    "loss_weight": 0.2 if mode == "opd" else 0.0,
                    "supervision_present": mode == "opd", "teacher_revision": "synthetic-teacher-v1" if mode == "opd" else None}
                use = log.training_use(stem + "-use", data)
                uses.append(use["id"])
                sample = {"id": stem, "reward": score, "trace_ref": log.evidence_ref(trace),
                    "method_use_ref": {"object_id": use["id"]}}
                if advantage is not None: sample["advantage"] = advantage
                samples.append(sample)
            log.training_update(prefix + "-update-" + str(round_index), {"consumption_ids": uses,
                "cycle_id": cycles[round_index], "status": "completed", "batch_id": batch,
                "input_checkpoint_id": checkpoints[round_index - 1], "output_checkpoint_id": checkpoints[round_index],
                "update_evidence": NOTICE})
            batch_records.append({"id": batch, "run_id": run, "step": round_index, "demo": True,
                "output_checkpoint_id": checkpoints[round_index], "groups": [{"id": group, "samples": samples}]})
        if role == "fresh":
            log.training_use(prefix + "-selected-unused", {"artifact_ids": [fresh["unused"]["id"]], "cycle_id": cycles[1],
                "status": "selected", "reason": "合成候选，仅选中，未完成训练使用"})
            log.training_use(prefix + "-excluded-rejected", {"artifact_ids": [fresh["rejected"]["id"]], "cycle_id": cycles[1],
                "status": "excluded", "reason": "验证未通过"})
        cost = log.trace(prefix + "-total-cost", {
            "selfplay_evidence_type": "run_accounting", "run_id": run,
            "cycle_ids": [cycles[1], cycles[2]], "output_checkpoint_id": checkpoints[2],
            "budget": _budget(30, 10, 50, 10) if role == "fresh" else _budget(0, 4, 86, 10),
            "controls": {"initial_solver_sha256": H("same-initial-solver"),
                "initial_pool_sha256": H(["fixture-question-old"]), "harness_sha256": H("synthetic-harness-v1"),
                "optimizer_sha256": H("same-synthetic-grpo-configuration"), "training_seed": 42},
            "stable_question_ids": ["fixture-question-old"],
            "scope": "post-initial-pool generation + verification + solving + optimization, both rounds", "notice": NOTICE})
        log.export_bundle(artifacts / (role + "-selfplay-bundle.json"))
    studio.import_method_evidence(journal)
    for batch in batch_records: studio.log_training_batch(batch)
    evals = {}
    for step in (0, 2):
        eid = prefix + "-external-" + str(step)
        studio.start_evaluation({"id": eid, "checkpoint_id": checkpoints[step], "panel_id": PREFIX + "-panel",
            "protocol": protocol, "purpose": "checkpoint_eval", "demo": True})
        for index, sample in enumerate(SAMPLES):
            score = int(index == 0 or (step == 2 and role == "fresh"))
            answer = sample["answers"][0] if score else "合成错误答案"
            studio.log_trajectory(eid, {"id": eid + "-" + sample["id"], "sample_id": sample["id"], "dataset": sample["dataset"],
                "demo": True, "raw_final_output": "<answer>" + answer + "</answer>", "parsed_answer": answer,
                "scores": {"em": score}, "runtime_status": "ok", "termination_reason": "final_answer",
                "usage": {"input_tokens": 16, "output_tokens": 4}, "steps": [{"index": 0, "kind": "model", "output": answer}]})
        studio.finish_evaluation(eid)
        with MethodEvidenceRecorder(data_dir=destination, scope={"run_id": run}, demo=True, usage="validation") as log:
            log.evaluation_binding(eid + "-binding", {"evaluation_id": eid, "binding_origin": "recorded_execution",
                "state_complete": True, "retrieval_config": {"mode": "fixture", "version": "fixture-v1"},
                "independent": True, "autonomous": True, "skills_disabled": True, "assessment_families": ["self_play"],
                "selfplay_context": {"evaluation_source": "fixed_external", "generated_answers_visible": False, "proposer_hints": False, "teacher_hints": False, "peer_answers": False,
                    "future_observations": False, "groundtruth_visible": False, "privileged_information": [],
                    "training_only_guidance_removed": True, "generated_questions_visible": False,
                    "data_isolation": "unverified", "independent_of_generated_pool": True}, "notice": NOTICE})
        evals[step] = eid
    return {"run_id": run, "checkpoint_ids": checkpoints, "evaluation_ids": evals,
        "cycle_ids": cycles, "reference_id": ref_id, "cost_ref": MethodEvidenceRecorder.evidence_ref(cost, "/budget"),
        "controls_ref": MethodEvidenceRecorder.evidence_ref(cost, "/controls"),
        "initial_pool_ref": MethodEvidenceRecorder.evidence_ref(cost, "/stable_question_ids"), "journal": str(journal)}


def register_center_comparisons(hub, comparison_file, source_workspace_id):
    """Explicitly register this example's comparison after BOTH run packages.

    Run export retains its scope. Cross-run comparison registration is separate,
    with the original comparison source bytes preserved for audit.
    """
    from searchagent_lab.core import ValidationError
    from searchagent_lab.team.hub import gid
    path = Path(comparison_file)
    raw = path.read_bytes()
    bundle = json.loads(raw)
    objects = []
    for original in bundle["objects"]:
        if original.get("kind") != "selfplay_comparison" or original["data"].get("comparison_type") != "pool_supply":
            raise ValidationError("This example rebinds explicit pool_supply comparisons only")
        obj = copy.deepcopy(original)
        obj["id"] = gid(source_workspace_id, "method", obj["id"])
        hub.store.experiment(obj["scope"]["experiment_id"])
        for arm in obj["data"]["arms"]:
            for key, category in (("run_id", "run"), ("checkpoint_id", "checkpoint"), ("evaluation_id", "evaluation")):
                arm[key] = gid(source_workspace_id, category, arm[key])
            arm["cycle_ids"] = [gid(source_workspace_id, "method", oid) for oid in arm["cycle_ids"]]
            for field in ("cost_ref", "controls_ref", "initial_pool_ref"):
                ref = arm.get(field)
                if ref:
                    target = hub.methods.object(gid(source_workspace_id, "method", ref["object_id"]))
                    arm[field] = MethodEvidenceRecorder.evidence_ref(target, ref.get("pointer", ""))
        obj["provenance"] = {"producer": "selfplay_tracking_example.register_center_comparisons",
            "source_workspace_id": source_workspace_id, "original_data_sha256": H(original["data"]), "notice": NOTICE}
        objects.append(obj)
    with hub.lock, hub.store.transaction():
        hub.methods.import_bundle({"method_schema": SCHEMA, "demo": True, "objects": objects}, raw, path.name)
    return {obj["id"]: hub.methods.selfplay_comparison(obj["id"]) for obj in objects}


def build_example(data_dir):
    destination = Path(data_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError("Choose a NEW directory; refusing to modify: " + str(destination))
    destination.mkdir(parents=True, exist_ok=False)
    artifacts = destination / "example-artifacts"
    artifacts.mkdir()
    protocol = json.loads((SOURCE_ROOT / "examples/protocol_v2.synthetic.json").read_text())
    protocol.update(harness_version="fixture-selfplay-h1", harness_fingerprint=H("synthetic-harness-v1"),
        panel_scope="Synthetic external probe; not official test data or a real model result")
    with StudioRecorder(destination) as studio:
        studio.store.register_project({"id": PREFIX + "-project", "name": "Self-play 合成示例", "demo": True})
        studio.store.register_experiment({"id": EXPERIMENT, "project_id": PREFIX + "-project",
            "name": "Self-play · 题目供给、学习信号与 solver", "demo": True})
        studio.register_experiment_profile(experiment_id=EXPERIMENT, track="main", kind="agent_iteration",
            question="固定旧题池与持续新供给如何分开检查？", change="synthetic fixture", constants=NOTICE)
        studio.store.register_panel({"id": PREFIX + "-panel", "name": "两题外部合成评测 · 非真实结果",
            "version": "fixture-v1", "split": "probe", "samples": SAMPLES, "demo": True})
        runs = {role: _build_run(studio, destination, artifacts, role, protocol) for role in ("fixed", "fresh")}
        arms = []
        for role in ("fixed", "fresh"):
            item = runs[role]
            arms.append({"label": "fixed_pool" if role == "fixed" else "fresh_supply", "run_id": item["run_id"],
                "checkpoint_id": item["checkpoint_ids"][2], "evaluation_id": item["evaluation_ids"][2],
                "cycle_ids": [item["cycle_ids"][1], item["cycle_ids"][2]], "initial_solver_sha256": H("same-initial-solver"),
                "initial_pool_sha256": H(["fixture-question-old"]), "harness_sha256": H("synthetic-harness-v1"),
                "optimizer_sha256": H("same-synthetic-grpo-configuration"), "training_seed": 42,
                "cost_ref": item["cost_ref"], "controls_ref": item["controls_ref"], "initial_pool_ref": item["initial_pool_ref"],
                "budget": _budget(30, 10, 50, 10) if role == "fresh" else _budget(0, 4, 86, 10)})
        comparisons = []
        with MethodEvidenceRecorder(data_dir=destination, scope={"experiment_id": EXPERIMENT}, demo=True, usage="validation") as log:
            data = {"comparison_type": "pool_supply", "arms": arms, "budget_tolerance": 0.05,
                "cohort": {"id": PREFIX + "-panel", "isolation_status": "unverified"}, "notice": NOTICE}
            comparisons.append(log.selfplay_comparison(PREFIX + "-compare-supply", data, title="固定旧题池与持续新供给（合成）"))
            incomplete = copy.deepcopy(data)
            incomplete["arms"][1]["budget"]["complete"] = False
            incomplete["arms"][1]["budget"]["components"].pop("generation")
            comparisons.append(log.selfplay_comparison(PREFIX + "-compare-budget-missing", incomplete,
                title="缺出题预算：不判为同预算（合成）"))
        comparison_file = artifacts / "selfplay-experiment-comparisons.json"
        _json(comparison_file, {"method_schema": SCHEMA, "demo": True, "objects": comparisons})
    init_source(destination, "synthetic-selfplay-example", "cpu-only-fixture")
    packages = []
    for role, item in runs.items():
        package = export_run(destination, item["run_id"], artifacts / (role + "-run.zip"))
        _, payload, _ = read_package(package["path"])
        assert payload["methods"] and all(obj["demo"] for obj in payload["methods"])
        packages.append(package["path"])
    result = {"synthetic_only": True, "notice": NOTICE, "data_dir": str(destination), "experiment_id": EXPERIMENT,
        "run_id": runs["fresh"]["run_id"], "runs": runs, "packages": packages,
        "comparison_ids": [obj["id"] for obj in comparisons], "experiment_comparison_file": str(comparison_file)}
    _json(destination / "example-verification.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="显式指定尚不存在的新目录")
    args = parser.parse_args()
    try:
        result = build_example(args.data_dir)
    except FileExistsError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
