"""SQLite persistence, validation, and read models.

Scores are imported evidence, not recomputed here. Completed trajectories and
panel manifests are immutable; reruns require a new evaluation ID. WAL and a
bounded busy timeout allow a separate rank-0 recorder and the viewer to coexist.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class ValidationError(ValueError):
    """The supplied record violates the explicit data contract."""


class NotFoundError(KeyError):
    """An explicitly named record does not exist."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"Data must be finite, JSON-serializable values: {exc}") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def ident(value: Any, name: str = "id") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", value):
        raise ValidationError(f"{name}: use 1–180 ASCII letters, numbers, '.', '_', ':' or '-'")
    return value


def text(value: Any, name: str, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValidationError(f"{name} must be a {'possibly empty ' if empty else 'non-empty '}string")
    return value


def integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{name} must be an integer >= {minimum}")
    return value


def number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{name} must be a finite number or null")
    return float(value)


def clone(value: Any) -> Any:
    return json.loads(dumps(value))


SCHEMA = """
CREATE TABLE IF NOT EXISTS trajectory_summaries(
 id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL, dataset TEXT NOT NULL,
 sample_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS trajectory_summary_eval ON trajectory_summaries(evaluation_id,dataset,sample_id);

CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics(
 run_id TEXT NOT NULL REFERENCES runs(id), step INTEGER NOT NULL,
 data TEXT NOT NULL, PRIMARY KEY(run_id,step));
CREATE TABLE IF NOT EXISTS checkpoints(
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 step INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS panels(
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS panel_samples(
 panel_id TEXT NOT NULL REFERENCES panels(id), dataset TEXT NOT NULL,
 sample_id TEXT NOT NULL, ordinal INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(panel_id,dataset,sample_id));
CREATE TABLE IF NOT EXISTS evaluations(
 id TEXT PRIMARY KEY, checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
 panel_id TEXT NOT NULL REFERENCES panels(id), status TEXT NOT NULL,
 created_at TEXT NOT NULL, protocol_hash TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trajectories(
 id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
 dataset TEXT NOT NULL, sample_id TEXT NOT NULL, outcome TEXT NOT NULL,
 em REAL, f1 REAL, data TEXT NOT NULL,
 UNIQUE(evaluation_id,dataset,sample_id));
CREATE TABLE IF NOT EXISTS annotations(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 trajectory_id TEXT NOT NULL REFERENCES trajectories(id),
 created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_eval_cp ON evaluations(checkpoint_id,panel_id,created_at);
CREATE INDEX IF NOT EXISTS idx_traj_eval ON trajectories(evaluation_id,dataset,outcome);
CREATE INDEX IF NOT EXISTS idx_annotation_traj ON annotations(trajectory_id);

"""

PROTOCOL_KEYS = ("scorer", "parser", "retriever", "budget", "decoding", "harness_version", "seed")
OUTCOMES = ("correct", "incorrect", "error", "unscored")


class Store:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "searchagent_lab.sqlite3"
        self.lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(self.db_path, timeout=20, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=20000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.conn.close()
            raise ValidationError(f"Unsupported database schema {version}; this build supports 1–2")
        self.conn.executescript(SCHEMA)
        missing=self.conn.execute("SELECT t.* FROM trajectories t LEFT JOIN trajectory_summaries s ON s.id=t.id WHERE s.id IS NULL").fetchall()
        if missing:
            with self.transaction():
                for row in missing:
                    d=json.loads(row['data'])
                    self.conn.execute("INSERT OR IGNORE INTO trajectory_summaries VALUES (?,?,?,?,?)",
                        (d['id'],d['evaluation_id'],d['dataset'],d['sample_id'],dumps(self._summary(d))))

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Nested writes share one transaction; import uses this for all-or-nothing."""
        with self.lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                if outer:
                    self.conn.rollback()
                raise
            else:
                if outer:
                    self.conn.commit()
            finally:
                self._depth -= 1

    def _get(self, table: str, record_id: str) -> dict:
        # Table names come only from constants in this module, never HTTP input.
        row = self.conn.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"{table}: {record_id}")
        return json.loads(row[0])

    def _same_or_insert(self, table: str, record_id: str, data: dict) -> bool:
        row = self.conn.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            return False
        if json.loads(row[0]) != data:
            raise ValidationError(f"{table}/{record_id} already exists with different content; use a new ID")
        return True

    def register_run(self, record: dict) -> str:
        d = clone(record)
        ident(d.get("id"), "run.id")
        text(d.get("name"), "run.name")
        d.setdefault("status", "running")
        if d["status"] not in ("running", "completed", "failed", "stopped"):
            raise ValidationError("run.status must be running/completed/failed/stopped")
        d.setdefault("demo", False)
        if not isinstance(d["demo"], bool):
            raise ValidationError("run.demo must be boolean")
        d.setdefault("config", {})
        with self.transaction():
            # Omitted timestamps reuse the stored one, making repeated registration safe.
            existing = self.conn.execute("SELECT data FROM runs WHERE id=?", (d["id"],)).fetchone()
            d.setdefault("created_at", json.loads(existing[0])["created_at"] if existing else now())
            if not self._same_or_insert("runs", d["id"], d):
                self.conn.execute("INSERT INTO runs VALUES (?,?)", (d["id"], dumps(d)))
        return d["id"]

    def set_run_status(self, run_id: str, status: str) -> None:
        if status not in ("running", "completed", "failed", "stopped"):
            raise ValidationError("Invalid run status")
        with self.transaction():
            d = self._get("runs", run_id)
            d["status"] = status
            self.conn.execute("UPDATE runs SET data=? WHERE id=?", (dumps(d), run_id))

    def log_metrics(self, run_id: str, step: int, values: dict) -> None:
        integer(step, "step")
        if not isinstance(values, dict) or not values:
            raise ValidationError("metrics must be a non-empty object")
        for key, val in values.items():
            text(key, "metric name")
            number(val, key)
        with self.transaction():
            self._get("runs", run_id)
            row = self.conn.execute("SELECT data FROM metrics WHERE run_id=? AND step=?", (run_id, step)).fetchone()
            merged = json.loads(row[0]) if row else {}
            for key, val in values.items():
                if key in merged and merged[key] != val:
                    raise ValidationError(f"Metric {run_id}/{step}/{key} already exists with another value")
                merged[key] = val
            self.conn.execute("INSERT INTO metrics VALUES (?,?,?) ON CONFLICT(run_id,step) DO UPDATE SET data=excluded.data", (run_id, step, dumps(merged)))

    def register_checkpoint(self, record: dict) -> str:
        d = clone(record)
        ident(d.get("id"), "checkpoint.id")
        ident(d.get("run_id"), "checkpoint.run_id")
        if d.get("kind") == "model_snapshot":
            if d.get("step") is not None:
                raise ValidationError("A model snapshot has no optimizer step; use a training checkpoint for step-linked weights")
            d["step"] = None
            # The existing SQL index is NOT NULL. -1 is only an ordering sentinel;
            # all evidence, exports and read models keep the actual step as null.
            indexed_step = -1
        else:
            indexed_step = integer(d.get("step"), "checkpoint.step")
        d.setdefault("name", d["id"])
        with self.transaction():
            self._get("runs", d["run_id"])
            if not self._same_or_insert("checkpoints", d["id"], d):
                self.conn.execute("INSERT INTO checkpoints VALUES (?,?,?,?)", (d["id"], d["run_id"], indexed_step, dumps(d)))
        return d["id"]

    def register_panel(self, record: dict) -> str:
        d = clone(record)
        ident(d.get("id"), "panel.id")
        text(d.get("name"), "panel.name")
        d.setdefault("version", "v1")
        d.setdefault("split", "dev")
        if d["split"] not in ("dev", "test", "probe"):
            raise ValidationError("panel.split must be dev/test/probe")
        samples = d.pop("samples", None)
        if not isinstance(samples, list) or not samples:
            raise ValidationError("panel.samples must be a non-empty fixed list")
        seen: set[tuple[str, str]] = set()
        counts: dict[str, int] = {}
        for sample in samples:
            text(sample.get("id"), "sample.id")
            text(sample.get("dataset"), "sample.dataset")
            text(sample.get("question"), "sample.question")
            answers = sample.get("answers")
            if not isinstance(answers, list) or not answers or not all(isinstance(x, str) and x.strip() for x in answers):
                raise ValidationError("sample.answers must be a non-empty list of non-empty strings")
            key = (sample["dataset"], sample["id"])
            if key in seen:
                raise ValidationError(f"Duplicate sample key {key}")
            seen.add(key)
            counts[sample["dataset"]] = counts.get(sample["dataset"], 0) + 1
        normalized = sorted(samples, key=lambda s: (s["dataset"], s["id"]))
        d["fingerprint"] = digest(normalized)
        d["size"] = len(samples)
        d["datasets"] = counts
        with self.transaction():
            if self._same_or_insert("panels", d["id"], d):
                return d["id"]
            self.conn.execute("INSERT INTO panels VALUES (?,?,?)", (d["id"], d["fingerprint"], dumps(d)))
            self.conn.executemany("INSERT INTO panel_samples VALUES (?,?,?,?,?)", [(d["id"], s["dataset"], s["id"], i, dumps(s)) for i, s in enumerate(samples)])
        return d["id"]

    def start_evaluation(self, record: dict) -> str:
        d = clone(record)
        for key in ("id", "checkpoint_id", "panel_id"):
            ident(d.get(key), f"evaluation.{key}")
        protocol = d.get("protocol")
        if not isinstance(protocol, dict):
            raise ValidationError("evaluation.protocol is required")
        missing = [k for k in PROTOCOL_KEYS if k not in protocol or protocol[k] in (None, "", {})]
        if missing:
            raise ValidationError(f"Protocol must explicitly describe {', '.join(missing)} (use 'unknown' if not recorded; comparisons will be disabled)")
        d["protocol_hash"] = digest(protocol)
        d["protocol_complete"] = not self._contains_unknown(protocol)
        d["status"] = "running"
        with self.transaction():
            checkpoint = self._get("checkpoints", d["checkpoint_id"])
            panel = self._get("panels", d["panel_id"])
            run = self._get("runs", checkpoint["run_id"])
            d["panel_hash"] = panel["fingerprint"]
            d["demo"] = bool(run.get("demo") or panel.get("demo"))
            existing = self.conn.execute("SELECT data FROM evaluations WHERE id=?", (d["id"],)).fetchone()
            d.setdefault("created_at", json.loads(existing[0])["created_at"] if existing else now())
            if existing:
                old = json.loads(existing[0])
                comparable_old = {k: v for k, v in old.items() if k not in ("status", "finished_at")}
                comparable_new = {k: v for k, v in d.items() if k not in ("status", "finished_at")}
                if comparable_old != comparable_new:
                    raise ValidationError("Evaluation ID already exists with different content")
                return d["id"]
            self.conn.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)", (d["id"], d["checkpoint_id"], d["panel_id"], d["status"], d["created_at"], d["protocol_hash"], dumps(d)))
        return d["id"]

    @staticmethod
    def _contains_unknown(v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("unknown", "未记录", "未知")
        if isinstance(v, dict):
            return any(Store._contains_unknown(x) for x in v.values())
        if isinstance(v, list):
            return any(Store._contains_unknown(x) for x in v)
        return False

    def log_trajectory(self, evaluation_id: str, record: dict) -> str:
        d = clone(record)
        for key in ("dataset", "sample_id"):
            text(d.get(key), f"trajectory.{key}")
        d.setdefault("id", "t-" + digest([evaluation_id, d["dataset"], d["sample_id"]])[:32])
        ident(d["id"], "trajectory.id")
        if "evaluation_id" in d and d["evaluation_id"] != evaluation_id:
            raise ValidationError("trajectory.evaluation_id does not match target evaluation")
        d["evaluation_id"] = evaluation_id
        d.setdefault("scores", {})
        if not isinstance(d["scores"], dict):
            raise ValidationError("scores must be an object; missing values must be null")
        for key, val in d["scores"].items():
            number(val, f"scores.{key}")
            if key == "em" and val not in (None, 0, 1):
                raise ValidationError("scores.em must be 0, 1, or null; use fractions, not percentages")
            if key in ("f1", "accuracy") and val is not None and not 0 <= val <= 1:
                raise ValidationError(f"scores.{key} must lie in [0,1]")
        d.setdefault("runtime_status", "unknown")
        if d["runtime_status"] not in ("ok", "error", "timeout", "cancelled", "unknown"):
            raise ValidationError("Invalid runtime_status")
        steps = d.setdefault("steps", [])
        if not isinstance(steps, list):
            raise ValidationError("steps must be a list")
        step_ids: set[int] = set()
        for s in steps:
            if not isinstance(s, dict):
                raise ValidationError("Each step must be an object")
            idx = integer(s.get("index"), "step.index")
            if idx in step_ids:
                raise ValidationError("step.index must be unique")
            step_ids.add(idx)
            text(s.get("kind"), "step.kind")
            if "latency_ms" in s:
                latency = number(s["latency_ms"], "step.latency_ms")
                if latency is not None and latency < 0:
                    raise ValidationError("step.latency_ms must be >= 0")
        if [s["index"] for s in steps] != sorted(step_ids):
            raise ValidationError("steps must be in ascending index order")
        d.setdefault("usage", {})
        if not isinstance(d["usage"], dict):
            raise ValidationError("usage must be an object")
        for key, val in d["usage"].items():
            if key in ("tool_calls", "model_calls", "input_tokens", "output_tokens", "latency_ms"):
                n = number(val, f"usage.{key}")
                if n is not None and n < 0:
                    raise ValidationError(f"usage.{key} must be >= 0")
        em = d["scores"].get("em")
        d["outcome"] = "error" if d["runtime_status"] in ("error", "timeout", "cancelled") else ("correct" if em == 1 else "incorrect" if em == 0 else "unscored")
        with self.transaction():
            ev = self._get("evaluations", evaluation_id)
            row = self.conn.execute("SELECT data FROM panel_samples WHERE panel_id=? AND dataset=? AND sample_id=?", (ev["panel_id"], d["dataset"], d["sample_id"])).fetchone()
            if row is None:
                raise ValidationError("Trajectory sample is not in this evaluation's fixed panel")
            sample = json.loads(row[0])
            for key, expected in (("question", sample["question"]), ("gold_answers", sample["answers"])):
                if key in d and d[key] != expected:
                    raise ValidationError(f"trajectory.{key} differs from the frozen panel")
                d[key] = expected
            if self._same_or_insert("trajectories", d["id"], d):
                return d["id"]
            if ev["status"] != "running":
                raise ValidationError("Finished evaluation is immutable; create a new evaluation ID")
            try:
                self.conn.execute("INSERT INTO trajectories VALUES (?,?,?,?,?,?,?,?)", (d["id"], evaluation_id, d["dataset"], d["sample_id"], d["outcome"], em, d["scores"].get("f1"), dumps(d)))
                self.conn.execute("INSERT INTO trajectory_summaries VALUES (?,?,?,?,?)",(d['id'],evaluation_id,d['dataset'],d['sample_id'],dumps(self._summary(d))))
            except sqlite3.IntegrityError as exc:
                raise ValidationError("Duplicate trajectory ID or (evaluation,dataset,sample_id); use a separate evaluation for additional seeds") from exc
        return d["id"]

    def finish_evaluation(self, evaluation_id: str, status: str = "completed") -> None:
        if status not in ("completed", "failed", "cancelled"):
            raise ValidationError("End status must be completed/failed/cancelled")
        with self.transaction():
            ev = self._get("evaluations", evaluation_id)
            if ev["status"] == status:
                return
            if ev["status"] != "running":
                raise ValidationError("A finished evaluation cannot change status")
            panel = self._get("panels", ev["panel_id"])
            count = self.conn.execute("SELECT count(*) FROM trajectories WHERE evaluation_id=?", (evaluation_id,)).fetchone()[0]
            if status == "completed" and count != panel["size"]:
                raise ValidationError(f"Cannot complete: {count}/{panel['size']} samples recorded. Use failed/cancelled for an interrupted run")
            ev["status"] = status
            ev["finished_at"] = now()
            self.conn.execute("UPDATE evaluations SET status=?, data=? WHERE id=?", (status, dumps(ev), evaluation_id))

    def annotate(self, trajectory_id: str, record: dict) -> dict:
        d = clone(record)
        text(d.get("category"), "annotation.category")
        text(d.get("note"), "annotation.note")
        if len(d["note"]) > 20000:
            raise ValidationError("Annotation note exceeds 20,000 characters")
        d.setdefault("step_index", None)
        d.setdefault("confidence", "pending")
        if d["confidence"] not in ("pending", "confirmed"):
            raise ValidationError("confidence must be pending/confirmed")
        if d["step_index"] is not None:
            integer(d["step_index"], "annotation.step_index")
        d["source"] = "manual"
        d["created_at"] = now()
        with self.transaction():
            traj = self._get("trajectories", trajectory_id)
            if d["step_index"] is not None and d["step_index"] not in [s["index"] for s in traj["steps"]]:
                raise ValidationError("Evidence step does not exist in this trajectory")
            cursor = self.conn.execute("INSERT INTO annotations(trajectory_id,created_at,data) VALUES (?,?,?)", (trajectory_id, d["created_at"], dumps(d)))
            d["id"] = cursor.lastrowid
        return d

    def runs(self) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.conn.execute("SELECT data FROM runs ORDER BY rowid DESC")]

    def panels(self) -> list[dict]:
        with self.lock:
            return [json.loads(r[0]) for r in self.conn.execute("SELECT data FROM panels ORDER BY rowid")]

    def panel(self, panel_id: str) -> dict:
        with self.lock:
            d = self._get("panels", panel_id)
            d["samples"] = [json.loads(r[0]) for r in self.conn.execute("SELECT data FROM panel_samples WHERE panel_id=? ORDER BY ordinal", (panel_id,))]
            return d

    def metrics(self, run_id: str) -> list[dict]:
        with self.lock:
            self._get("runs", run_id)
            return [{"step": r[0], **json.loads(r[1])} for r in self.conn.execute("SELECT step,data FROM metrics WHERE run_id=? ORDER BY step", (run_id,))]

    def _stats(self, evaluation_id: str, panel: dict) -> dict:
        rows = self.conn.execute("""SELECT dataset,count(*) AS recorded,
          count(em) AS scored,avg(em) AS em,avg(f1) AS f1,
          sum(outcome='correct') AS correct,sum(outcome='incorrect') AS incorrect,
          sum(CASE WHEN em=1 THEN 1 ELSE 0 END) AS answer_correct,
          sum(outcome='error') AS errors,sum(outcome='unscored') AS unscored
          FROM trajectories WHERE evaluation_id=? GROUP BY dataset""", (evaluation_id,)).fetchall()
        by_ds = {r["dataset"]: dict(r) for r in rows}
        datasets = {}
        for name, expected in panel["datasets"].items():
            r = by_ds.get(name, {"recorded": 0, "scored": 0, "em": None, "f1": None, "correct": 0, "incorrect": 0, "errors": 0, "unscored": 0, "answer_correct": 0})
            r["expected"] = expected
            r["fully_scored"] = r["scored"] == expected
            datasets[name] = r
        # Do not silently average only datasets which happen to have finished.
        complete = all(r["fully_scored"] for r in datasets.values())
        macro = sum(r["em"] for r in datasets.values()) / len(datasets) if complete else None
        return {"datasets": datasets, "recorded": sum(r["recorded"] for r in datasets.values()),
                "scored": sum(r["scored"] for r in datasets.values()), "answer_correct": sum(r["answer_correct"] for r in datasets.values()), "expected": panel["size"],
                "errors": sum(r["errors"] for r in datasets.values()), "macro_em": macro,
                "fully_scored": complete}

    def evaluation(self, evaluation_id: str) -> dict:
        with self.lock:
            d = self._get("evaluations", evaluation_id)
            panel = self._get("panels", d["panel_id"])
            d["checkpoint"] = self._get("checkpoints", d["checkpoint_id"])
            d["panel"] = panel
            d["stats"] = self._stats(evaluation_id, panel)
            return d

    def overview(self, run_id: str, panel_id: str) -> dict:
        with self.lock:
            run = self._get("runs", run_id)
            panel = self._get("panels", panel_id)
            checkpoints = []
            for row in self.conn.execute("SELECT data FROM checkpoints WHERE run_id=? ORDER BY step,id", (run_id,)):
                cp = json.loads(row[0])
                evals = [self.evaluation(r[0]) for r in self.conn.execute("SELECT id FROM evaluations WHERE checkpoint_id=? AND panel_id=? ORDER BY created_at DESC,rowid DESC", (cp["id"], panel_id)).fetchall()]
                cp["evaluations"] = evals
                checkpoints.append(cp)
            return {"run": run, "panel": panel, "checkpoints": checkpoints, "metrics": self.metrics(run_id)}

    @staticmethod
    def _summary(d: dict) -> dict:
        return {k: d.get(k) for k in ("id", "evaluation_id", "dataset", "sample_id", "question", "outcome", "runtime_status", "scores", "usage", "parsed_answer", "termination_reason")} | {"step_count": (len(d["steps"]) if "steps" in d else d.get("step_count",0))}

    def trajectories(self, evaluation_id: str, dataset: str = "", outcome: str = "", query: str = "", offset: int = 0, limit: int = 50) -> dict:
        integer(offset, "offset")
        integer(limit, "limit", 1)
        if limit > 200:
            raise ValidationError("limit must be <= 200")
        if outcome and outcome not in OUTCOMES:
            raise ValidationError("Invalid outcome filter")
        with self.lock:
            self._get("evaluations", evaluation_id)
            # Search actual question/sample text, not the hidden gold answer or raw trace.
            clauses = ["t.evaluation_id=?"]
            args: list[Any] = [evaluation_id]
            if dataset:
                clauses.append("t.dataset=?")
                args.append(dataset)
            if query:
                # The question is also in the immutable summary. Searching a
                # list must not parse every raw model/tool response in its trace.
                clauses.append("(json_extract(s.data,'$.question') LIKE ? ESCAPE '\\' OR t.sample_id LIKE ? ESCAPE '\\')")
                q = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                args.extend([q, q])
            where = " AND ".join(clauses)
            counts = {k: 0 for k in OUTCOMES}
            join = " JOIN trajectory_summaries s ON s.id=t.id"
            # For the common unsearched list, counts use the existing covering
            # outcome index. Only text search needs the small summary payload.
            count_source = "trajectories t" + (join if query else "")
            for r in self.conn.execute(f"SELECT t.outcome,count(*) FROM {count_source} WHERE {where} GROUP BY t.outcome", args):
                counts[r[0]] = r[1]
            counts["all"] = sum(counts.values())
            if outcome:
                clauses.append("t.outcome=?")
                args.append(outcome)
            where = " AND ".join(clauses)
            total = counts[outcome] if outcome else counts["all"]
            rows = self.conn.execute(f"SELECT s.data FROM trajectories t{join} WHERE {where} ORDER BY t.dataset,t.sample_id LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
            return {"items": [json.loads(r[0]) for r in rows], "total": total, "counts": counts, "offset": offset, "limit": limit}

    def trajectory(self, trajectory_id: str) -> dict:
        with self.lock:
            d = self._get("trajectories", trajectory_id)
            sample = self.conn.execute("""SELECT ps.data FROM panel_samples ps JOIN evaluations ev ON ev.panel_id=ps.panel_id
                WHERE ev.id=? AND ps.dataset=? AND ps.sample_id=?""", (d["evaluation_id"], d["dataset"], d["sample_id"])).fetchone()
            d["panel_sample"] = json.loads(sample[0]) if sample else None
            d["annotations"] = [{**json.loads(r["data"]), "id": r["id"]} for r in self.conn.execute("SELECT id,data FROM annotations WHERE trajectory_id=? ORDER BY id DESC", (trajectory_id,))]
            return d

    def compare(self, a_id: str, b_id: str, dataset: str = "", group: str = "", query: str = "") -> dict:
        with self.lock:
            a, b = self.evaluation(a_id), self.evaluation(b_id)
            reasons = []
            if a_id == b_id:
                reasons.append("请选择两条不同的评测记录")
            if a["panel_hash"] != b["panel_hash"]:
                reasons.append("固定题单内容 / 标准答案的 SHA256 不一致")
            if a["protocol_hash"] != b["protocol_hash"]:
                reasons.append("检索、预算、解码、seed、解析或评分协议不一致")
            if not (a["protocol_complete"] and b["protocol_complete"]):
                reasons.append("协议包含 unknown，不能声称同协议对比")
            if a["demo"] != b["demo"]:
                reasons.append("演示数据不能与真实评测对比")
            result = {"a": a, "b": b, "compatible": not reasons, "reasons": reasons}
            if reasons:
                return result
            da = {(r["dataset"], r["sample_id"]): r for r in [json.loads(x[0]) for x in self.conn.execute("SELECT data FROM trajectory_summaries WHERE evaluation_id=?", (a_id,))]}
            db = {(r["dataset"], r["sample_id"]): r for r in [json.loads(x[0]) for x in self.conn.execute("SELECT data FROM trajectory_summaries WHERE evaluation_id=?", (b_id,))]}
            paired = set(da) & set(db)
            groups = {k: 0 for k in ("rescued", "harmed", "both_correct", "both_wrong", "unscored")}
            items = []
            for key in sorted(paired):
                x, y = da[key], db[key]
                if dataset and key[0] != dataset:
                    continue
                if query and query.casefold() not in (x["question"] + " " + key[1]).casefold():
                    continue
                em_a, em_b = x["scores"].get("em"), y["scores"].get("em")
                if em_a is None or em_b is None:
                    g = "unscored"
                elif em_a == 0 and em_b == 1:
                    g = "rescued"
                elif em_a == 1 and em_b == 0:
                    g = "harmed"
                else:
                    g = "both_correct" if em_a == 1 else "both_wrong"
                groups[g] += 1
                if not group or group == g:
                    items.append({"dataset": key[0], "sample_id": key[1], "question": x["question"], "group": g, "a": self._summary(x), "b": self._summary(y)})
            full = (a["status"] == b["status"] == "completed" and a["stats"]["fully_scored"] and b["stats"]["fully_scored"])
            result.update({"groups": groups, "items": items, "paired": len(paired), "expected": a["panel"]["size"], "full": full,
                           "macro_delta": b["stats"]["macro_em"] - a["stats"]["macro_em"] if full else None})
            return result

    def export_evaluation(self, evaluation_id: str) -> dict:
        with self.lock:
            ev = self.evaluation(evaluation_id)
            rows = [self.trajectory(r[0]) for r in self.conn.execute("SELECT id FROM trajectories WHERE evaluation_id=? ORDER BY dataset,sample_id", (evaluation_id,)).fetchall()]
            return {"schema_version": 1, "export_type": "evaluation_evidence", "exported_at": now(), "evaluation": ev, "trajectories": rows}

    def health(self) -> dict:
        with self.lock:
            return {"ok": self.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "schema_version": 1,
                    "runs": self.conn.execute("SELECT count(*) FROM runs").fetchone()[0],
                    "evaluations": self.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0],
                    "trajectories": self.conn.execute("SELECT count(*) FROM trajectories").fetchone()[0]}
