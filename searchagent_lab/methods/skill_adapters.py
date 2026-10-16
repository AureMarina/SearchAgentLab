"""Read-only adapters for saved Skill reports, with explicit identity binding.

These adapters never execute upstream code or infer a skill version from a
name. Original JSON is retained as a source object; importing via StudioRecorder
also preserves the original file bytes. Reported utility/assertion scores are
not promoted to a controlled SearchAgent Lab evaluation or training credit.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from ..core import ValidationError
from ..inspection.repository import MAX_FILE_BYTES, digest, strict_json
from .schema import SCHEMA, identifier, validate_object


SLIM_SOURCE = "https://github.com/ejhshen/SLIM/blob/main/slim_method/lifecycle.py"
SLIM_SOURCE_SHA256 = "ed1a49146b565f89b721ba9a75ba96473f9eeb300a404437877343b8e3b5471b"
SKILL_CREATOR_SCHEMA = "https://github.com/anthropics/skills/blob/main/skills/skill-creator/references/schemas.md"
SKILL_CREATOR_AGGREGATOR = "https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/aggregate_benchmark.py"
REVIEWED_ON = "2026-10-10"
_UTILITY_FIELDS = ("perf_with", "perf_without", "utility", "utility_ema", "usage_count",
                   "low_utility_streak", "failure_bucket_size")


def _json_object(value: Any, label: str) -> dict:
    if isinstance(value, bytes):
        if not 0 < len(value) <= MAX_FILE_BYTES:
            raise ValidationError(label + " must be 1 byte–50 MiB")
        try:
            value = strict_json(value.decode("utf-8-sig"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValidationError(label + " must be finite UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValidationError(label + " must be a JSON object")
    try:
        digest(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError(label + " must contain finite JSON") from exc
    return copy.deepcopy(value)


def _scope(scope: dict) -> dict:
    if not isinstance(scope, dict) or not scope or set(scope) - {"project_id", "experiment_id", "run_id"}:
        raise ValidationError("An explicit project/experiment/run scope is required")
    for key, value in scope.items():
        identifier(value, key)
    return copy.deepcopy(scope)


def _subject(value: dict) -> dict:
    if not isinstance(value, dict) or sum(bool(value.get(k)) for k in ("asset_id", "bank_id", "instance_id")) != 1:
        raise ValidationError("Map each source skill to exactly one asset_id, bank_id or instance_id")
    allowed = {"asset_id", "bank_id", "instance_id", "member", "member_content_hash"}
    if set(value) - allowed:
        raise ValidationError("Unknown skill identity mapping field")
    for key in ("asset_id", "bank_id", "instance_id"):
        if value.get(key):
            identifier(value[key], key)
    if "member" in value and not value.get("bank_id"):
        raise ValidationError("A legacy member mapping requires bank_id")
    return copy.deepcopy(value)


def _ref(event: dict, pointer: str = "") -> dict:
    return {"object_id": event["id"], "pointer": pointer, "sha256": digest(event["data"])}


def _event(record_id, kind, data, scope, demo, usage, **attributes):
    event = {"id": record_id, "kind": kind, "data": data, "scope": scope,
             "demo": demo, "usage": usage, **attributes}
    validate_object(event)
    return event


def _prefix(explicit: str | None, kind: str, source: Any, scope: dict) -> str:
    value = explicit if explicit is not None else kind + "-" + digest([source, scope])[:24]
    identifier(value, "record_prefix")
    if len(value) > 120:
        raise ValidationError("record_prefix may contain at most 120 characters")
    return value


def adapt_slim_lifecycle(report: bytes | dict, *, scope: dict, subject_map: dict,
                         checkpoint_id: str | None = None, record_prefix: str | None = None,
                         demo: bool = False) -> dict:
    """Adapt a native ``slim_lifecycle_stepN.json`` saved by lifecycle.update.

    ``subject_map`` binds upstream skill IDs to existing frozen Lab assets or
    banks. The general-skill group must be mapped explicitly as a group; its
    reported utility is never distributed across individual skills. Canonical
    ``audited_task_specific_skills`` takes precedence over its ``audited_skills``
    alias, so upstream aliases are not double-counted. Every audited entry must
    have an explicit identity mapping; skipped/expanded summaries remain raw.
    """
    data = _json_object(report, "SLIM lifecycle report")
    sc = _scope(scope)
    if not sc.get("run_id"):
        raise ValidationError("A lifecycle report needs an explicit run_id")
    if not isinstance(subject_map, dict):
        raise ValidationError("subject_map must map upstream skill IDs to frozen identities")
    step = data.get("global_step")
    if type(step) is not int or step < 0:
        raise ValidationError("SLIM lifecycle report requires nonnegative global_step")
    if checkpoint_id is not None:
        identifier(checkpoint_id, "checkpoint_id")
    rows_key = "audited_task_specific_skills" if "audited_task_specific_skills" in data else "audited_skills"
    rows = data.get(rows_key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("SLIM lifecycle report requires an audited skill array")
    audits = [(row, f"/{rows_key}/{index}", "task_specific") for index, row in enumerate(rows)]
    group = data.get("general_group")
    if group is not None:
        if not isinstance(group, dict):
            raise ValidationError("SLIM general_group must be an object or null")
        audits.insert(0, (group, "/general_group", "general_group"))
    prefix = _prefix(record_prefix, "slim", data, sc)
    source = _event(prefix + "-source", "extension.slim_lifecycle", data, sc, demo, "evolution",
        title="SLIM lifecycle · original report",
        provenance={"adapter": "slim-lifecycle-v1", "reviewed_on": REVIEWED_ON,
                    "upstream_source": SLIM_SOURCE, "upstream_source_sha256": SLIM_SOURCE_SHA256})
    if isinstance(report, bytes):
        source["provenance"]["source_file_sha256"] = hashlib.sha256(report).hexdigest()
    events = [source]
    for index, (row, pointer, audit_scope) in enumerate(audits):
        skill_id = row.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            raise ValidationError("Each SLIM audit needs an upstream skill_id")
        if skill_id not in subject_map:
            raise ValidationError("Missing explicit frozen identity mapping for SLIM skill: " + skill_id)
        identity = _subject(subject_map[skill_id])
        if audit_scope == "general_group" and not identity.get("bank_id"):
            raise ValidationError("SLIM general_group utility must map to an explicit frozen group bank_id, not an individual skill")
        observed = {**identity, "event": "audit", "step": step,
            "source_skill_id": skill_id, "audit_scope": audit_scope,
            "feedback_role": "development_feedback", "decision_basis": "upstream_reported_validation",
            "reported_utility": {key: copy.deepcopy(row[key]) for key in _UTILITY_FIELDS if key in row},
            "source_refs": [_ref(source, pointer)],
            "comparison_status": "reported_only",
            "limitation": "Reported SLIM validation utility; matched Lab evaluations are not supplied by this file."}
        if checkpoint_id is not None:
            observed["checkpoint_id"] = checkpoint_id
        for key in ("status", "decision"):
            if key in row:
                observed[key] = copy.deepcopy(row[key])
        events.append(_event(prefix + f"-audit-{index}", "skill_evolution", observed, sc, demo, "evolution",
                             derived_from=[source["id"]], title="SLIM · " + skill_id))
    return {"method_schema": SCHEMA, "scope": sc, "demo": demo, "objects": events,
            "description": "Explicitly mapped SLIM lifecycle report. Raw utility is not verified controlled gain or instance credit."}


def adapt_skill_creator_results(documents: dict[str, bytes | dict], *, scope: dict, subject: dict,
                                record_prefix: str | None = None, checkpoint_id: str | None = None,
                                feedback_role: str = "unknown", demo: bool = False) -> dict:
    """Retain grading/timing/benchmark JSON as reported regression evidence.

    ``subject`` explicitly pins the evaluated version. Expectations and report
    averages do not become Search-QA EM. Upstream aggregate_benchmark has used
    output character count as a token fallback, so benchmark ``tokens`` remains
    a reported field with unspecified provenance. Only timing.json.total_tokens
    is labeled as that specific reported counter; no counters are invented.
    """
    if not isinstance(documents, dict) or not documents or set(documents) - {"grading.json", "timing.json", "benchmark.json"}:
        raise ValidationError("Provide one or more grading.json, timing.json, benchmark.json documents")
    if feedback_role not in ("unknown", "development_feedback", "frozen_test"):
        raise ValidationError("feedback_role is unknown, development_feedback or frozen_test")
    sc = _scope(scope)
    if not sc.get("run_id"):
        raise ValidationError("A Skill result report needs an explicit run_id")
    identity = _subject(subject)
    parsed = {name: _json_object(value, name) for name, value in documents.items()}
    prefix = _prefix(record_prefix, "skillcreator", parsed, sc)
    usage = "test" if feedback_role == "frozen_test" else "validation" if feedback_role == "development_feedback" else "unknown"
    events = []
    for index, (name, data) in enumerate(sorted(parsed.items())):
        events.append(_event(prefix + f"-source-{index}", "extension.skill_creator_result", data, sc, demo, usage,
            title=name, provenance={"adapter": "skill-creator-results-v1", "source_filename": name,
                "reviewed_on": REVIEWED_ON, "upstream_schema": SKILL_CREATOR_SCHEMA,
                "upstream_aggregator": SKILL_CREATOR_AGGREGATOR}))
        if isinstance(documents[name], bytes):
            events[-1]["provenance"]["source_file_sha256"] = hashlib.sha256(documents[name]).hexdigest()
    reported = {}
    if "grading.json" in parsed:
        grading = parsed["grading.json"]
        if not isinstance(grading.get("summary"), dict) or not isinstance(grading.get("expectations"), list):
            raise ValidationError("grading.json requires summary and expectations")
        reported["grading"] = {key: copy.deepcopy(grading[key]) for key in
            ("summary", "expectations", "execution_metrics", "timing") if key in grading}
        reported["score_definition"] = "Reported expectation pass rate, not end-to-end task EM"
    if "timing.json" in parsed:
        timing = parsed["timing.json"]
        if not any(key in timing for key in ("total_duration_seconds", "total_tokens")):
            raise ValidationError("timing.json requires duration or token counter")
        reported["timing"] = copy.deepcopy(timing)
        if "total_tokens" in timing:
            reported["token_source"] = "timing.json.total_tokens (upstream reported)"
    if "benchmark.json" in parsed:
        benchmark = parsed["benchmark.json"]
        if not isinstance(benchmark.get("runs"), list) or not isinstance(benchmark.get("run_summary"), dict):
            raise ValidationError("benchmark.json requires runs and run_summary")
        for run in benchmark["runs"]:
            if not isinstance(run, dict) or not isinstance(run.get("configuration"), str) or not isinstance(run.get("result"), dict):
                raise ValidationError("Benchmark runs require configuration and nested result")
        reported["benchmark"] = {key: copy.deepcopy(benchmark[key]) for key in
            ("metadata", "runs", "run_summary", "notes") if key in benchmark}
        reported["benchmark_token_source"] = "unknown; upstream aggregator may use output_chars fallback"
    evidence = {**identity, "event": "benchmark_report", "feedback_role": feedback_role,
        "comparison_status": "reported_only", "reported_results": reported,
        "source_refs": [_ref(event) for event in events],
        "limitation": "Reported assertions/timing only; no skill invocation, local execution, policy gain or controlled EM is inferred."}
    if checkpoint_id is not None:
        identifier(checkpoint_id, "checkpoint_id")
        evidence["checkpoint_id"] = checkpoint_id
    events.append(_event(prefix + "-report", "skill_evolution", evidence, sc, demo, usage,
                         derived_from=[event["id"] for event in events], title="Skill regression · reported results"))
    return {"method_schema": SCHEMA, "scope": sc, "demo": demo, "objects": events,
            "description": "Reported Skill creator regression evidence with explicit version identity; no inference or training executed."}


def source_bytes_sha256(raw: bytes) -> str:
    """Small public helper for exact source-file receipts, separate from JSON hash."""
    if not isinstance(raw, bytes):
        raise ValidationError("Source content must be bytes")
    return hashlib.sha256(raw).hexdigest()
