"""CPU-only OPD integration fixtures. ALL evidence and outcomes are synthetic.

    python examples/opd_tracking_example.py --data-dir /tmp/new-opd-demo

The destination must not exist. This runs no student/teacher, search environment,
optimizer or scoring service. It exercises evidence intake and comparison using
explicit fixture observations, and never writes into the bundled real data.
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


NOTICE = "合成接入示例：所有轨迹、分数、teacher 续跑和更新事件均为 fixture，没有执行模型或训练。"
PREFIX = "opd-v095"
EXPERIMENT = PREFIX + "-experiment"
RUN = PREFIX + "-run"
CP0, CP1 = PREFIX + "-cp-0", PREFIX + "-cp-1"
SCOPE = {"run_id": RUN}
H = MethodEvidenceRecorder.content_sha256
STUDENT_CONTEXT = {"teacher_hints": False, "peer_answers": False, "future_observations": False,
    "groundtruth_visible": False, "privileged_information": []}
TEACHER_CONTEXT = {"teacher_hints": False, "peer_answers": False, "future_observations": True,
    "groundtruth_visible": False,
    "privileged_information": [{"kind": "future_observation", "text": "[合成] 林舟出生于银湾。"}],
    "construction": "hindsight_self_teacher", "model_revision": "fixture-teacher-v1"}
SAMPLES = [
    {"id": "fixture-q1", "dataset": "FixtureQA", "question": "[合成] 蓝色车站导演出生在哪里？", "answers": ["银湾"]},
    {"id": "fixture-q2", "dataset": "FixtureQA", "question": "[合成] 车站是什么颜色？", "answers": ["蓝色"]},
    {"id": "fixture-q3", "dataset": "FixtureQA", "question": "[合成] 档案的作者是谁？", "answers": ["叶岚"]},
]


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _snapshot():
    return {"history": [{"role": "user", "content": SAMPLES[0]["question"]},
                        {"role": "assistant", "content": "<search>蓝色车站 导演</search>"},
                        {"role": "tool", "content": "[合成] 蓝色车站由林舟执导；同名人物需要核对。"}],
        "environment": {"corpus_version": "fixture-only-v1", "index_sha256": H("fixture-index")},
        "tool_state": {"searches_used": 2, "available_tools": ["search"]},
        "remaining_budget": {"tool_calls": 2, "output_tokens": 1024},
        "harness": {"version": "fixture-h1", "fingerprint": H("fixture-h1")}}


def _start(snapshot):
    return {"snapshot_hash": H(snapshot), "state_alignment": "restored",
        **{key + "_hash": H(snapshot[key]) for key in ("history", "environment", "tool_state", "harness")},
        "remaining_budget": copy.deepcopy(snapshot["remaining_budget"]),
        "decoding": {"temperature": 0, "top_p": 1}, "seed": 42}


def _attempt(log, suffix, role, snapshot, success, *, mismatch=False):
    context = copy.deepcopy(TEACHER_CONTEXT if role == "teacher" else STUDENT_CONTEXT)
    start = _start(snapshot)
    if mismatch:
        start["remaining_budget"]["tool_calls"] = 3
    verification = {"status": "passed" if success else "failed", "method": "scorer",
        "verifier": {"name": "synthetic_casefold_em", "version": "fixture-v1"}, "notice": NOTICE}
    trace = log.trace(PREFIX + "-trace-" + suffix, {"notice": NOTICE, "demo": True,
        "continuation_start": start, "context": context, "verification": verification,
        "steps": [{"index": 3, "kind": "model", "output": "<search>林舟 导演 出生地</search>"},
                  {"index": 4, "kind": "tool", "tool_call": {"name": "search", "arguments": {"query": "林舟 导演 出生地"}},
                   "tool_response": "[合成] 林舟出生于银湾。"},
                  {"index": 5, "kind": "model", "output": "<answer>" + ("银湾" if success else "海港") + "</answer>"}]})
    result = {"role": role, "attempt_id": PREFIX + "-attempt-" + suffix, "attempt_index": 1,
        "checkpoint_id": CP0 if role == "student_before" else CP1,
        "status": "completed", "start": start, "start_ref": log.evidence_ref(trace, "/continuation_start"),
        "trace_ref": log.evidence_ref(trace), "verification": verification,
        "verification_ref": log.evidence_ref(trace, "/verification"), "context": context,
        "cost": {"input_tokens": 80, "output_tokens": 12, "tool_calls": 1, "elapsed_ms": 100, "notice": NOTICE}}
    return result


def _group(log, name, outcomes, advantages):
    samples = []
    for i, (passed, advantage) in enumerate(zip(outcomes, advantages)):
        samples.append({"rollout_id": PREFIX + "-" + name + "-rollout-" + str(i),
            "verification": {"status": "passed" if passed else "failed", "method": "scorer",
                "verifier": {"name": "fixture-task-verifier", "version": "fixture-v1"}},
            "original_advantage": advantage, "effective_advantage": advantage,
            "teacher_supervision": i == 0, "task_reward": int(passed)})
    return log.opd_diagnostic(PREFIX + "-group-" + name, {"diagnostic_type": "group",
        "context": "training_on_policy", "training_step": 1, "student_checkpoint_id": CP0,
        "group_id": PREFIX + "-group-" + name, "label": name + " · 完整合成 reward group",
        "group": {"expected_size": len(samples), "samples_complete": True, "samples": samples}, "notice": NOTICE})


def _training(log):
    log.opd_configuration(PREFIX + "-profile", native_config={"distillation": {"enabled": True,
        "distillation_loss": {"use_task_rewards": True, "use_policy_gradient": False,
            "distillation_loss_coef": 0.2, "loss_mode": "kl"}}},
        teacher={"construction": "hindsight_self_teacher", "model": "synthetic-no-model",
                 "revision": "fixture-teacher-v1", "context": TEACHER_CONTEXT},
        method_name="OPD + RLVR · 合成接入", title=NOTICE)
    snapshot = _snapshot()
    original_trace = log.trace(PREFIX + "-original-failure-trace", {"notice": NOTICE,
        "steps": snapshot["history"], "raw_final_output": "<answer>海港</answer>"})
    for suffix in ("repair", "rescore"):
        log.opd_failure(PREFIX + "-failure-" + suffix, {"sample_id": "fixture-q1",
            "student_checkpoint_id": CP0, "failure_step": 2, "failure_category": "entity_disambiguation",
            "eligible": True, "attempt_budget": 1, "original_advantage": 0.0,
            "sampling_protocol": "synthetic-frozen-failures-v1", "trace_ref": log.evidence_ref(original_trace),
            "state": {"status": "captured", "snapshot": snapshot, "snapshot_hash": H(snapshot)}, "notice": NOTICE})
    before = _attempt(log, "before", "student_before", snapshot, False)
    after = _attempt(log, "after", "student_after", snapshot, True)
    mismatched = _attempt(log, "mismatch", "student_after", snapshot, True, mismatch=True)
    teacher_attempt = _attempt(log, "teacher", "teacher", snapshot, True)
    teacher_data = {key: copy.deepcopy(value) for key, value in teacher_attempt.items()
                    if key not in ("role", "attempt_id", "checkpoint_id")}
    teacher_data.update(failure_id=PREFIX + "-failure-repair", teacher_id="fixture-hindsight-teacher",
        teacher_revision="fixture-teacher-v1", mode="continuation", state_alignment="restored",
        teacher_trace_ref=teacher_attempt["trace_ref"])
    teacher = log.opd_teacher(PREFIX + "-teacher-continuation", teacher_data)
    rescored = log.opd_teacher(PREFIX + "-teacher-rescore", {"failure_id": PREFIX + "-failure-rescore",
        "teacher_id": "fixture-self-teacher", "teacher_revision": "fixture-teacher-v1", "mode": "rescore",
        "status": "completed", "context": TEACHER_CONTEXT, "token_ids": [1, 2],
        "student_log_probs": [-1.5, -2.0], "teacher_log_probs": [-1.0, -1.0], "notice": NOTICE})
    accepted = log.opd_supervision(PREFIX + "-supervision-active", {"failure_id": PREFIX + "-failure-repair",
        "teacher_ids": [teacher["id"]], "objective": "distillation", "status": "accepted",
        "original_advantage": 0.0, "effective_advantage": None, "loss_mask": [1, 1],
        "loss_mask_tokens": 2, "loss_weight": 0.2, "loss_active": True,
        "signals": {"task": {"original_advantage": 0.0, "effective_advantage": 0.0, "loss_active": False},
                    "opd": {"objective": "distillation", "raw": [0.5, 1.0], "effective": [0.1, 0.2], "loss_active": True}},
        "reason": "[合成] 全错组结果优势为零，但单独蒸馏项存在信号。"})
    zero = log.opd_supervision(PREFIX + "-supervision-zero", {"failure_id": PREFIX + "-failure-rescore",
        "teacher_ids": [rescored["id"]], "objective": "advantage_reweight", "status": "accepted",
        "original_advantage": 0.0, "effective_advantage": 0.0, "loss_mask": [1, 1],
        "loss_mask_tokens": 2, "loss_weight": 2.0, "loss_active": False,
        "reason": "[合成] 0 × teacher 权重仍为 0，rescore 也没有执行任务。"})
    gated = log.opd_supervision(PREFIX + "-supervision-gated", {"failure_id": PREFIX + "-failure-rescore",
        "teacher_ids": [rescored["id"]], "objective": "distillation", "status": "rejected",
        "original_advantage": -1.0, "effective_advantage": 0.0, "loss_mask": [0, 0],
        "loss_mask_tokens": 0, "loss_weight": 0.2, "loss_active": False,
        "reason": "[合成] 原始 teacher gap 非零，但选择门控后被排除。"})
    pending_data = copy.deepcopy(accepted["data"])
    pending_data["reason"] = "[合成] 已采样该独立监督记录，尚无 completed 更新事件。"
    pending = log.opd_supervision(PREFIX + "-supervision-pending-update", pending_data)
    for name, outcomes, advantages in (("all-wrong", [False, False], [0.0, 0.0]),
                                      ("mixed", [False, True], [-1.0, 1.0]),
                                      ("all-correct", [True, True], [0.0, 0.0])):
        _group(log, name, outcomes, advantages)
    n = 63
    tokens = ["<img src=x onerror=alert('fixture')>" if i == 0 else "tok" + str(i) for i in range(n)]
    log.opd_diagnostic(PREFIX + "-token-diagnostic", {"diagnostic_type": "token", "context": "training_on_policy",
        "training_step": 1, "step": 2, "student_checkpoint_id": CP0,
        "failure_id": PREFIX + "-failure-rescore", "teacher_ids": [rescored["id"]], "supervision_id": gated["id"],
        "tokens": {"tokens": tokens, "token_ids": list(range(100, 100 + n)), "teacher_log_probs": [-1.0] * n,
            "student_log_probs": [-1.5] * n, "gaps": [0.5] * n,
            "task_advantages": [-1.0] * n, "opd_advantages": [0.5] * n, "token_advantages": [0.0] * n,
            "task_mask": [1] * n, "opd_mask": [0] * n},
        "signals": {"task": {"objective": "policy_gradient", "original_advantage": -1.0, "effective_advantage": -1.0},
                    "opd": {"objective": "distillation", "raw": [0.5] * n, "effective": [0.0] * n,
                        "loss_weight": 0.2, "loss_mask_tokens": 0, "loss_active": False}},
        "masks": {"sample": False, "step": False, "token": [0] * n},
        "coverage": {"source_complete": True, "token_count": n}, "notice": NOTICE})
    log.opd_diagnostic(PREFIX + "-no-topk", {"diagnostic_type": "distribution", "context": "training_on_policy",
        "training_step": 1, "student_checkpoint_id": CP0, "source_format": "synthetic-sampled-token-v1",
        "reported_metrics": [{"name": "overlap_ratio", "value": None,
            "definition": "Unavailable: this sample only has sampled-token log probabilities.", "definition_version": "fixture-v1",
            "source": "synthetic fixture", "support": {"kind": "sampled_token", "available": False}}], "notice": NOTICE})
    log.opd_diagnostic(PREFIX + "-cost-diagnostic", {"diagnostic_type": "step", "context": "training_on_policy",
        "training_step": 1, "student_checkpoint_id": CP0, "label": "分开记录 rollout 与 teacher 成本",
        "costs": {"rollout": {"input_tokens": 160, "output_tokens": 24, "tool_calls": 2, "complete": True},
                  "teacher_inference": {"input_tokens": 80, "output_tokens": 12, "complete": True},
                  "teacher_environment": {"tool_calls": 1, "elapsed_ms": 100, "complete": True}},
        "coverage": {"boundary": "synthetic fixture training batch only; no monetary total"}, "notice": NOTICE})
    log.opd_diagnostic(PREFIX + "-signal-relation", {"diagnostic_type": "token", "context": "training_on_policy",
        "training_step": 1, "student_checkpoint_id": CP0, "label": "明确共有 token 上的 task / OPD 信号关系",
        "signal_alignment": {"kind": "token", "complete": True, "positions": [0, 1, 2, 3],
            "position_unit": "response_token_index", "source": "synthetic exact trainer tensor exporter",
            "definition": "Final recorded signed update signals at identical response-token positions; diagnostic correlation only."},
        "tokens": {"tokens": ["查", "询", "核", "对"], "token_ids": [11, 12, 13, 14]},
        "signals": {"task": {"objective": "policy_gradient", "effective": [-1.0, 1.0, -1.0, 1.0],
                    "loss_mask": [1, 1, 1, 1], "loss_weight": 1.0, "source": "synthetic task update signal"},
            "opd": {"objective": "policy_gradient", "effective": [-0.5, 0.5, 0.5, 0.0],
                    "loss_mask": [1, 1, 1, 1], "loss_weight": 0.2, "source": "synthetic OPD update signal"}},
        "notice": NOTICE})
    uses = []
    for suffix, obj, status in (("active", accepted, "sampled"), ("zero", zero, "sampled"),
                               ("gated", gated, "excluded"), ("pending-update", pending, "sampled")):
        data = {"artifact_ids": [obj["id"]], "status": status, "step": 1,
            "batch_id": PREFIX + "-batch-1", "sample_id": PREFIX + "-sample-" + suffix,
            "signal": {key: obj["data"][key] for key in ("objective", "original_advantage", "effective_advantage", "loss_mask_tokens", "loss_weight", "loss_active")}}
        use = log.training_use(PREFIX + "-use-" + suffix, data)
        if suffix in ("active", "zero"):
            uses.append(use["id"])
    log.training_update(PREFIX + "-update", {"consumption_ids": uses, "status": "completed",
        "batch_id": PREFIX + "-batch-1", "step": 1, "input_checkpoint_id": CP0,
        "output_checkpoint_id": CP1, "update_evidence": NOTICE})
    batch = {"id": PREFIX + "-batch-1", "run_id": RUN, "step": 1, "demo": True,
        "groups": [{"id": PREFIX + "-batch-all-wrong", "samples": [
            {"id": PREFIX + "-sample-" + suffix, "reward": 0, "advantage": 0.0,
             "token_ids": [1, 2], "response_mask": [1, 1], "trace_ref": log.evidence_ref(original_trace),
             "supervision_ref": {"object_id": obj["id"]}}
            for suffix, obj in (("active", accepted), ("zero", zero))]}]}
    return {"before": before, "after": after, "mismatched": mismatched,
            "teacher_id": teacher["id"], "rescore_id": rescored["id"], "batch": batch}


def _evaluations(studio, destination):
    protocol = json.loads((SOURCE_ROOT / "examples/protocol_v2.synthetic.json").read_text(encoding="utf-8"))
    protocol.update(harness_version="fixture-h1", harness_fingerprint=H("fixture-h1"),
                    panel_scope="Synthetic OPD fixture; not official dev/test or model results")
    ids = {}
    for suffix, checkpoint, outcomes in (("before", CP0, [False, True, False]), ("after", CP1, [True, False, True])):
        eid = PREFIX + "-eval-" + suffix
        studio.start_evaluation({"id": eid, "checkpoint_id": checkpoint, "panel_id": PREFIX + "-panel",
                                "protocol": protocol, "purpose": "checkpoint_eval", "demo": True})
        for sample, correct in zip(SAMPLES, outcomes):
            answer = sample["answers"][0] if correct else "fixture-wrong"
            studio.log_trajectory(eid, {"id": eid + "-" + sample["id"], "sample_id": sample["id"],
                "dataset": sample["dataset"], "demo": True, "scores": {"em": int(correct)},
                "raw_final_output": "<answer>" + answer + "</answer>", "parsed_answer": answer,
                "runtime_status": "ok", "termination_reason": "final_answer",
                "usage": {"input_tokens": 40, "output_tokens": 6},
                "steps": [{"index": 0, "kind": "model", "input": [{"role": "user", "content": sample["question"]}],
                           "output": "<search>合成档案</search>"},
                          {"index": 1, "kind": "tool", "tool_call": {"name": "search", "arguments": {"query": "合成档案"}},
                           "tool_response": "[合成] 与本题相关的固定档案。"},
                          {"index": 2, "kind": "model", "output": "<answer>" + answer + "</answer>"}]})
        studio.finish_evaluation(eid)
        with MethodEvidenceRecorder(data_dir=destination, scope=SCOPE, demo=True) as log:
            log.evaluation_binding("bind-" + eid, {"evaluation_id": eid, "binding_origin": "recorded_execution",
                "state_complete": True, "retrieval_config": {"mode": "fixture", "version": "fixture-v1"},
                "independent": True, "autonomous": True, "assessment_families": ["opd"],
                "skills_disabled": True, "opd_context": STUDENT_CONTEXT, "notice": NOTICE}, usage="validation")
            if suffix == "before":
                log.opd_diagnostic(PREFIX + "-eval-only-diagnostic", {"diagnostic_type": "step", "context": "evaluation",
                    "evaluation_id": eid, "trajectory_id": eid + "-fixture-q1", "student_checkpoint_id": CP0,
                    "step": 2, "label": "仅绑定训练前评测 · 原始步骤 2", "reported_metrics": [
                        {"name": "fixture_answer_token_count", "value": 6, "unit": "tokens",
                         "definition": "Synthetic observed final-answer token count; not OPD training advantage.",
                         "definition_version": "fixture-v1", "source": "synthetic fixture"}], "notice": NOTICE}, usage="validation")
        ids[suffix] = eid
    return ids


def _native_reports(studio, artifacts):
    paths = {}
    native_ocsd = {"global_step": 1, "batch_idx": 0, "traj_uid": "external-training-trajectory",
        "uid": "external-group", "step_index": 2, "available_future_len": 1,
        "future_obs_text_1": "[合成] 林舟出生于银湾。", "num_tokens": 3,
        "step_selected": True, "is_nll_selected": False, "advantage": -1.0,
        "step_delta_F": 0.8, "step_delta_A": 0.3, "step_residual_e": 0.5, "step_q": 0.24,
        "step_abs_delta_F": 0.8, "step_abs_delta_A": 0.3, "step_abs_residual_e": 0.5,
        "step_abs_q": 0.24, "mean_old_log_prob": -1.8, "mean_full_log_prob": -1.0, "mean_abl_log_prob": -1.5}
    paths["ocsd"] = artifacts / "ocsd_step_rows.jsonl"
    _write_jsonl(paths["ocsd"], [native_ocsd])
    studio.import_ocsd_diagnostics(paths["ocsd"], scope=SCOPE, student_checkpoint_id=CP0,
        record_prefix=PREFIX + "-native-ocsd", source_population_size=8, source_complete=False, demo=True)
    native_agent = {"global_step": 1, "sample_idx": 0, "turn_step": 2, "traj_uid": "external-training-trajectory",
        "episode_reward": 0.0, "episode_length": 3.0, "seq_advantage": -1.0,
        "tokens": ["核", "对", "来源"], "token_ids": [31, 32, 33], "gaps": [0.5, 0.0, -0.5],
        "teacher_log_probs": [-1.0, -1.0, -2.0], "student_log_probs": [-1.5, -1.0, -1.5],
        "token_advantages": [0.2, 0.0, -0.2]}
    paths["agentopsd"] = artifacts / "agentopsd_step_1.jsonl"
    _write_jsonl(paths["agentopsd"], [native_agent])
    studio.import_agentopsd_debug(paths["agentopsd"], scope=SCOPE, student_checkpoint_id=CP0,
        identity_map={0: {"failure_id": PREFIX + "-failure-rescore"}}, record_prefix=PREFIX + "-native-agentopsd", demo=True)
    paths["verl"] = artifacts / "verl_validation_1.jsonl"
    _write_jsonl(paths["verl"], [{"input": SAMPLES[0]["question"], "output": "<answer>银湾</answer>",
        "gts": ["银湾"], "score": 1.0, "step": 1, "source_note": NOTICE}])
    studio.import_verl_generations(paths["verl"], scope=SCOPE, purpose="validation",
        student_checkpoint_id=CP1, record_prefix=PREFIX + "-native-verl", demo=True)
    paths["zero_overlap"] = artifacts / "verl_zero_overlap_metrics.json"
    _write_json(paths["zero_overlap"], {"actor/distillation/overlap_ratio": 0.0,
        "actor/distillation/overlap_token_advantage": 0.0, "actor/distillation/student_mass": 0.4,
        "actor/distillation/teacher_mass": 0.8})
    studio.import_opd_metrics(paths["zero_overlap"], scope=SCOPE, source_family="verl", training_step=1,
        student_checkpoint_id=CP0, record_prefix=PREFIX + "-zero-overlap", support={
            "actor/distillation/overlap_ratio": {"available": True, "count": 63, "total": 63, "k": 20},
            "actor/distillation/overlap_token_advantage": {"available": False, "count": 0, "total": 63, "k": 20}}, demo=True)
    paths["uniopsd"] = artifacts / "uniopsd_credit_metrics.json"
    _write_json(paths["uniopsd"], {"rlsd/opsd_rescue_frac": 0.25, "rlsd/opsd_rescue_ceiling": 0.5,
        "rlsd/fused_step_zero_row_frac": 0.5, "rlsd/c_mean": 0.6})
    studio.import_opd_metrics(paths["uniopsd"], scope=SCOPE, source_family="uniopsd", training_step=1,
        student_checkpoint_id=CP0, record_prefix=PREFIX + "-native-uniopsd", demo=True)
    return {key: str(value) for key, value in paths.items()}


def build_example(data_dir):
    destination = Path(data_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError("Choose a NEW directory; refusing to modify: " + str(destination))
    destination.mkdir(parents=True, exist_ok=False)
    artifacts = destination / "example-artifacts"
    artifacts.mkdir()
    with StudioRecorder(destination) as studio:
        studio.store.register_project({"id": PREFIX + "-project", "name": "OPD 接入合成示例", "demo": True})
        studio.store.register_experiment({"id": EXPERIMENT, "project_id": PREFIX + "-project",
            "name": "OPD · 信号、同状态续跑与自主结果", "demo": True})
        studio.register_experiment_profile(experiment_id=EXPERIMENT, track="main", kind="agent_iteration",
            question="哪些信号进入更新，teacher 能否续跑，student 能否自主改善？", change=NOTICE, constants=NOTICE)
        studio.store.register_run({"id": RUN, "experiment_id": EXPERIMENT, "run_type": "training",
            "name": "OPD + RLVR 合成日志", "base_model": "synthetic-no-model", "model_revision": "fixture-v1",
            "algorithm": "synthetic native OPD logging; no optimizer executed", "demo": True, "seed": 42, "status": "completed"})
        for i, checkpoint in enumerate((CP0, CP1)):
            studio.store.register_checkpoint({"id": checkpoint, "run_id": RUN, "step": i,
                "artifact_uri": "synthetic://no-weights/" + checkpoint, "weights_sha256": H(checkpoint), "demo": True})
        studio.store.register_panel({"id": PREFIX + "-panel", "name": "三题合成面板 · 非真实成绩",
            "version": "fixture-v1", "split": "probe", "demo": True, "samples": SAMPLES})
        journal = artifacts / "opd-evidence.jsonl"
        with MethodEvidenceRecorder(journal_path=journal, scope=SCOPE, usage="training", demo=True,
                provenance={"producer": "opd_tracking_example.py", "version": "fixture-v1", "notice": NOTICE}) as log:
            training = _training(log)
            log.export_bundle(artifacts / "opd-evidence.json")
        studio.import_method_evidence(journal)
        studio.log_training_batch(training["batch"])
        evaluations = _evaluations(studio, destination)
        native = _native_reports(studio, artifacts)
        cohort = {"id": PREFIX + "-frozen-failures", "usage": "development", "frozen": True,
                  "feedback_used": True, "isolation_status": "unverified"}
        base = {"baseline_checkpoint_id": CP0, "current_checkpoint_id": CP1, "attempts_per_arm": 1,
                "cohort": cohort, "notice": NOTICE}
        with MethodEvidenceRecorder(data_dir=destination, scope=SCOPE, demo=True) as log:
            log.opd_recovery(PREFIX + "-recovery-full", {**base, "cases": [{"failure_id": PREFIX + "-failure-repair",
                "attempts": [training["before"], {"role": "teacher", "teacher_record_id": training["teacher_id"]}, training["after"]]}],
                "full_task": {"before_evaluation_id": evaluations["before"], "after_evaluation_id": evaluations["after"]}}, usage="validation")
            log.opd_recovery(PREFIX + "-recovery-missing", {**base, "cases": [{"failure_id": PREFIX + "-failure-rescore",
                "attempts": [{"role": "teacher", "teacher_record_id": training["rescore_id"]}]}]},
                title="未续跑 · rescore 不算修复", usage="validation")
            log.opd_recovery(PREFIX + "-recovery-mismatch", {**base, "cases": [{"failure_id": PREFIX + "-failure-repair",
                "attempts": [training["before"], {"role": "teacher", "teacher_record_id": training["teacher_id"]}, training["mismatched"]]}]},
                title="剩余搜索预算不同 · 不可比", usage="validation")
        # Compute only fixture comparisons. These values are not model results.
        recovery = {name: studio.methods.opd_recovery(PREFIX + "-recovery-" + name)
                    for name in ("full", "missing", "mismatch")}
    init_source(destination, "synthetic-opd-example", "cpu-only-fixture")
    package = export_run(destination, RUN, artifacts / "opd-run.zip")
    _, payload, _ = read_package(package["path"])
    assert all(item["demo"] for item in payload["methods"])
    result = {"synthetic_only": True, "notice": NOTICE, "data_dir": str(destination),
        "experiment_id": EXPERIMENT, "run_id": RUN, "evaluations": evaluations,
        "recovery_ids": {name: PREFIX + "-recovery-" + name for name in recovery},
        "recovery_status": {name: value["status"] for name, value in recovery.items()},
        "native_sources": native, "packages": [package["path"]], "event_count": len(payload["methods"]),
        "token_diagnostic_id": PREFIX + "-token-diagnostic", "runtime_diagnostic_id": PREFIX + "-eval-only-diagnostic",
        "fixture_only_full_task": recovery["full"]["full_task"]}
    _write_json(destination / "example-verification.json", result)
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
