"""CPU-only method evidence example. All generated records are synthetic.

Run from the source directory:
    python examples/method_evidence_example.py --data-dir /tmp/new-method-demo

The requested destination MUST NOT exist. This exercises logging, imports,
explicit method profiles and result packages; it runs no model or optimizer.
Importing this file has no side effects. Call build_example explicitly.
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
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.package import export_run, init_source, read_package


NOTICE = "合成接入与界面演示：没有调用模型、搜索服务、teacher 或优化器，所有成绩与训练记录均为 fixture。"
QUESTION = "[合成档案] 蓝色车站导演林舟出生在哪里？"
BANK_V1 = {"metadata": {"demo": True, "source": "literal fixture"}, "general_skills": [
    {"skill_id": "verify-person", "title": "核对实体", "principle": "核对人物与问题指代是否一致。", "when_to_apply": "检索到人物资料后"}]}
BANK_V2 = copy.deepcopy(BANK_V1)
BANK_V2["general_skills"][0]["principle"] = "核对人物与问题指代是否一致，再查原始资料中的出生地。"


def _trace(answer: str, *, question=QUESTION) -> dict:
    return {"demo": True, "question": question, "raw_final_output": f"<answer>{answer}</answer>",
            "parsed_answer": answer, "scores": {"em": int(answer == "银湾")}, "runtime_status": "ok",
            "termination_reason": "final_answer", "steps": [
                {"index": 0, "kind": "model", "input": [{"role": "user", "content": question}], "output": "search 林舟 出生地"},
                {"index": 1, "kind": "tool", "tool_call": {"name": "search", "arguments": {"query": "林舟 出生地"}},
                 "tool_response": {"text": "合成档案：林舟出生于银湾。"}, "model_visible": "合成档案：林舟出生于银湾。"},
                {"index": 2, "kind": "model", "output": f"<answer>{answer}</answer>"}]}


def _signal(objective, original=None, effective=None, active=None, tokens=None, weight=None):
    return {"objective": objective, "original_advantage": original, "effective_advantage": effective,
            "loss_active": active, "loss_mask_tokens": tokens, "loss_weight": weight}


def _skill(log: MethodEvidenceRecorder) -> dict:
    log.method_profile("sk-profile", method_name="Skill 生命周期 · 合成接入", families=["skill"],
                       configuration={"method": "fixture", "deployment_skill_bank": True}, title=NOTICE)
    failed = log.trace("sk-source-failure", _trace("海港"), title="原始失败轨迹（合成）", usage="evolution")
    log.skill_bank("sk-bank-v1", BANK_V1, version="fixture-v1", usage="evolution")
    log.skill_bank("sk-bank-v2", BANK_V2, version="fixture-v2", derived_from=[failed["id"]],
                   evidence_refs=[log.evidence_ref(failed)], usage="evolution")
    log.skill_evolution("sk-evolution-accepted", {"bank_id": "sk-bank-v2", "parent_bank_id": "sk-bank-v1",
        "status": "accepted", "decision": "accepted", "step": 1,
        "generator": {"id": "synthetic-author", "version": "fixture-v1"},
        "generation_input": "[合成] 检查失败轨迹里的实体消歧问题。",
        "raw_generation_output": BANK_V2, "reason": "合成版本试验接受；不是实际研究结论。",
        "version_trial": {"status": "synthetic", "matched_policy": "sk-cp-0", "candidate_id": "sk-bank-v2"}},
        derived_from=[failed["id"]])
    log.skill_evolution("sk-evolution-rejected", {"bank_id": "sk-bank-v2", "parent_bank_id": "sk-bank-v1",
        "status": "rejected", "decision": "rejected", "step": 1,
        "raw_generation_output": {"suggestion": "[合成错误建议] 总是回答银湾。"},
        "reason": "硬编码某题答案，候选未进入技能库。"}, derived_from=[failed["id"]])

    actual = _trace("银湾")
    injection_text = BANK_V2["general_skills"][0]["principle"]
    final_input = [{"role": "system", "content": injection_text}, {"role": "user", "content": QUESTION}]
    actual["steps"][0]["input"] = final_input
    success = log.trace("sk-training-trace", actual)
    log.skill_retrieval("sk-retrieval-used", {"bank_id": "sk-bank-v2", "sample_id": "sk-sample-1",
        "trajectory_id": "sk-training-trace", "trajectory_complete": True,
        "candidates": [{"member": "verify-person", "score": 0.8}], "selected": ["verify-person"],
        "injections": [{"member": "verify-person", "rendered_text": injection_text,
                        "evidence_ref": log.evidence_ref(success, "/steps/0/input/0/content")}],
        "injections_complete": True, "retrieval_config": {"mode": "fixture", "version": "fixture-v1"},
        "trace_ref": log.evidence_ref(success)}, final_input=final_input)
    log.skill_retrieval("sk-retrieval-trimmed", {"bank_id": "sk-bank-v2", "sample_id": "sk-sample-trimmed",
        "trajectory_id": "sk-trajectory-trimmed", "trajectory_complete": True,
        "candidates": ["verify-person"], "selected": ["verify-person"],
        "injections": [], "injections_complete": True, "reason": "[合成] 最终组装因预算裁掉了技能。"},
        final_input=[{"role": "user", "content": QUESTION}])
    log.skill_retrieval("sk-retrieval-unknown", {"bank_id": "sk-bank-v2", "sample_id": "sk-sample-unlogged",
        "trajectory_id": "sk-trajectory-unlogged", "trajectory_complete": False,
        "candidates": ["verify-person"], "selected": ["verify-person"],
        "injections_complete": False, "missing_reason": "历史格式只记了检索命中，最终输入未知。"})
    log.training_use("sk-consumed", {"artifact_ids": ["sk-retrieval-used"], "status": "sampled",
        "batch_id": "sk-batch-1", "group_id": "sk-group-1", "sample_id": "sk-sample-1", "step": 1,
        "signal": _signal("policy_gradient", 0.5, 0.5, True, 3, 1.0)})
    log.training_update("sk-update-1", {"consumption_ids": ["sk-consumed"], "status": "completed",
        "batch_id": "sk-batch-1", "input_checkpoint_id": "sk-cp-0", "output_checkpoint_id": "sk-cp-1",
        "update_evidence": "synthetic fixture; no optimizer executed"})
    return {"id": "sk-batch-1", "run_id": "sk-run", "step": 1, "demo": True,
        "groups": [{"id": "sk-group-1", "samples": [{"id": "sk-sample-1", "reward": 1,
            "advantage": 0.5, "token_ids": [1, 2, 3], "response_mask": [1, 1, 1],
            "trace_ref": log.evidence_ref(success), "method_use_ref": {"object_id": "sk-consumed"}}]}]}


def _selfplay(log: MethodEvidenceRecorder) -> list[dict]:
    log.method_profile("sp-profile", method_name="Self-play 供给与学习信号 · 合成接入", families=["self_play"],
                       configuration={"algorithm": "synthetic GRPO", "reference_solver": "fixed-fixture-solver"}, title=NOTICE)
    participants = [{"id": "fixture-proposer", "role": "proposer", "model_revision": "fixture-p0"},
                    {"id": "fixture-solver", "role": "solver", "model_revision": "fixture-s0"}]
    for round_index in (1, 2):
        log.selfplay_cycle(f"sp-cycle-{round_index}", {"round": round_index, "participants": participants,
            "status": "recorded", "generation_budget": {"unit": "fixture tokens", "value": 1000},
            "coverage": {"generated_questions_complete": True, "reviews_complete": True,
                         "consumption_complete": True, "signals_complete": True}})
    source = log.record("sp-source", "dataset_manifest", {"documents": [
        {"id": "fiction-1", "text": "[合成] 蓝色车站由林舟执导，林舟出生于银湾。"}]}, usage="training")
    for suffix, text, dedup in [
        ("valid", QUESTION, "unique"),
        ("rejected", "[合成] 林舟的获奖年份是多少？", "unique"),
        ("unknown", "[合成] 档案中未能验证的问题？", "unknown"),
        ("duplicate", QUESTION, "duplicate"),
    ]:
        uid = "fiction-question-1" if suffix in ("valid", "duplicate") else "fiction-question-" + suffix
        data = {"question": text, "proposed_answer": "银湾" if suffix == "valid" else "合成候选",
            "stable_question_id": uid, "first_generated_cycle_id": "sp-cycle-1", "generation_step": 1,
            "deduplication": {"status": dedup, "version": "fixture-normalized-text-v1"}}
        if suffix == "duplicate":
            data["duplicate_of"] = "sp-q-valid"
        log.generated_question("sp-q-" + suffix, data, version="fixture-v1", derived_from=[source["id"]])
        log.proposal("sp-proposal-" + suffix, {"cycle_id": "sp-cycle-1", "question_id": "sp-q-" + suffix,
            "actor_id": "fixture-proposer", "solver_actor_id": "fixture-solver",
            "raw_input": source["data"], "raw_output": {"question": text, "answer": data["proposed_answer"]}})
    reviewer = {"name": "fixture-verifier", "version": "fixture-v1"}
    log.question_review("sp-review-valid", {"question_id": "sp-q-valid", "status": "accepted",
        "verified_answer": "银湾", "reviewer": reviewer,
        "verification": {"status": "passed", "verifier": reviewer, "method": "scorer"},
        "reason": "[合成] 给定档案具有完整答案依据。"}, evidence_refs=[log.evidence_ref(source)])
    log.question_review("sp-review-rejected", {"question_id": "sp-q-rejected", "status": "rejected",
        "reviewer": reviewer, "verification": {"status": "failed", "verifier": reviewer},
        "reason": "[合成] 档案中没有获奖年份。"})
    log.question_review("sp-review-unknown", {"question_id": "sp-q-unknown", "status": "needs_review",
        "reviewer": reviewer, "verification": {"status": "error", "verifier": reviewer, "error": "synthetic timeout"},
        "reason": "验证器出错；结果未知，不能计作通过。"})
    traces = []
    for attempt, answer, advantage in [(1, "海港", -0.5), (2, "银湾", 0.5)]:
        trace = log.trace(f"sp-trace-{attempt}", _trace(answer)); traces.append(trace)
        log.solver_attempt(f"sp-attempt-{attempt}", {"cycle_id": "sp-cycle-1", "question_id": "sp-q-valid",
            "actor_id": "fixture-solver", "attempt_index": attempt, "group_id": "sp-group-1",
            "reward": int(answer == "银湾"), "advantage": advantage, "scores": {"em": int(answer == "银湾")},
            "groundtruth_visible": False, "trace_ref": log.evidence_ref(trace)})
        log.training_use(f"sp-consumed-{attempt}", {"artifact_ids": ["sp-q-valid", f"sp-attempt-{attempt}"],
            "cycle_id": "sp-cycle-1", "status": "sampled", "batch_id": "sp-batch-1",
            "group_id": "sp-group-1", "sample_id": f"sp-sample-{attempt}", "step": 1,
            "signal": _signal("policy_gradient", advantage, advantage, True, 3, 1.0)})
    for suffix in ("rejected", "duplicate"):
        log.training_use("sp-excluded-" + suffix, {"artifact_ids": ["sp-q-" + suffix],
            "cycle_id": "sp-cycle-1", "status": "excluded", "reason": suffix})
    log.training_use("sp-selected-unknown", {"artifact_ids": ["sp-q-unknown"], "cycle_id": "sp-cycle-1",
        "status": "selected", "reason": "[合成] 仅候选；没有采样和更新证据。"})
    log.training_update("sp-update-1", {"consumption_ids": ["sp-consumed-1", "sp-consumed-2"],
        "cycle_id": "sp-cycle-1", "status": "completed", "batch_id": "sp-batch-1",
        "input_checkpoint_id": "sp-cp-0", "output_checkpoint_id": "sp-cp-1", "update_evidence": NOTICE})
    # Round 2 consumes the same stable question. It is not a newly generated one.
    log.training_use("sp-reused-zero-signal", {"artifact_ids": ["sp-q-valid"], "cycle_id": "sp-cycle-2",
        "status": "sampled", "batch_id": "sp-batch-2", "group_id": "sp-group-2", "sample_id": "sp-reused-sample",
        "step": 2, "reuse_count": 1, "signal": _signal("policy_gradient", 0.0, 0.0, False, 3, 1.0)})
    log.training_update("sp-update-2", {"consumption_ids": ["sp-reused-zero-signal"], "cycle_id": "sp-cycle-2",
        "batch_id": "sp-batch-2", "status": "completed", "update_evidence": NOTICE})
    return [
        {"id": "sp-batch-1", "run_id": "sp-run", "step": 1, "demo": True,
         "groups": [{"id": "sp-group-1", "samples": [
             {"id": f"sp-sample-{i+1}", "reward": i, "advantage": -0.5 if i == 0 else 0.5,
              "token_ids": [1, 2, 3], "response_mask": [1, 1, 1], "trace_ref": log.evidence_ref(traces[i])}
             for i in range(2)]}]},
        {"id": "sp-batch-2", "run_id": "sp-run", "step": 2, "demo": True,
         "groups": [{"id": "sp-group-2", "samples": [{"id": "sp-reused-sample", "reward": 1,
             "advantage": 0.0, "trace_ref": log.evidence_ref(traces[1])}]}]},
    ]


def _opd(log: MethodEvidenceRecorder) -> dict:
    log.method_profile("opd-profile", method_name="OPD 修复与监督 · 合成接入", families=["opd"],
        configuration={"teacher": "synthetic-teacher", "deployment_teacher": False,
                       "repair_protocol": "two attempts per eligible captured failure"}, title=NOTICE)
    failed = log.trace("opd-student-failure", _trace("海港"))
    for suffix, state_status, eligible, attempt_budget in [
        ("repair", "captured", True, 2), ("rescore", "captured", True, 2), ("unknown", "text_only", None, None),
    ]:
        log.opd_failure("opd-failure-" + suffix, {"sample_id": "opd-sample-" + suffix,
            "student_checkpoint_id": "opd-cp-0", "failure_step": 2, "eligible": eligible,
            "attempt_budget": attempt_budget, "original_advantage": 0.0 if suffix != "unknown" else None,
            "sampling_protocol": "synthetic-predeclared-failures-v1", "trace_ref": log.evidence_ref(failed),
            "state": {"status": state_status, "prefix": failed["data"]["steps"][:2],
                "environment": {"version": "fictional-search-v1", "corpus": "fixture-only"},
                "remaining_budget": {"tool_calls": 2}, "restore_handle": "synthetic://no-real-environment"}},
            derived_from=[failed["id"]])
    context = {"student_visible": failed["data"]["steps"][:2],
               "extra_information": [{"type": "hindsight_hint", "text": "[合成提示] 已有 observation 写了出生地，请核对。"}],
               "prompt_version": "fixture-teacher-v1", "teacher_revision": "synthetic-not-executed"}
    teacher_ids = []
    for attempt, answer in [(1, "海港"), (2, "银湾")]:
        teacher_trace = log.trace(f"opd-teacher-trace-{attempt}", _trace(answer))
        teacher = log.opd_teacher(f"opd-teacher-{attempt}", {"failure_id": "opd-failure-repair",
            "teacher_id": "synthetic-teacher", "mode": "continuation", "status": "completed",
            "attempt_index": attempt, "state_alignment": "restored", "context": context,
            "teacher_trace_ref": log.evidence_ref(teacher_trace), "raw_output": teacher_trace["data"],
            "verification": {"status": "passed" if attempt == 2 else "failed", "method": "environment",
                "verifier": {"name": "synthetic-environment-verifier", "version": "fixture-v1"},
                "demo": True, "notice": NOTICE}})
        teacher_ids.append(teacher["id"])
    rescore = log.opd_teacher("opd-teacher-rescore", {"failure_id": "opd-failure-rescore",
        "teacher_id": "synthetic-self-teacher", "mode": "rescore", "status": "completed",
        "context": context, "student_log_probs": [-2.0, -1.5, -1.0],
        "teacher_log_probs": [-1.0, -1.0, -0.8], "token_ids": [1, 2, 3],
        "distribution": {"name": "sampled-token log probabilities", "temperature": 1.0, "implementation": "fixture-v1"}})
    log.opd_teacher("opd-teacher-error", {"failure_id": "opd-failure-unknown", "teacher_id": "synthetic-teacher",
        "mode": "continuation", "status": "error", "attempt_index": 1, "state_alignment": "text_only",
        "context": context, "error": "[合成] 环境状态未恢复。", "verification": {"status": "not_run"}})
    log.opd_supervision("opd-supervision-active", {"failure_id": "opd-failure-repair", "teacher_ids": teacher_ids,
        "objective": "distillation", "status": "accepted", "loss_mask_tokens": 3, "loss_mask": [1, 1, 1],
        "loss_weight": 0.2, "loss_active": True, "original_advantage": 0.0,
        "effective_advantage": None, "reason": "[合成] 接续成功分支进入单独蒸馏项。"})
    log.opd_supervision("opd-supervision-zero", {"failure_id": "opd-failure-rescore", "teacher_ids": [rescore["id"]],
        "objective": "advantage_reweight", "status": "accepted", "loss_mask_tokens": 3, "loss_mask": [1, 1, 1],
        "loss_weight": 1.0, "loss_active": False, "original_advantage": 0.0, "effective_advantage": 0.0,
        "reason": "仅重加权原 advantage；0 × w 仍为 0，没有任务优势信号。"})
    log.opd_supervision("opd-supervision-rejected", {"failure_id": "opd-failure-unknown",
        "teacher_ids": ["opd-teacher-error"], "objective": "distillation", "status": "rejected",
        "loss_mask_tokens": None, "loss_weight": None, "loss_active": None,
        "reason": "teacher 未产生可验证分支；监督未知，没有训练消费。"})
    for suffix, original, effective, active, objective, weight in [
        ("active", 0.0, None, True, "distillation", 0.2),
        ("zero", 0.0, 0.0, False, "advantage_reweight", 1.0),
    ]:
        log.training_use("opd-use-" + suffix, {"artifact_ids": ["opd-supervision-" + suffix],
            "status": "sampled", "batch_id": "opd-batch-1", "group_id": "opd-group-1",
            "sample_id": "opd-sample-" + ("repair" if suffix == "active" else "rescore"), "step": 1,
            "signal": _signal(objective, original, effective, active, 3, weight)})
    log.training_update("opd-update-1", {"consumption_ids": ["opd-use-active", "opd-use-zero"],
        "status": "completed", "batch_id": "opd-batch-1", "input_checkpoint_id": "opd-cp-0",
        "output_checkpoint_id": "opd-cp-1", "update_evidence": NOTICE})
    return {"id": "opd-batch-1", "run_id": "opd-run", "step": 1, "demo": True,
        "groups": [{"id": "opd-group-1", "samples": [
            {"id": "opd-sample-repair", "reward": 0, "advantage": 0.0,
             "trace_ref": log.evidence_ref(failed), "supervision_ref": {"object_id": "opd-supervision-active"}},
            {"id": "opd-sample-rescore", "reward": 0, "advantage": 0.0,
             "trace_ref": log.evidence_ref(failed), "supervision_ref": {"object_id": "opd-supervision-zero"}}]}]}


def build_example(data_dir: str | Path) -> dict:
    destination = Path(data_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Choose a NEW directory; refusing to modify: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    artifacts = destination / "example-artifacts"
    artifacts.mkdir()
    protocol = json.loads((SOURCE_ROOT / "examples/protocol_v2.synthetic.json").read_text(encoding="utf-8"))
    protocol.update(harness_version="fixture-h1", harness_fingerprint=MethodEvidenceRecorder.content_sha256("fixture-h1"),
                    panel_scope="Synthetic fixture; not official dev/test and not model results")
    samples = [{"id": "fixture-q1", "dataset": "FixtureQA", "question": QUESTION, "answers": ["银湾"]},
               {"id": "fixture-q2", "dataset": "FixtureQA", "question": "[合成] 档案的颜色？", "answers": ["蓝色"]}]
    specifications = [("sk", "skill", _skill), ("sp", "self_play", _selfplay), ("opd", "opd", _opd)]
    verification = {"synthetic_only": True, "data_dir": str(destination), "notice": NOTICE,
                    "events": {}, "packages": [], "families": {}}
    with StudioRecorder(destination) as studio:
        studio.store.register_project({"id": "method-fixtures", "name": "方法接入合成示例", "demo": True})
        studio.store.register_panel({"id": "method-fixture-panel", "name": "两题合成评测 · 非真实成绩",
            "version": "fixture-v1", "split": "probe", "demo": True, "samples": samples})
        for prefix, family, build in specifications:
            experiment_id, run_id = prefix + "-experiment", prefix + "-run"
            studio.store.register_experiment({"id": experiment_id, "project_id": "method-fixtures",
                "name": family + " · 合成证据链", "demo": True})
            studio.register_experiment_profile(experiment_id=experiment_id, track="main", kind="agent_iteration",
                question="检查方法产物、实际使用与独立结果的关联", change="synthetic fixture", constants=NOTICE)
            studio.store.register_run({"id": run_id, "experiment_id": experiment_id, "run_type": "training",
                "name": family + " 合成日志", "base_model": "synthetic-no-model", "model_revision": "fixture-v1",
                "algorithm": "fixture only; no optimizer executed", "demo": True, "seed": 42, "status": "completed"})
            for step in (0, 1):
                studio.store.register_checkpoint({"id": f"{prefix}-cp-{step}", "run_id": run_id, "step": step,
                    "artifact_uri": f"synthetic://no-weights/{prefix}/{step}", "demo": True,
                    "weights_sha256": MethodEvidenceRecorder.content_sha256(f"synthetic:{prefix}:{step}")})
            # Offline JSONL is the exact same event format as import-methods.
            journal = artifacts / f"{prefix}-method-events.jsonl"
            with MethodEvidenceRecorder(journal_path=journal, scope={"run_id": run_id}, usage="training", demo=True,
                    provenance={"producer": "method_evidence_example.py", "version": "fixture-v1", "notice": NOTICE}) as log:
                batches = build(log)
                bundle = log.export_bundle(artifacts / f"{prefix}-method-bundle.json")
            imported = studio.import_method_evidence(journal)
            assert imported["count"] == len(bundle["objects"])
            verification["events"][family] = imported["count"]
            for batch in batches if isinstance(batches, list) else [batches]:
                studio.log_training_batch(batch)

            # These are manually constructed fixture outcomes, never model runs.
            evaluations = [(prefix + "-external-eval", 1, None)]
            if prefix == "sk":
                evaluations = [("sk-eval-old-bank", 1, "sk-bank-v1"), ("sk-eval-new-bank", 2, "sk-bank-v2"),
                               ("sk-eval-bank-off", 1, None)]
            for evaluation_id, correct_count, bank_id in evaluations:
                ev = {"id": evaluation_id, "checkpoint_id": prefix + "-cp-1", "panel_id": "method-fixture-panel",
                    "protocol": protocol, "purpose": "checkpoint_eval", "demo": True}
                if prefix == "sk" and evaluation_id != "sk-eval-old-bank":
                    ev["comparison"] = {"allowed_method_changes": ["/bank_content_hash"]}
                studio.start_evaluation(ev)
                for index, sample in enumerate(samples):
                    answer = sample["answers"][0] if index < correct_count else "synthetic-wrong"
                    studio.log_trajectory(evaluation_id, {"id": evaluation_id + "-" + sample["id"],
                        "sample_id": sample["id"], "dataset": sample["dataset"], "demo": True,
                        "scores": {"em": int(answer == sample["answers"][0])}, "raw_final_output": f"<answer>{answer}</answer>",
                        "parsed_answer": answer, "runtime_status": "ok", "termination_reason": "final_answer",
                        "usage": {"input_tokens": 20, "output_tokens": 4},
                        "steps": [{"index": 0, "kind": "model", "output": f"<answer>{answer}</answer>"}]})
                studio.finish_evaluation(evaluation_id)
                state = {"evaluation_id": evaluation_id, "binding_origin": "recorded_execution", "state_complete": True,
                    "retrieval_config": {"mode": "fixture", "version": "fixture-v1"}, "independent": True,
                    "autonomous": True, "assessment_families": [family], "notice": NOTICE}
                if bank_id is not None:
                    state["bank_id"] = bank_id
                else:
                    state["skills_disabled"] = True
                with MethodEvidenceRecorder(data_dir=destination, scope={"run_id": run_id}, demo=True) as live:
                    live.evaluation_binding("bind-" + evaluation_id, state, usage="validation")
            verification["families"][run_id] = [family]
        verification["method_comparison"] = studio.repo.evaluation_pair("sk-eval-old-bank", "sk-eval-new-bank", axis="method_state")
        assert verification["method_comparison"]["compatible"], verification["method_comparison"]["reasons"]
        verification["skill_off_on_comparison"] = studio.repo.evaluation_pair("sk-eval-bank-off", "sk-eval-new-bank", axis="method_state")
        assert verification["skill_off_on_comparison"]["compatible"], verification["skill_off_on_comparison"]["reasons"]
        assert not studio.repo.evaluation_pair("sk-eval-old-bank", "sk-eval-new-bank", axis="checkpoint")["compatible"]

    init_source(destination, "synthetic-example", "cpu-only-fixture")
    for prefix, _, _ in specifications:
        package = export_run(destination, prefix + "-run", artifacts / (prefix + "-run.zip"))
        _, payload, _ = read_package(package["path"])
        assert payload["methods"] and all(event["demo"] for event in payload["methods"])
        assert payload["batches"] and payload["evaluations"]
        verification["packages"].append(package["path"])
    (destination / "example-verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return verification


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
