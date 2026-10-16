"""Strict, transactional import of documented JSON/JSONL evidence, plus CSV metrics.

No heuristic reconstruction of tool inputs, no pickle, and no scorer rewriting.
Unknown original fields are retained in the trajectory JSON.
"""
from __future__ import annotations
import csv
import gzip
import json
from pathlib import Path
from typing import Any
from .store import Store, ValidationError, digest, clone


def _load_json(raw: str) -> Any:
    def reject(value: str) -> None:
        raise ValidationError(f"Non-finite JSON number: {value}")
    try:
        return json.loads(raw, parse_constant=reject)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def apply_event(store: Store, event: dict) -> None:
    if not isinstance(event, dict) or "type" not in event:
        raise ValidationError("Every JSONL line needs a type and data")
    kind = event["type"]
    d = event.get("data")
    if not isinstance(d, dict):
        raise ValidationError("Event data must be an object")
    if kind == "project":
        store.register_project(d)
    elif kind == "experiment":
        store.register_experiment(d)
    elif kind == "experiment_note":
        note = clone(d["note"])
        note.setdefault("event_id", "import-note-"+digest([d["experiment_id"],note])[:32])
        store.note_experiment(d["experiment_id"], note)
    elif kind == "reference":
        store.add_reference(d["experiment_id"], d["evaluation_id"], d["reason"])
    elif kind == "run":
        store.register_run(d)
    elif kind == "checkpoint":
        store.register_checkpoint(d)
    elif kind == "panel":
        store.register_panel(d)
    elif kind == "metrics":
        store.log_metrics(d["run_id"], d["step"], d["metrics"])
    elif kind == "evaluation":
        store.start_evaluation(d)
    elif kind == "trajectory":
        store.log_trajectory(d["evaluation_id"], d)
    elif kind == "evaluation_finish":
        store.finish_evaluation(d["id"], d.get("status", "completed"))
    elif kind == "run_status":
        store.set_run_status(d["id"], d["status"])
    else:
        raise ValidationError(f"Unknown event type: {kind}")


def import_bundle(store: Store, bundle: dict) -> dict:
    if not isinstance(bundle, dict) or bundle.get("schema_version") not in (1, 2):
        raise ValidationError("Bundle must contain schema_version: 1 or 2")
    allowed = {"schema_version", "description", "projects", "experiments", "experiment_notes", "references", "runs", "panels", "checkpoints", "metrics", "evaluations", "trajectories", "finish_evaluations"}
    unexpected = set(bundle) - allowed
    if unexpected:
        raise ValidationError(f"Unknown top-level keys: {', '.join(sorted(unexpected))}. Evidence export is not an import bundle")
    counts = {}
    with store.transaction():
        for key, kind in (("projects", "project"), ("experiments", "experiment"), ("runs", "run"), ("panels", "panel"), ("checkpoints", "checkpoint"), ("metrics", "metrics"), ("evaluations", "evaluation"), ("trajectories", "trajectory"), ("finish_evaluations", "evaluation_finish"), ("references", "reference"), ("experiment_notes", "experiment_note")):
            records = bundle.get(key, [])
            if not isinstance(records, list):
                raise ValidationError(f"{key} must be a list")
            counts[key] = len(records)
            for index, record in enumerate(records):
                try:
                    if bundle["schema_version"] == 2 and kind == "run" and not record.get("experiment_id"):
                        raise ValidationError("v2 run must declare experiment_id; do not use a checkpoint label as ownership")
                    apply_event(store, {"type": kind, "data": record})
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValidationError(f"{key}[{index}]: {exc}") from exc
    return counts


def import_file(store: Store, path: str | Path) -> dict:
    file = Path(path).expanduser()
    if not file.is_file():
        raise ValidationError(f"Import file does not exist: {file}")
    opener = gzip.open if file.suffix == ".gz" else open
    base_suffix = file.with_suffix("").suffix if file.suffix == ".gz" else file.suffix
    with opener(file, "rt", encoding="utf-8-sig") as stream:
        if base_suffix == ".json":
            raw = stream.read(256 * 1024 * 1024 + 1)
            if len(raw) > 256 * 1024 * 1024:
                raise ValidationError("JSON bundle exceeds 256 MiB. Use JSONL streaming import")
            return import_bundle(store, _load_json(raw))
        if base_suffix != ".jsonl":
            raise ValidationError("Supported: .json, .json.gz, .jsonl, .jsonl.gz; CSV uses import-metrics")
        counts: dict[str, int] = {}
        with store.transaction():
            line_number = 0
            while True:
                raw = stream.readline(32 * 1024 * 1024 + 1)
                if not raw:
                    break
                line_number += 1
                if len(raw) > 32 * 1024 * 1024:
                    raise ValidationError(f"Line {line_number} exceeds 32 MiB")
                if not raw.strip():
                    continue
                try:
                    event = _load_json(raw)
                    apply_event(store, event)
                    counts[event["type"]] = counts.get(event["type"], 0) + 1
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValidationError(f"Line {line_number}: {exc}") from exc
        return counts


def import_metrics_csv(store: Store, path: str | Path, run_id: str, step_key: str = "step") -> dict:
    count = 0
    with store.transaction(), Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or step_key not in reader.fieldnames:
            raise ValidationError(f"CSV needs a '{step_key}' column")
        for line, row in enumerate(reader, start=2):
            try:
                step = int(row[step_key])
                metrics = {key: (float(value) if value not in (None, "") else None) for key, value in row.items() if key != step_key}
                store.log_metrics(run_id, step, metrics)
                count += 1
            except (ValueError, TypeError) as exc:
                raise ValidationError(f"CSV line {line}: {exc}") from exc
    return {"metric_rows": count}
