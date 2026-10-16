"""Separate execution evidence, contract fixtures and learning evidence.

These read models never rescore old traces or infer a component's fault from an
answer format symptom. Independent agent runs use the existing Store; contract
fixtures live beside training batches and never enter an EM denominator.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import re
from typing import Any

from ..core import ValidationError, NotFoundError, clone, digest, dumps, ident, now
from ..read_cache import request_scope


PURPOSE_LABELS = {"agent_eval": "Agent 执行评测", "checkpoint_eval": "训练 checkpoint 评测",
                  "harness_check": "Harness 工程检查", "unknown": "旧记录 · 用途未声明"}
USAGE_KEYS = ("latency_ms", "search_calls", "tool_calls", "model_calls", "input_tokens", "output_tokens")
HARNESS_PATHS = ("/harness", "/harness_version", "/harness_fingerprint", "/parser",
                 "/input/prompt_sha256", "/input/stop_conditions", "/context_builder")
COMPARISON_AXES = ("checkpoint", "harness", "method_state")


def _method_change_path(path):
    """Only an explicit bank or retrieval leaf may vary in this comparison.

    Execution protocol, model weights, and other method extensions remain
    fixed. A blanket '/' or '/retrieval_config' exemption is not accepted.
    """
    return isinstance(path, str) and (path == "/bank_content_hash" or
        (path.startswith("/retrieval_config/") and len(path) > len("/retrieval_config/")
         and not path.endswith("/") and not re.search(r"~(?![01])", path)))


def _unknown_method_value(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("", "unknown", "unrecorded", "not_recorded", "未知", "未记录")
    if isinstance(value, dict):
        return any(_unknown_method_value(v) for v in value.values())
    if isinstance(value, list):
        return any(_unknown_method_value(v) for v in value)
    return False


def metric_scope(key):
    name = key.lower()
    if name.startswith(("val/", "validation/", "eval/", "evaluation/", "test/", "val_", "eval_", "test_")):
        return "logged_evaluation"
    if name.startswith(("actor/", "critic/", "train/", "training/", "optimizer/", "learner/", "trainer/")):
        return "recorded_training_metric"
    if name in ("loss", "pg_loss", "kl_loss", "grad_norm", "learning_rate", "advantage", "nonzero_advantage_fraction"):
        return "recorded_training_metric"
    return "unclassified_metric"


def _numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def _known(value):
    if value is None or value == "" or value == {} or value == []:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in ("unknown", "未记录", "未知", "not_recorded")
    if isinstance(value, dict):
        return all(_known(v) for v in value.values())
    if isinstance(value, list):
        return all(_known(v) for v in value)
    return True


def _fingerprint(value):
    """Only recognize a recorded immutable digest, never hash an unknown label."""
    if not isinstance(value, str):
        return None
    return value.rsplit(":", 1)[-1].lower() if re.fullmatch(r"(?:local-sha256:|sha256:)?[0-9a-fA-F]{64}", value) else None


def purpose_of(evaluation):
    value = evaluation.get("purpose")
    explicit = value in ("agent_eval", "checkpoint_eval")
    return {"value": value if explicit else "unknown", "source": "explicit" if explicit else "legacy",
            "label": PURPOSE_LABELS[value if explicit else "unknown"],
            "notes": [] if explicit else ["原日志未登记评测用途；保留原分数，不按名称或 step 补造用途。"]}


def _declared_record_purposes(record):
    if not isinstance(record, dict): return set()
    values = set()
    containers = [record]
    if record.get("type") == "trajectory" and isinstance(record.get("data"), dict): containers.append(record["data"])
    if isinstance(record.get("episode"), dict): containers.append(record["episode"])
    for obj in containers + [obj.get("metadata", {}) for obj in containers]:
        if not isinstance(obj, dict): continue
        for key in ("record_purpose", "purpose", "evaluation_purpose", "origin", "record_kind", "usage_context"):
            value = obj.get(key)
            if isinstance(value, dict): value = value.get("value")
            if value in ("agent_eval", "checkpoint_eval", "harness_check", "train_rollout", "training_rollout", "training", "sft_demonstration"):
                values.add("train_rollout" if value in ("training_rollout", "training") else value)
    return values


def _distribution(values, expected=None):
    valid = [v for v in values if _numeric(v)]
    count = len(valid)
    return {"recorded": count, "count": count, "missing": (expected if expected is not None else len(values)) - count,
            "total": sum(valid) if valid else None, "mean": sum(valid) / count if count else None,
            "min": min(valid) if valid else None, "max": max(valid) if valid else None}


def _rows(counter):
    return [{"name": name, "count": count} for name, count in sorted(counter.items(), key=lambda x: (-x[1], x[0]))]


def _sample_statistics(groups):
    samples = [s for g in groups for s in g.get("samples", [])]
    reward = _distribution([s.get("reward") for s in samples])
    advantages = [s["advantage"] for s in samples if _numeric(s.get("advantage"))]
    complete, mixed, uniform, missing = 0, 0, 0, 0
    for group in groups:
        group_samples = group.get("samples", [])
        values = [s.get("reward") for s in group_samples]
        if values and all(_numeric(v) for v in values):
            complete += 1
            if len(set(values)) > 1:
                mixed += 1
            else:
                uniform += 1
        else:
            missing += 1
    nonzero = sum(v != 0 for v in advantages)
    return {"sample_count": len(samples), "reward": reward if reward["count"] else None,
            "advantage": {"recorded": len(advantages), "nonzero": nonzero,
                          "zero": len(advantages) - nonzero, "missing": len(samples) - len(advantages),
                          "nonzero_fraction": nonzero / len(advantages)} if advantages else None,
            "groups": {"reward_complete": complete, "mixed_reward": mixed, "uniform_reward": uniform,
                       "missing_reward": missing}}


class EvaluationViews:
    def __init__(self, repository):
        self.repo = repository
        self.store = repository.store

    def _evaluations(self, run_id):
        with self.store.lock:
            ids = [r[0] for r in self.store.conn.execute("""SELECT e.id FROM evaluations e
                JOIN checkpoints c ON c.id=e.checkpoint_id WHERE c.run_id=?
                ORDER BY e.created_at DESC,e.rowid DESC""", (run_id,))]
        return [self.store.evaluation(eid) for eid in ids]

    def _training_evidence(self, run):
        with self.store.lock:
            rows = [json.loads(r[0]) for r in self.store.conn.execute("SELECT data FROM metrics WHERE run_id=?", (run["id"],))]
        training_rows = sum(any(metric_scope(k) == "recorded_training_metric" and _numeric(v) for k, v in row.items()) for row in rows)
        evaluation_rows = sum(any(metric_scope(k) == "logged_evaluation" and _numeric(v) for k, v in row.items()) for row in rows)
        with self.repo.lock:
            batch_count = self.repo.conn.execute("SELECT count(*) FROM training_batches WHERE run_id=?", (run["id"],)).fetchone()[0]
        config = run.get("config", {})
        return {"has_training_evidence": bool(training_rows or batch_count), "metric_rows": len(rows),
                "training_metric_rows": training_rows, "logged_evaluation_rows": evaluation_rows,
                "batch_count": batch_count, "has_training_metadata": any(config.get(k) for k in ("training_log", "training_output", "train_run_id"))}

    def _context(self, ev):
        cp, run, protocol = ev["checkpoint"], ev["run"], ev["protocol"]
        harness = protocol.get("harness") if isinstance(protocol.get("harness"), dict) else {}
        panel = ev["panel"]
        with self.store.lock:
            isolation = self.store.latest_audit(panel["id"])
            missing_source = self.store.conn.execute("""SELECT 1 FROM panel_samples WHERE panel_id=? AND
                (lower(trim(coalesce(json_extract(data,'$.metadata.source_id'),''))) IN ('','unknown','未知','未记录') OR
                 lower(trim(coalesce(json_extract(data,'$.metadata.source_revision'),''))) IN ('','unknown','未知','未记录')) LIMIT 1""", (panel["id"],)).fetchone()
            audit = self.store.audit(isolation["id"]) if isolation.get("id") else {}
        eligibility_reasons = []
        isolation_verified = (isolation.get("status") == "no_detected_overlap_in_supplied_scope" and
                              bool(isolation.get("inventory_hash")) and run["project_id"] in isolation.get("projects", []) and
                              isolation.get("exact_conflicts") == 0 and isolation.get("near_candidates") == 0 and
                              audit.get("incomplete_reasons") == [] and bool(audit.get("files")) and
                              all(f.get("declared_complete") is True and _known(f.get("sha256")) for f in audit["files"]))
        if not isolation_verified: eligibility_reasons.append("未完成涵盖本项目的训练与正式测试清单隔离核查")
        if missing_source: eligibility_reasons.append("题目来源 ID / 版本未完整登记")
        if panel.get("split") not in ("dev", "test"): eligibility_reasons.append("题单当前登记为 probe / 诊断集")
        if not ev["readiness"]["complete"] or not ev["protocol_complete"]: eligibility_reasons.append("执行协议未完整登记")
        if purpose_of(ev)["value"] == "unknown": eligibility_reasons.append("评测用途未声明")
        eligible = not eligibility_reasons
        return {"model": {"base_model": cp.get("base_model", run.get("base_model")),
                          "revision": cp.get("model_revision", run.get("model_revision")),
                          "checkpoint_id": cp["id"], "weights_sha256": cp.get("weights_sha256"),
                          "step": cp.get("step"), "kind": cp.get("kind", "legacy_checkpoint")},
                "harness": {"version": harness.get("version", protocol.get("harness_version")),
                            "fingerprint": harness.get("fingerprint", protocol.get("harness_fingerprint")) or _fingerprint(protocol.get("harness_version"))},
                "protocol": {"hash": ev["protocol_hash"], "complete": ev["readiness"]["complete"],
                             "missing": ev["readiness"]["missing"], "recorded": protocol},
                "panel": {"id": panel["id"], "hash": ev["panel_hash"], "split": panel.get("split"),
                          "size": panel["size"], "isolation": isolation,
                          "classification": "isolated_" + panel["split"] if eligible else "fixed_diagnostic",
                          "label": ("隔离验证集" if panel["split"] == "dev" else "隔离测试集") if eligible else "固定题评测 / 诊断",
                          "scope_note": protocol.get("panel_scope", ev.get("note", "")),
                          "isolation_verified": isolation_verified, "source_metadata_complete": not bool(missing_source),
                          "generalization_eligible": eligible, "eligibility_reasons": eligibility_reasons,
                          "notice": "隔离结论限于所提供的版本化清单；不验证预训练是否见过题目，dev 结果也不自动成为正式 test 成绩。"}}

    def checks(self, run_id="", evaluation_id=""):
        clauses, params = [], []
        if run_id:
            clauses.append("run_id=?"); params.append(run_id)
        if evaluation_id:
            clauses.append("evaluation_id=?"); params.append(evaluation_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.repo.lock:
            return [json.loads(r[0]) for r in self.repo.conn.execute(
                "SELECT data FROM harness_checks" + where + " ORDER BY rowid DESC", params)]

    @request_scope
    def summary(self, run_id="", evaluation_id=""):
        run = self.store.run(run_id) if run_id else None
        if not evaluation_id:
            if not run:
                raise ValidationError("run or evaluation is required")
            return {"run_id": run["id"], "evaluation_id": None,
                    "evaluations": [{"id": e["id"], "name": e.get("name", e["id"]), "purpose": purpose_of(e),
                                     "status": e["status"], "checkpoint": e["checkpoint"], "panel_id": e["panel_id"],
                                     "stats": e["stats"]} for e in self._evaluations(run["id"])],
                    "checks": self.checks(run["id"]), "notice": "Harness 工程检查与问答评测分别记录；工程检查通过率不是 EM。"}
        ev = self.store.evaluation(evaluation_id)
        if run and ev["run"]["id"] != run_id:
            raise ValidationError("Evaluation is outside selected run")
        run = ev["run"]
        with self.store.lock:
            traces = [json.loads(r[0]) for r in self.store.conn.execute(
                "SELECT data FROM trajectory_summaries WHERE evaluation_id=?", (evaluation_id,))]
        runtime = Counter(t.get("runtime_status") or "unknown" for t in traces)
        terminal = Counter(t.get("termination_reason") or "unknown" for t in traces)
        symptoms = Counter()
        for trace in traces:
            reason = trace.get("termination_reason")
            if reason and reason not in ("final_answer", "stop", "completed", "success", "unknown"):
                symptoms[reason] += 1
            elif trace.get("runtime_status") in ("error", "timeout", "cancelled"):
                symptoms["runtime_" + trace["runtime_status"]] += 1
        expected = ev["stats"]["expected"]
        training = self._training_evidence(run)
        cp = ev["checkpoint"]
        training.update(run_id=run["id"], checkpoint_id=cp["id"], step=cp.get("step"),
                        origin="model_snapshot" if cp.get("kind") == "model_snapshot" else "checkpoint" if training["has_training_evidence"] or training["has_training_metadata"] else "unknown")
        return {"run_id": run["id"], "evaluation_id": ev["id"], "purpose": purpose_of(ev),
                "status": ev["status"], "context": self._context(ev), "stats": ev["stats"],
                "coverage": {"expected": expected, "recorded": len(traces), "scored": ev["stats"]["scored"],
                             "completed": runtime["ok"], "completion_rate": runtime["ok"] / expected if expected else None,
                             "scoring_rate": ev["stats"]["scored"] / expected if expected else None,
                             "recording_rate": len(traces) / expected if expected else None,
                             "runtime": {k: runtime[k] for k in ("ok", "error", "timeout", "cancelled", "unknown")},
                             "completion_definition": "原日志 runtime_status=ok；不等于答案正确。"},
                "outcomes": {"answer_correct": ev["stats"]["answer_correct"], "macro_em": ev["stats"]["macro_em"],
                             "termination_reasons": _rows(terminal), "symptoms": _rows(symptoms),
                             "attribution": "症状不等于根因；format_error 不能单独归因给模型或 harness。"},
                "usage": {k: _distribution([(t.get("usage") or {}).get(k) for t in traces], expected) for k in USAGE_KEYS},
                "training": training, "checks": self.checks(run["id"]),
                "notice": "保留原始评分与终止记录；只汇总已记录的消耗，不从工具调用数推造搜索次数。"}

    @request_scope
    def training_summary(self, run_id):
        run = self.store.run(run_id)
        evidence = self._training_evidence(run)
        rows = self.store.metrics(run_id)
        recorded_keys = sorted({k for row in rows for k, value in row.items() if k != "step" and _numeric(value)})
        series = []
        for key in recorded_keys:
            points = [{"step": row["step"], "value": row[key]} for row in rows if _numeric(row.get(key))]
            values = [p["value"] for p in points]
            scope = metric_scope(key)
            series.append({"key": key, "label": key, "points": points, "latest": points[-1],
                           "min": min(values), "max": max(values), "scope": scope})
        with self.repo.lock:
            batches = [dict(r) for r in self.repo.conn.execute(
                "SELECT id,step,group_count,trajectory_count FROM training_batch_summaries WHERE run_id=? ORDER BY step,id", (run_id,))]
            # Only reward/advantage scalars are read here. Token arrays and rollout
            # messages stay in the batch endpoint, preserving overview performance.
            scalars = self.repo.conn.execute("""SELECT b.id AS batch_id,g.key AS group_index,
                json_array_length(json_extract(g.value,'$.samples')) AS sample_count,
                s.key AS sample_index,json_extract(s.value,'$.reward') AS reward,
                json_extract(s.value,'$.advantage') AS advantage
                FROM training_batches b,json_each(b.data,'$.groups') g
                LEFT JOIN json_each(g.value,'$.samples') s ON 1=1 WHERE b.run_id=?""", (run_id,)).fetchall()
        group_values = {}
        for row in scalars:
            group = group_values.setdefault((row["batch_id"], row["group_index"]), {"samples": []})
            if row["sample_index"] is not None:
                group["samples"].append({"reward": row["reward"], "advantage": row["advantage"]})
        batch_summary = _sample_statistics(list(group_values.values()))
        batch_summary.update(batch_count=len(batches), group_count=sum(b["group_count"] for b in batches))
        from .training_stage import stage_of
        with self.repo.lock:
            batch_stages=[r[0] for r in self.repo.conn.execute("SELECT DISTINCT json_extract(data,'$.training_stage') FROM training_batches WHERE run_id=?",(run_id,))]
        training_stage=stage_of(run,batch_stages)
        evaluations = self._evaluations(run_id)
        with self.store.lock:
            checkpoints = [json.loads(r[0]) for r in self.store.conn.execute("SELECT data FROM checkpoints WHERE run_id=? ORDER BY step,id", (run_id,))]
        for cp in checkpoints:
            cp["evaluations"] = [{"id": ev["id"], "name": ev.get("name", ev["id"]), "purpose": purpose_of(ev),
                                  "status": ev["status"], "panel_id": ev["panel_id"], "stats": ev["stats"],
                                  "panel": self._context(ev)["panel"]} for ev in evaluations if ev["checkpoint_id"] == cp["id"]]
        explicit = run.get("run_type")
        classification = {"value": explicit or ("training" if evidence["has_training_evidence"] else "unknown"),
                          "source": "explicit" if explicit else "recorded_evidence" if evidence["has_training_evidence"] else "legacy",
                          **evidence}
        return {"run_id": run_id, "run": run, "classification": classification, "training_stage": training_stage,
                "metrics": {"rows": rows, "series": series, "recorded_keys": recorded_keys},
                "batches": batches, "batch_summary": batch_summary, "checkpoints": checkpoints,
                "coverage": {"checkpoint_count": sum(cp.get("kind") != "model_snapshot" for cp in checkpoints),
                             "model_snapshot_count": sum(cp.get("kind") == "model_snapshot" for cp in checkpoints),
                             "evaluated_checkpoint_count": sum(bool(cp["evaluations"]) and cp.get("kind") != "model_snapshot" for cp in checkpoints),
                             "evaluation_count": len(evaluations), "completed_evaluation_count": sum(ev["status"] == "completed" for ev in evaluations)},
                "method_summary": self.repo.method_summary(run_id),
                "notice": "训练 reward、loss、advantage 来自实际训练日志；训练 rollout 不进入固定题 EM。验证日志指标也不能替代独立评测记录。"}

    def training_reference(self, sample, run, *, strict=True):
        """A training reference has stricter ownership than generic evidence links."""
        reasons, sample_purposes = [], _declared_record_purposes(sample)
        if len(sample_purposes)>1 or sample_purposes - {"train_rollout", "sft_demonstration"}:
            reasons.append("样本显式用途与 training rollout 冲突")
        reference = sample.get("trace_ref")
        trace_role, known_run, evidence_purpose = ("sft_demonstration" if sample_purposes=={"sft_demonstration"} else "train_rollout"), None, "unknown"
        if "trace_ref" in sample:
            source_purposes = set()
            trace_role = "evidence_only"
            try:
                if not self.repo.methods: raise ValidationError("Trace resolver required")
                self.repo.methods.resolve_ref(reference, owner={"scope": {
                    "run_id": run["id"], "project_id": run["project_id"], "experiment_id": run["experiment_id"]}})
                raw, declared_usage = {}, None
                if reference.get("trajectory"):
                    trace = self.store.trajectory(reference["trajectory"])
                    ev = self.store.evaluation(trace["evaluation_id"])
                    raw, known_run = trace, ev["run"]["id"]
                    source_purposes |= _declared_record_purposes(ev)
                    # Even a legacy evaluation container is evaluation evidence;
                    # batch membership does not turn its execution into sampling.
                    declared_usage = "evaluation_container"
                elif reference.get("object_id"):
                    obj = self.repo.methods.object(reference["object_id"])
                    raw, known_run, declared_usage = obj["data"], obj.get("scope", {}).get("run_id"), obj.get("usage")
                elif reference.get("file"):
                    raw = self.repo.inspections.record(reference["file"], reference["record"])["raw"]
                    if isinstance(raw, dict):
                        container = raw.get("data") if raw.get("type") == "trajectory" and isinstance(raw.get("data"), dict) else raw.get("episode") if isinstance(raw.get("episode"), dict) else raw
                        known_run = container.get("run_id") or (container.get("metadata", {}).get("run_id") if isinstance(container.get("metadata"), dict) else None)
                source_purposes |= _declared_record_purposes(raw)
                if known_run and known_run != run["id"]:
                    reasons.append("被引用执行属于另一条 run；不能绑定为本 run 的训练采样")
                if len(source_purposes)>1 or source_purposes - {"train_rollout", "sft_demonstration"} or declared_usage in ("validation", "test"):
                    reasons.append("明确 Agent / checkpoint 评测用途的执行不能登记为训练 rollout")
                if not reasons and declared_usage != "evaluation_container" and (source_purposes in ({"train_rollout"}, {"sft_demonstration"}) or declared_usage == "training"):
                    trace_role = "sft_demonstration" if source_purposes=={"sft_demonstration"} else "train_rollout"
                evidence_purpose = next(iter(source_purposes)) if len(source_purposes) == 1 else "conflicting" if source_purposes else "unknown"
            except (ValidationError, NotFoundError, KeyError, TypeError) as exc:
                reasons.append(str(exc))
        if reasons and strict:
            raise ValidationError("; ".join(dict.fromkeys(reasons)))
        return {"trace_role": "conflicting" if reasons else trace_role, "known_run_id": known_run,
                "recorded_purpose": evidence_purpose, "reasons": list(dict.fromkeys(reasons)),
                "notice": "仅为训练记录所引用的证据；引用本身不证明该执行是训练采样。" if trace_role == "evidence_only" else
                          "按已记录的训练用途和 batch 关联查看；不推断未记录的梯度。"}

    def training_rollout(self, run_id, batch_id, group_id, sample_id="", sample_index=None):
        run = self.store.run(run_id)
        with self.repo.lock:
            row = self.repo.conn.execute("SELECT data FROM training_batches WHERE id=? AND run_id=?", (batch_id, run_id)).fetchone()
        if not row:
            raise NotFoundError("Training batch does not belong to selected run")
        batch = json.loads(row[0])
        groups = [(i, g) for i, g in enumerate(batch["groups"]) if g["id"] == group_id]
        if len(groups) != 1:
            raise NotFoundError("Training group does not exist in selected batch")
        group_index, group = groups[0]
        samples = group["samples"]
        matches = []
        if sample_index is not None:
            if isinstance(sample_index, str) and sample_index.isascii() and sample_index.isdigit(): sample_index = int(sample_index)
            if type(sample_index) is not int or sample_index < 0: raise ValidationError("sample_index must be a nonnegative integer")
            if sample_index < len(samples): matches = [(sample_index, samples[sample_index])]
        else:
            matches = [(i, s) for i, s in enumerate(samples) if s.get("id") not in (None, "") and str(s["id"]) == str(sample_id)]
        if len(matches) > 1:
            raise ValidationError("Sample ID is ambiguous within this training group")
        if sample_index is None and str(sample_id).isdigit():
            index = int(sample_id)
            if matches and index < len(samples) and samples[index].get("id") in (None, "") and matches[0][0] != index:
                raise ValidationError("Numeric sample ID conflicts with an ordinal; provide sample_index explicitly")
            if not matches and index < len(samples) and samples[index].get("id") in (None, ""):
                matches = [(index, samples[index])]
        if not matches:
            raise NotFoundError("Training sample does not exist in selected group")
        sample_index, sample = matches[0]
        provenance = self.training_reference(sample, run, strict=False)
        from .training_stage import stage_of
        is_sft = (batch.get("training_stage") or stage_of(run)["value"])=="sft" or provenance["trace_role"]=="sft_demonstration"
        if is_sft and provenance["trace_role"]=="train_rollout" and not sample.get("trace_ref"):
            provenance={**provenance,"trace_role":"sft_demonstration"}
        result = {"origin": "sft_demonstration" if is_sft else "train_rollout", "run_id": run_id,
                "batch": {"id": batch["id"], "step": batch["step"], "content_hash": batch.get("content_hash")},
                "group": {"id": group["id"], "index": group_index, "sample_count": len(samples),
                          "statistics": _sample_statistics([group])},
                "sample": sample, "sample_index": sample_index,
                "trace_ref": sample.get("trace_ref") if provenance["trace_role"] != "conflicting" else None,
                "trace_role": provenance["trace_role"], "trace_provenance": provenance,
                "notice": "这是保存的 SFT 监督示范，不是模型自主执行；缺失监督字段不重建，也不计入固定题成绩。" if is_sft else "这是训练采样的 rollout。奖励和优势只描述该训练 batch，不作为固定评测集成绩。"}
        if provenance["trace_role"] != "conflicting" and any(isinstance(sample.get(k), list) for k in ("steps", "messages", "trajectories")):
            from ..inspection.adapters import normalize_record
            from ..inspection.diagnostics import diagnose
            view = normalize_record(sample)
            view["record_purpose"] = {"value": result["origin"], "source": "training_batch_membership",
                                      "evidence": [{"batch_id": batch_id, "group_id": group_id, "sample_index": sample_index}]}
            result["document"] = {"target": "training:" + digest([run_id, batch_id, group_id, sample_index])[:32],
                "source_hash": digest(sample), "source": {"kind": "training_batch", "run_id": run_id,
                    "batch_id": batch_id, "group_id": group_id, "sample_index": sample_index},
                "raw": sample, "raw_text": None, "parse_error": None, "view": view,
                "report": diagnose(sample), "reviews": []}
        else:
            result["document"] = None
        return result

    def register_agent_evaluation(self, record):
        if not isinstance(record, dict) or not all(isinstance(record.get(k), dict) for k in ("run", "model_snapshot", "evaluation")):
            raise ValidationError("run, model_snapshot and evaluation objects are required")
        run, snapshot, evaluation = [clone(record[k]) for k in ("run", "model_snapshot", "evaluation")]
        if run.get("run_type", "evaluation") != "evaluation":
            raise ValidationError("An independent Agent evaluation must use run_type=evaluation")
        if snapshot.get("run_id", run.get("id")) != run.get("id"):
            raise ValidationError("model_snapshot.run_id differs from evaluation run")
        if evaluation.get("checkpoint_id", snapshot.get("id")) != snapshot.get("id"):
            raise ValidationError("evaluation.checkpoint_id differs from model snapshot")
        if evaluation.get("purpose", "agent_eval") != "agent_eval":
            raise ValidationError("Independent Agent evaluation purpose must be agent_eval")
        run["run_type"] = "evaluation"; snapshot["run_id"] = run["id"]
        evaluation["checkpoint_id"] = snapshot["id"]; evaluation["purpose"] = "agent_eval"
        with self.store.transaction():
            self.store.register_run(run)
            self.store.register_model_snapshot(snapshot)
            if "panel" in record:
                self.store.register_panel(record["panel"])
                if evaluation.get("panel_id", record["panel"]["id"]) != record["panel"]["id"]:
                    raise ValidationError("evaluation.panel_id differs from provided panel")
                evaluation["panel_id"] = record["panel"]["id"]
            self.store.start_evaluation(evaluation)
        return {"run_id": run["id"], "model_snapshot_id": snapshot["id"], "evaluation_id": evaluation["id"], "purpose": "agent_eval"}

    def record_harness_check(self, record):
        if not isinstance(record, dict):
            raise ValidationError("Harness check must be an object")
        d = clone(record)
        for key in ("id", "run_id"):
            ident(d.get(key), "harness_check." + key)
        run = self.store.run(d["run_id"])
        if d.get("evaluation_id"):
            ev = self.store.evaluation(d["evaluation_id"])
            if ev["run"]["id"] != run["id"]:
                raise ValidationError("Harness check is outside selected evaluation run")
        if not isinstance(d.get("checks"), list) or not d["checks"]:
            raise ValidationError("Provide a non-empty list of actually executed contract checks")
        for check in d["checks"]:
            if not isinstance(check, dict) or type(check.get("passed")) is not bool:
                raise ValidationError("Each check requires a boolean passed result")
            if not check.get("name") and not check.get("id"):
                raise ValidationError("Each check requires a name or id")
        d["purpose"] = "harness_check"
        d["total"] = len(d["checks"]); d["passed"] = sum(c["passed"] for c in d["checks"])
        d["status"] = "passed" if d["passed"] == d["total"] else "failed"
        d.setdefault("name", d["id"])
        d["score_semantics"] = "contract assertions; not task EM or model capability"
        supplied_hash = d.pop("content_hash", None)
        with self.repo.lock, self.repo.conn:
            old = self.repo.conn.execute("SELECT data FROM harness_checks WHERE id=?", (d["id"],)).fetchone()
            d.setdefault("created_at", json.loads(old[0])["created_at"] if old else now())
            d["content_hash"] = digest(d)
            if supplied_hash and supplied_hash != d["content_hash"]:
                raise ValidationError("Harness check content hash mismatch")
            if old:
                if json.loads(old[0]) != d:
                    raise ValidationError("Harness check evidence is immutable; use a new id")
                return d
            self.repo.conn.execute("INSERT INTO harness_checks VALUES (?,?,?,?)", (d["id"], run["id"], d.get("evaluation_id"), dumps(d)))
        return d

    @request_scope
    def pair(self, a_id, b_id, axis="checkpoint", *, allow_same_harness=False):
        if axis not in COMPARISON_AXES:
            raise ValidationError("axis must be checkpoint, harness or method_state")
        from .repository import diff_values
        a, b = self.store.evaluation(a_id), self.store.evaluation(b_id)
        pa, pb = self.repo.profile(a["experiment_id"]), self.repo.profile(b["experiment_id"])
        ca, cb = self._context(a), self._context(b)
        reasons, controls = [], []

        def control(name, left, right, *, allow_change=False):
            status = "missing" if not _known(left) or not _known(right) else "same" if left == right else "changed"
            controls.append({"name": name, "status": status, "a": left, "b": right})
            if status == "missing": reasons.append(name + " 未完整登记")
            elif status == "changed" and not allow_change: reasons.append(name + " 发生变化")

        if a_id == b_id: reasons.append("请选择两条不同评测")
        if pa["track"] != pb["track"] or "unassigned" in (pa["track"], pb["track"]):
            reasons.append("研究用途未明确或不在同一分区")
        if pa.get("kind") != pb.get("kind") or pa.get("kind") == "evaluation_calibration":
            reasons.append("评测校准与模型/Agent 改进不混算")
        if a["project_id"] != b["project_id"]: reasons.append("不同项目不计算迭代增量")
        if a["demo"] != b["demo"]: reasons.append("演示与真实评测不混算")
        for ev in (a, b):
            if purpose_of(ev)["value"] == "unknown": reasons.append("评测用途未声明，旧结果只作原始证据")
            if not ev["readiness"]["complete"] or not ev["protocol_complete"]:
                reasons.append("执行协议未完整登记")
        control("固定题单与答案", a["panel_hash"], b["panel_hash"])
        for key, label in (("scorer", "评分器"), ("retriever", "检索环境"), ("runtime", "运行环境"),
                           ("budget", "执行预算"), ("decoding", "采样设置"), ("seed", "随机种子"),
                           ("state_reset", "状态重置")):
            control(label, a["protocol"].get(key), b["protocol"].get(key))
        # A run's model_revision often identifies the *base* model. It cannot
        # stand in for the current trained checkpoint's weight digest.
        wa = _fingerprint(a["checkpoint"].get("weights_sha256"))
        wb = _fingerprint(b["checkpoint"].get("weights_sha256"))
        if a["checkpoint"].get("kind") == "model_snapshot":
            wa = wa or _fingerprint(a["checkpoint"].get("model_revision"))
        if b["checkpoint"].get("kind") == "model_snapshot":
            wb = wb or _fingerprint(b["checkpoint"].get("model_revision"))
        control("模型权重指纹", wa, wb, allow_change=axis == "checkpoint")
        control("底座模型", ca["model"].get("base_model"), cb["model"].get("base_model"))
        if axis == "checkpoint":
            control("Harness 版本", ca["harness"]["version"], cb["harness"]["version"])
            control("Harness 指纹", ca["harness"]["fingerprint"], cb["harness"]["fingerprint"])
            control("完整执行协议", a["protocol_hash"], b["protocol_hash"])
            reasons.extend(self.repo.pair(a_id, b_id).get("reasons", []))
            if wa and wb and wa == wb: reasons.append("权重指纹相同，不能标记为 checkpoint 更新")
            if purpose_of(a)["value"] != "checkpoint_eval" or purpose_of(b)["value"] != "checkpoint_eval":
                reasons.append("Checkpoint 对比需要双方显式登记为 checkpoint_eval")
        elif axis == "harness":
            control("Harness 版本", ca["harness"]["version"], cb["harness"]["version"], allow_change=True)
            control("Harness 指纹", ca["harness"]["fingerprint"], cb["harness"]["fingerprint"], allow_change=True)
            allowed = b.get("comparison", {}).get("allowed_harness_changes", [])
            if not isinstance(allowed, list) or not all(isinstance(p, str) and any(p == prefix or p.startswith(prefix + "/") for prefix in HARNESS_PATHS) for p in allowed):
                reasons.append("allowed_harness_changes 只能声明具体 harness 字段")
                allowed = []
            differences = diff_values(a["protocol"], b["protocol"])
            # Entropy runtime comparisons can change an explicitly recorded
            # selection policy within the same harness and fixed protocol.
            # Public harness comparisons retain their existing default gate.
            if not differences and not allow_same_harness: reasons.append("没有登记 Harness 变化")
            for difference in differences:
                path = difference["path"]
                if not any(path == pointer or path.startswith(pointer + "/") for pointer in allowed):
                    reasons.append("未声明或非 Harness 变化：" + path)
            reasons.extend(self.store._method_pair_reasons(a_id, b_id))
        else:
            # Changing a SkillBank is an explicit intervention in the method
            # state, not a checkpoint update and not a broad protocol waiver.
            control("Harness 版本", ca["harness"]["version"], cb["harness"]["version"])
            control("Harness 指纹", ca["harness"]["fingerprint"], cb["harness"]["fingerprint"])
            control("完整执行协议", a["protocol_hash"], b["protocol_hash"])
            states = [self.store.method_state(eid) for eid in (a_id, b_id)]
            for side, state in zip(("A", "B"), states):
                binding = (state.get("binding") or {}).get("data", {})
                if state.get("status") != "recorded":
                    reasons.append(side + " 方法状态未完整登记")
                if binding.get("binding_origin") != "recorded_execution":
                    reasons.append(side + " 缺少执行时登记的方法状态；事后关联只作参考")
                if binding.get("bank_id") and binding.get("skills_disabled") is True:
                    reasons.append(side + " 同时声明技能库与 skills_disabled，状态存在冲突")
                if _unknown_method_value(state.get("runtime", {}).get("extensions", {})):
                    reasons.append(side + " 其他方法状态含未知值")
            ar, br = [state.get("runtime", {}) for state in states]
            # A deliberately disabled bank is a known condition, not missing.
            def bank_label(state):
                runtime = state.get("runtime", {})
                if runtime.get("bank_content_hash"):
                    return runtime["bank_content_hash"]
                return "skills_disabled" if (state.get("binding") or {}).get("data", {}).get("skills_disabled") is True else None
            control("SkillBank 状态", bank_label(states[0]), bank_label(states[1]), allow_change=True)
            allowed = b.get("comparison", {}).get("allowed_method_changes", [])
            if not isinstance(allowed, list) or not allowed or not all(_method_change_path(p) for p in allowed):
                reasons.append("allowed_method_changes 需逐项声明 /bank_content_hash 或 /retrieval_config/ 下的具体字段")
                allowed = []
            differences = diff_values(ar, br)
            if not differences:
                reasons.append("没有登记 SkillBank 或方法检索状态变化")
            for difference in differences:
                path = difference["path"]
                # Exact paths only. Allowing a leaf never exempts unrelated
                # nested parameters which happen to share its prefix.
                if path not in allowed:
                    reasons.append("未声明或非 SkillBank / 检索状态变化：" + path)
            if ar and br:
                controls.append({"name": "方法状态差异", "status": "changed" if differences else "same",
                                 "a": ar, "b": br})
        reasons = list(dict.fromkeys(reasons))
        result = {"a": a, "b": b, "axis": axis, "compatible": not reasons, "reasons": reasons,
                  "controls": controls, "configuration_differences": diff_values(ca, cb),
                  "groups": None, "items": [], "full": False, "macro_delta": None, "net_correct_change": None,
                  "rl_attribution": False, "included_in_main_iteration": False,
                  "claim": "仅描述原始差异；控变量不足" if reasons else "固定已登记条件下的描述性差异；不单独证明 RL 因果提升",
                  "track": pb["track"], "delta_kind": {"harness": "Harness 受控对比", "checkpoint": "Checkpoint 受控对比",
                                                            "method_state": "方法状态受控对比"}[axis],
                  "paired_usage": None}
        if axis == "method_state":
            result["method_states"] = {"a": states[0], "b": states[1]}
            result["method_state_differences"] = differences
            if not reasons:
                result["claim"] = "固定模型、执行协议与题单下的方法状态对照；描述本次条件下的增量，不分配单条技能信用"
        # Always provide aligned original traces for inspection. Green comparison
        # status and derived gain fields remain gated by all controls above.
        with self.store.lock:
            trace_maps = [{(r["dataset"], r["sample_id"]): r for r in [json.loads(x[0]) for x in self.store.conn.execute(
                "SELECT data FROM trajectory_summaries WHERE evaluation_id=?", (ev["id"],))]} for ev in (a, b)]
        da, db = trace_maps
        counts = {k: 0 for k in ("rescued", "harmed", "both_correct", "both_wrong", "unscored")}
        for sample in self.store.panel(b["panel_id"])["samples"]:
            key = (sample["dataset"], sample["id"])
            x, y = da.get(key), db.get(key)
            group = "unpaired"
            if x and y and not reasons:
                ea, eb = x["scores"].get("em"), y["scores"].get("em")
                group = "unscored" if ea is None or eb is None else "rescued" if (ea, eb) == (0, 1) else "harmed" if (ea, eb) == (1, 0) else "both_correct" if ea == 1 else "both_wrong"
                counts[group] += 1
            result["items"].append({"sample": sample, "a": x, "b": y, "group": group})
        result["paired"] = len(set(da) & set(db)); result["expected"] = b["panel"]["size"]
        if not reasons:
            full = a["status"] == b["status"] == "completed" and a["stats"]["fully_scored"] and b["stats"]["fully_scored"]
            result.update(groups=counts, full=full, included_in_main_iteration=bool(pb["participates_in_main"]),
                          macro_delta=b["stats"]["macro_em"] - a["stats"]["macro_em"] if full else None,
                          net_correct_change=b["stats"]["answer_correct"] - a["stats"]["answer_correct"] if full else None)
            paired_usage = {}
            for key in (*USAGE_KEYS, "total_tokens"):
                values = []
                for sample in set(da) & set(db):
                    usages = [d[sample].get("usage") or {} for d in (da, db)]
                    def measured(usage):
                        if key != "total_tokens": return usage.get(key)
                        values = [usage.get(k) for k in ("input_tokens", "output_tokens")]
                        return sum(values) if all(_numeric(v) for v in values) else None
                    left, right = [measured(u) for u in usages]
                    if _numeric(left) and _numeric(right): values.append((left, right))
                paired_usage[key] = {"paired": len(values), "missing": result["paired"] - len(values),
                    "mean_a": sum(v[0] for v in values) / len(values) if values else None,
                    "mean_b": sum(v[1] for v in values) / len(values) if values else None,
                    "mean_delta": sum(v[1] - v[0] for v in values) / len(values) if values else None}
            result["paired_usage"] = paired_usage
        return result


def execute_contract_fixtures(fixtures, handlers):
    """Execute registered local Python handlers; no shell, network or model tool.

    Each fixture is {id, handler, input, expected} or uses expected_error. The
    caller supplies its actual parser/context/stop handler. This is a local SDK
    function only; the HTTP API cannot execute arbitrary handlers.
    """
    if not isinstance(fixtures, list) or not fixtures or not isinstance(handlers, dict):
        raise ValidationError("fixtures and a local handler mapping are required")
    results = []
    for fixture in fixtures:
        if not isinstance(fixture, dict) or fixture.get("handler") not in handlers or not callable(handlers[fixture["handler"]]):
            raise ValidationError("Each fixture must name an explicitly supplied callable handler")
        if "expected" not in fixture and "expected_error" not in fixture:
            raise ValidationError("Each fixture requires an expected value or expected_error")
        name = fixture.get("name", fixture.get("id", fixture["handler"]))
        try:
            actual = handlers[fixture["handler"]](clone(fixture.get("input")))
            actual = clone(actual)
            passed = "expected_error" not in fixture and actual == fixture["expected"]
            reason = "assertion matched" if passed else "observed result differs from fixture"
        except Exception as exc:
            actual = {"error_type": type(exc).__name__, "message": str(exc)}
            passed = fixture.get("expected_error") == type(exc).__name__
            reason = "expected exception" if passed else "unexpected exception"
        results.append({"id": fixture.get("id", name), "name": name, "handler": fixture["handler"],
                        "passed": passed, "expected": fixture.get("expected", {"error_type": fixture.get("expected_error")}),
                        "actual": actual, "reason": reason, "fixture": clone(fixture), "fixture_sha256": digest(fixture)})
    return results
