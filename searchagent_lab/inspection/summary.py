"""Small projections of the existing rule findings and hash-bound review sidecar.

No new classifier, scorer, judge or causal claims. Both execution views use this
same projection. Reads never write annotations or change official scores.
"""
from __future__ import annotations

PRIORITY = {name: i for i, name in enumerate((
    "runtime_error", "tool_error", "tool_args", "parser_mismatch", "parser_missing",
    "score_mismatch", "budget_stop", "tool_empty", "repeated_query", "alias_lost",
    "json_invalid", "no_steps", "tool_missing_return", "visible_missing", "unscored"))}


def problem_summary(doc: dict) -> dict:
    findings = doc.get("report", {}).get("findings", [])
    by_id = {item["id"]: item for item in findings}
    latest = {}
    for review in doc.get("reviews", []):
        key = ("finding", review["finding_id"]) if review.get("finding_id") else ("pointer", review.get("pointer", ""))
        latest[key] = review
    confirmed = [r for r in latest.values() if r["status"] == "confirmed"]
    base = {"source_hash": doc["source_hash"], "target": doc["target"],
            "cause_status": "unknown", "review_count": len(doc.get("reviews", [])),
            "ruleset": doc.get("report", {}).get("ruleset"), "finding_count": len(findings)}
    if confirmed:
        review = max(confirmed, key=lambda r: r.get("id", 0))
        finding = by_id.get(review.get("finding_id"), {})
        return {**base, "status": "confirmed", "label": "已复核", "title": review["note"],
                "explanation": finding.get("explanation", "人工记录的观察；不是训练根因证明。"),
                "finding_id": review.get("finding_id"), "pointer": review.get("pointer", ""),
                "step_index": finding.get("step_index"), "evidence": finding.get("evidence", []),
                "limitation": "人工确认的是该条观察，不自动确认训练原因或改动正式评分。",
                "next_check": finding.get("next_check", "按当前证据继续核查。")}
    active = [f for f in findings if latest.get(("finding", f["id"]), {}).get("status") != "rejected"]
    if active:
        finding = min(active, key=lambda f: (PRIORITY.get(f["rule"], 99), f.get("step_index") if isinstance(f.get("step_index"), int) else 10**9))
        evidence = finding.get("evidence", [])
        gap = finding.get("basis") == "insufficient" and finding["rule"] != "parser_missing"
        return {**base, "status": "insufficient" if gap else "automatic", "label": "记录缺失" if gap else "自动线索",
                "title": finding["title"], "explanation": finding["explanation"],
                "finding_id": finding["id"], "pointer": evidence[0]["pointer"] if evidence else "",
                "step_index": finding.get("step_index"), "evidence": evidence,
                "limitation": finding["limitation"], "next_check": finding["next_check"]}
    return {**base, "status": "unknown", "label": "待判断", "title": "暂无可用的问题摘要",
            "finding_id": None, "pointer": "", "step_index": None, "evidence": [],
            "explanation": "未触发规则或已有线索被驳回，不代表这条轨迹没有问题。",
            "limitation": "不从任务成败自动生成根因。", "next_check": "查看实际步骤，必要时留下人工复核。"}
