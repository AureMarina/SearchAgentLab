"""Explicit, small integration surface; no veRL/torch imports or monkey patches."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from .config import default_data_dir
from .store import Store


class Recorder:
    """Write local evidence from a rank-0 logger or an offline conversion script.

    For distributed training, only your coordinator/rank 0 should log shared
    metrics. Agent workers can write JSONL sidecars and import them afterwards.
    This class never loads a checkpoint, calls a model, or computes a reward.
    """
    def __init__(self, data_dir: str | Path | None = None):
        self.store = Store(default_data_dir() if data_dir is None else data_dir)

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.store.close()

    def register_run(self, *, id: str, name: str, **metadata: Any) -> str:
        return self.store.register_run({"id": id, "name": name, **metadata})

    def log_metrics(self, run_id: str, step: int, metrics: dict[str, float | None]) -> None:
        self.store.log_metrics(run_id, step, metrics)

    def register_checkpoint(self, *, id: str, run_id: str, step: int, **metadata: Any) -> str:
        return self.store.register_checkpoint({"id": id, "run_id": run_id, "step": step, **metadata})

    def register_panel(self, panel: dict) -> str:
        return self.store.register_panel(panel)

    def start_evaluation(self, *, id: str, checkpoint_id: str, panel_id: str, protocol: dict, **metadata: Any) -> str:
        return self.store.start_evaluation({"id": id, "checkpoint_id": checkpoint_id, "panel_id": panel_id, "protocol": protocol, **metadata})

    def log_trajectory(self, evaluation_id: str, trajectory: dict) -> str:
        return self.store.log_trajectory(evaluation_id, trajectory)

    def finish_evaluation(self, evaluation_id: str, status: str = "completed") -> None:
        self.store.finish_evaluation(evaluation_id, status)

    def set_run_status(self, run_id: str, status: str) -> None:
        self.store.set_run_status(run_id, status)

    def register_project(self, *, id: str, name: str, **metadata: Any) -> str:
        return self.store.register_project({"id": id, "name": name, **metadata})

    def register_experiment(self, *, id: str, project_id: str, name: str, **metadata: Any) -> str:
        return self.store.register_experiment({"id": id, "project_id": project_id, "name": name, **metadata})

    def note_experiment(self, experiment_id: str, *, note: str, kind: str = "observation", evidence: list | None = None) -> dict:
        return self.store.note_experiment(experiment_id, {"note": note, "kind": kind, "evidence": evidence or []})

    def reference_evaluation(self, experiment_id: str, evaluation_id: str, reason: str) -> None:
        self.store.add_reference(experiment_id, evaluation_id, reason)
