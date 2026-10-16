"""Explicit offline adapter for YOUR already-exported JSONL result schema.

Produces a schema-v2 bundle with project/experiment/run ownership. No heuristic
answer parsing, scoring, or reconstructed tool turns. Full original records are
retained as source_record. Run `import` in a scratch workspace to validate first.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

MISSING = object()
# Reserved parent/identity fields cannot be supplied by an accidental raw mapping.
TARGETS = {
    "id", "dataset", "sample_id", "question", "gold_answers", "scores",
    "reward", "raw_final_output", "parsed_answer", "runtime_status",
    "termination_reason", "usage", "steps", "metadata",
}


def read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def get_path(record: Any, path: list[str | int]) -> Any:
    value = record
    try:
        for key in path:
            value = value[key]
        return value
    except (KeyError, IndexError, TypeError):
        return MISSING


def put_path(target: dict, path: str, value: Any) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        target = target.setdefault(key, {})
        if not isinstance(target, dict):
            raise ValueError(f"Conflicting mapping targets near {path!r}")
    target[keys[-1]] = value


def selected(records: Any, record_id: str, kind: str) -> dict:
    if not isinstance(records, list):
        raise ValueError(f"workspace-metadata.{kind} must be an array")
    matches = [r for r in records if isinstance(r, dict) and r.get("id") == record_id]
    if len(matches) != 1:
        raise ValueError(f"workspace-metadata must contain exactly one {kind} record {record_id!r}")
    return matches[0]


def convert(args: argparse.Namespace) -> dict:
    if args.step < 0:
        raise ValueError("--step must be nonnegative")
    panel = read_json(args.panel)
    protocol = read_json(args.protocol)
    mapping = read_json(args.mapping)
    if not isinstance(panel, dict) or not isinstance(protocol, dict):
        raise ValueError("Panel and protocol must be JSON objects")
    if not isinstance(mapping, dict) or not {"dataset", "sample_id"}.issubset(mapping):
        raise ValueError("Mapping must define dataset and sample_id")
    for target, path in mapping.items():
        if not isinstance(target, str) or target.split(".")[0] not in TARGETS or any(not part for part in target.split(".")):
            raise ValueError(f"Unsupported mapping target: {target!r}; never map evaluation_id or source_record")
        if not isinstance(path, list) or not all(type(k) in (str, int) for k in path):
            raise ValueError("Source paths must be JSON arrays of dictionary keys / list indices")
    if bool(panel.get("demo")) != bool(args.demo):
        raise ValueError("Panel demo flag and --demo must match. Do not relabel synthetic data as real.")

    if args.workspace_metadata:
        metadata = read_json(args.workspace_metadata)
        project = selected(metadata.get("projects"), args.project_id, "projects")
        experiment = selected(metadata.get("experiments"), args.experiment_id, "experiments")
        run = selected(metadata.get("runs"), args.run_id, "runs")
        if experiment.get("project_id") != project["id"] or run.get("experiment_id") != experiment["id"]:
            raise ValueError("Metadata project/experiment/run ancestry does not match requested IDs")
        if "project_id" in run and run["project_id"] != project["id"]:
            raise ValueError("Metadata run.project_id conflicts with its experiment")
        if bool(run.get("demo")) != bool(args.demo):
            raise ValueError("Run demo flag and --demo must match")
    else:
        project = {"id": args.project_id, "name": args.project_name or args.project_id}
        experiment = {"id": args.experiment_id, "project_id": args.project_id,
                      "name": args.experiment_name or args.experiment_id}
        run = {"id": args.run_id, "experiment_id": args.experiment_id,
               "name": args.run_name or args.run_id, "base_model": args.base_model,
               "model_revision": args.model_revision, "algorithm": args.algorithm,
               "seed": args.seed, "demo": args.demo}
    raw_path = Path(args.results)
    opener = gzip.open if raw_path.suffix == ".gz" else open
    traces = []
    with opener(raw_path, "rt", encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"Line {line_no}: expected a JSON object")
            trace = {"evaluation_id": args.evaluation_id, "source_record": raw}
            for target, path in mapping.items():
                value = get_path(raw, path)
                if value is not MISSING:
                    put_path(trace, target, value)
            if "dataset" not in trace or "sample_id" not in trace:
                raise ValueError(f"Line {line_no}: dataset / sample_id not found at specified path")
            # Deliberately no defaults for scores, tool contents, or runtime status.
            traces.append(trace)
    if not traces:
        raise ValueError("No result records supplied")
    result = {"schema_version": 2, "projects": [project], "experiments": [experiment],
              "runs": [run], "panels": [panel],
              "checkpoints": [{"id": args.checkpoint_id, "run_id": args.run_id,
                               "step": args.step}],
              "evaluations": [{"id": args.evaluation_id, "checkpoint_id": args.checkpoint_id,
                               "panel_id": panel["id"], "protocol": protocol}],
              "trajectories": traces}
    if args.complete:
        result["finish_evaluations"] = [{"id": args.evaluation_id, "status": "completed"}]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("panel", "results", "mapping", "protocol", "project-id", "experiment-id",
                "run-id", "checkpoint-id", "evaluation-id", "out"):
        parser.add_argument("--" + arg, required=True)
    parser.add_argument("--step", type=int, required=True)
    for field in ("project-name", "experiment-name", "run-name"):
        parser.add_argument("--" + field)
    parser.add_argument("--base-model", default="unknown")
    parser.add_argument("--model-revision", default="unknown")
    parser.add_argument("--algorithm", default="unknown")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--workspace-metadata", help="Reuse exact frozen projects/experiments/runs from this JSON; avoids conflicting re-registration")
    parser.add_argument("--complete", action="store_true", help="Only if every frozen question is present; importer will verify coverage")
    parser.add_argument("--demo", action="store_true", help="Flag supplied panel/run/results as synthetic")
    args = parser.parse_args()
    try:
        out = Path(args.out).expanduser()
        if out.exists():
            raise ValueError("Output exists. Choose a new path to preserve evidence.")
        bundle = convert(args)
        # Encode before creating the file; reject NaN / Infinity without partial output.
        encoded = json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
        print(f"Wrote {len(bundle['trajectories'])} explicitly mapped records to {out}. Validate using searchagent-lab import in a NEW workspace.")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
