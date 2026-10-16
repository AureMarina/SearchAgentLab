"""CPU-only entropy evidence demo. Every value and trajectory is synthetic.

    python examples/entropy_tracking_example.py --data-dir /tmp/new-entropy-demo

The destination must not exist. No model, logits, tokenizer, retriever, teacher
or optimizer is run. This exercises saved metadata, controlled comparisons and
result transport without touching bundled real experiments.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from searchagent_lab.methods.entropy_adapters import native_entropy_definition
from searchagent_lab.methods.recorder import MethodEvidenceRecorder
from searchagent_lab.methods.schema import SCHEMA
from searchagent_lab.studio.sdk import StudioRecorder
from searchagent_lab.team.package import export_run, init_source, read_package


PREFIX = "entropy-v096"
EXPERIMENT = PREFIX + "-experiment"
RUN = PREFIX + "-run"
CP0, CP1 = PREFIX + "-cp-0", PREFIX + "-cp-1"
SCOPE = {"run_id": RUN}
NOTICE = "合成接入示例：所有熵、轨迹、分数、选择与更新均为 fixture，没有执行模型或训练。"
H = MethodEvidenceRecorder.content_sha256
TOKENIZER = {"name": "synthetic-tokenizer", "revision": "fixture-v1", "vocab_sha256": H("synthetic-vocab-v1")}
ENTROPY_CONTEXT = {"runtime_intervention": "none", "extra_resources": {
    "branching": False, "summary_model": False, "teacher": False}, "training_only_guidance_removed": True}
SAMPLES = [
    {"id": "fixture-q1", "dataset": "FixtureQA", "question": "[合成] 蓝色车站导演出生在哪里？", "answers": ["银湾"]},
    {"id": "fixture-q2", "dataset": "FixtureQA", "question": "[合成] 车站是什么颜色？", "answers": ["蓝色"]},
    {"id": "fixture-q3", "dataset": "FixtureQA", "question": "[合成] 档案作者是谁？", "answers": ["叶岚"]},
    {"id": "fixture-q4", "dataset": "FixtureQA", "question": "[合成] 档案建立于哪一年？", "answers": ["1999"]},
    {"id": "fixture-q5", "dataset": "FixtureQA", "question": "[合成] 哪条河流经过车站？", "answers": ["银河"]},
    {"id": "fixture-q6", "dataset": "FixtureQA", "question": "[合成] 谁修建了图书馆？", "answers": ["林舟"]},
]
COHORT = {"id": PREFIX + "-fixed-dev", "usage": "development", "frozen": True,
    "feedback_used": True, "isolation_status": "unverified"}


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _token_definition(*, scoring_temperature=1.0, sampling_temperature=0.0, tokenizer=None):
    return {"name": "完整词表熵 · 固定模型 token 位置", "estimator": "full_vocab_token_entropy",
        "quantity_kind": "entropy", "unit": "nats", "formula": "-sum_v p(v|prefix,T) * ln p(v|prefix,T)",
        "support": {"kind": "full_vocabulary", "size": 128}, "top_k": "disabled", "window": "all",
        "scoring_temperature": scoring_temperature, "sampling_temperature": sampling_temperature,
        "log_base": "e", "normalization": "none",
        "token_scope": {"mask_definition": "explicit model-generated positions, excluding environment observations", "segment_type": "model_tokens"},
        "tokenizer": copy.deepcopy(tokenizer or TOKENIZER), "aggregation": {"name": "masked_token_mean"},
        "distribution": {"kind": "teacher_forced_full_vocabulary", "top_p": "disabled"},
        "implementation": {"name": "synthetic entropy exporter", "version": "fixture-v1"}, "notice": NOTICE}


def _proxy_definition(name, estimator, *, unit="score", quantity_kind="proxy", complete_fixture=False):
    data = {"name": name, "estimator": estimator, "quantity_kind": quantity_kind, "unit": unit,
        "formula": "Explicit synthetic scalar for interface verification; not a measured model estimator.",
        "distribution": {"kind": "synthetic_scalar"}, "token_scope": {"segment_type": "none", "mask_definition": "not_applicable"},
        "aggregation": {"name": "none"}, "implementation": {"version": "fixture-v1"}, "notice": NOTICE}
    if complete_fixture:
        data.update(formula="Lookup trajectory_id in the explicit synthetic score table v1; no learned estimator executed.",
            support={"kind":"explicit_synthetic_score_table","count":6}, top_k="disabled", window="all",
            scoring_temperature=1.0, sampling_temperature=0.0, log_base="not_applicable", normalization="none",
            tokenizer=copy.deepcopy(TOKENIZER),
            protocol_notice="These complete protocol fields exercise comparison gates on synthetic inputs only.")
    return data


def _register_and_evaluate(studio, destination):
    studio.store.register_project({"id": PREFIX + "-project", "name": "Entropy 研究接入合成示例", "demo": True})
    studio.store.register_experiment({"id": EXPERIMENT, "project_id": PREFIX + "-project",
        "name": "Entropy · 定义、探索与自主结果", "demo": True})
    studio.register_experiment_profile(experiment_id=EXPERIMENT, track="main", kind="agent_iteration",
        question="熵量是否可比、选择是否生效、同预算下是否帮助自主问答？", change=NOTICE, constants=NOTICE)
    studio.repo.register_presentation({"experiment_id": EXPERIMENT, "family": "entropy", "is_baseline": False})
    runs = {"entropy": RUN, "baseline": RUN + "-baseline", "random": RUN + "-random"}
    checkpoints = {"entropy": CP1, "baseline": PREFIX + "-cp-baseline", "random": PREFIX + "-cp-random"}
    for role, run_id in runs.items():
        studio.store.register_run({"id": run_id, "experiment_id": EXPERIMENT, "run_type": "training",
            "name": role + " · 合成独立训练组", "base_model": "synthetic-no-model", "model_revision": "fixture-v1",
            "algorithm": role + " logging fixture; no training executed", "demo": True, "seed": 42, "status": "completed"})
        for cp, step in ([(CP0, 0), (CP1, 1)] if role == "entropy" else [(checkpoints[role] + "-initial", 0), (checkpoints[role], 1)]):
            studio.store.register_checkpoint({"id": cp, "run_id": run_id, "step": step,
                "artifact_uri": "synthetic://no-weights/" + cp, "weights_sha256": H(CP0) if step == 0 else H(cp), "demo": True})
    studio.store.register_panel({"id": PREFIX + "-panel", "name": "六题合成开发面板 · 非真实成绩",
        "version": "fixture-v1", "split": "probe", "demo": True, "samples": SAMPLES})
    protocol = json.loads((SOURCE_ROOT / "examples/protocol_v2.synthetic.json").read_text(encoding="utf-8"))
    protocol.update(harness_version="fixture-h1", harness_fingerprint=H("fixture-h1"),
                    panel_scope="Synthetic entropy fixture; no official benchmark or generalization claim")
    specs = (("before", RUN, CP0, [0, 1, 0, 1, 0, 1]), ("after", RUN, CP1, [1, 1, 1, 1, 0, 0]),
             ("baseline", runs["baseline"], checkpoints["baseline"], [1, 0, 0, 1, 0, 1]),
             ("random", runs["random"], checkpoints["random"], [1, 1, 0, 1, 0, 0]))
    evaluations = {}
    for suffix, run_id, checkpoint, outcomes in specs:
        eid = PREFIX + "-eval-" + suffix
        studio.start_evaluation({"id": eid, "checkpoint_id": checkpoint, "panel_id": PREFIX + "-panel",
            "protocol": protocol, "purpose": "checkpoint_eval", "demo": True})
        for sample, correct in zip(SAMPLES, outcomes):
            answer = sample["answers"][0] if correct else "fixture-wrong"
            studio.log_trajectory(eid, {"id": eid + "-" + sample["id"], "sample_id": sample["id"],
                "dataset": sample["dataset"], "demo": True, "scores": {"em": correct},
                "raw_final_output": "<answer>" + answer + "</answer>", "parsed_answer": answer,
                "runtime_status": "ok", "termination_reason": "final_answer",
                "usage": {"input_tokens": 50, "output_tokens": 8},
                "steps": [{"index": 0, "kind": "model", "input": [{"role": "user", "content": sample["question"]}],
                    "output": "<search>合成档案</search>"},
                    {"index": 1, "kind": "tool", "tool_call": {"name": "search", "arguments": {"query": "合成档案"}},
                    "tool_response": "[合成] 固定来源、固定观察。"},
                    {"index": 2, "kind": "model", "output": "<answer>" + answer + "</answer>"}]})
        studio.finish_evaluation(eid)
        with MethodEvidenceRecorder(data_dir=destination, scope={"run_id": run_id}, demo=True) as log:
            log.evaluation_binding("bind-" + eid, {"evaluation_id": eid, "binding_origin": "recorded_execution",
                "state_complete": True, "retrieval_config": {"mode": "fixture", "version": "fixture-v1"},
                "independent": True, "autonomous": True, "assessment_families": ["entropy"], "skills_disabled": True,
                "entropy_context": ENTROPY_CONTEXT, "notice": NOTICE}, usage="validation")
        evaluations[suffix] = eid
    return runs, checkpoints, evaluations


def _prefix_and_training(log, evaluations):
    log.entropy_configuration(PREFIX + "-profile", native_config={"actor_rollout_ref": {
        "actor": {"entropy_coeff": 0.01, "calculate_entropy": True, "loss_agg_mode": "token-mean"},
        "rollout": {"temperature": 0.8, "top_p": 1, "top_k": -1}}}, method_name="Entropy + RLVR · 合成接入")
    definitions = {"token": PREFIX + "-def-token", "onpolicy": PREFIX + "-def-onpolicy",
        "temp": PREFIX + "-def-temp", "tokenizer": PREFIX + "-def-tokenizer", "new": PREFIX + "-def-new",
        "semantic": PREFIX + "-def-semantic", "budget": PREFIX + "-def-budget"}
    changed_tokenizer = {**TOKENIZER, "revision": "fixture-v2"}
    for key, data in (("token", _token_definition()), ("onpolicy", _token_definition(sampling_temperature=0.8)),
            ("temp", _token_definition(scoring_temperature=2.0)), ("tokenizer", _token_definition(tokenizer=changed_tokenizer)),
            ("new", _proxy_definition("新熵量 · 合成占位定义", "synthetic_new_entropy_quantity", complete_fixture=True)),
            ("semantic", _proxy_definition("语义不确定性 · 合成占位定义", "synthetic_semantic_uncertainty", complete_fixture=True)),
            ("budget", _proxy_definition("预算原始记录 · 值不作为熵", "budget_metadata_record"))):
        log.entropy_definition(definitions[key], data)
    frozen_input = {"messages": [{"role": "user", "content": SAMPLES[0]["question"]},
        {"role": "assistant", "content": "<search>蓝色车站 导演</search>"},
        {"role": "tool", "content": "[合成] 需要核对同名人物。"}],
        "teacher_forced_token_ids": list(range(1, 64)), "prediction_positions": list(range(63))}
    input_trace = log.trace(PREFIX + "-fixed-input", {"frozen_input": frozen_input, "notice": NOTICE})
    frozen = {"id": PREFIX + "-prefix-1", "sha256": H(frozen_input),
        "input_ref": log.evidence_ref(input_trace, "/frozen_input"), "tokenizer": TOKENIZER, "scoring_temperature": 1.0}
    mask = [0 if i in (14, 15, 16) else 1 for i in range(63)]
    tokens = {"tokens": ["[观察]" if not m else ("查" if i < 30 else "答") for i, m in enumerate(mask)],
        "token_ids": list(range(1, 64)), "positions": list(range(63)), "mask": mask,
        "segment_types": ["observation" if not m else ("query" if i < 30 else "answer") for i, m in enumerate(mask)],
        "entropy": [round(1.2 + (i % 5) * 0.1, 6) for i in range(63)],
        "log_probs": [-1.0] * 63, "probabilities": [math.exp(-1)] * 63}
    base = {"context": "fixed_prefix_probe", "prefix_id": frozen["id"], "prefix_hash": frozen["sha256"],
        "frozen_prefix": frozen, "token_count": 63,
        "coverage": {"support_count": sum(mask), "total": 63, "complete": True, "mask_known": True}, "notice": NOTICE}
    fixed_ids = {}
    for suffix, checkpoint, definition, delta in (("before", CP0, "token", 0.0), ("after", CP1, "token", -0.2),
            ("temp-mismatch", CP1, "temp", 0.2), ("tokenizer-mismatch", CP1, "tokenizer", -0.1)):
        observed_tokens = copy.deepcopy(tokens)
        observed_tokens["entropy"] = [round(v + delta, 6) for v in tokens["entropy"]]
        data = {**copy.deepcopy(base), "definition_id": definitions[definition], "checkpoint_id": checkpoint,
            "tokens": observed_tokens, "value": sum(v for v, m in zip(observed_tokens["entropy"], mask) if m) / sum(mask),
            "label": {"before": "相同固定前缀 · 更新前", "after": "相同固定前缀 · 更新后",
                "temp-mismatch": "评分温度不一致 · 不比较 ΔH", "tokenizer-mismatch": "Tokenizer 不一致 · 不比较 ΔH"}[suffix]}
        if definition == "temp":
            data["frozen_prefix"]["scoring_temperature"] = 2.0
        if definition == "tokenizer":
            data["frozen_prefix"]["tokenizer"] = changed_tokenizer
        oid = PREFIX + "-fixed-" + suffix
        log.entropy_observation(oid, data, usage="validation")
        fixed_ids[suffix] = oid
    missing = {**copy.deepcopy(base), "definition_id": definitions["token"], "checkpoint_id": CP1,
        "value": None, "missing_reason": "选中位置没有保存 token entropy；未运行补测。", "token_count": 0,
        "coverage": {"support_count": 0, "complete": False, "mask_known": False}}
    log.entropy_observation(PREFIX + "-fixed-missing", missing, usage="validation")
    fixed_ids["missing"] = PREFIX + "-fixed-missing"
    log.entropy_observation(PREFIX + "-on-policy", {"definition_id": definitions["onpolicy"],
        "context": "training_on_policy", "training_step": 1, "checkpoint_id": CP0, "value": 1.8,
        "token_count": 12, "label": "当前 rollout 状态均值 · 不并入固定前缀曲线",
        "coverage": {"support_count": 12, "source_complete": True}, "notice": NOTICE})
    log.entropy_observation(PREFIX + "-runtime-observation", {"definition_id": definitions["token"],
        "context": "evaluation", "checkpoint_id": CP1, "evaluation_id": evaluations["after"],
        "trajectory_id": evaluations["after"] + "-fixture-q1", "step": 2, "value": 0.6,
        "tokens": {"tokens": ["银", "湾"], "token_ids": [80, 81], "entropy": [0.5, 0.7], "mask": [1, 1],
            "segment_types": ["answer", "answer"]}, "token_count": 2, "label": "仅绑定训练后评测 · 答案步骤 2", "notice": NOTICE}, usage="validation")
    uses = []
    for suffix, effective, loss_mask, status in (("active", [0.2, 0.0, -0.1], [1, 1, 1], "sampled"),
            ("masked", [0.2, 0.0, 0.0], [0, 1, 1], "sampled"), ("pending", None, None, "sampled")):
        intervention = log.entropy_intervention(PREFIX + "-intervention-" + suffix, {
            "definition_id": definitions["onpolicy"], "observation_ids": [PREFIX + "-on-policy"],
            "mode": "training", "intervention_type": "entropy_token_reweighting", "context": "training_on_policy",
            "training_step": 1, "checkpoint_id": CP0,
            "selection": {"policy": "entropy", "selected": True, "selected_count": 12, "candidate_count": 63,
                "selected_positions": list(range(12)), "source": "explicit fixture selection hook"},
            "signals": {"task": {"objective": "policy_gradient", "raw": [-1, 1, 0], "effective": [-1, 1, 0],
                "loss_mask": [1, 1, 1], "loss_weight": 1.0},
                "entropy": {"objective": "entropy_regularization", "raw": [1.8, 1.6, 1.7], "effective": effective,
                    "loss_mask": loss_mask, "raw_weight": 0.01, "effective_weight": 0.01,
                    "source": "actual recorded signed fixture signal; not inferred from scalar entropy"}}, "notice": NOTICE})
        use = log.training_use(PREFIX + "-use-" + suffix, {"artifact_ids": [intervention["id"]],
            "status": status, "batch_id": PREFIX + "-batch-1", "step": 1,
            "signal": {"source": "synthetic sampler callback"}, "notice": NOTICE})
        if suffix != "pending":
            uses.append(use["id"])
    log.training_update(PREFIX + "-update", {"consumption_ids": uses, "status": "completed", "step": 1,
        "batch_id": PREFIX + "-batch-1", "input_checkpoint_id": CP0, "output_checkpoint_id": CP1,
        "update_evidence": NOTICE})
    return definitions, fixed_ids


def _native_reports(studio, artifacts, definitions):
    paths = {}
    paths["verl"] = artifacts / "verl_entropy_metrics.json"
    _write_json(paths["verl"], {"actor/entropy": 1.7, "actor/pg_loss": 0.02, "source_note": NOTICE})
    studio.import_entropy_metrics(paths["verl"], scope=SCOPE, source_family="verl", training_step=1,
        checkpoint_id=CP0, record_prefix=PREFIX + "-native-verl", demo=True)
    paths["ragen"] = artifacts / "ragen_collapse_metrics.jsonl"
    _write_jsonl(paths["ragen"], [{"step": 1, "metrics": {"collapse_first_turn_sample/mi_estimate": -0.03,
        "collapse_first_turn_sample/retrieval_accuracy": 0.25, "collapse_first_turn_sample/retrieval_chance_level": 0.25,
        "collapse_first_turn_sample/conditional_entropy_est": 1.7, "collapse_trajectory_sample/mi_estimate": 0.02}}])
    studio.import_entropy_metrics(paths["ragen"], scope=SCOPE, source_family="ragen", source_format="jsonl",
        checkpoint_id=CP0, record_prefix=PREFIX + "-native-ragen", demo=True)
    for family, value in (("arpo", 0.42), ("ssrl", 0.21)):
        definition_id = PREFIX + "-def-" + family
        with MethodEvidenceRecorder(data_dir=artifacts.parent, scope=SCOPE, demo=True) as log:
            log.entropy_definition(definition_id, native_entropy_definition(family))
        paths[family] = artifacts / (family + "_explicit_hook_rows.jsonl")
        _write_jsonl(paths[family], [{"context": "training_on_policy", "checkpoint_id": CP0, "training_step": 1,
            "value": value, "label": ("有限候选熵贡献 · 不含尾部" if family == "arpo" else "已采样 query token 贡献 · 非 surprisal"),
            "coverage": {"support_count": 4, "source_complete": False,
                "boundary": "Explicit synthetic hook row, not an upstream universal JSON exporter"}, "notice": NOTICE},
            {"context": "training_on_policy", "checkpoint_id": CP0, "training_step": 1, "value": None,
                "missing_reason": "上游内部缺少候选/有效 query 时可能返回 0；本条明确无支持，保留 unknown。",
                "reported_native_value": 0.0, "coverage": {"support_count": 0, "source_complete": False}, "notice": NOTICE}])
        studio.import_entropy_rows(paths[family], scope=SCOPE, definition_id=definition_id,
            source_family=family + "-explicit-hook", record_prefix=PREFIX + "-native-" + family, demo=True)
    paths["seal"] = artifacts / "seal_saved_score_return.json"
    _write_json(paths["seal"], {"input_ids": [10, 11, 12], "token_texts": ["查", "核", "答"],
        "obs_logprob": [-1, -2, -1], "obs_prob": [math.exp(-1), math.exp(-2), math.exp(-1)], "entropy": [1.2, 1.4, 1.0]})
    studio.import_seal_scores(paths["seal"], scope=SCOPE, definition_id=definitions["token"],
        common={"context": "unknown", "checkpoint_id": CP0, "label": "SEAL 返回数组 · 未提供冻结前缀或 mask"},
        record_prefix=PREFIX + "-native-seal", demo=True)
    return {key: str(path) for key, path in paths.items()}


def _uncertainty(log, definitions, evaluations):
    eid = evaluations["after"]
    tids = [eid + "-" + sample["id"] for sample in SAMPLES]
    estimators = []
    for key, label, values in (("token", "普通 token entropy · 合成", [0.2, 0.6, 0.6, 0.3, 0.9, 0.4]),
            ("new", "新熵量 · 合成", [0.1, 0.1, 0.2, 0.2, 0.8, 0.8]),
            ("semantic", "语义不确定性 · 缺一条合成记录", [0.1, 0.2, None, 0.3, 0.7, 0.8])):
        scores = []
        for i, (tid, value) in enumerate(zip(tids, values)):
            data = {"definition_id": definitions[key], "probe_type": "estimator_score", "context": "evaluation",
                "checkpoint_id": CP1, "evaluation_id": eid, "trajectory_id": tid, "value": value,
                "source_label": "uncertainty score only; error target is formal evaluation EM", "notice": NOTICE}
            if value is None:
                data["missing_reason"] = "Synthetic estimator did not export this sample"
            evidence = log.entropy_probe(PREFIX + "-score-" + key + "-" + str(i), data, usage="validation")
            scores.append({"trajectory_id": tid, "value": value, "evidence_ref": log.evidence_ref(evidence)})
        estimators.append({"id": key, "label": label, "definition_id": definitions[key],
            "higher_is_uncertain": True, "scores": scores,
            "probe_protocol": {"sampling_budget": {"num_samples": 1, "max_new_tokens": 128},
                "sample_set_hash": H(sorted(tids))}})
    for suffix, group in (("estimators", estimators[:2]), ("estimators-missing", estimators)):
        log.entropy_comparison(PREFIX + "-compare-" + suffix, {"comparison_type": "uncertainty",
            "evaluation_id": eid, "trajectory_ids": tids, "estimators": group, "cohort": COHORT,
            "label_target": "error_from_formal_em", "ood_status": "not_measured",
            "notice": NOTICE}, title="两种指标共同样本" if suffix == "estimators" else "三种指标缺一条", usage="validation")


def _costs_and_comparisons(destination, runs, checkpoints, evaluations, definitions, artifacts):
    arms, cost_events = [], {}
    training_controls = {"initial_weights_sha256": H(CP0), "training_data_sha256": H("fixture-frozen-training-samples"),
        "trainer_revision": "synthetic-matched-trainer-v1", "optimizer": {"name": "adamw", "learning_rate": 0.0001}, "seed": 42}
    for role in ("baseline", "random", "entropy"):
        run_id, cp = runs[role], checkpoints[role]
        eid = evaluations["after" if role == "entropy" else role]
        selection = {"policy": "none" if role == "baseline" else role, "selected_count": 0 if role == "baseline" else 12,
            "candidate_count": 63, "seed": 42}
        if role == "entropy":
            selection["definition_id"] = definitions["onpolicy"]
        budget = {"training_generated_tokens": 1200, "training_tool_calls": 12, "optimizer_updates": 1}
        with MethodEvidenceRecorder(data_dir=destination, scope={"run_id": run_id}, demo=True) as log:
            did = definitions["budget"] if role == "entropy" else PREFIX + "-def-budget-" + role
            if role != "entropy":
                log.method_profile(PREFIX + "-profile-" + role, method_name=role + " · 合成对照", families=["entropy"],
                    configuration={"entropy": {"training_enabled": role == "random", "selection_policy": selection["policy"], "notice": NOTICE}})
                log.entropy_definition(did, _proxy_definition("预算记录 · " + role, "budget_metadata_record"))
            if role == "random":
                random_definition = PREFIX + "-def-random-selection"
                log.entropy_definition(random_definition, _token_definition(sampling_temperature=0.8))
                intervention = log.entropy_intervention(PREFIX + "-intervention-random", {
                    "definition_id": random_definition, "observation_ids": [], "mode": "training",
                    "intervention_type": "random_token_reweighting_control", "context": "training_on_policy",
                    "training_step": 1, "checkpoint_id": cp + "-initial", "selection": selection,
                    "signals": {"task": {"objective": "policy_gradient", "effective": [-1, 1, 0],
                        "loss_mask": [1, 1, 1], "loss_weight": 1.0},
                        "entropy": {"objective": "entropy_regularization", "raw": [1.6, 1.8, 1.7],
                            "effective": [0.2, 0, -0.1], "loss_mask": [1, 1, 1],
                            "raw_weight": 0.01, "effective_weight": 0.01}}, "notice": NOTICE})
                use = log.training_use(PREFIX + "-use-random", {"artifact_ids": [intervention["id"]],
                    "status": "sampled", "batch_id": PREFIX + "-batch-random-1", "step": 1, "notice": NOTICE})
                log.training_update(PREFIX + "-update-random", {"consumption_ids": [use["id"]],
                    "status": "completed", "step": 1, "batch_id": PREFIX + "-batch-random-1",
                    "input_checkpoint_id": cp + "-initial", "output_checkpoint_id": cp, "update_evidence": NOTICE})
            evidence = log.entropy_probe(PREFIX + "-cost-" + role, {"definition_id": did,
                "probe_type": "budget_record", "context": "evaluation", "value": 0,
                "run_id": run_id, "checkpoint_id": cp, "evaluation_id": eid, "selection": selection,
                "budget": budget, "cost_kind": "observed_fixture_training_cost",
                "training_controls": copy.deepcopy(training_controls),
                "configured_caps": {"max_train_tokens": 1600, "max_tool_calls": 20},
                "cost_completeness": "only the named recorded dimensions; not complete compute or money", "notice": NOTICE})
            cost_events[role] = evidence
            arms.append({"id": role, "checkpoint_id": cp, "evaluation_id": eid, "selection": selection,
                "budget": budget, "cost_ref": log.evidence_ref(evidence),
                "intervention_ids": [PREFIX + "-intervention-active"] if role == "entropy" else
                    [PREFIX + "-intervention-random"] if role == "random" else []})
            if role == "entropy":
                wrong = copy.deepcopy(evidence["data"])
                wrong["budget"]["training_generated_tokens"] = 1600
                cost_events["mismatch"] = log.entropy_probe(PREFIX + "-cost-entropy-more-tokens", wrong)
    # This evidence spans independent runs, so it belongs to the experiment.
    comparisons = []
    with MethodEvidenceRecorder(data_dir=destination, scope={"experiment_id": EXPERIMENT}, demo=True) as log:
        base = {"comparison_type": "intervention", "mode": "training", "evaluation_axis": "checkpoint",
            "budget_keys": ["training_generated_tokens", "training_tool_calls", "optimizer_updates"],
            "arms": arms, "cohort": COHORT, "notice": NOTICE}
        comparisons.append(log.entropy_comparison(PREFIX + "-compare-budget", base, title="同预算三组", usage="validation"))
        wrong = copy.deepcopy(base)
        wrong["arms"][-1]["budget"]["training_generated_tokens"] = 1600
        wrong["arms"][-1]["cost_ref"] = log.evidence_ref(cost_events["mismatch"])
        comparisons.append(log.entropy_comparison(PREFIX + "-compare-budget-mismatch", wrong, title="预算不一致", usage="validation"))
    _write_json(artifacts / "entropy-experiment-comparisons.json", {"method_schema": SCHEMA,
        "scope": {"experiment_id": EXPERIMENT}, "demo": True, "objects": comparisons})
    return [obj["id"] for obj in comparisons]


def register_center_comparisons(hub, comparison_file, source_workspace_id):
    """Rebind this demo's experiment comparisons AFTER importing all run packs.

    This small example handles only the explicit intervention comparison shape.
    It does not change package semantics or automatically join unknown sources.
    Typed identities are mapped using the known source namespace, and evidence
    hashes are refreshed from already imported center objects. Original local
    comparison file bytes remain archived as source evidence.
    """
    from searchagent_lab.core import ValidationError
    from searchagent_lab.team.hub import gid

    path = Path(comparison_file)
    raw = path.read_bytes()
    bundle = json.loads(raw)
    objects = []
    for original in bundle["objects"]:
        if original.get("kind") != "entropy_comparison" or original["data"].get("comparison_type") != "intervention":
            raise ValidationError("This example rebinds explicit intervention comparisons only")
        obj = copy.deepcopy(original)
        obj["id"] = gid(source_workspace_id, "method", original["id"])
        # Hub intentionally shares approved project/experiment/panel plan IDs;
        # only run-owned entities receive the source namespace.
        experiment_id = original["scope"]["experiment_id"]
        hub.store.experiment(experiment_id)
        obj["scope"] = {"experiment_id": experiment_id}
        for arm in obj["data"]["arms"]:
            arm["checkpoint_id"] = gid(source_workspace_id, "checkpoint", arm["checkpoint_id"])
            arm["evaluation_id"] = gid(source_workspace_id, "evaluation", arm["evaluation_id"])
            selection = arm.get("selection", {})
            if selection.get("definition_id"):
                selection["definition_id"] = gid(source_workspace_id, "method", selection["definition_id"])
            arm["intervention_ids"] = [gid(source_workspace_id, "method", oid) for oid in arm.get("intervention_ids", [])]
            ref = arm["cost_ref"]
            if not ref.get("object_id"):
                raise ValidationError("Demo budget evidence requires a typed object_id reference")
            center_id = gid(source_workspace_id, "method", ref["object_id"])
            target = hub.methods.object(center_id)
            if target["kind"] != "entropy_probe" or target["data"].get("probe_type") != "budget_record":
                raise ValidationError("Expected an imported typed budget_record probe")
            arm["cost_ref"] = MethodEvidenceRecorder.evidence_ref(target, ref.get("pointer", ""))
        obj["provenance"] = {"producer": "entropy_tracking_example.register_center_comparisons",
            "source_workspace_id": source_workspace_id, "original_data_sha256": H(original["data"]),
            "normalization": "explicit typed IDs; evidence hashes from imported center objects", "notice": NOTICE}
        objects.append(obj)
    normalized = {"method_schema": SCHEMA, "demo": True, "objects": objects}
    with hub.lock, hub.store.transaction():
        hub.methods.import_bundle(normalized, raw, path.name)
    return {obj["id"]: hub.methods.entropy_comparison(obj["id"])["status"] for obj in objects}


def build_example(data_dir):
    destination = Path(data_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError("Choose a NEW directory; refusing to modify: " + str(destination))
    destination.mkdir(parents=True, exist_ok=False)
    artifacts = destination / "example-artifacts"
    artifacts.mkdir()
    with StudioRecorder(destination) as studio:
        runs, checkpoints, evaluations = _register_and_evaluate(studio, destination)
        journal = artifacts / "entropy-evidence.jsonl"
        with MethodEvidenceRecorder(journal_path=journal, scope=SCOPE, usage="training", demo=True,
                provenance={"producer": "entropy_tracking_example.py", "version": "fixture-v1", "notice": NOTICE}) as log:
            definitions, fixed = _prefix_and_training(log, evaluations)
            for suffix, after in (("prefix", "after"), ("temp-mismatch", "temp-mismatch"),
                    ("tokenizer-mismatch", "tokenizer-mismatch"), ("missing", "missing")):
                log.entropy_comparison(PREFIX + "-compare-" + suffix, {"comparison_type": "fixed_prefix",
                    "pairs": [{"id": "same-input", "before_observation_id": fixed["before"],
                        "after_observation_id": fixed[after]}], "cohort": COHORT, "notice": NOTICE},
                    title={"prefix": "相同固定前缀：更新前后", "temp-mismatch": "评分温度不一致",
                        "tokenizer-mismatch": "Tokenizer 不一致", "missing": "Token 缺测"}[suffix], usage="validation")
            _uncertainty(log, definitions, evaluations)
            log.export_bundle(artifacts / "entropy-evidence.json")
        studio.import_method_evidence(journal)
        native = _native_reports(studio, artifacts, definitions)
        budget_comparisons = _costs_and_comparisons(destination, runs, checkpoints, evaluations, definitions, artifacts)
        comparison_ids = [PREFIX + "-compare-" + suffix for suffix in (
            "prefix", "temp-mismatch", "tokenizer-mismatch", "missing", "estimators", "estimators-missing")] + budget_comparisons
        comparisons = {oid: studio.methods.entropy_comparison(oid) for oid in comparison_ids}
    init_source(destination, "synthetic-entropy-example", "cpu-only-fixture")
    packages, method_count = [], 0
    for role, run_id in runs.items():
        package = export_run(destination, run_id, artifacts / ("entropy-" + role + "-run.zip"))
        _, payload, _ = read_package(package["path"])
        assert all(item["demo"] for item in payload["methods"])
        packages.append(package["path"])
        method_count += len(payload["methods"])
    result = {"synthetic_only": True, "notice": NOTICE, "data_dir": str(destination),
        "experiment_id": EXPERIMENT, "run_id": RUN, "runs": runs, "checkpoints": checkpoints, "evaluations": evaluations,
        "comparison_ids": comparison_ids, "comparison_status": {oid: data.get("status") for oid, data in comparisons.items()},
        "event_count_in_run_packages": method_count, "token_observation_id": PREFIX + "-fixed-before",
        "runtime_observation_id": PREFIX + "-runtime-observation", "native_sources": native, "packages": packages,
        "experiment_comparison_file": str(artifacts / "entropy-experiment-comparisons.json"),
        "fixture_comparisons": comparisons}
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
    compact = {key: value for key, value in result.items() if key != "fixture_comparisons"}
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
