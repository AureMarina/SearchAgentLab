"""Read-only adapters for saved OPD evidence; no trainer/model dependencies.

Source rows and exact source-file hashes are preserved. StudioRecorder also
archives original file bytes. An adapter does not establish optimizer execution,
task EM, teacher continuation, a complete batch, or a missing formal identity.
Parquet is optional: JSONL/rows and every viewer use only the standard library.
"""
from __future__ import annotations

import copy
import hashlib
import io
import math
from typing import Any

from ..core import ValidationError
from ..inspection.repository import MAX_FILE_BYTES, digest, strict_json
from .schema import SCHEMA, identifier, validate_object


REVIEWED_ON = "2026-10-10"
SOURCES = {
    "verl_generations": {
        "url": "https://github.com/verl-project/verl/blob/main/verl/trainer/ppo/ray_trainer.py",
        "sha256": "eb41f99ccc66976eab14ebe35aa1aa5b7318e02469240ef1569ac4c884c94312",
    },
    "verl_losses": {
        "url": "https://github.com/verl-project/verl/blob/main/verl/trainer/distillation/losses.py",
        "sha256": "00c9a6851b9de1d17d13305a6d8524d20e900999d471d39c499344b5069865da",
    },
    "ocsd_diagnostic": {
        "url": "https://github.com/yiy1x/OCSD/blob/main/verl/trainer/ppo/oc_opsd_ray_trainer.py",
        "sha256": "9f62e0c1d7e0e0ef807fe53eb5c7a3d5bad1a951939f4b602be5567c9486504a",
    },
    "agentopsd_debug": {
        "url": "https://github.com/ZethWang/AgentOPSD/blob/master/verl/trainer/ppo/opsd_ray_trainer.py",
        "sha256": "4833b87bc33fafbab995c524389c1727518d349f0446617c09c345c1b0d9a020",
    },
    "uniopsd_metrics": {
        "url": "https://github.com/Zenghuang-Fu/Uniopsd/blob/master/verl/trainer/ppo/rlsd_ray_trainer.py",
        "sha256": "ec0f168f6e93a494ad098873fe0200460502164635c099edd9db6d9eaa645f88",
    },
}
MAX_ADAPTED_ROWS = 4000
MAX_TOKENS_PER_ROW = 131072
IDENTITY_FIELDS = {
    "student_checkpoint_id", "evaluation_id", "trajectory_id", "failure_id",
    "teacher_ids", "supervision_id", "sample_id", "batch_id", "group_id",
}
CONTEXTS = {"training_on_policy", "evaluation", "failure_probe", "fixed_prefix_probe", "unknown"}


def _finite(value: Any, label: str) -> Any:
    try:
        digest(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError(label + " must contain finite JSON values") from exc
    return copy.deepcopy(value)


def _number(value, label, *, nullable=True):
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(label + " must be a finite number" + (" or null" if nullable else ""))


def _integer(value, label, *, nullable=False):
    if value is None and nullable:
        return
    if type(value) is not int or value < 0:
        raise ValidationError(label + " must be a nonnegative integer")


def _boolean(value, label):
    if value is not None and type(value) is not bool:
        raise ValidationError(label + " must be boolean or null")


def _scope(scope):
    if not isinstance(scope, dict) or not scope.get("run_id") or set(scope) - {"project_id", "experiment_id", "run_id"}:
        raise ValidationError("OPD native reports require an explicitly registered run_id scope")
    for key, value in scope.items():
        identifier(value, key)
    return copy.deepcopy(scope)


def _event(record_id, kind, data, scope, demo, usage, **attributes):
    event = {"id": record_id, "kind": kind, "data": data, "scope": scope,
             "demo": demo, "usage": usage, **attributes}
    validate_object(event)
    return event


def _ref(event, pointer=""):
    return {"object_id": event["id"], "pointer": pointer, "sha256": digest(event["data"])}


def _raw_bytes(raw, label):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FILE_BYTES:
        raise ValidationError(label + " must be 1 byte–50 MiB")
    return raw


def parse_jsonl_rows(source: bytes | list[dict]) -> tuple[list[dict], list[int | None]]:
    """Strict finite JSONL. Keep original line numbers, including blank lines."""
    if isinstance(source, bytes):
        _raw_bytes(source, "OPD JSONL")
        rows, lines = [], []
        try:
            for number, line in enumerate(source.decode("utf-8-sig").splitlines(), 1):
                if not line.strip():
                    continue
                row = strict_json(line)
                if not isinstance(row, dict):
                    raise ValueError("each row must be an object")
                rows.append(row)
                lines.append(number)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValidationError("Invalid OPD JSONL: " + str(exc)) from exc
    elif isinstance(source, list):
        rows = _finite(source, "OPD rows")
        lines = [None] * len(rows)
    else:
        raise ValidationError("Provide original JSONL bytes or a list of row objects")
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("OPD report needs at least one row object")
    return rows, lines


def parse_ocsd_parquet(raw: bytes) -> list[dict]:
    """Optional native Parquet reader, never imported by ordinary viewers."""
    _raw_bytes(raw, "OCSD Parquet")
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise ValidationError(
            "OCSD Parquet import needs optional pyarrow on the collecting machine. "
            "Install pyarrow there, or export to finite JSONL/row dictionaries and use "
            "adapt_ocsd_diagnostics(..., source_format='rows'). The viewer needs no pyarrow."
        ) from exc
    try:
        table = parquet.read_table(io.BytesIO(raw))
        rows = table.to_pylist()
    except Exception as exc:
        raise ValidationError("Cannot read OCSD Parquet: " + str(exc)) from exc
    # Arrow nulls map to Python None. Non-finite reported floats are rejected;
    # silently replacing an actual numeric NaN would erase source semantics.
    return _finite(rows, "OCSD Parquet rows")


def _normalize_identity_map(identity_map):
    if identity_map is None:
        identity_map = {}
    if not isinstance(identity_map, dict):
        raise ValidationError("identity_map must map zero-based source-row indices to explicit Lab identities")
    normalized = {}
    for key, value in identity_map.items():
        if not (type(key) is int and key >= 0 or isinstance(key, str) and key.isascii() and key.isdigit()):
            raise ValidationError("identity_map keys must be zero-based source-row indices")
        index = int(key)
        if index in normalized:
            raise ValidationError("Duplicate identity_map row index")
        if not isinstance(value, dict) or set(value) - IDENTITY_FIELDS:
            raise ValidationError("Unknown OPD identity mapping field")
        normalized[index] = value
        for field, target in value.items():
            if field == "teacher_ids":
                if not isinstance(target, list):
                    raise ValidationError("teacher_ids mapping must be an array")
                for teacher in target:
                    identifier(teacher, "teacher_ids")
            else:
                identifier(target, field)
    return normalized


def _identity(normalized, row_index, common_checkpoint):
    result = copy.deepcopy(normalized.get(row_index, {}))
    if common_checkpoint is not None:
        identifier(common_checkpoint, "student_checkpoint_id")
        if result.get("student_checkpoint_id", common_checkpoint) != common_checkpoint:
            raise ValidationError("Conflicting source-row and common student_checkpoint_id")
        result["student_checkpoint_id"] = common_checkpoint
    return result


def _prepare(source, *, scope, source_key, source_format, context, record_prefix,
             demo, row_offset, row_limit, source_population_size, source_complete,
             source_rows=None, source_lines=None, producer_revision=None):
    sc = _scope(scope)
    if context not in CONTEXTS:
        raise ValidationError("Unknown OPD diagnostic context")
    if type(demo) is not bool:
        raise ValidationError("demo must be boolean")
    if producer_revision is not None and (not isinstance(producer_revision, str) or not producer_revision.strip() or len(producer_revision)>256):
        raise ValidationError("producer_revision must be an explicit nonempty revision string")
    if source_rows is None:
        rows, lines = parse_jsonl_rows(source)
    else:
        rows, lines = source_rows, source_lines or [None] * len(source_rows)
        if not rows or any(not isinstance(row, dict) for row in rows):
            raise ValidationError("OPD report needs at least one row object")
    _integer(row_offset, "row_offset")
    if row_limit is not None and (type(row_limit) is not int or not 1 <= row_limit <= MAX_ADAPTED_ROWS):
        raise ValidationError("row_limit must be 1–4000")
    if row_offset >= len(rows):
        raise ValidationError("row_offset is outside the source report")
    stop = len(rows) if row_limit is None else min(row_offset + row_limit, len(rows))
    if stop - row_offset > MAX_ADAPTED_ROWS:
        raise ValidationError("At most 4000 rows per import; use explicit row_offset/row_limit shards")
    _boolean(source_complete, "source_complete")
    _integer(source_population_size, "source_population_size", nullable=True)
    if source_population_size is not None and source_population_size < len(rows):
        raise ValidationError("source_population_size is smaller than saved source rows")
    if source_complete is True and source_population_size is not None and source_population_size != len(rows):
        raise ValidationError("A complete source cannot omit declared population rows")
    provenance = {"adapter": source_key + "-v1", "source_format": source_format,
        "reviewed_on": REVIEWED_ON, "upstream_source": SOURCES[source_key]["url"],
        "upstream_source_sha256": SOURCES[source_key]["sha256"],
        "producer_revision": producer_revision, "producer_revision_basis": "caller declaration" if producer_revision else "not recorded"}
    file_hash = hashlib.sha256(source).hexdigest() if isinstance(source, bytes) else None
    if file_hash is not None:
        provenance["source_file_sha256"] = file_hash
    else:
        provenance["source_rows_json_sha256"] = digest(rows)
        provenance["source_bytes_available"] = False
    prefix = record_prefix or "opd-" + source_key.replace("_", "-") + "-" + digest([provenance, sc, row_offset, stop])[:20]
    identifier(prefix, "record_prefix")
    if len(prefix) > 120:
        raise ValidationError("record_prefix may contain at most 120 characters")
    usage = "training" if context == "training_on_policy" else "validation" if context != "unknown" else "unknown"
    coverage = {"saved_source_rows": len(rows), "selected_row_offset": row_offset,
        "selected_row_count": stop - row_offset, "selected_rows_complete": row_offset == 0 and stop == len(rows),
        "source_population_size": source_population_size, "source_complete": source_complete,
        "source_population_complete": source_complete,
        "population_completeness_basis": "caller declaration" if source_complete is not None else "not recorded by native dump"}
    manifest = _event(prefix + "-manifest", "extension.opd_source_manifest", {
        "source_format": source_format, "coverage": coverage, "source_file_sha256": file_hash,
        "reported_only": True, "does_not_establish": ["task_em", "actual_optimizer_update", "teacher_continuation"]},
        sc, demo, usage, provenance=provenance, title="OPD 原始报告 · 导入范围")
    events = [manifest]
    selected = []
    for index in range(row_offset, stop):
        original = _event(prefix + f"-source-{index}", "extension.opd_source_row", copy.deepcopy(rows[index]),
            sc, demo, usage, provenance={**provenance, "source_row_index": index, "source_line": lines[index]},
            derived_from=[manifest["id"]], title=source_key + " · 原始行 " + str(index))
        events.append(original)
        selected.append((index, rows[index], original))
    return {"scope": sc, "prefix": prefix, "usage": usage, "events": events,
            "selected": selected, "coverage": coverage, "provenance": provenance, "source_format": source_format}


def _diagnostic(prepared, index, source_event, data, demo, identity_map, common_checkpoint):
    if "identity_map" not in prepared:
        prepared["identity_map"] = _normalize_identity_map(identity_map)
        if any(row >= prepared["coverage"]["saved_source_rows"] for row in prepared["identity_map"]):
            raise ValidationError("identity_map row index is outside the source report")
    identity = _identity(prepared["identity_map"], index, common_checkpoint)
    payload = {**data, **identity, "source_format": prepared["source_format"],
        "source_version": prepared["provenance"]["producer_revision"] or "unknown",
        "schema_reference_sha256": prepared["provenance"]["upstream_source_sha256"],
        "reported_only": True, "source_row_index": index, "source_refs": [_ref(source_event)],
        "identity_status": "explicit_mapping" if identity else "unmapped",
        "coverage": {**prepared["coverage"], **data.get("coverage", {})}}
    prepared["events"].append(_event(prepared["prefix"] + f"-diagnostic-{index}", "opd_diagnostic", payload,
        prepared["scope"], demo, prepared["usage"], derived_from=[source_event["id"]],
        title=data.get("label", "OPD 原生日志诊断")))


def _bundle(prepared, demo):
    return {"method_schema": SCHEMA, "scope": prepared["scope"], "demo": demo,
        "objects": prepared["events"],
        "description": "Original OPD evidence with explicit identity and source coverage; no model, environment or training was executed."}


def _metric(name, value, definition, source, *, unit="reported", support=None):
    _number(value, name)
    return {"name": name, "value": value, "definition": definition,
        "definition_version": SOURCES[source]["sha256"], "source": SOURCES[source]["url"],
        "unit": unit, "support": {"kind": "upstream_reported", "available": value is not None, **(support or {})}}


def adapt_verl_generations(source: bytes | list[dict], *, scope, purpose,
        identity_map=None, student_checkpoint_id=None, record_prefix=None, demo=False,
        row_offset=0, row_limit=None, source_population_size=None, source_complete=None,
        producer_revision=None) -> dict:
    """Import native _write_generations rows without promoting score to EM.

    purpose is mandatory: training_rollout, validation, evaluation_only, unknown.
    Native JSONL has no frozen protocol/model/harness binding. Evaluation-only
    therefore remains reported evidence until separately registered and bound.
    Native step is a TRAINING step, never an environment turn or token position.
    """
    purposes = {"training_rollout": "training_on_policy", "validation": "evaluation",
                "evaluation_only": "evaluation", "unknown": "unknown"}
    if purpose not in purposes:
        raise ValidationError("purpose must explicitly be training_rollout/validation/evaluation_only/unknown")
    context = purposes[purpose]
    prepared = _prepare(source, scope=scope, source_key="verl_generations", source_format="verl-generations-jsonl-v1",
        context=context, record_prefix=record_prefix, demo=demo, row_offset=row_offset, row_limit=row_limit,
        source_population_size=source_population_size, source_complete=source_complete, producer_revision=producer_revision)
    for index, row, original in prepared["selected"]:
        if set(("input", "output", "gts", "score", "step")) - row.keys():
            raise ValidationError("veRL generation row requires input/output/gts/score/step")
        if not isinstance(row["input"], str) or not isinstance(row["output"], str):
            raise ValidationError("veRL input/output must be saved decoded text")
        _integer(row["step"], "veRL training step")
        metric = _metric("score", row["score"], "Native reward/scorer result; scoring definition is unspecified by _write_generations. This is not automatically Search-QA EM.", "verl_generations")
        data = {"diagnostic_type": "step", "context": context, "training_step": row["step"],
            "declared_purpose": purpose, "label": "veRL · 生成结果（原始评分）",
            "generation": copy.deepcopy(row), "reported_metrics": [metric],
            "coverage": {"teacher_log_probs": "not supplied by native schema", "token_masks": "not supplied by native schema"},
            "autonomy_status": "unknown", "score_definition_status": "unknown"}
        _diagnostic(prepared, index, original, data, demo, identity_map, student_checkpoint_id)
    return _bundle(prepared, demo)


_OCSD_METRICS = {
    "advantage": "Pre-OC advantage, response-mask mean; not the final effective advantage.",
    "step_delta_F": "Response-mask mean of full replay log probability minus student old log probability.",
    "step_delta_A": "Response-mask mean of observation-ablated replay log probability minus student old log probability.",
    "step_residual_e": "Response-mask mean of full replay minus observation-ablated replay log probability.",
    "step_q": "Response-mask mean of tanh((full minus ablated log probability)/2); not tanh of the aggregated mean.",
    "step_abs_delta_F": "Response-mask mean of absolute full/student delta.",
    "step_abs_delta_A": "Response-mask mean of absolute ablated/student delta.",
    "step_abs_residual_e": "Response-mask mean of absolute full/ablated residual.",
    "step_abs_q": "Response-mask mean of absolute token q.",
    "mean_old_log_prob": "Response-mask mean of student old log probability.",
    "mean_full_log_prob": "Response-mask mean of full replay log probability.",
    "mean_abl_log_prob": "Response-mask mean of observation-ablated replay log probability.",
}


def adapt_ocsd_diagnostics(source: bytes | list[dict], *, scope, source_format="jsonl",
        context="training_on_policy", identity_map=None, student_checkpoint_id=None,
        record_prefix=None, demo=False, row_offset=0, row_limit=None,
        source_population_size=None, source_complete=None, producer_revision=None) -> dict:
    """Import actual _save_oc_diagnostic rows (Parquet, JSONL or dictionaries).

    step_selected means future observations exist. is_nll_selected is a distinct
    nullable field. num_tokens=0 retains reported zero summaries with no support.
    Native max_samples may truncate; whole-batch coverage stays unknown by default.
    """
    if source_format not in ("parquet", "jsonl", "rows"):
        raise ValidationError("OCSD source_format must be parquet/jsonl/rows")
    rows = parse_ocsd_parquet(source) if source_format == "parquet" else None
    prepared = _prepare(source, scope=scope, source_key="ocsd_diagnostic", source_format="ocsd-step-" + source_format + "-v1",
        context=context, record_prefix=record_prefix, demo=demo, row_offset=row_offset, row_limit=row_limit,
        source_population_size=source_population_size, source_complete=source_complete, source_rows=rows,
        producer_revision=producer_revision)
    for index, row, original in prepared["selected"]:
        if set(("global_step", "traj_uid", "step_index", "num_tokens", "step_selected")) - row.keys():
            raise ValidationError("OCSD row requires global_step/traj_uid/step_index/num_tokens/step_selected")
        for key in ("global_step", "step_index", "num_tokens"):
            _integer(row[key], "OCSD " + key)
        for key in ("step_selected", "is_nll_selected"):
            _boolean(row.get(key), "OCSD " + key)
        metrics = [_metric(key, row[key], definition, "ocsd_diagnostic", unit="response-token mean",
                    support={"kind": "response_mask", "count": row["num_tokens"], "available": row["num_tokens"] > 0,
                             "normalization": "sum(value * response_mask) / max(sum(response_mask), 1)"})
                   for key, definition in _OCSD_METRICS.items() if key in row]
        observation = {"has_future_observation": row["step_selected"], "is_nll_selected": row.get("is_nll_selected"),
            "definition": "Native step_selected is available_future_len > 0, not NLL selection; values are response-token aggregates.",
            "source": SOURCES["ocsd_diagnostic"]["url"]}
        for native, canonical in (("step_delta_F", "full"), ("step_delta_A", "ablated"), ("step_residual_e", "residual"),
                                  ("available_future_len", "available_future_len"), ("future_obs_text_1", "future_observation_text")):
            if native in row:
                observation[canonical] = copy.deepcopy(row[native])
        data = {"diagnostic_type": "observation", "context": context, "training_step": row["global_step"],
            "step": row["step_index"], "external_traj_uid": str(row["traj_uid"]), "label": "OCSD · 观察对照（步骤汇总）",
            "observation": observation, "reported_metrics": metrics,
            "coverage": {"token_vectors": "not recorded", "teacher_prompt": "not recorded", "source_may_be_max_samples_truncated": True}}
        if "advantage" in row:
            data["signals"] = {"task": {"original_advantage": row["advantage"], "source": "OCSD pre-OC response-mask mean"}}
        _diagnostic(prepared, index, original, data, demo, identity_map, student_checkpoint_id)
    return _bundle(prepared, demo)


_AGENT_TOKEN_FIELDS = ("tokens", "token_ids", "gaps", "teacher_log_probs", "student_log_probs", "token_advantages")


def adapt_agentopsd_debug(source: bytes | list[dict], *, scope, context="training_on_policy",
        identity_map=None, student_checkpoint_id=None, record_prefix=None, demo=False,
        row_offset=0, row_limit=None, source_population_size=None, source_complete=None,
        producer_revision=None) -> dict:
    """Import SAVE_CGTD_DEBUG rows, retaining every supplied valid response token.

    Equal-length arrays are required. No token IDs are retokenized; padding or
    absent OPD/task masks are not reconstructed. Original prompt and original
    full-sequence token positions were not exported by this native format.
    """
    prepared = _prepare(source, scope=scope, source_key="agentopsd_debug", source_format="agentopsd-token-jsonl-v1",
        context=context, record_prefix=record_prefix, demo=demo, row_offset=row_offset, row_limit=row_limit,
        source_population_size=source_population_size, source_complete=source_complete, producer_revision=producer_revision)
    for index, row, original in prepared["selected"]:
        if set(("global_step", "turn_step", "traj_uid", *_AGENT_TOKEN_FIELDS)) - row.keys():
            raise ValidationError("AgentOPSD row requires step/identity fields and all six native token arrays")
        for key in ("global_step", "turn_step"):
            _integer(row[key], "AgentOPSD " + key)
        if any(not isinstance(row[key], list) for key in _AGENT_TOKEN_FIELDS):
            raise ValidationError("AgentOPSD token fields must be arrays")
        lengths = {len(row[key]) for key in _AGENT_TOKEN_FIELDS}
        if len(lengths) != 1:
            raise ValidationError("AgentOPSD token arrays must have exactly equal lengths")
        count = next(iter(lengths))
        if not 1 <= count <= MAX_TOKENS_PER_ROW:
            raise ValidationError("AgentOPSD token rows need 1–131072 tokens; shard explicitly, never silently truncate")
        for value in row["token_ids"]:
            _integer(value, "token_id")
        if any(not isinstance(value, str) for value in row["tokens"]):
            raise ValidationError("Decoded tokens must be text")
        for key in _AGENT_TOKEN_FIELDS[2:]:
            for value in row[key]:
                _number(value, key, nullable=False)
        metrics = []
        for key, definition in (
            ("episode_reward", "Upstream episode reward; not automatically Search-QA EM."),
            ("episode_length", "Upstream reported episode length."),
            ("seq_advantage", "The first valid token of native seq_advantages; separate from recorded final token_advantages."),
        ):
            if key in row:
                metrics.append(_metric(key, row[key], definition, "agentopsd_debug"))
        data = {"diagnostic_type": "token", "context": context, "training_step": row["global_step"],
            "step": row["turn_step"], "external_traj_uid": str(row["traj_uid"]), "label": "AgentOPSD · token 调试记录",
            "tokens": {key: copy.deepcopy(row[key]) for key in _AGENT_TOKEN_FIELDS}, "reported_metrics": metrics,
            "coverage": {"token_count": count, "token_basis": "native response_mask-filtered positions only",
                "valid_response_tokens_complete": True, "original_sequence_positions": "not recorded",
                "task_mask": "unknown", "opd_mask": "unknown", "zero_token_rows": "omitted upstream",
                "topk": "unavailable; sampled-token logprobs cannot reconstruct top-k overlap"}}
        _diagnostic(prepared, index, original, data, demo, identity_map, student_checkpoint_id)
    return _bundle(prepared, demo)


_VERL_DEFINITIONS = {
    "distillation/overlap_ratio": "Mean teacher/student top-k overlap count over valid response positions, divided by k.",
    "distillation/overlap_token_advantage": "Mean upstream teacher-token KL contribution over positions with positive overlap; native zero fallback when no overlap exists. Not the paper's interchangeable metric.",
    "distillation/student_mass": "Mean student probability mass on native teacher top-k support over response-mask positions.",
    "distillation/teacher_mass": "Mean teacher probability mass on native teacher top-k support over response-mask positions.",
    "distillation/loss": "Reported distillation objective before outer task/distillation combination; definition depends on configured loss mode and aggregation.",
}
_UNIOPSD_DEFINITIONS = {
    "rlsd/opsd_rescue_frac": "Fraction of ALL rows where outcome step credit equals zero and fused credit is nonzero: mean((step_credit == 0) & (fused_credit != 0)). Credit coverage, not rescued tasks and not conditioned only on zero-credit rows.",
    "rlsd/opsd_rescue_ceiling": "Fraction of ALL rows with teacher evidence and without step evidence: mean(has_teacher & ~has_step). Coverage ceiling for the reported rescue fraction.",
    "rlsd/fused_step_zero_row_frac": "Fraction of ALL rows with exactly zero fused credit.",
    "rlsd/c_mean": "Mean outcome-credit fusion weight c; teacher weight is 1-c.",
    "rlsd/c_frac_env": "Fraction of rows with fusion weight c >= 0.9.",
    "rlsd/c_frac_teacher": "Fraction of rows with fusion weight c <= 0.1.",
    "rlsd/c_frac_mid": "Fraction of rows with 0.1 < fusion weight c < 0.9.",
}


def adapt_opd_metrics(source: bytes | dict, *, scope, source_family, training_step=None,
        context="training_on_policy", student_checkpoint_id=None, record_prefix=None,
        support=None, demo=False, producer_revision=None) -> dict:
    """Import a saved scalar dictionary from veRL/UniOPSD logger callbacks.

    Raw metric names and source-specific definitions remain distinct. Optional
    support metadata (count, total, k, available) must come from actual logging;
    absent support is not fabricated from scalar averages or metric names.
    """
    if source_family not in ("verl", "uniopsd"):
        raise ValidationError("source_family must be verl or uniopsd")
    if isinstance(source, bytes):
        _raw_bytes(source, "OPD metric JSON")
        try:
            data = strict_json(source.decode("utf-8-sig"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise ValidationError("OPD metrics need finite JSON") from exc
    else:
        data = _finite(source, "OPD metrics")
    if not isinstance(data, dict) or not data:
        raise ValidationError("OPD metrics must be a nonempty scalar dictionary")
    if support is not None and not isinstance(support, dict):
        raise ValidationError("support must map exact metric names to observed support objects")
    _integer(training_step, "training_step", nullable=True)
    key = "verl_losses" if source_family == "verl" else "uniopsd_metrics"
    prepared = _prepare(source, scope=scope, source_key=key, source_format=source_family + "-metrics-json-v1",
        context=context, record_prefix=record_prefix, demo=demo, row_offset=0, row_limit=None,
        source_population_size=None, source_complete=None, source_rows=[data], producer_revision=producer_revision)
    definitions = _VERL_DEFINITIONS if source_family == "verl" else _UNIOPSD_DEFINITIONS
    metrics = []
    for name, value in data.items():
        if not isinstance(name, str):
            raise ValidationError("Metric names must be text")
        _number(value, name)
        normalized = name.removeprefix("actor/") if source_family == "verl" else name
        definition = definitions.get(normalized, "Upstream reported metric; exact aggregation/normalization was not supplied. Keep this source-specific definition separate.")
        supplied = copy.deepcopy((support or {}).get(name, {}))
        if not isinstance(supplied, dict):
            raise ValidationError("Metric support must be an object")
        if source_family == "uniopsd" and (name.endswith("_frac") or name in ("rlsd/opsd_rescue_ceiling", "rlsd/c_frac_env", "rlsd/c_frac_teacher", "rlsd/c_frac_mid")):
            if value is not None and not 0 <= value <= 1:
                raise ValidationError("Reported row fraction must lie in [0, 1]")
            supplied.setdefault("kind", "all_rows")
        if source_family == "verl" and normalized.startswith("distillation/overlap"):
            supplied.setdefault("kind", "teacher_topk")
            supplied.setdefault("available", None)
            # A recorded zero overlap ratio proves no overlapping support for
            # that aggregate. It never proves probability alignment.
            ratio = data.get("actor/distillation/overlap_ratio", data.get("distillation/overlap_ratio"))
            if normalized.endswith("overlap_token_advantage") and ratio == 0:
                supplied["available"] = False
                supplied["count"] = 0
                supplied["reason"] = "native zero fallback with zero reported overlap"
        metrics.append(_metric(name, value, definition, key, support=supplied))
    diagnostic = {"diagnostic_type": "distribution", "context": context,
        "reported_metrics": metrics, "label": ("veRL · 分布与蒸馏指标" if source_family == "verl" else "UniOPSD · credit 覆盖指标")}
    if training_step is not None:
        diagnostic["training_step"] = training_step
    _diagnostic(prepared, 0, prepared["selected"][0][2], diagnostic, demo, None, student_checkpoint_id)
    return _bundle(prepared, demo)


def verl_opd_configuration(native_config: dict, *, teacher=None, source_revision=None) -> dict:
    """Extract explicit native switches; unknown fields remain missing.

    Effective coefficient is a source-code rule, not proof of executed updates.
    use_policy_gradient and use_task_rewards are independent switches.
    """
    raw = _finite(native_config, "Native veRL configuration")
    if not isinstance(raw, dict) or not isinstance(raw.get("distillation"), dict):
        raise ValidationError("Native configuration requires distillation object")
    distill = raw["distillation"]
    loss = distill.get("distillation_loss", {})
    if not isinstance(loss, dict):
        raise ValidationError("distillation_loss must be an object")
    result = {"native_config": raw, "source": SOURCES["verl_losses"]["url"],
        "source_revision": source_revision or "unknown", "reviewed_source_sha256": SOURCES["verl_losses"]["sha256"],
        "configuration_only": True}
    if "enabled" in distill:
        _boolean(distill["enabled"], "enabled")
        result["enabled"] = distill["enabled"]
    for name in ("use_task_rewards", "use_policy_gradient"):
        if name in loss:
            _boolean(loss[name], name)
            result[name] = loss[name]
    for name in ("distillation_loss_coef", "topk", "loss_mode", "loss_max_clamp", "policy_loss_mode"):
        if name in loss:
            result[name] = copy.deepcopy(loss[name])
    if "distillation_loss_coef" in result:
        _number(result["distillation_loss_coef"], "distillation_loss_coef")
    if loss.get("use_task_rewards") is False:
        result["source_rule_effective_distillation_coef"] = 1.0
        result["coefficient_basis"] = "Reviewed native source sets outer distillation coefficient to 1.0 when task rewards are disabled; execution not observed."
    elif loss.get("use_task_rewards") is True and loss.get("distillation_loss_coef") is not None:
        result["source_rule_effective_distillation_coef"] = loss["distillation_loss_coef"]
        result["coefficient_basis"] = "Reviewed native source uses configured coefficient with task rewards; execution not observed."
    if teacher is not None:
        if not isinstance(teacher, dict):
            raise ValidationError("teacher must describe the explicit teacher construction/context")
        result["teacher"] = _finite(teacher, "Teacher configuration")
    return result
