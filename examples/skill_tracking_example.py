"""CPU-only Skill tracking integration example; every result is synthetic.

    python examples/skill_tracking_example.py --data-dir /tmp/new-skill-demo

The destination must not exist. This creates log fixtures, explicit evaluation
declarations, immutable skill versions and transport packages. It does not call
a model, search service, teacher, script runner, optimizer or deployment API.
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
from searchagent_lab.team.package import export_run, init_source


NOTICE = "合成接入样例：所有轨迹、脚本结果、分数和训练更新均是 fixture，没有执行模型或训练。"
QUESTION = "[合成] 蓝色车站的导演出生在哪里？"
DESCRIPTION = "cross-check: 核对人物身份并检查原始资料。"
BODY_V1 = "# 核对人物\n先核对问题中的人物指代，再读取检索结果。"
BODY_V2 = "# 核对人物与来源\n先核对人物指代，再交叉检查原始资料中的出生地。"
BODY_V3 = "# 被拒绝的候选\n遇到这道样例题就直接回答银湾。"
SCRIPT = "def verify_same_person(expected, observed):\n    return expected == observed\n"
EXTRA_BODY = "# 输出格式\n用 <answer> 包裹最终回答。"
SAMPLES = [{"id": "fixture-q1", "dataset": "FixtureQA", "question": QUESTION, "answers": ["银湾"]},
           {"id": "fixture-q2", "dataset": "FixtureQA", "question": "[合成] 车站的颜色？", "answers": ["蓝色"]}]
MODES = {"fixed": "fixed_skills_rl", "iteration": "fixed_policy_skill_iteration",
         "joint": "joint_evolution", "inline": "fixed_skills_rl"}


def _trace(body=BODY_V1, answer="银湾", inline=False, with_skill=True, description=DESCRIPTION, question=QUESTION):
    if inline:
        with_skill = False
    final_input = [{"role": "system", "content": description + "\n" + body if with_skill else "回答问题并核对检索资料。"}]
    if inline:
        final_input.append({"role": "assistant", "content": body})
    final_input.append({"role": "user", "content": question})
    result = {"demo": True, "notice": NOTICE, "question": question,
        "raw_final_output": "<answer>" + answer + "</answer>", "parsed_answer": answer,
        "scores": {"em": int(answer == "银湾")}, "runtime_status": "ok", "termination_reason": "final_answer",
        "steps": [
            {"index": 0, "kind": "model", "input": [{"role": "system", "content": description if with_skill else "回答问题并核对检索资料。"}],
             "output": body if inline else "load cross-check skill" if with_skill else "search 蓝色车站 导演"},
            {"index": 1, "kind": "tool", "tool_call": {"name": "read", "arguments": {"path": "SKILL.md"}},
             "tool_response": {"text": body}},
            {"index": 2, "kind": "model", "input": final_input, "output": "search 蓝色车站 导演 出生地"},
            {"index": 3, "kind": "tool", "tool_call": {"name": "search", "arguments": {"query": "蓝色车站 导演 出生地"}},
             "tool_response": {"text": "[合成档案] 导演林舟出生于银湾。"}},
            {"index": 4, "kind": "tool", "tool_call": {"name": "run_script", "arguments": {"path": "scripts/verify.py"}},
             "tool_response": {"exit_code": 0, "verified": True, "notice": NOTICE}},
            {"index": 5, "kind": "model", "output": "<answer>" + answer + "</answer>"}]}
    if not with_skill:
        result["steps"][1] = {"index": 1, "kind": "tool",
            "tool_call": {"name": "search", "arguments": {"query": "蓝色车站 导演"}},
            "tool_response": {"text": "[合成档案] 导演是林舟。"}}
        result["steps"][4]["tool_call"] = {"name": "verify_evidence", "arguments": {"person": "林舟", "answer": answer}}
    return result


def _asset(log, prefix, version, body, parent=None):
    data = {"skill_id": prefix + "-cross-check", "version": version,
            "package": {"files": {"SKILL.md": body, "scripts/verify.py": SCRIPT,
                                  "references/source-policy.txt": "优先核对原始资料。"}},
            "applicability": "检索到人物信息后", "source": {"kind": "synthetic_fixture", "notice": NOTICE},
            "owner": "fixture-maintainer"}
    if parent:
        data["parent_asset_id"] = parent
    return log.skill_asset(prefix + "-asset-" + version, data, title="交叉核对 · " + version)


def _instance(log, prefix, trace, *, asset_id=None, inline_text=None, evaluation_id=None, trajectory_id=None, comparison_ids=None):
    data = {"step": 2, "steps": [2, 3, 4], "trace_ref": log.evidence_ref(trace),
        "selection": {"source": "model", "step": 0,
                      "evidence_ref": log.evidence_ref(trace, "/steps/0/output")},
        "visibility": {"entry_text": DESCRIPTION, "final_input": trace["data"]["steps"][0]["input"], "complete": True},
        "loading": {"rendered_text": inline_text or (BODY_V2 if "v2" in (asset_id or "") else BODY_V1),
                    "final_input": trace["data"]["steps"][2]["input"], "complete": True},
        "related_actions": [{"step": 3, "label": "读取检索资料", "evidence_ref": log.evidence_ref(trace, "/steps/3")},
                            {"step": 4, "label": "核对人物脚本", "evidence_ref": log.evidence_ref(trace, "/steps/4")}],
        "execution": {"status": "observed", "kind": "script", "complete": True,
                      "evidence_ref": log.evidence_ref(trace, "/steps/4"),
                      "verifier": {"name": "synthetic-script-exit", "version": "fixture-v1"}},
        "local_checks": [{"name": "same-person", "status": "passed", "notice": NOTICE}],
        "task_result": {"em": 1, "notice": NOTICE},
        "usage": {"added_context_tokens": 24, "related_tool_calls": 2, "elapsed_ms": 10, "notice": NOTICE}}
    if asset_id:
        data["asset_id"] = asset_id
    else:
        data["inline_skill"] = {"text": inline_text, "generated_at_step": 0}
        # A model-generated plan is not a read from an external skill directory.
        # Only a verified action is recorded; it has no invented end boundary.
        data["execution"]["kind"] = "verified_step"
        data.pop("visibility")
    if evaluation_id:
        data["evaluation_id"] = evaluation_id
    if trajectory_id:
        data["trajectory_id"] = trajectory_id
    if comparison_ids is not None:
        data["comparison_ids"] = comparison_ids
    return log.skill_instance(prefix + "-instance", data, title="一次技能实例 · " + prefix)


def _training_events(log, prefix):
    mode = MODES[prefix]
    log.method_profile(prefix + "-profile", method_name="Skill · " + mode + " · 合成接入",
        families=["skill"], skill_mode=mode,
        configuration={"external_bank": prefix != "inline", "inline_generated_instances": prefix == "inline",
                       "notice": NOTICE}, title=NOTICE)
    if prefix != "inline":
        _asset(log, prefix, "v1", BODY_V1)
        extra = {"skill_id": prefix + "-answer-format", "version": "v1",
                 "package": {"files": {"SKILL.md": EXTRA_BODY}},
                 "source": {"kind": "synthetic_fixture"}}
        log.skill_asset(prefix + "-asset-extra", extra)
        log.skill_bank(prefix + "-bank-initial", {"asset_ids": [prefix + "-asset-v1", prefix + "-asset-extra"]}, version="v1")
        if prefix in ("joint", "iteration"):
            _asset(log, prefix, "v2", BODY_V2, prefix + "-asset-v1")
            _asset(log, prefix, "v3", BODY_V3, prefix + "-asset-v2")
            log.skill_bank(prefix + "-bank-current", {"asset_ids": [prefix + "-asset-v2", prefix + "-asset-extra"]}, version="v2")
            log.skill_bank(prefix + "-bank-without-cross", {"asset_ids": [prefix + "-asset-extra"]}, version="v2-without-cross")
    body = "先提出一个出生地候选，再用原始资料核对候选。" if prefix == "inline" else BODY_V1
    trace = log.trace(prefix + "-training-trace", _trace(body, inline=prefix == "inline"))
    instance = _instance(log, prefix + "-training", trace,
        inline_text=body if prefix == "inline" else None,
        asset_id=None if prefix == "inline" else prefix + "-asset-v1")
    if prefix == "iteration":
        return None
    artifacts = [instance["id"]]
    credit = [{"instance_id": instance["id"],
        "skill_decision": {"reward": 0.5, "advantage": 0.25, "mask": [1, 1],
                           "source": "synthetic semantic-unit reward", "objective": "policy_gradient"},
        "action_decision": {"reward": 1.0, "advantage": 0.5, "mask": [1, 1, 1],
                            "source": "synthetic task reward", "objective": "policy_gradient"}}]
    if prefix == "fixed":
        routed = copy.deepcopy(instance["data"])
        routed["selection"] = {"source": "retriever", "step": 0, "reason": "合成自动注入；不属于模型主动选择。"}
        routed_instance = log.skill_instance("fixed-retrieved-instance", routed, title="检索器自动注入 · 非模型选择")
        artifacts.append(routed_instance["id"])
        credit.append({"instance_id": routed_instance["id"],
            "skill_decision": {"reward": 0.8, "advantage": 0.2, "mask": [1], "source": "upstream reported attribution"},
            "action_decision": {"reward": 1.0, "advantage": 0.5, "mask": [1, 1, 1], "source": "synthetic task reward"}})
    use = log.training_use(prefix + "-use", {"artifact_ids": artifacts, "status": "sampled",
        "batch_id": prefix + "-batch", "step": 1,
        "credit_assignment": credit,
        "signal": {"objective": "policy_gradient", "original_advantage": 0.5, "effective_advantage": 0.5,
                   "loss_mask_tokens": 5, "loss_weight": 1.0, "loss_active": True}})
    log.training_update(prefix + "-update", {"consumption_ids": [use["id"]], "status": "completed",
        "step": 1, "batch_id": prefix + "-batch", "input_checkpoint_id": prefix + "-cp-0",
        "output_checkpoint_id": prefix + "-cp-1", "update_evidence": NOTICE})
    return {"id": prefix + "-batch", "run_id": prefix + "-skill-run", "step": 1, "demo": True,
        "groups": [{"id": prefix + "-group", "samples": [{"id": prefix + "-sample", "reward": 1,
            "advantage": 0.5, "token_ids": [1, 2, 3], "response_mask": [1, 1, 1],
            "trace_ref": log.evidence_ref(trace), "method_use_ref": {"object_id": use["id"]}}]}]}


def _evaluation(studio, destination, prefix, suffix, cp, bank, correct, protocol):
    evaluation_id = prefix + "-eval-" + suffix
    studio.start_evaluation({"id": evaluation_id, "checkpoint_id": prefix + "-cp-" + str(cp),
        "panel_id": "skill-fixture-panel", "protocol": protocol, "purpose": "checkpoint_eval", "demo": True,
        "comparison": {"allowed_method_changes": ["/bank_content_hash"]}})
    for index, sample in enumerate(SAMPLES):
        answer = sample["answers"][0] if index < correct else "fixture-wrong"
        only_format = bool(bank and "without-cross" in bank)
        body = EXTRA_BODY if only_format else BODY_V2 if bank and "current" in bank else BODY_V1
        actual = _trace(body, answer, with_skill=bool(bank), question=sample["question"],
                        description="answer-format: 统一最终回答的标记。" if only_format else DESCRIPTION)
        if only_format:
            actual["steps"][0]["output"] = "load answer-format skill"
            actual["steps"][4]["tool_call"] = {"name": "verify_evidence", "arguments": {"answer": answer}}
        studio.log_trajectory(evaluation_id, {"id": evaluation_id + "-" + sample["id"],
            "sample_id": sample["id"], "dataset": sample["dataset"], "demo": True,
            "scores": {"em": int(index < correct)}, "raw_final_output": "<answer>" + answer + "</answer>",
            "parsed_answer": answer, "runtime_status": "ok", "termination_reason": "final_answer",
            "usage": {"input_tokens": 60 if bank else 30, "output_tokens": 6}, "steps": actual["steps"]})
    studio.finish_evaluation(evaluation_id)
    state = {"evaluation_id": evaluation_id, "binding_origin": "recorded_execution", "state_complete": True,
        "retrieval_config": {"mode": "explicit-fixture", "version": "fixture-v1"},
        "independent": True, "autonomous": True, "assessment_families": ["skill"], "notice": NOTICE}
    state.update({"bank_id": bank} if bank else {"skills_disabled": True})
    with MethodEvidenceRecorder(data_dir=destination, scope={"run_id": prefix + "-skill-run"}, demo=True) as log:
        log.evaluation_binding("bind-" + evaluation_id, state, usage="validation")
    return evaluation_id


def _comparisons_and_lifecycle(destination, prefix, evaluation_ids):
    checkpoint = prefix + ("-cp-0" if prefix == "iteration" else "-cp-1")
    with MethodEvidenceRecorder(data_dir=destination, scope={"run_id": prefix + "-skill-run"}, demo=True) as log:
        comparison_ids = []
        if prefix == "joint":
            cells = [{"row_id": row, "column_id": column, "evaluation_id": evaluation_ids[(row, column)]}
                     for row in ("initial", "current") for column in ("off", "initial_bank", "current_bank")]
            log.skill_comparison("joint-matrix", {"comparison_type": "cross_matrix", "initial_checkpoint_id": "joint-cp-0",
                "current_checkpoint_id": "joint-cp-1", "initial_bank_id": "joint-bank-initial",
                "current_bank_id": "joint-bank-current", "cells": cells}, usage="validation")
            log.skill_comparison("joint-matrix-partial", {"comparison_type": "cross_matrix", "initial_checkpoint_id": "joint-cp-0",
                "current_checkpoint_id": "joint-cp-1", "initial_bank_id": "joint-bank-initial",
                "current_bank_id": "joint-bank-current", "cells": cells[:2]}, usage="validation",
                title="未测格示例 · 同一已声明评测子集")
            log.skill_comparison("joint-ablation", {"comparison_type": "skill_ablation", "checkpoint_id": "joint-cp-1",
                "skill_asset_id": "joint-asset-v2", "with_evaluation_id": evaluation_ids[("current", "current_bank")],
                "without_evaluation_id": evaluation_ids[("current", "without_cross")]}, usage="validation")
            comparison_ids = ["joint-matrix", "joint-ablation"]
            trace = log.trace("joint-evaluation-trace", _trace(BODY_V2))
            evaluation_id = evaluation_ids[("current", "current_bank")]
            _instance(log, "joint-evaluation", trace, asset_id="joint-asset-v2", evaluation_id=evaluation_id,
                      trajectory_id=evaluation_id + "-fixture-q1", comparison_ids=["joint-ablation"])
        trace = log.trace(prefix + "-source-failure", _trace(BODY_V1, "海港"), usage="evolution")
        for suffix, asset, event, decision, step, reason in [
            ("created", "v2", "create", "proposed", 1, "根据合成失败轨迹提出交叉核对版本。"),
            ("accepted", "v2", "review", "accepted", 2, "合成开发对照支持使用 v2；不是实际研究结论。"),
            ("rejected", "v3", "review", "rejected", 2, "候选硬编码题目答案，不进入已固定的评测技能库。"),
            ("retained", "v2", "audit", "retain", 3, "继续观察当前版本在该运行中的表现。"),
            ("retired", "extra", "audit", "retire", 3, "仅本运行中的退役事件，历史资产与早先评测快照继续可查。")]:
            data = {"asset_id": prefix + "-asset-" + asset, "event": event, "decision": decision,
                "step": step, "checkpoint_id": checkpoint, "reason": reason,
                "feedback_role": "development_feedback", "source_refs": [log.evidence_ref(trace)],
                "effective_scope": {"run_id": prefix + "-skill-run"}}
            if suffix == "accepted":
                data["comparison_ids"] = comparison_ids
            log.skill_evolution(prefix + "-lifecycle-" + suffix, data, usage="evolution")


def build_example(data_dir: str | Path) -> dict:
    destination = Path(data_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError("Choose a NEW directory; refusing to modify: " + str(destination))
    destination.mkdir(parents=True, exist_ok=False)
    artifacts = destination / "example-artifacts"
    artifacts.mkdir()
    protocol = json.loads((SOURCE_ROOT / "examples/protocol_v2.synthetic.json").read_text(encoding="utf-8"))
    protocol.update(harness_version="skill-fixture-h1", harness_fingerprint=MethodEvidenceRecorder.content_sha256("skill-fixture-h1"),
                    panel_scope="Synthetic fixture only; no model results or official test data")
    result = {"synthetic_only": True, "notice": NOTICE, "data_dir": str(destination), "modes": MODES,
              "events": {}, "evaluations": [], "comparisons": ["joint-matrix", "joint-matrix-partial", "joint-ablation"], "packages": []}
    with StudioRecorder(destination) as studio:
        studio.store.register_project({"id": "skill-fixtures", "name": "Skill 生命周期合成接入", "demo": True})
        studio.store.register_panel({"id": "skill-fixture-panel", "name": "两题合成协议", "version": "fixture-v1",
            "split": "probe", "demo": True, "samples": SAMPLES})
        for prefix in MODES:
            experiment_id, run_id = prefix + "-skill-experiment", prefix + "-skill-run"
            studio.store.register_experiment({"id": experiment_id, "project_id": "skill-fixtures",
                "name": ("无外部库 SkillCredit" if prefix == "inline" else MODES[prefix]) + " · 合成", "demo": True})
            studio.register_experiment_profile(experiment_id=experiment_id, track="main", kind="agent_iteration",
                question="区分资产、运行实例与生命周期", change="synthetic Skill tracking fixture", constants=NOTICE)
            studio.store.register_run({"id": run_id, "experiment_id": experiment_id, "run_type": "training",
                "name": prefix + " Skill 合成日志", "base_model": "synthetic-no-model", "model_revision": "fixture-v1",
                "algorithm": "fixture only; no optimizer executed", "demo": True, "status": "completed", "seed": 42})
            for step in ((0,) if prefix == "iteration" else (0, 1)):
                studio.store.register_checkpoint({"id": prefix + "-cp-" + str(step), "run_id": run_id, "step": step,
                    "artifact_uri": f"synthetic://no-weights/{prefix}/{step}", "demo": True,
                    "weights_sha256": MethodEvidenceRecorder.content_sha256(f"skill-fixture:{prefix}:{step}")})
            journal = artifacts / (prefix + "-skill-events.jsonl")
            with MethodEvidenceRecorder(journal_path=journal, scope={"run_id": run_id}, demo=True, usage="training",
                    provenance={"producer": "skill_tracking_example.py", "notice": NOTICE}) as log:
                batch = _training_events(log, prefix)
                bundle = log.export_bundle(artifacts / (prefix + "-skill-bundle.json"))
            imported = studio.import_method_evidence(journal)
            assert imported["count"] == len(bundle["objects"])
            result["events"][prefix] = imported["count"]
            if batch:
                studio.log_training_batch(batch)
            evaluations = {}
            if prefix == "joint":
                scores = {("initial", "off"): 0, ("initial", "initial_bank"): 1, ("initial", "current_bank"): 1,
                          ("current", "off"): 1, ("current", "initial_bank"): 1, ("current", "current_bank"): 2}
                for (row, column), correct in scores.items():
                    bank = None if column == "off" else prefix + ("-bank-initial" if column == "initial_bank" else "-bank-current")
                    evaluations[(row, column)] = _evaluation(studio, destination, prefix, row + "-" + column.replace("_", "-"),
                        0 if row == "initial" else 1, bank, correct, protocol)
                evaluations[("current", "without_cross")] = _evaluation(studio, destination, prefix,
                    "current-without-cross", 1, "joint-bank-without-cross", 1, protocol)
            elif prefix == "iteration":
                for column, correct in (("initial", 1), ("current", 2)):
                    evaluations[("initial", column)] = _evaluation(studio, destination, prefix, column + "-bank", 0,
                        prefix + "-bank-" + column, correct, protocol)
            elif prefix == "fixed":
                for step in (0, 1):
                    evaluations[(str(step), "initial_bank")] = _evaluation(studio, destination, prefix, "policy-" + str(step), step,
                        "fixed-bank-initial", step, protocol)
            else:
                evaluations[("current", "off")] = _evaluation(studio, destination, prefix, "autonomous", 1, None, 1, protocol)
            result["evaluations"].extend(evaluations.values())
            if prefix in ("joint", "iteration"):
                _comparisons_and_lifecycle(destination, prefix, evaluations)

        audit = {"skill_id": "cross-check-source-id", "perf_with": 0.5, "perf_without": 0.25,
                 "utility": 0.25, "utility_ema": 0.2, "usage_count": 8, "low_utility_streak": 0,
                 "failure_bucket_size": 2, "status": "active", "decision": "retain"}
        slim = {"global_step": 4, "general_group": None, "general_group_skipped_reason": "not_routed",
                "audited_task_specific_skills": [audit], "audited_skills": [audit],
                "retained_task_specific_skills": [audit["skill_id"]], "retired_task_specific_skills": [],
                "expanded_task_specific_skills": [], "notice": NOTICE}
        slim_path = artifacts / "slim_lifecycle_step4.json"
        slim_path.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["slim_import"] = studio.import_slim_lifecycle(slim_path, scope={"run_id": "joint-skill-run"},
            subject_map={audit["skill_id"]: {"asset_id": "joint-asset-v2"}}, checkpoint_id="joint-cp-1",
            record_prefix="joint-slim", demo=True)
        grading = {"expectations": [{"text": "检查了原始资料", "passed": True, "evidence": NOTICE}],
                   "summary": {"passed": 1, "failed": 0, "total": 1, "pass_rate": 1.0}}
        timing = {"total_duration_seconds": 1.2, "total_tokens": 66, "notice": NOTICE}
        report_paths = []
        for name, value in (("grading.json", grading), ("timing.json", timing)):
            path = artifacts / name
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            report_paths.append(path)
        result["creator_import"] = studio.import_skill_creator_results(report_paths,
            scope={"run_id": "iteration-skill-run"}, subject={"asset_id": "iteration-asset-v2"},
            checkpoint_id="iteration-cp-0", feedback_role="development_feedback", record_prefix="iteration-creator", demo=True)

    init_source(destination, "synthetic-skill-example", "cpu-only-fixture")
    for prefix in MODES:
        package = export_run(destination, prefix + "-skill-run", artifacts / (prefix + "-skill-run.zip"))
        result["packages"].append(package["path"])
    (destination / "example-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="显式指定一个尚不存在的新目录")
    args = parser.parse_args()
    try:
        result = build_example(args.data_dir)
    except FileExistsError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
