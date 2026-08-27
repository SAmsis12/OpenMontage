from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from lib.pipeline_loader import (
    PIPELINE_DEFS_DIR,
    get_stage_human_approval_default,
    get_stage_order,
    load_pipeline_readonly,
)

from .contract import BridgeError
from .readers import ProjectEvidence, read_project

OPERATIONS = ["capabilities", "status", "preview", "cost", "approval"]
PROJECT_LAYOUT = [
    "artifacts",
    "assets/images",
    "assets/video",
    "assets/audio",
    "assets/music",
    "renders",
]
PIPELINE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
STATUS_VALUES = {"pending", "in_progress", "awaiting_human", "completed", "failed"}


def _number(value: object, *, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise BridgeError("EVIDENCE_INVALID")
    return round(float(value), 4)


def pipeline_names() -> list[str]:
    names = []
    for path in PIPELINE_DEFS_DIR.iterdir():
        if (
            path.suffix == ".yaml"
            and not path.is_symlink()
            and path.is_file()
            and PIPELINE_RE.fullmatch(path.stem)
        ):
            names.append(path.stem)
    return sorted(names)


def load_allowed_pipeline(name: str) -> dict[str, Any]:
    if not isinstance(name, str) or name not in pipeline_names():
        raise BridgeError("UNKNOWN_PIPELINE")
    try:
        return load_pipeline_readonly(name)
    except Exception:
        raise BridgeError("UNKNOWN_PIPELINE") from None


def _tools(manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    required: set[str] = set()
    optional: set[str] = set()
    for stage in manifest["stages"]:
        required.update(stage.get("required_tools", []))
        optional.update(stage.get("optional_tools", []))
    optional -= required
    return sorted(required), sorted(optional)


def capabilities_result() -> dict[str, Any]:
    pipelines = []
    for name in pipeline_names():
        manifest = load_allowed_pipeline(name)
        pipelines.append(
            {
                "name": name,
                "category": manifest["category"],
                "stability": manifest["stability"],
            }
        )
    return {
        "mode": "read_only_dry_run",
        "operations": OPERATIONS,
        "guarantees": {
            "writes": False,
            "tool_execution": False,
            "provider_calls": False,
            "secret_reads": False,
            "shell": False,
        },
        "receipt": "ephemeral_preview_attestation",
        "tool_availability": "unknown_not_probed",
        "pipelines": pipelines,
    }


def _cost_from_log(cost_log: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if cost_log.get("version") != "1.0":
        raise BridgeError("EVIDENCE_INVALID")
    total = _number(cost_log.get("budget_total_usd"), field="budget_total_usd")
    entries = cost_log.get("entries")
    approved = cost_log.get("approved_tools", [])
    if not isinstance(entries, list) or not isinstance(approved, list) or not all(
        isinstance(tool, str) for tool in approved
    ):
        raise BridgeError("EVIDENCE_INVALID")
    spent = 0.0
    reserved = 0.0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("status"), str):
            raise BridgeError("EVIDENCE_INVALID")
        status = entry["status"]
        if status in {"completed", "failed"}:
            spent += _number(entry.get("actual_usd", 0.0), field="actual_usd")
        if status == "reserved":
            reserved += _number(entry.get("reserved_usd", 0.0), field="reserved_usd")
    current = {
        "spent_usd": round(spent, 4),
        "reserved_usd": round(reserved, 4),
        "remaining_usd": round(total - spent - reserved, 4),
        "evidence": "cost_log",
    }
    context = {"budget_total_usd": total, "approved_tools": set(approved)}
    return current, context


def _cost_from_checkpoints(
    evidence: ProjectEvidence, stages: list[str]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for stage in reversed(stages):
        checkpoint = evidence.checkpoints.get(stage)
        if not checkpoint or "cost_snapshot" not in checkpoint:
            continue
        snapshot = checkpoint["cost_snapshot"]
        if not isinstance(snapshot, dict):
            raise BridgeError("EVIDENCE_INVALID")
        spent = _number(snapshot.get("total_spent_usd"), field="total_spent_usd")
        reserved = _number(snapshot.get("total_reserved_usd"), field="total_reserved_usd")
        remaining = _number(snapshot.get("budget_remaining_usd"), field="budget_remaining_usd")
        return (
            {
                "spent_usd": spent,
                "reserved_usd": reserved,
                "remaining_usd": remaining,
                "evidence": "checkpoint_snapshot",
            },
            {"budget_total_usd": round(spent + reserved + remaining, 4), "approved_tools": set()},
        )
    return None


def current_cost(
    evidence: ProjectEvidence, stages: list[str]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if evidence.cost_log is not None:
        return _cost_from_log(evidence.cost_log)
    checkpoint = _cost_from_checkpoints(evidence, stages)
    if checkpoint is not None:
        return checkpoint
    return (
        {
            "spent_usd": None,
            "reserved_usd": None,
            "remaining_usd": None,
            "evidence": "unavailable",
        },
        None,
    )


def _status_projection(evidence: ProjectEvidence) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not evidence.initialized:
        return (
            {
                "project_id": evidence.project_id,
                "existence": "absent" if not evidence.exists else "uninitialized",
                "pipeline_type": None,
                "current_stage": None,
                "terminal": False,
                "evidence": "unavailable",
                "stages": [],
                "cost": {
                    "spent_usd": None,
                    "reserved_usd": None,
                    "remaining_usd": None,
                    "evidence": "unavailable",
                },
            },
            None,
        )

    manifest = load_allowed_pipeline(evidence.pipeline_type or "")
    stages = get_stage_order(manifest)
    projected = []
    for checkpoint_stage in evidence.checkpoints:
        if checkpoint_stage not in stages:
            evidence.degraded = True
    for stage in stages:
        gated = bool(get_stage_human_approval_default(manifest, stage))
        checkpoint = evidence.checkpoints.get(stage)
        if checkpoint is None:
            status = "pending"
            approval = "required" if gated else "not_required"
        else:
            raw_status = checkpoint.get("status")
            if raw_status not in STATUS_VALUES:
                evidence.degraded = True
                status = "unknown"
                approval = "unknown" if gated else "not_required"
            elif gated and raw_status == "completed" and checkpoint.get("human_approved") is not True:
                evidence.degraded = True
                status = "unknown"
                approval = "unknown"
            else:
                status = raw_status
                if not gated:
                    approval = "not_required"
                elif raw_status == "completed" and checkpoint.get("human_approved") is True:
                    approval = "satisfied"
                else:
                    approval = "required"
        projected.append(
            {"name": stage, "status": status, "gated": gated, "approval": approval}
        )
    terminal = bool(projected) and all(stage["status"] == "completed" for stage in projected)
    current_stage = None if terminal else next(
        (stage["name"] for stage in projected if stage["status"] != "completed"), None
    )
    cost, context = current_cost(evidence, stages)
    return (
        {
            "project_id": evidence.project_id,
            "existence": "initialized",
            "pipeline_type": evidence.pipeline_type,
            "current_stage": current_stage,
            "terminal": terminal,
            "evidence": "degraded" if evidence.degraded else "complete",
            "stages": projected,
            "cost": cost,
        },
        context,
    )


def status_result(projects_root: Path, project_id: str) -> dict[str, Any]:
    evidence = read_project(projects_root, project_id)
    return _status_projection(evidence)[0]


def preview_result(projects_root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    evidence = read_project(projects_root, arguments["project_id"])
    manifest = load_allowed_pipeline(arguments["pipeline_type"])
    if evidence.initialized and evidence.pipeline_type != arguments["pipeline_type"]:
        raise BridgeError("INVALID_REQUEST")
    stages = get_stage_order(manifest)
    target = arguments["target_stage"]
    if target is not None and target not in stages:
        raise BridgeError("INVALID_REQUEST")
    required, optional = _tools(manifest)
    estimate = round(sum(item["estimated_usd"] for item in arguments["proposed_operations"]), 4)
    reasons: list[str] = []
    if estimate > 0:
        reasons.append("first_paid_tool")
    if any(item["estimated_usd"] > 0.5 for item in arguments["proposed_operations"]):
        reasons.append("single_action_threshold")
    if target and get_stage_human_approval_default(manifest, target):
        reasons.append("human_gate")
    if evidence.initialized:
        status, _ = _status_projection(evidence)
        next_stage = status["current_stage"]
    else:
        next_stage = stages[0] if stages else None
    return {
        "dry_run": True,
        "project": {
            "exists": evidence.initialized,
            "would_initialize": not evidence.initialized,
            "layout": PROJECT_LAYOUT,
        },
        "pipeline": {"name": arguments["pipeline_type"], "stages": stages, "next_stage": next_stage},
        "tools": {"required": required, "optional": optional, "availability": "unknown_not_probed"},
        "cost": {
            "estimate_usd": estimate if arguments["proposed_operations"] else None,
            "estimate_source": "request_provided" if arguments["proposed_operations"] else "unavailable",
            "would_reserve": False,
            "would_spend": False,
        },
        "approval": {"required": bool(reasons), "reasons": reasons, "granted": False},
        "effects": [],
    }


def cost_result(projects_root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    evidence = read_project(projects_root, arguments["project_id"])
    stages: list[str] = []
    if evidence.initialized:
        stages = get_stage_order(load_allowed_pipeline(evidence.pipeline_type or ""))
    current, context = current_cost(evidence, stages)
    proposal = None
    if context is not None and "proposed_cost_usd" in arguments:
        estimate = arguments["proposed_cost_usd"]
        tool = arguments["tool"]
        usable = max(0.0, current["remaining_usd"] - context["budget_total_usd"] * 0.10)
        proposal = {
            "estimated_usd": estimate,
            "estimate_source": "request_provided",
            "would_exceed_usable_budget": estimate > usable,
            "would_require_approval": estimate > 0.5 or (
                estimate > 0 and tool not in context["approved_tools"]
            ),
        }
    return {"current": current, "proposal": proposal, "mutation": "none"}


def approval_result(projects_root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    evidence = read_project(projects_root, arguments["project_id"])
    if not evidence.initialized:
        raise BridgeError("PROJECT_UNREADABLE")
    manifest = load_allowed_pipeline(evidence.pipeline_type or "")
    stage = arguments["stage"]
    required = get_stage_human_approval_default(manifest, stage)
    if required is None:
        raise BridgeError("INVALID_REQUEST")
    checkpoint = evidence.checkpoints.get(stage)
    raw_state = checkpoint.get("status") if checkpoint else "pending"
    if raw_state not in STATUS_VALUES:
        evidence.degraded = True
        state = "unknown"
    elif (
        required
        and raw_state == "completed"
        and checkpoint is not None
        and checkpoint.get("human_approved") is not True
    ):
        evidence.degraded = True
        state = "unknown"
    else:
        state = raw_state
    satisfied = bool(required and state == "completed" and checkpoint and checkpoint.get("human_approved") is True)
    can_advance = state == "completed" and (not required or satisfied)
    return {
        "stage": stage,
        "required": bool(required),
        "source": "pipeline_manifest",
        "state": state,
        "satisfied": satisfied,
        "can_advance": can_advance,
        "mutation": "none",
    }
