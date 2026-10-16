"""Audits only the *supplied* project training/test inventories.

Exact normalized questions and source IDs are checked across all projects. Near
matches are lexical candidates for human review, not semantic decontamination.
No inventory can prove absence from a base model's pretraining.
"""
from __future__ import annotations
import difflib
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable
from .core import ValidationError, digest, text

LIMITATION = ("仅覆盖所提供且已列明版本的训练/正式测试清单；完整覆盖是用户声明，不是系统证明。"
              "近重复为词面启发式，不能排除语义改写、来源共享或底座预训练污染。"
              "新增 self-play/蒸馏/生成/技能材料后必须重新审核；旧报告不会自动覆盖新数据。")


def normalize_question(value: str) -> str:
    s = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def tokens(value: str) -> set[str]:
    return set(normalize_question(value).split())


def read_rows(path: Path) -> Iterable[dict]:
    """Documented JSON list / {samples:[]} or JSONL; never guess a data adapter."""
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8-sig") as f:
            for line, raw in enumerate(f, 1):
                if raw.strip():
                    item = json.loads(raw)
                    if not isinstance(item, dict): raise ValidationError(f"{path.name}:{line}: each record must be an object")
                    yield item
    elif path.suffix.lower() == ".json":
        with path.open(encoding="utf-8-sig") as f: obj = json.load(f)
        rows = obj.get("samples") if isinstance(obj, dict) else obj
        if not isinstance(rows, list): raise ValidationError("Inventory JSON must be a list or {samples: [...]} object")
        for item in rows:
            if not isinstance(item, dict): raise ValidationError("Inventory rows must be objects")
            yield item
    else:
        raise ValidationError("Inventory files must be .json/.jsonl. Convert actual fields explicitly first")


def audit_panel(panel: dict, inventory: dict, base_dir: str | Path,
                expected_projects: list[str] | None = None) -> dict:
    if not isinstance(inventory, dict): raise ValidationError("inventory must be an object")
    projects = inventory.get("projects")
    entries = inventory.get("entries")
    if not isinstance(projects, list) or not projects or not all(isinstance(x,str) and x for x in projects):
        raise ValidationError("inventory.projects must explicitly list every research project")
    if len(set(projects)) != len(projects): raise ValidationError("Duplicate project in inventory")
    if not isinstance(entries, list): raise ValidationError("inventory.entries must be a list")
    required = set(expected_projects or projects)
    incomplete = []
    if not required.issubset(projects): incomplete.append("Missing projects: " + ", ".join(sorted(required-set(projects))))
    by_id: dict[tuple[str,str],list] = {}; by_question: dict[str,list] = {}; inverted: dict[str,set] = {}
    samples = panel.get("samples", [])
    if not samples: raise ValidationError("Panel must contain actual frozen questions")
    normalized = []
    for i, s in enumerate(samples):
        text(s.get("question"), "panel question")
        normalized.append(normalize_question(s["question"]))
        by_id.setdefault((s["dataset"], str(s.get("metadata",{}).get("source_id", s["id"]))), []).append(i)
        by_question.setdefault(normalized[-1], []).append(i)
        for token in tokens(s["question"]): inverted.setdefault(token,set()).add(i)
    exact, near, files = [], [], []
    exact_seen, near_seen = set(), set()
    roles: dict[str,set] = {p:set() for p in projects}
    comparisons = 0
    max_near_comparisons = int(inventory.get("max_near_comparisons", 2000000))
    if max_near_comparisons < 1: raise ValidationError("max_near_comparisons must be positive")
    for entry in entries:
        if not isinstance(entry, dict): raise ValidationError("Each inventory entry must be an object")
        project, role = entry.get("project_id"), entry.get("role")
        if project not in projects or role not in ("train", "test"):
            raise ValidationError("Each inventory entry needs a listed project_id and role=train/test")
        for field in ("path", "revision", "description"): text(entry.get(field), "inventory entry."+field)
        roles[project].add(role)
        if entry["revision"].strip().casefold() in ("unknown", "unspecified", "not recorded", "未记录", "未提供"):
            incomplete.append(f"{project}/{role}/{entry['path']}: source revision is unknown")
        if entry.get("declared_complete") is not True:
            incomplete.append(f"{project}/{role}/{entry['path']}: coverage not declared complete")
        path = (Path(base_dir) / entry["path"]).resolve()
        if not path.is_file(): raise ValidationError(f"Inventory source does not exist: {path}")
        file_hash = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda:f.read(1024*1024), b""): file_hash.update(block)
        file_sha = file_hash.hexdigest()
        if entry.get("sha256") and entry["sha256"] != file_sha:
            raise ValidationError(f"Inventory checksum mismatch for {entry['path']}")
        n = 0
        for n, row in enumerate(read_rows(path), 1):
            for field in ("dataset", "id", "question"): text(row.get(field), "inventory row." + field)
            qn = normalize_question(row["question"])
            source_id = str(row.get("metadata",{}).get("source_id", row["id"]))
            matches = {i:"source_id" for i in by_id.get((row["dataset"], source_id), [])}
            matches.update({i:"normalized_question" for i in by_question.get(qn,[])})
            source = {"project_id":project, "role":role, "file":entry["path"], "file_sha256":file_sha,
                      "revision":entry["revision"], "row":n, "dataset":row["dataset"], "id":row["id"]}
            for i, reason in matches.items():
                key = (i,entry["path"],project,role,n)
                if key not in exact_seen:
                    exact_seen.add(key)
                    exact.append({"validation_dataset":samples[i]["dataset"], "validation_id":samples[i]["id"],
                                  "reason":reason, "source":source})
            # Candidate retrieval by shared normalized tokens, then two independent
            # lexical similarity tests. A capped search is explicitly incomplete.
            shared: dict[int,int] = {}
            rtokens = set(qn.split())
            for token in rtokens:
                for i in inverted.get(token,()): shared[i] = shared.get(i,0)+1
            for i, common in shared.items():
                if i in matches or common < 2: continue
                if comparisons >= max_near_comparisons: continue
                comparisons += 1
                ptokens = set(normalized[i].split())
                jaccard = common/max(1,len(ptokens|rtokens))
                ratio = difflib.SequenceMatcher(None, normalized[i], qn, autojunk=False).ratio()
                if jaccard >= .8 or ratio >= .92:
                    key = (i,entry["path"],project,role,n)
                    if key not in near_seen:
                        near_seen.add(key)
                        near.append({"validation_dataset":samples[i]["dataset"], "validation_id":samples[i]["id"],
                            "validation_question":samples[i]["question"], "candidate_question":row["question"],
                            "token_jaccard":round(jaccard,4), "character_ratio":round(ratio,4), "source":source})
        recheck = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda:f.read(1024*1024), b""): recheck.update(block)
        if recheck.hexdigest() != file_sha:
            raise ValidationError("Inventory changed while auditing; seal files before running the audit")
        files.append({**entry, "sha256":file_sha, "rows":n})
    for project, covered in roles.items():
        for role in ("train", "test"):
            if role not in covered: incomplete.append(f"{project}: no {role} inventory")
    if comparisons >= max_near_comparisons: incomplete.append("Near-duplicate candidate comparisons reached the cap; audit may be incomplete")
    status = "blocked_overlap" if exact else "manual_review" if near else "incomplete_scope" if incomplete else "no_detected_overlap_in_supplied_scope"
    return {"status":status, "panel_hash":panel["fingerprint"], "projects":projects,
        "inventory_hash":digest({"projects":projects, "files":files}), "files":files,
        "exact_conflicts":exact, "near_candidates":near, "incomplete_reasons":incomplete,
        "heuristic": {"normalization":"NFKC + casefold + punctuation to spaces + whitespace collapse",
                       "token_jaccard_threshold":.8, "character_ratio_threshold":.92,
                       "comparisons":comparisons, "cap":max_near_comparisons},
        "limitation":LIMITATION, "base_model_pretraining_verified":False}
