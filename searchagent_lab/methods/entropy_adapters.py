"""Read saved Entropy evidence without loading a model, tokenizer or trainer.

veRL and RAGEN adapters accept explicitly saved logger dictionaries. SEAL's
score arrays require an explicit producer-side serialization hook. ARPO/SSRL
internal values use documented normalized rows; no native export is invented.
StudioRecorder archives original bytes; every event retains its source row,
line, hash and actual producer revision independently of the reviewed source.
"""
from __future__ import annotations

import copy
import hashlib
import math
from typing import Any

from ..core import ValidationError
from ..inspection.repository import MAX_FILE_BYTES, digest, strict_json
from .schema import SCHEMA, identifier, validate_object

REVIEWED_ON = "2026-10-10"
# Hashes identify reviewed upstream files, never the user's training revision.
SOURCES = {
    "verl_torch": {"url": "https://github.com/verl-project/verl/blob/main/verl/utils/torch_functional.py", "sha256": "bc7ff798e2f5c06e0121668490739814efc519ff66d4d7dd7c8fc40b4840eb03"},
    "verl_fsdp": {"url": "https://github.com/verl-project/verl/blob/main/verl/workers/engine/fsdp/transformer_impl.py", "sha256": "717b595c729469bde375234710b967e5b4a8d39ba93419f7b2a380f080483842"},
    "verl_trainer": {"url": "https://github.com/verl-project/verl/blob/main/verl/trainer/ppo/ray_trainer.py", "sha256": "eb41f99ccc66976eab14ebe35aa1aa5b7318e02469240ef1569ac4c884c94312"},
    "seal_draw_token": {"url": "https://github.com/WindyLee0822/SEAL/blob/main/draw_token.py", "sha256": "4f2a1e5edea26492653fe4d67e02a791456c730ddb1c310fd9e192ddc41b5b7e"},
    "ragen_collapse": {"url": "https://github.com/mll-lab-nu/RAGEN/blob/main/ragen/trainer/collapse_metrics.py", "sha256": "7f8cdd8aca3fe5be679ec20c6b096c799d3b020e29b6ddea900a88b12a8bd69b"},
    "ragen_mi_docs": {"url": "https://github.com/mll-lab-nu/RAGEN/blob/main/docs/reference_mutual_information_metrics.md", "sha256": "46a430010971512c7827463974d7f5431d22bc38223d766a015fd1e4464198d4"},
    "arpo_branch": {"url": "https://github.com/RUC-NLPIR/ARPO/blob/main/ARPO/verl_arpo_entropy/verl/workers/rollout/vllm_rollout/vllm_rollout_with_tools.py", "sha256": "8497a5eb1d10cf8772430f1eaf9faea8679126a6378bf5f484162345ca083da7"},
    "ssrl_search": {"url": "https://github.com/TsinghuaC3I/SSRL/blob/main/llm_agent/generation.py", "sha256": "480b919cd789134e890360a92670e2d389e90809857d53ee62160df918234f79"},
    "polygraph_risk": {"url": "https://github.com/IINemo/lm-polygraph/blob/main/src/lm_polygraph/ue_metrics/risk_cov_curve.py", "sha256": "f460316f05041c2d3f4e2c4eb7b8ab5be4bdbafbc09671e3775f37943b2199f4"},
    "polygraph_auc": {"url": "https://github.com/IINemo/lm-polygraph/blob/main/src/lm_polygraph/ue_metrics/roc_auc.py", "sha256": "e7509ec868458e3eab1738ff1c46c0792a493649b26d9171acda8ab960baac40"},
}
MAX_ROWS = 4000
MAX_TOKEN_VALUES = 131072
CONTEXTS = {"training_on_policy", "fixed_prefix_probe", "evaluation", "unknown"}
IDENTITY_FIELDS = {"checkpoint_id", "evaluation_id", "trajectory_id", "sample_id", "batch_id"}


def _finite(value: Any, label: str):
    try:
        digest(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError(label + " must contain only finite JSON values") from exc
    return copy.deepcopy(value)


def _number(value, label, *, nonnegative=False):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(label + " must be a finite number or null")
    if nonnegative and value < 0:
        raise ValidationError(label + " must be nonnegative")


def _revision(value):
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256):
        raise ValidationError("producer_revision must be explicit nonempty text")
    return value or "unknown"


def _parse(source, source_format):
    if source_format not in {"json", "jsonl", "rows"}:
        raise ValidationError("source_format must be json, jsonl or rows")
    if isinstance(source, bytes):
        if not 0 < len(source) <= MAX_FILE_BYTES:
            raise ValidationError("Entropy source must be 1 byte–50 MiB")
        if source_format == "rows":
            raise ValidationError("rows requires Python dictionaries, not file bytes")
        try:
            text = source.decode("utf-8-sig")
            if source_format == "jsonl":
                pairs = [(strict_json(line), i) for i, line in enumerate(text.splitlines(), 1) if line.strip()]
                rows, lines = [x[0] for x in pairs], [x[1] for x in pairs]
            else:
                parsed = strict_json(text)
                rows = parsed if isinstance(parsed, list) else [parsed]
                lines = [None] * len(rows)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValidationError("Invalid entropy JSON: " + str(exc)) from exc
        fingerprint, basis = hashlib.sha256(source).hexdigest(), "original_file_bytes"
    else:
        parsed = _finite(source, "Entropy rows")
        rows = parsed if isinstance(parsed, list) else [parsed]
        lines = [None] * len(rows)
        fingerprint, basis = digest(parsed), "canonical_json_not_original_file_bytes"
    if not 1 <= len(rows) <= MAX_ROWS or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("Entropy import needs 1–4000 row dictionaries; shard larger logs explicitly")
    return rows, lines, fingerprint, basis


def _prepare(source, *, scope, source_format, source_family, record_prefix,
             producer_revision, demo, source_complete=None, source_population_size=None):
    if not isinstance(scope, dict) or not scope or set(scope) - {"project_id", "experiment_id", "run_id"}:
        raise ValidationError("Provide explicit Lab project/experiment/run scope")
    for key, value in scope.items():
        identifier(value, key)
    if type(demo) is not bool or source_complete is not None and type(source_complete) is not bool:
        raise ValidationError("demo/source_complete must be boolean (source_complete may be unknown)")
    if source_population_size is not None and (type(source_population_size) is not int or source_population_size < 0):
        raise ValidationError("source_population_size must be nonnegative integer or unknown")
    rows, lines, fingerprint, basis = _parse(source, source_format)
    if source_population_size is not None and source_population_size < len(rows):
        raise ValidationError("Source population cannot be smaller than imported rows")
    if source_complete is True and source_population_size is not None and source_population_size != len(rows):
        raise ValidationError("Complete source row count must match source_population_size")
    revision = _revision(producer_revision)
    prefix = record_prefix or "entropy-" + fingerprint[:20]
    identifier(prefix, "record_prefix")
    coverage = {"imported_rows": len(rows), "source_complete": source_complete,
        "source_population_size": source_population_size,
        "boundary": "Only explicitly exported rows; completeness of a training batch or evaluation is not inferred."}
    provenance = {"adapter": "entropy-" + source_family + "-v1", "reviewed_on": REVIEWED_ON,
        "producer_revision": revision, "source_sha256": fingerprint, "hash_basis": basis}
    manifest = {"id": prefix + "-source", "kind": "trace", "title": "Entropy · 原始来源",
        "scope": copy.deepcopy(scope), "demo": demo, "usage": "unknown", "provenance": provenance,
        "data": {"format": "entropy-source-manifest-v1", "source_family": source_family,
            "source_format": source_format, "source_sha256": fingerprint, "hash_basis": basis,
            "coverage": coverage, "reported_only": True}}
    return {"rows": rows, "lines": lines, "prefix": prefix, "scope": copy.deepcopy(scope),
        "provenance": provenance, "manifest": manifest, "objects": [manifest], "demo": demo}


def _identity(identity_map, index, common):
    if identity_map is None:
        identity_map = {}
    if not isinstance(identity_map, dict):
        raise ValidationError("identity_map must map zero-based source row indices to explicit Lab IDs")
    normalized = {}
    for key, value in identity_map.items():
        if not (type(key) is int and key >= 0 or isinstance(key, str) and key.isascii() and key.isdigit()):
            raise ValidationError("identity_map keys must be nonnegative source row indices")
        if int(key) in normalized:
            raise ValidationError("Duplicate identity_map index")
        if not isinstance(value, dict) or set(value) - IDENTITY_FIELDS:
            raise ValidationError("Unknown entropy identity field")
        normalized[int(key)] = value
        for field, target in value.items():
            identifier(target, field)
    result = copy.deepcopy(normalized.get(index, {}))
    for field, target in common.items():
        if target is None:
            continue
        identifier(target, field)
        if result.get(field, target) != target:
            raise ValidationError("Conflicting entropy common and per-row identities")
        result[field] = target
    return result


def _event(prepared, event_id, kind, data, *, index=None):
    data = _finite(data, "Entropy evidence")
    data.setdefault("source_version", prepared["provenance"]["producer_revision"])
    data.setdefault("source_format", prepared["manifest"]["data"]["source_format"])
    if index is not None:
        data["native_source"] = {"row_index": index, "line_number": prepared["lines"][index],
            "source_sha256": prepared["provenance"]["source_sha256"],
            "hash_basis": prepared["provenance"]["hash_basis"], "row": copy.deepcopy(prepared["rows"][index])}
    obj = {"id": event_id, "kind": kind, "scope": copy.deepcopy(prepared["scope"]),
        "demo": prepared["demo"], "usage": "unknown", "provenance": copy.deepcopy(prepared["provenance"]),
        "derived_from": [prepared["manifest"]["id"]], "data": data}
    validate_object(obj)
    prepared["objects"].append(obj)
    return obj


def _bundle(prepared):
    for obj in prepared["objects"]:
        validate_object(obj)
    return {"method_schema": SCHEMA, "scope": prepared["scope"], "demo": prepared["demo"],
        "objects": prepared["objects"], "description": "Reported saved entropy evidence; no model, search or training executed."}


def verl_entropy_configuration(native_config, *, source_revision=None):
    """Read supplied veRL switches, retaining actual config and unknown fields."""
    data = _finite(native_config, "veRL configuration")
    if not isinstance(data, dict):
        raise ValidationError("native_config must be a dictionary")
    actor_rollout = data.get("actor_rollout_ref", {})
    if not isinstance(actor_rollout, dict):
        raise ValidationError("actor_rollout_ref must be a dictionary")
    actor, rollout = actor_rollout.get("actor", {}), actor_rollout.get("rollout", {})
    if not isinstance(actor, dict) or not isinstance(rollout, dict):
        raise ValidationError("actor and rollout configuration must be dictionaries")
    result = {"native_config": data, "configuration_only": True, "source_revision": _revision(source_revision),
        "schema_reference": copy.deepcopy(SOURCES["verl_trainer"])}
    for key in ("entropy_coeff", "calculate_entropy", "loss_agg_mode", "loss_scale_factor"):
        if key in actor:
            result[key] = copy.deepcopy(actor[key])
    if "entropy_coeff" in result:
        _number(result["entropy_coeff"], "entropy_coeff")
    if "calculate_entropy" in result and type(result["calculate_entropy"]) is not bool:
        raise ValidationError("calculate_entropy must be boolean")
    for key in ("temperature", "top_p", "top_k"):
        if key in rollout:
            result["sampling_" + key] = copy.deepcopy(rollout[key])
    return result


def native_entropy_definition(source_family, *, metric=None, parameters=None, producer_revision=None):
    """Source-specific template; unknown actual runtime facts stay absent.

    parameters are explicit logging metadata such as temperature/tokenizer/mask.
    Reviewed source identity is separate from the actual producer revision.
    """
    if parameters is not None and not isinstance(parameters, dict):
        raise ValidationError("definition parameters must be a dictionary")
    base = {"unit": "nats", "quantity_kind": "entropy", "log_base": "e",
        "normalization": "none", "token_scope": {"mask": "unknown"},
        "aggregation": {"name": "unknown"}, "distribution": {}, "support": {"kind": "unknown"}}
    if source_family == "verl":
        key = "verl_trainer"
        base.update(name="veRL · actor/entropy", estimator="full_vocab_token_entropy",
            formula="-sum_v p(v|prefix,T) * ln p(v|prefix,T)",
            support={"kind": "full_vocabulary", "sampling_truncation": "not_applied_by_this_entropy_forward"},
            distribution={"kind": "model_next_token", "temperature": "unknown"},
            aggregation={"name": "unknown", "source_rule": "agg_loss with response_mask, configured loss_agg_mode and loss_scale_factor"})
    elif source_family == "seal":
        key = "seal_draw_token"
        base.update(name="SEAL · 固定文本 token entropy", estimator="full_vocab_token_entropy",
            formula="-sum_v softmax(logits)[v] * log_softmax(logits)[v]",
            support={"kind": "full_vocabulary"}, distribution={"kind": "teacher_forced_next_token"},
            source_rule="Reviewed score_text uses unscaled logits, next-token shift, and returned continuation arrays. Actual producer protocol must be logged.")
    elif source_family == "arpo":
        key = "arpo_branch"
        base.update(name="ARPO · 有限候选分支分数", estimator="returned_candidate_entropy_contribution",
            quantity_kind="proxy", unit="dimensionless", normalization="divide_by_ln_vocab_size",
            formula="sum_positions sum_returned_candidates -exp(logp) * logp / ln(vocab_size)",
            support={"kind": "returned_candidates", "requested_top_k": 10, "tail": "not_observed"},
            top_k=10, window={"max_positions": 20, "aggregation": "sum_not_token_mean"},
            aggregation={"name": "window_sum_divide_ln_vocab_size"},
            distribution={"kind": "vllm_returned_logprob_candidates"},
            source_rule="Internal current_entropy_dict; producer hook must distinguish no-support fallback zero from a measured zero.")
    elif source_family == "ssrl":
        key = "ssrl_search"
        base.update(name="SSRL · 已采样 query token 的贡献", estimator="sampled_token_entropy_contribution",
            quantity_kind="proxy", unit="bits", log_base="2", normalization="token_mean",
            formula="mean_sampled_query_tokens(-p_sample * log2(p_sample)), p=clamp(exp(logp),1e-10,1)",
            support={"kind": "sampled_tokens_only", "full_vocabulary": False},
            token_scope={"segment": "first_search_query", "mask": "unknown"},
            aggregation={"name": "query_token_mean"}, distribution={"kind": "observed_token_logprob"},
            source_rule="Internal _calculate_search_entropy return; missing tag/out-of-range fallback zero is not certified zero uncertainty.")
    elif source_family == "ragen":
        key = "ragen_collapse"
        if not isinstance(metric, str) or "/" not in metric:
            raise ValidationError("RAGEN definition requires the exact namespaced metric")
        suffix = metric.rsplit("/", 1)[-1]
        base.update(name="RAGEN · " + metric, estimator="cross_prompt_reasoning_proxy", quantity_kind="proxy",
            distribution={"kind": "finite_prompt_cross_scoring"},
            support={"kind": "sampled_reasoning_and_prompts", "complete_population": "unknown"},
            token_scope={"segment": "reasoning_excluding_format_boundary", "mask": "unknown"},
            aggregation={"name": "sample_mean", "sample_namespace": metric.split("/", 1)[0]},
            normalization="sequence_sum" if "seq" in suffix else "length_normalize_before_prompt_logsumexp")
        if suffix.startswith("mi_") and suffix not in {"mi_upper_bound"}:
            base.update(quantity_kind="mi_proxy", estimator="finite_prompt_mi_proxy",
                formula="mean(matched_cross_logprob - logmeanexp(cross_prompt_logprob))",
                source_rule="Signed finite-sample proxy. Length-normalized scores are mixed after normalization; not strict sequence mutual information.")
            if "zscore" in suffix:
                base.update(unit="score", normalization="divide_by_recorded_marginal_std_or_ema_plus_eps")
        elif suffix.startswith("retrieval_"):
            base.update(unit="dimensionless", normalization="fraction_or_chance_margin",
                estimator="prompt_identification_accuracy", formula="rank original prompt using reasoning cross-logprob",
                source_rule="Prompt identification, including duplicate prompt signatures in the reviewed implementation; not Search evidence retrieval hit rate.")
        elif suffix in {"conditional_entropy_est", "conditional_entropy_seq_est"}:
            base.update(estimator="sampled_conditional_reasoning_entropy_proxy", formula="-mean(matched_cross_logprob)")
        elif suffix in {"reasoning_entropy_est", "reasoning_entropy_seq_est"}:
            base.update(estimator="finite_prompt_marginal_reasoning_entropy_proxy", formula="-mean(logmeanexp(cross_prompt_logprob))")
        else:
            base.update(estimator="ragen_reported_diagnostic", formula="source-specific reported diagnostic; retain exact field name")
    else:
        raise ValidationError("Definition source_family must be verl, seal, ragen, arpo or ssrl")
    base["implementation"] = {"source_url": SOURCES[key]["url"], "schema_reference_sha256": SOURCES[key]["sha256"],
        "reviewed_on": REVIEWED_ON, "producer_revision": _revision(producer_revision), "version": "source-template-v1"}
    base["schema_reference_sha256"] = SOURCES[key]["sha256"]
    if parameters:
        base.update(_finite(parameters, "Definition parameters"))
    return base


_RAGEN_SUFFIXES = {
    "mi_estimate", "mi_seq_estimate", "mi_upper_bound", "matched_log_prob_mean", "marginal_log_prob_mean",
    "conditional_entropy_est", "conditional_entropy_seq_est", "reasoning_entropy_est", "reasoning_entropy_seq_est",
    "mi_zscore", "mi_zscore_seq", "mi_zscore_ema", "mi_zscore_ema_seq", "marginal_std", "marginal_std_seq",
    "marginal_std_ema", "marginal_std_ema_seq", "retrieval_accuracy", "retrieval_chance_level", "retrieval_above_chance",
} | {name + "@" + str(k) for name in ("retrieval_accuracy", "retrieval_chance_level", "retrieval_above_chance") for k in (2, 4, 8)}
_RAGEN_PREFIXES = {"collapse", "collapse_first_turn_sample", "collapse_trajectory_sample"}


def adapt_entropy_metrics(source, *, scope, source_family, source_format="json", context="training_on_policy",
        training_step=None, checkpoint_id=None, evaluation_id=None, trajectory_id=None, identity_map=None,
        record_prefix=None, definition_parameters=None, demo=False, producer_revision=None,
        source_complete=None, source_population_size=None):
    """Import saved native logger dictionaries or explicit {metrics, step} rows.

    veRL actor/entropy is an aggregate; RAGEN metric namespaces and signed MI
    are kept. Additional native keys remain in the raw row. No token masks,
    prefix, teacher, task correctness or optimizer execution are inferred.
    """
    if source_family not in {"verl", "ragen"} or context not in CONTEXTS:
        raise ValidationError("Use native verl/ragen metrics with an explicit valid context")
    if context == "fixed_prefix_probe":
        raise ValidationError("A scalar native logger cannot establish a fixed prefix; use explicit entropy rows")
    if training_step is not None and (type(training_step) is not int or training_step < 0):
        raise ValidationError("training_step must be a nonnegative integer")
    prepared = _prepare(source, scope=scope, source_format=source_format, source_family=source_family,
        record_prefix=record_prefix, producer_revision=producer_revision, demo=demo,
        source_complete=source_complete, source_population_size=source_population_size)
    found = 0
    definitions = {}
    for index, row in enumerate(prepared["rows"]):
        metrics = row.get("metrics", row)
        if not isinstance(metrics, dict):
            raise ValidationError("metrics must be a dictionary")
        step = training_step
        if step is None and "metrics" in row:
            step = row.get("step", row.get("global_step"))
        if step is not None and (type(step) is not int or step < 0):
            raise ValidationError("Native metric step must be a nonnegative integer")
        identities = _identity(identity_map, index, {"checkpoint_id": checkpoint_id,
            "evaluation_id": evaluation_id, "trajectory_id": trajectory_id})
        for metric, value in metrics.items():
            recognized = metric == "actor/entropy" if source_family == "verl" else (
                isinstance(metric, str) and "/" in metric and metric.split("/", 1)[0] in _RAGEN_PREFIXES
                and metric.rsplit("/", 1)[-1] in _RAGEN_SUFFIXES)
            if not recognized:
                continue
            _number(value, metric, nonnegative=source_family == "verl")
            if source_family == "ragen" and value is not None:
                suffix = metric.rsplit("/", 1)[-1]
                if suffix.startswith(("retrieval_accuracy", "retrieval_chance_level")) and not 0 <= value <= 1:
                    raise ValidationError("RAGEN prompt identification fraction must be within [0,1]")
                if suffix.startswith("retrieval_above_chance") and not -1 <= value <= 1:
                    raise ValidationError("RAGEN prompt identification chance margin must be within [-1,1]")
            if metric not in definitions:
                did = prepared["prefix"] + "-definition-" + digest(metric)[:12]
                definition = native_entropy_definition(source_family, metric=metric,
                    parameters=definition_parameters, producer_revision=producer_revision)
                _event(prepared, did, "entropy_definition", definition)
                definitions[metric] = did
            data = {"definition_id": definitions[metric], "context": context, "value": value,
                "native_metric_name": metric, "label": metric, "reported_only": True, **identities,
                "coverage": copy.deepcopy(prepared["manifest"]["data"]["coverage"])}
            if value is None:
                data["missing_reason"] = "Native logger explicitly reported null; no value reconstructed."
            if step is not None:
                data["training_step"] = step
            kind = "entropy_observation" if source_family == "verl" else "entropy_probe"
            if kind == "entropy_probe":
                data["probe_type"] = "prompt_identification" if "/retrieval_" in metric else "input_dependence"
            _event(prepared, prepared["prefix"] + "-row-" + str(index) + "-" + digest(metric)[:12], kind, data, index=index)
            found += 1
    if not found:
        raise ValidationError("No reviewed entropy metric fields found; use an explicit definition and normalized row exporter")
    prepared["manifest"]["data"]["coverage"]["recognized_metrics"] = found
    return _bundle(prepared)


def _tokens(tokens):
    if not isinstance(tokens, dict):
        raise ValidationError("tokens must be parallel named arrays")
    arrays = [value for value in tokens.values() if isinstance(value, list)]
    if arrays and (len({len(value) for value in arrays}) != 1 or len(arrays[0]) > MAX_TOKEN_VALUES):
        raise ValidationError("Token arrays must have equal lengths and at most 131072 positions")
    return _finite(tokens, "Token arrays")


def adapt_entropy_rows(source, *, scope, definition_id, source_family="normalized", source_format="jsonl",
        context=None, common=None, identity_map=None, record_prefix=None, demo=False,
        producer_revision=None, source_complete=None, source_population_size=None):
    """Read explicit normalized observation/probe rows (not an upstream format).

    A row is the method data dictionary, with optional kind='entropy_probe'.
    The caller supplies the previously registered immutable definition_id and
    actual context/identity metadata. Data values, nulls, masks and token arrays
    are preserved; no logits, prefixes, probabilities or outcomes are invented.
    """
    identifier(definition_id, "definition_id")
    if not isinstance(source_family, str) or not source_family or len(source_family) > 80:
        raise ValidationError("source_family must identify the explicit producer hook")
    if context is not None and context not in CONTEXTS:
        raise ValidationError("Unknown entropy context")
    if common is not None and not isinstance(common, dict):
        raise ValidationError("common must be explicit shared method data")
    prepared = _prepare(source, scope=scope, source_format=source_format, source_family=source_family,
        record_prefix=record_prefix, producer_revision=producer_revision, demo=demo,
        source_complete=source_complete, source_population_size=source_population_size)
    for index, row in enumerate(prepared["rows"]):
        data = _finite(common or {}, "Shared entropy metadata")
        for key, value in row.items():
            if key in data and data[key] != value:
                raise ValidationError("Conflicting common and row field: " + key)
            data[key] = copy.deepcopy(value)
        kind = data.pop("kind", "entropy_observation")
        if kind not in {"entropy_observation", "entropy_probe"}:
            raise ValidationError("Normalized rows support entropy_observation or entropy_probe only")
        if data.get("definition_id", definition_id) != definition_id:
            raise ValidationError("Conflicting definition_id")
        data["definition_id"] = definition_id
        if context is not None:
            if data.get("context", context) != context:
                raise ValidationError("Conflicting context")
            data["context"] = context
        for field, target in _identity(identity_map, index, {}).items():
            if data.get(field, target) != target:
                raise ValidationError("Conflicting mapped and source identity")
            data[field] = target
        if "tokens" in data:
            data["tokens"] = _tokens(data["tokens"])
        data.setdefault("coverage", copy.deepcopy(prepared["manifest"]["data"]["coverage"]))
        data["reported_only"] = True
        _event(prepared, prepared["prefix"] + "-row-" + str(index), kind, data, index=index)
    return _bundle(prepared)


def adapt_seal_scores(source, *, scope, definition_id, common, token_mask=None, token_positions=None,
        segment_types=None, source_format="json", record_prefix=None, demo=False, producer_revision=None):
    """Read serialized return arrays from SEAL score_text.

    Its normal output is HTML; callers must explicitly save the return object.
    A binary token_mask permits a masked mean over saved entropies. Without
    one scalar value stays unknown. Controlled prefix comparison also needs
    explicit original token_positions and segment_types; no offsets inferred.
    """
    identifier(definition_id, "definition_id")
    if not isinstance(common, dict):
        raise ValidationError("Supply actual checkpoint/prefix/tokenizer/scoring metadata in common")
    if "tokens" in common:
        raise ValidationError("SEAL tokens come from saved arrays; pass token_positions/segment_types/token_mask explicitly")
    prepared = _prepare(source, scope=scope, source_format=source_format, source_family="seal-saved-scores",
        record_prefix=record_prefix, producer_revision=producer_revision, demo=demo)
    if len(prepared["rows"]) != 1:
        raise ValidationError("Import each SEAL scoring return object separately")
    row = prepared["rows"][0]
    fields = {"input_ids": "token_ids", "token_texts": "tokens", "obs_logprob": "log_probs",
        "obs_prob": "probabilities", "entropy": "entropy"}
    if any(not isinstance(row.get(key), list) for key in fields):
        raise ValidationError("SEAL scores require input_ids/token_texts/obs_logprob/obs_prob/entropy arrays")
    tokens = _tokens({target: row[key] for key, target in fields.items()})
    for value in tokens["entropy"]:
        _number(value, "SEAL token entropy", nonnegative=True)
    for value in tokens["log_probs"]:
        _number(value, "SEAL observed logprob")
        if value is not None and value > 0:
            raise ValidationError("Observed logprob must be nonpositive")
    for value in tokens["probabilities"]:
        _number(value, "SEAL observed probability", nonnegative=True)
        if value is not None and value > 1:
            raise ValidationError("Observed probability must be in [0,1]")
    count = len(tokens["entropy"])
    if token_positions is not None:
        if not isinstance(token_positions, list) or len(token_positions) != count or any(type(x) is not int or x < 0 for x in token_positions):
            raise ValidationError("token_positions must be parallel original nonnegative positions")
        if len(set(token_positions)) != len(token_positions):
            raise ValidationError("Original token positions must be distinct")
        tokens["positions"] = copy.deepcopy(token_positions)
    if segment_types is not None:
        if not isinstance(segment_types, list) or len(segment_types) != count or any(not isinstance(x, str) or not x.strip() for x in segment_types):
            raise ValidationError("segment_types must explicitly label each saved token position")
        tokens["segment_types"] = copy.deepcopy(segment_types)
    data = _finite(common, "SEAL protocol")
    if data.get("definition_id", definition_id) != definition_id:
        raise ValidationError("Conflicting SEAL definition_id")
    data.update(definition_id=definition_id, tokens=tokens, token_count=count, reported_only=True,
        schema_reference_sha256=SOURCES["seal_draw_token"]["sha256"])
    if token_mask is None:
        data.update(value=None, missing_reason="No explicit model-token mask supplied; returned token arrays are preserved without a pooled entropy.")
    else:
        if not isinstance(token_mask, list) or len(token_mask) != count or any(type(x) is not int or x not in (0, 1) for x in token_mask):
            raise ValidationError("token_mask must be a parallel explicit 0/1 array")
        tokens["mask"] = copy.deepcopy(token_mask)
        selected = [h for h, selected in zip(tokens["entropy"], token_mask) if selected]
        if selected and all(h is not None for h in selected):
            data["value"] = math.fsum(selected) / len(selected)
            data["derived_value"] = {"operation": "masked_mean_of_recorded_token_entropy", "selected_tokens": len(selected)}
            data.pop("missing_reason", None)
        else:
            data.update(value=None, missing_reason="No supported selected tokens, or selected token entropy is missing.")
    _event(prepared, prepared["prefix"] + "-scores", "entropy_observation", data, index=0)
    return _bundle(prepared)
