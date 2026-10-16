"""Research-aware storage. Projects own experiments; experiments own runs.

Version-1 evidence is migrated into a separate unassigned project, never guessed
from model or algorithm names. Shared manifests and completed traces are immutable.
All comparisons use explicitly recorded evidence; no model or scorer is executed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from .core import (Store as CoreStore, ValidationError, NotFoundError, now, dumps,
                   digest, ident, text, integer, number, clone, PROTOCOL_KEYS, OUTCOMES)

LEGACY_PROJECT = "legacy-unassigned"
RESEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiments(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_ownership(
 run_id TEXT PRIMARY KEY REFERENCES runs(id), experiment_id TEXT NOT NULL REFERENCES experiments(id));
CREATE TABLE IF NOT EXISTS experiment_notes(
 id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL REFERENCES experiments(id),
 created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baseline_references(
 id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL REFERENCES experiments(id),
 evaluation_id TEXT NOT NULL REFERENCES evaluations(id), data TEXT NOT NULL,
 UNIQUE(experiment_id,evaluation_id));
CREATE TABLE IF NOT EXISTS isolation_audits(
 id INTEGER PRIMARY KEY AUTOINCREMENT, panel_id TEXT NOT NULL REFERENCES panels(id),
 created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ownership_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
 created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_experiment_project ON experiments(project_id);
CREATE INDEX IF NOT EXISTS idx_run_experiment ON run_ownership(experiment_id);
CREATE INDEX IF NOT EXISTS idx_isolation_panel ON isolation_audits(panel_id,id);
"""

# These are records of execution settings, not claims that a runtime obeyed them.
PROTOCOL_V2_PATHS = (
    "scorer.name", "scorer.version", "parser.name", "parser.version",
    "input.prompt_sha256", "input.chat_template_sha256", "input.tokenizer_revision",
    "input.stop_conditions", "retriever.corpus_version", "retriever.index_sha256",
    "retriever.version", "retriever.top_k", "retriever.ranking", "retriever.truncation",
    "budget.max_tool_calls", "budget.max_output_tokens", "budget.context_window",
    "decoding.temperature", "runtime.backend", "runtime.backend_version",
    "runtime.hardware", "runtime.dtype", "runtime.batch_size", "runtime.determinism",
    "state_reset.per_question", "state_reset.between_evaluations", "mode", "groundtruth_to_agent",
)
TRACE_FIELDS = ("index", "kind", "input", "output", "observation", "tool_call",
                "tool_response", "model_visible", "error", "status", "token_ids")


def is_training_record(record: dict) -> bool:
    """Honor explicit origins across native and imported record spellings."""
    for key in ("purpose", "record_purpose", "evaluation_purpose", "origin", "record_kind", "usage_context"):
        value = record.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if value in ("train_rollout", "training_rollout", "training", "sft_demonstration"):
            return True
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        for key in ("purpose", "record_purpose", "evaluation_purpose", "origin"):
            value = metadata.get(key)
            if isinstance(value, dict): value = value.get("value")
            if value in ("train_rollout", "training_rollout", "training", "sft_demonstration"): return True
    return False


def protocol_readiness(protocol: dict) -> dict:
    missing = []
    for path in PROTOCOL_V2_PATHS:
        value: Any = protocol
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value is None or value == "" or value == {} or CoreStore._contains_unknown(value):
            missing.append(path)
    if protocol.get("mode") != "inference_only":
        missing.append("mode must be inference_only")
    if protocol.get("groundtruth_to_agent") is not False:
        missing.append("groundtruth_to_agent must be false")
    for key in ("per_question", "between_evaluations"):
        if not isinstance(protocol.get("state_reset"), dict) or protocol["state_reset"].get(key) is not True:
            missing.append(f"state_reset.{key} must be true")
    return {"complete": not missing, "missing": sorted(set(missing)),
            "limitation": "只检查登记的协议，不验证实际推理端执行、权重内容或硬件确定性。"}


def trace_projection(t: dict) -> list:
    """Ignore only step-level latency/timing/presentation metadata, not observations.

    Timestamps *inside* a tool return remain evidence and participate in comparison.
    """
    return [{key: step[key] for key in TRACE_FIELDS if key in step}
            for step in t.get("steps", [])]


from .read_cache import cached_read

class Store(CoreStore):
    def __init__(self, data_dir: str | Path, *, _center=False):
        if not _center and (Path(data_dir).expanduser().resolve() / ".team" / "center.json").exists():
            raise ValidationError("This is a center workspace. Use team-serve / team-import / team-plan --center; do not write via a local Recorder or serve process")
        super().__init__(data_dir)
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        # SQLite backup captures committed WAL state. Never mutate a v1 database
        # without first creating a recoverable database snapshot.
        if version == 1:
            import sqlite3
            backup = self.data_dir / ("before-v2-" + now().replace(":", "-") + ".sqlite3")
            with sqlite3.connect(backup) as target:
                self.conn.backup(target)
        self.conn.executescript(RESEARCH_SCHEMA)
        with self.transaction():
            for row in self.conn.execute("SELECT id FROM runs WHERE id NOT IN (SELECT run_id FROM run_ownership)").fetchall():
                self._legacy_owner(row[0])
            self.conn.execute("PRAGMA user_version=2")

    def _legacy_owner(self, run_id: str) -> str:
        if not self.conn.execute("SELECT 1 FROM projects WHERE id=?", (LEGACY_PROJECT,)).fetchone():
            self.register_project({"id": LEGACY_PROJECT, "name": "未归类 · 旧版记录", "legacy": True,
                "description": "旧版没有研究归属；不会按模型名猜测属于 Self-play、Latent 或 Entropy。"})
        exp_id = "legacy-" + digest(run_id)[:24]
        self.register_experiment({"id": exp_id, "project_id": LEGACY_PROJECT,
            "name": self._get("runs", run_id).get("name", run_id), "legacy": True})
        self.conn.execute("INSERT OR IGNORE INTO run_ownership VALUES (?,?)", (run_id, exp_id))
        return exp_id

    def register_project(self, record: dict) -> str:
        d = clone(record)
        ident(d.get("id"), "project.id"); text(d.get("name"), "project.name")
        if not isinstance(d.setdefault("metric_definitions", {}), dict):
            raise ValidationError("project.metric_definitions must be an object")
        for key, definition in d["metric_definitions"].items():
            text(key, "metric field")
            if not isinstance(definition, dict):
                raise ValidationError("Each metric definition must be an object")
        with self.transaction():
            if not self._same_or_insert("projects", d["id"], d):
                self.conn.execute("INSERT INTO projects VALUES (?,?)", (d["id"], dumps(d)))
        return d["id"]

    def register_experiment(self, record: dict) -> str:
        d = clone(record)
        for key in ("id", "project_id"): ident(d.get(key), "experiment." + key)
        text(d.get("name"), "experiment.name")
        if not isinstance(d.setdefault("config", {}), dict):
            raise ValidationError("experiment.config must be an object")
        with self.transaction():
            self._get("projects", d["project_id"])
            if not self._same_or_insert("experiments", d["id"], d):
                self.conn.execute("INSERT INTO experiments VALUES (?,?,?)", (d["id"], d["project_id"], dumps(d)))
        return d["id"]

    def register_run(self, record: dict) -> str:
        d = clone(record)
        if 'training_stage' in d:
            from .studio.training_stage import validate_stage
            validate_stage(d['training_stage'])
        if "run_type" in d and d["run_type"] not in ("training", "evaluation", "harness_check"):
            raise ValidationError("run_type must be training/evaluation/harness_check; omit for legacy unknown records")
        with self.transaction():
            exp_id = d.get("experiment_id")
            if exp_id:
                exp = self._get("experiments", ident(exp_id, "run.experiment_id"))
                if "project_id" in d and d["project_id"] != exp["project_id"]:
                    raise ValidationError("run.project_id does not match experiment owner")
                d["project_id"] = exp["project_id"]
                if "seed" in d and d["seed"] is not None: integer(d["seed"], "run.seed")
            elif "project_id" in d:
                raise ValidationError("A project label alone is not enough: provide run.experiment_id")
            run_id = super().register_run(d)
            owner = self.conn.execute("SELECT experiment_id FROM run_ownership WHERE run_id=?", (run_id,)).fetchone()
            if exp_id:
                if owner and owner[0] != exp_id:
                    raise ValidationError("Run already belongs to a different experiment; cannot relabel silently")
                self.conn.execute("INSERT OR IGNORE INTO run_ownership VALUES (?,?)", (run_id, exp_id))
            elif not owner:
                self._legacy_owner(run_id)
            return run_id

    def register_checkpoint(self, record: dict) -> str:
        with self.transaction():
            run = self.run(record.get("run_id", ""))
            if run.get("run_type") in ("evaluation", "harness_check") and record.get("kind") != "model_snapshot":
                raise ValidationError("An independent evaluation uses a model_snapshot, not a fabricated optimizer checkpoint")
            for key in ("project_id", "experiment_id"):
                if key in record and record[key] != run[key]:
                    raise ValidationError("checkpoint."+key+" conflicts with owning run")
            return super().register_checkpoint(record)

    def register_model_snapshot(self, record: dict) -> str:
        """Register frozen model identity without inventing a training step."""
        return self.register_checkpoint(dict(record, kind="model_snapshot"))

    def assign_legacy_run(self, run_id: str, experiment_id: str, reason: str) -> None:
        """Explicit one-time classification, permitted only from the legacy bucket."""
        text(reason, "reason")
        with self.transaction():
            run = self.run(run_id); target = self._get("experiments", experiment_id)
            if run["project_id"] != LEGACY_PROJECT:
                raise ValidationError("Only unassigned legacy runs may be classified; existing research ownership is immutable")
            if target["project_id"] == LEGACY_PROJECT:
                raise ValidationError("Select an actual research experiment")
            self.conn.execute("UPDATE run_ownership SET experiment_id=? WHERE run_id=?", (experiment_id, run_id))
            self.conn.execute("INSERT INTO ownership_history(run_id,created_at,data) VALUES (?,?,?)",
                (run_id, now(), dumps({"from": run["experiment_id"], "to": experiment_id, "reason": reason})))

    def run(self, run_id: str) -> dict:
        with self.lock:
            d = self._get("runs", run_id)
            row = self.conn.execute("SELECT e.id,e.project_id FROM run_ownership o JOIN experiments e ON e.id=o.experiment_id WHERE o.run_id=?", (run_id,)).fetchone()
            if not row: raise ValidationError("Run has no research ownership")
            d.update(experiment_id=row[0], project_id=row[1])
            return d

    def projects(self) -> list[dict]:
        with self.lock:
            # Resolve run ownership once for the whole catalog. The old loop did
            # two reads per run again for every project and session request.
            runs_by_project: dict[str, list[dict]] = {}
            for run in self.runs():
                runs_by_project.setdefault(run["project_id"], []).append(run)
            experiment_counts = dict(self.conn.execute(
                "SELECT project_id,count(*) FROM experiments GROUP BY project_id"))
            result = []
            for row in self.conn.execute("SELECT id,data FROM projects ORDER BY rowid"):
                d = json.loads(row["data"])
                project_runs = runs_by_project.get(row["id"], [])
                d["experiment_count"] = experiment_counts.get(row["id"], 0)
                d["run_count"] = len(project_runs)
                d["models"] = sorted({r.get("base_model", "未记录") for r in project_runs})
                result.append(d)
            return result

    def experiments(self, project_id: str, model: str = "") -> list[dict]:
        with self.lock:
            run_counts: dict[str, int] = {}
            for run in self.runs(project_id, model=model):
                key = run["experiment_id"]
                run_counts[key] = run_counts.get(key, 0) + 1
            result = []
            for row in self.conn.execute("SELECT data FROM experiments WHERE project_id=? ORDER BY rowid", (project_id,)):
                d = json.loads(row[0]); count = run_counts.get(d["id"], 0)
                if model and not count: continue
                d["run_count"] = count; result.append(d)
            return result

    def runs(self, project_id: str = "", experiment_id: str = "", model: str = "") -> list[dict]:
        with self.lock:
            if project_id: self._get("projects", project_id)
            if experiment_id:
                exp = self._get("experiments", experiment_id)
                if project_id and exp["project_id"] != project_id:
                    raise ValidationError("Experiment does not belong to the selected project")
            clauses, args = [], []
            if project_id: clauses.append("e.project_id=?"); args.append(project_id)
            if experiment_id: clauses.append("e.id=?"); args.append(experiment_id)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self.conn.execute("SELECT r.data,e.id AS experiment_id,e.project_id FROM runs r JOIN run_ownership o ON o.run_id=r.id JOIN experiments e ON e.id=o.experiment_id" + where + " ORDER BY r.rowid", args).fetchall()
            records = [dict(json.loads(row["data"]), experiment_id=row["experiment_id"], project_id=row["project_id"])
                       for row in rows]
            return [r for r in records if not model or r.get("base_model") == model]

    def catalog(self, project_id: str, experiment_id: str = "", model: str = "") -> dict:
        with self.lock:
            project = self._get("projects", project_id)
            all_runs = self.runs(project_id)
            return {"project": project, "models": sorted({r.get("base_model", "未记录") for r in all_runs}),
                    "experiments": self.experiments(project_id, model),
                    "runs": self.runs(project_id, experiment_id, model)}

    def experiment(self, experiment_id: str) -> dict:
        with self.lock:
            d = self._get("experiments", experiment_id)
            d["notes"] = [dict(json.loads(r["data"]), id=r["id"]) for r in self.conn.execute("SELECT id,data FROM experiment_notes WHERE experiment_id=? ORDER BY id DESC", (experiment_id,))]
            d["references"] = []
            for row in self.conn.execute("SELECT id,evaluation_id,data FROM baseline_references WHERE experiment_id=? ORDER BY id", (experiment_id,)):
                ref = json.loads(row["data"])
                ev = self.evaluation(row["evaluation_id"])
                ref.update(id=row["id"], evaluation_id=ev["id"], source_project=ev["project_id"], source_run=ev["run"]["id"], checkpoint=ev["checkpoint"], panel_hash=ev["panel_hash"], protocol_hash=ev["protocol_hash"])
                d["references"].append(ref)
            return d

    def note_experiment(self, experiment_id: str, record: dict) -> dict:
        d = clone(record)
        text(d.get("note"), "experiment note")
        if len(d["note"]) > 20000: raise ValidationError("Note exceeds 20,000 characters")
        d.setdefault("kind", "observation")
        if d["kind"] not in ("observation", "positive", "negative", "decision"):
            raise ValidationError("kind must be observation/positive/negative/decision")
        d.setdefault("evidence", [])
        if not isinstance(d["evidence"], list): raise ValidationError("evidence must be a list")
        d["created_at"] = now(); d["source"] = "manual"
        with self.transaction():
            self._get("experiments", experiment_id)
            if "event_id" in d:
                ident(d["event_id"], "note.event_id")
                prior = self.conn.execute("SELECT id,data FROM experiment_notes WHERE experiment_id=? AND json_extract(data,'$.event_id')=?", (experiment_id,d["event_id"])).fetchone()
                if prior:
                    old = json.loads(prior["data"])
                    if {k:v for k,v in old.items() if k != "created_at"} != {k:v for k,v in d.items() if k != "created_at"}:
                        raise ValidationError("Experiment note event_id already exists with different content")
                    return dict(old, id=prior["id"])
            for item in d["evidence"]:
                if not isinstance(item, dict): raise ValidationError("evidence entries must be objects")
                if item.get("type") not in ("evaluation", "trajectory"):
                    raise ValidationError("Evidence type must be evaluation or trajectory")
                self._get("evaluations" if item["type"] == "evaluation" else "trajectories", item["id"])
            cur = self.conn.execute("INSERT INTO experiment_notes(experiment_id,created_at,data) VALUES (?,?,?)", (experiment_id, d["created_at"], dumps(d)))
            return dict(d, id=cur.lastrowid)

    def add_reference(self, experiment_id: str, evaluation_id: str, reason: str) -> None:
        text(reason, "baseline reference reason")
        with self.transaction():
            self._get("experiments", experiment_id); self._get("evaluations", evaluation_id)
            ev = self.evaluation(evaluation_id)
            dest_track = self.research_track(experiment_id)
            source_track = self.research_track(ev['experiment_id'])
            if dest_track != source_track:
                raise ValidationError('不得把复现/未分类结果关联为主实验迭代基线；保留原记录，另行明确设计主实验')
            data = {"reason": reason}
            existing = self.conn.execute("SELECT data FROM baseline_references WHERE experiment_id=? AND evaluation_id=?", (experiment_id, evaluation_id)).fetchone()
            if existing:
                if json.loads(existing[0]) != data: raise ValidationError("Reference already exists with another reason")
                return
            self.conn.execute("INSERT INTO baseline_references(experiment_id,evaluation_id,data) VALUES (?,?,?)", (experiment_id, evaluation_id, dumps(data)))

    def register_panel(self, record: dict) -> str:
        d = clone(record)
        samples = d.get("samples")
        declared_hash = d.pop("fingerprint", None)
        for field in ("read_only", "isolation", "source_metadata_complete", "size", "datasets"):
            d.pop(field, None)
        if declared_hash is not None and isinstance(samples, list) and samples:
            if declared_hash != digest(sorted(samples, key=lambda x:(x["dataset"], x["id"]))):
                raise ValidationError("Groundtruth content does not match the declared SHA256")
        if d.get("selection_status") in ("DRAFT_NOT_APPROVED", "candidate", "PROVISIONAL_CANDIDATE_NOT_APPROVED_FOR_VALIDATION"):
            raise ValidationError("Draft/candidate questions are not approved fixed groundtruth; review sources and isolation before explicitly freezing")
        return super().register_panel(d)

    def panels(self) -> list[dict]:
        with self.lock:
            result = super().panels()
            for p in result: p["isolation"] = self.latest_audit(p["id"])
            return result

    def panel(self, panel_id: str) -> dict:
        with self.lock:
            p = super().panel(panel_id)
            p["read_only"] = True
            p["isolation"] = self.latest_audit(panel_id)
            p["source_metadata_complete"] = all(
                s.get("metadata", {}).get("source_id") and s.get("metadata", {}).get("source_revision")
                for s in p["samples"])
            return p

    def latest_audit(self, panel_id: str) -> dict:
        row = self.conn.execute("SELECT id,data FROM isolation_audits WHERE panel_id=? ORDER BY id DESC LIMIT 1", (panel_id,)).fetchone()
        if not row:
            return {"status": "not_checked", "notice": "未提供并核查全项目训练/正式测试清单；不能声称已隔离。"}
        d = json.loads(row["data"])
        return {"id": row["id"], "status": d["status"], "created_at": d["created_at"],
                "inventory_hash": d["inventory_hash"], "projects": d["projects"],
                "exact_conflicts": len(d["exact_conflicts"]), "near_candidates": len(d["near_candidates"]),
                "notice": d["limitation"]}

    def record_audit(self, panel_id: str, report: dict) -> dict:
        with self.transaction():
            panel = self._get("panels", panel_id)
            if report.get("panel_hash") != panel["fingerprint"]:
                raise ValidationError("Audit report does not match this immutable panel")
            if not report.get("inventory_hash") or "status" not in report:
                raise ValidationError("Audit must be generated against a versioned inventory")
            d = clone(report); d["created_at"] = now()
            cur = self.conn.execute("INSERT INTO isolation_audits(panel_id,created_at,data) VALUES (?,?,?)", (panel_id, d["created_at"], dumps(d)))
            return dict(d, id=cur.lastrowid)

    def audit(self, audit_id: int) -> dict:
        with self.lock:
            row = self.conn.execute("SELECT data FROM isolation_audits WHERE id=?", (audit_id,)).fetchone()
            if not row: raise NotFoundError("isolation audit does not exist")
            return json.loads(row[0])

    def start_evaluation(self, record: dict) -> str:
        if "purpose" in record and record["purpose"] not in ("agent_eval", "checkpoint_eval"):
            raise ValidationError("Evaluation purpose must be agent_eval/checkpoint_eval; training rollouts use training_batches, contract fixtures use harness checks")
        if is_training_record(record) or record.get("usage") == "training":
            raise ValidationError("Training rollouts cannot be registered as evaluations")
        with self.lock:
            cp = self._get("checkpoints", record.get("checkpoint_id", ""))
            run = self.run(cp["run_id"])
            if run.get("run_type") == "harness_check":
                raise ValidationError("Harness contract checks do not produce model evaluation scores")
            if record.get("purpose") == "checkpoint_eval" and cp.get("kind") == "model_snapshot":
                raise ValidationError("checkpoint_eval requires an actual training checkpoint")
            for key, expected in (("project_id",run["project_id"]),("experiment_id",run["experiment_id"]),("run_id",run["id"])):
                if key in record and record[key] != expected:
                    raise ValidationError("evaluation."+key+" conflicts with owning checkpoint")
        protocol = record.get("protocol", {})
        if record.get("purpose") in ("agent_eval", "checkpoint_eval") and isinstance(protocol, dict):
            if protocol.get("mode") != "inference_only" or protocol.get("groundtruth_to_agent") is not False:
                raise ValidationError("Explicit evaluation requires inference_only and groundtruth_to_agent=false")
        if isinstance(protocol, dict) and protocol.get("mode") in ("training", "train", "rollout", "train_rollout", "training_rollout"):
            raise ValidationError("Training-mode rollouts are not held-out evaluations")
        if isinstance(protocol, dict) and protocol.get("protocol_version") == 2:
            for key in ("input", "runtime", "state_reset"):
                if not isinstance(protocol.get(key), dict):
                    raise ValidationError(f"v2 protocol.{key} must be an object; use explicit unknown values")
            # Inputs that explicitly expose golds/update weights are not validation.
            if protocol.get("mode") != "inference_only" or protocol.get("groundtruth_to_agent") is not False:
                raise ValidationError("Fixed validation requires inference_only and groundtruth_to_agent=false")
            if any(protocol["state_reset"].get(k) is not True for k in ("per_question", "between_evaluations")):
                raise ValidationError("Validation must reset agent state per question and between evaluations")
        return super().start_evaluation(record)

    def log_trajectory(self, evaluation_id: str, record: dict) -> str:
        if is_training_record(record):
            raise ValidationError("Training rollouts belong to training_batches, not evaluation trajectories")
        if "reward" in record: number(record["reward"], "trajectory.reward")
        return super().log_trajectory(evaluation_id, record)

    @cached_read
    def evaluation(self, evaluation_id: str) -> dict:
        with self.lock:
            d = super().evaluation(evaluation_id)
            d["run"] = self.run(d["checkpoint"]["run_id"])
            d["experiment_id"] = d["run"]["experiment_id"]
            d["project_id"] = d["run"]["project_id"]
            d["readiness"] = protocol_readiness(d["protocol"])
            d["method_state"] = self.method_state(evaluation_id)
            return d

    @cached_read
    def overview(self, run_id: str, panel_id: str, project_id: str = "", experiment_id: str = "") -> dict:
        with self.lock:
            run = self.run(run_id)
            if project_id and run["project_id"] != project_id: raise ValidationError("Run is outside selected research project")
            if experiment_id and run["experiment_id"] != experiment_id: raise ValidationError("Run is outside selected experiment")
            d = super().overview(run_id, panel_id)
            d["run"] = run
            d["project"] = self._get("projects", run["project_id"])
            d["experiment"] = self.experiment(run["experiment_id"])
            d["panel"]["isolation"] = self.latest_audit(panel_id)
            d["metric_definitions"] = {**d["project"].get("metric_definitions", {}),
                **d["experiment"].get("metric_definitions", {}), **run.get("metric_definitions", {})}
            return d

    @cached_read
    def method_state(self, evaluation_id: str) -> dict:
        if hasattr(self, "_team_methods"):
            return self._team_methods.evaluation_state(evaluation_id)
        # No sidecar is created for old workspaces until method features are used.
        if not (self.data_dir / 'method_evidence.sqlite3').exists():
            return {'status':'not_recorded','fingerprint':None,'binding':None}
        from .methods.repository import MethodRepository
        with MethodRepository(self.data_dir,self) as methods:
            return methods.evaluation_state(evaluation_id)

    @cached_read
    def _method_pair_reasons(self, a_id: str, b_id: str) -> list[str]:
        if hasattr(self, "_team_methods"):
            return self._team_methods.pair_state(a_id,b_id)["reasons"]
        if not (self.data_dir / 'method_evidence.sqlite3').exists(): return []
        from .methods.repository import MethodRepository
        with MethodRepository(self.data_dir,self) as methods:
            return methods.pair_state(a_id,b_id)['reasons']

    def research_track(self, experiment_id: str) -> str:
        """Read explicit classification without mutating old evidence or guessing names."""
        if hasattr(self, "_team_studio"):
            return self._team_studio.profile(experiment_id)["track"]
        path = self.data_dir / 'experiment_studio.sqlite3'
        if not path.exists(): return 'unassigned'
        import sqlite3
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as c:
                row = c.execute('SELECT data FROM profiles WHERE experiment_id=?', (experiment_id,)).fetchone()
            return json.loads(row[0]).get('track','unassigned') if row else 'unassigned'
        except sqlite3.OperationalError:
            return 'unassigned'

    def _purpose_pair_reasons(self, a: dict, b: dict) -> list[str]:
        ta,tb = self.research_track(a['experiment_id']),self.research_track(b['experiment_id'])
        if ta != tb:
            return ['主实验 / 文献复现 / 未分类不能跨分区计算迭代增量；原始证据可独立查看']
        return []

    def _pair_reasons(self, a: dict, b: dict, allow_cross_project: bool) -> list[str]:
        reasons = self._method_pair_reasons(a["id"],b["id"]) + self._purpose_pair_reasons(a,b)
        if a["project_id"] != b["project_id"] and not allow_cross_project:
            reasons.append("跨项目比较必须明确选择双方并确认；默认不混合研究结果")
        if a["run"]["id"] != b["run"]["id"]:
            for key, label in (("base_model", "底座模型"), ("model_revision", "底座版本")):
                va, vb = a["run"].get(key), b["run"].get(key)
                if not va or not vb or self._contains_unknown(va) or self._contains_unknown(vb):
                    reasons.append(label + "未完整记录，不能作为受控跨运行比较")
                elif va != vb: reasons.append(label + "不同，不能作为同底座的受控比较")
        if not a["readiness"]["complete"] or not b["readiness"]["complete"]:
            # Legacy same-run comparisons retain v1 behavior, with an explicit warning.
            if a["run"]["id"] != b["run"]["id"] or a["protocol"].get("protocol_version") == 2 or b["protocol"].get("protocol_version") == 2:
                reasons.append("复现协议缺少输入模板、运行环境或状态重置等字段")
        return reasons

    def compare(self, a_id: str, b_id: str, dataset: str = "", group: str = "", query: str = "", allow_cross_project: bool = False) -> dict:
        with self.lock:
            a, b = self.evaluation(a_id), self.evaluation(b_id)
            extra = self._pair_reasons(a, b, allow_cross_project)
            if extra:
                return {"a": a, "b": b, "compatible": False, "reasons": extra}
            d = super().compare(a_id, b_id, dataset, group, query)
            d["warnings"] = ["训练 step 相同不代表训练样本量、token、GPU 时间或其他预算相同。"]
            if not a["readiness"]["complete"]:
                d["warnings"].append("旧版协议仅可核对已记录字段；输入和运行环境等未完整登记。")
            d["cross_project"] = a["project_id"] != b["project_id"]
            return d

    def matrix(self, run_id: str, panel_id: str, dataset: str = "", selections: dict | None = None, project_id: str = "") -> dict:
        """One fixed question per row, one selected evaluation per checkpoint column."""
        with self.lock:
            overview = self.overview(run_id, panel_id, project_id)
            panel = self.panel(panel_id)
            if dataset and dataset not in panel["datasets"]: raise ValidationError("Dataset is not in this fixed panel")
            choices = selections or {}
            if not isinstance(choices, dict): raise ValidationError("selections must map checkpoint IDs to evaluation IDs")
            cps = overview["checkpoints"]
            if set(choices) - {c["id"] for c in cps}: raise ValidationError("Selection contains a checkpoint outside this run")
            columns, trajectories = [], []
            for cp in cps:
                candidates = cp["evaluations"]
                chosen = choices.get(cp["id"])
                ev = next((e for e in candidates if e["id"] == chosen), None) if chosen else next(iter(candidates), None)
                if chosen and not ev: raise ValidationError("Selected evaluation does not belong to checkpoint/panel")
                columns.append({"checkpoint": {k: v for k,v in cp.items() if k != "evaluations"}, "evaluation": ev,
                                "repeat_count": len(candidates), "selection_rule": "explicit" if chosen else "latest_record_not_best"})
                trajectories.append({(r["dataset"], r["sample_id"]): r for r in
                    [json.loads(x[0]) for x in self.conn.execute("SELECT data FROM trajectory_summaries WHERE evaluation_id=?", (ev["id"],))]} if ev else {})
            evs = [c["evaluation"] for c in columns if c["evaluation"]]
            comparable = all(e["protocol_complete"] and (e["readiness"]["complete"] or e["protocol"].get("protocol_version") != 2) for e in evs) and len({e["protocol_hash"] for e in evs}) <= 1
            if any(self._method_pair_reasons(evs[0]["id"],e["id"]) for e in evs[1:]): comparable = False
            rows = []
            for sample in panel["samples"]:
                if dataset and dataset != sample["dataset"]: continue
                key = sample["dataset"], sample["id"]
                cells = []
                previous = None
                for index, mapping in enumerate(trajectories):
                    t = mapping.get(key)
                    if not t:
                        cells.append(None); previous = None; continue
                    cell = self._summary(t)
                    cell["change"] = None
                    if comparable and previous:
                        a, b = previous["scores"].get("em"), t["scores"].get("em")
                        if a == 0 and b == 1: cell["change"] = "rescued"
                        elif a == 1 and b == 0: cell["change"] = "harmed"
                    cells.append(cell); previous = t
                rows.append({"sample": sample, "cells": cells})
            return {"run": overview["run"], "panel": {k:v for k,v in panel.items() if k!="samples"},
                    "columns": columns, "rows": rows, "comparable": comparable,
                    "notice": "固定小面板用于逐题诊断；不代表完整基准性能。未评分/未评测不补零。"}

    def reproducibility(self, a_id: str, b_id: str) -> dict:
        with self.lock:
            a, b = self.evaluation(a_id), self.evaluation(b_id)
            reasons = self._method_pair_reasons(a_id,b_id)
            if a_id == b_id: reasons.append("请选择同一 checkpoint 的两条独立评测记录")
            if a["checkpoint_id"] != b["checkpoint_id"]: reasons.append("复现检查必须使用同一 checkpoint")
            if a["panel_hash"] != b["panel_hash"]: reasons.append("Groundtruth 题单哈希不一致")
            if a["protocol_hash"] != b["protocol_hash"]: reasons.append("运行/检索/评分协议不一致")
            if not a["protocol_complete"] or not b["protocol_complete"]: reasons.append("协议包含 unknown")
            if not a["readiness"]["complete"] or not b["readiness"]["complete"]: reasons.append("协议缺少复现所需的输入/运行环境/状态重置记录")
            result = {"a": a, "b": b, "eligible": not reasons, "reasons": reasons,
                "limitations": ["检查已保存的两次执行，不会运行模型或重新搜索。", "两次一致不保证未来逐 token 确定性。",
                    "忽略步骤顶层耗时/时间戳及展示标题；工具内容内部字段不忽略。", "权重路径和哈希为导入元数据，未读取实际模型文件验证。"]}
            if reasons: return result
            maps = [{(r["dataset"], r["sample_id"]):r for r in [json.loads(x[0]) for x in self.conn.execute("SELECT data FROM trajectories WHERE evaluation_id=?", (ev["id"],))]} for ev in (a,b)]
            items = []; counts = {k: 0 for k in ("matched", "different", "insufficient", "missing")}
            for s in self.panel(a["panel_id"])["samples"]:
                x, y = (m.get((s["dataset"], s["id"])) for m in maps)
                item = {"dataset": s["dataset"], "sample_id": s["id"], "question": s["question"], "a": x and x["id"], "b": y and y["id"]}
                if not x or not y:
                    item["state"] = "missing"; counts["missing"] += 1; items.append(item); continue
                flags = {}
                for field, label in (("parsed_answer", "answer_equal"), ("raw_final_output", "raw_output_equal")):
                    flags[label] = x.get(field) == y.get(field) if x.get(field) is not None and y.get(field) is not None else None
                flags["score_equal"] = x["scores"] == y["scores"] if x["scores"].get("em") is not None and y["scores"].get("em") is not None else None
                px, py = trace_projection(x), trace_projection(y)
                flags["trace_equal"] = px == py if px and py else None
                flags["runtime_equal"] = (x.get("runtime_status"), x.get("termination_reason")) == (y.get("runtime_status"), y.get("termination_reason"))
                differing = next((i for i in range(max(len(px),len(py))) if i>=len(px) or i>=len(py) or px[i]!=py[i]), None)
                if False in flags.values(): state = "different"
                elif None in flags.values(): state = "insufficient"
                else: state = "matched"
                counts[state] += 1
                item.update(flags, state=state, first_different_step_position=differing,
                    first_different_step_index=(px[differing].get("index") if differing is not None and differing<len(px) else None))
                items.append(item)
            completed = a["status"] == b["status"] == "completed"
            result.update(items=items, counts=counts, complete=completed,
                all_recorded_evidence_matches=completed and counts["matched"]==a["panel"]["size"],
                weights_hash_recorded=bool(a["checkpoint"].get("weights_sha256")))
            return result

    def export_evaluation(self, evaluation_id: str) -> dict:
        d = super().export_evaluation(evaluation_id)
        d["schema_version"] = 2
        ev = d["evaluation"]
        d["ownership"] = {"project": self._get("projects", ev["project_id"]),
            "experiment": self.experiment(ev["experiment_id"]), "run": ev["run"]}
        d["groundtruth"] = self.panel(ev["panel_id"])
        return d

    def health(self) -> dict:
        with self.lock:
            return {"ok": self.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "schema_version": 2,
                    **{key: self.conn.execute(f"SELECT count(*) FROM {key}").fetchone()[0]
                       for key in ("projects", "experiments", "runs", "panels", "checkpoints", "evaluations", "trajectories", "isolation_audits")}}
