from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.hermes_bridge.contract import BridgeError, handle_request, parse_request


def _request(operation: str, arguments: dict) -> dict:
    return {
        "version": "1.0",
        "request_id": "req_test",
        "operation": operation,
        "arguments": arguments,
    }


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _project(root: Path) -> Path:
    project = root / "film"
    project.mkdir()
    _write(
        project / "project.json",
        {"version": "1.0", "project_id": "film", "pipeline_type": "cinematic"},
    )
    return project


def _result(root: Path, operation: str, arguments: dict) -> dict:
    response = handle_request(parse_request(_request(operation, arguments)), projects_root=root)
    assert response["ok"] is True
    return response["result"]


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_capabilities_are_static_read_only_and_pipeline_catalog_is_allowlisted(tmp_path: Path) -> None:
    result = _result(tmp_path, "capabilities", {})
    assert result["mode"] == "read_only_dry_run"
    assert result["operations"] == ["capabilities", "status", "preview", "cost", "approval"]
    assert result["guarantees"] == {
        "writes": False,
        "tool_execution": False,
        "provider_calls": False,
        "secret_reads": False,
        "shell": False,
    }
    assert result["tool_availability"] == "unknown_not_probed"
    assert "cinematic" in {pipeline["name"] for pipeline in result["pipelines"]}


def test_preview_rejects_pipeline_traversal_and_unknown_pipeline(tmp_path: Path) -> None:
    args = {
        "project_id": "film",
        "pipeline_type": "../cinematic",
        "target_stage": None,
        "proposed_operations": [],
    }
    with pytest.raises(BridgeError, match="UNKNOWN_PIPELINE"):
        handle_request(parse_request(_request("preview", args)), projects_root=tmp_path)

    args["pipeline_type"] = "not-shipped"
    with pytest.raises(BridgeError, match="UNKNOWN_PIPELINE"):
        handle_request(parse_request(_request("preview", args)), projects_root=tmp_path)


def test_preview_is_dry_run_with_no_effects_and_request_sourced_cost(tmp_path: Path) -> None:
    result = _result(
        tmp_path,
        "preview",
        {
            "project_id": "new-film",
            "pipeline_type": "cinematic",
            "target_stage": None,
            "proposed_operations": [
                {"tool": "video_selector", "operation": "generate", "estimated_usd": 0.3}
            ],
        },
    )
    assert result["dry_run"] is True
    assert result["project"]["would_initialize"] is True
    assert result["pipeline"]["next_stage"] == "research"
    assert result["cost"] == {
        "estimate_usd": 0.3,
        "estimate_source": "request_provided",
        "would_reserve": False,
        "would_spend": False,
    }
    assert result["approval"]["granted"] is False
    assert result["effects"] == []
    assert not (tmp_path / "new-film").exists()


def test_status_and_approval_fail_closed_on_degraded_checkpoint(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(
        project / "checkpoint_proposal.json",
        {
            "project_id": "film",
            "pipeline_type": "cinematic",
            "stage": "proposal",
            "status": "completed",
            "human_approved": False,
        },
    )
    status = _result(tmp_path, "status", {"project_id": "film"})
    proposal = next(stage for stage in status["stages"] if stage["name"] == "proposal")
    assert status["evidence"] == "degraded"
    assert proposal == {
        "name": "proposal",
        "status": "unknown",
        "gated": True,
        "approval": "unknown",
    }
    approval = _result(tmp_path, "approval", {"project_id": "film", "stage": "proposal"})
    assert approval["required"] is True
    assert approval["satisfied"] is False
    assert approval["can_advance"] is False


def test_cost_log_precedes_checkpoint_and_never_mutates(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(
        project / "checkpoint_research.json",
        {
            "project_id": "film",
            "pipeline_type": "cinematic",
            "stage": "research",
            "status": "completed",
            "human_approved": False,
            "cost_snapshot": {
                "total_spent_usd": 9.0,
                "total_reserved_usd": 0.0,
                "budget_remaining_usd": 0.0,
            },
        },
    )
    _write(
        project / "cost_log.json",
        {
            "version": "1.0",
            "budget_total_usd": 2.0,
            "approved_tools": [],
            "entries": [
                {"tool": "image", "status": "completed", "actual_usd": 0.2},
                {"tool": "video", "status": "reserved", "reserved_usd": 0.1},
            ],
        },
    )
    before = _tree_snapshot(tmp_path)
    cost = _result(
        tmp_path,
        "cost",
        {"project_id": "film", "proposed_cost_usd": 0.75, "tool": "video_selector"},
    )
    assert cost["current"] == {
        "spent_usd": 0.2,
        "reserved_usd": 0.1,
        "remaining_usd": 1.7,
        "evidence": "cost_log",
    }
    assert cost["proposal"]["estimate_source"] == "request_provided"
    assert cost["proposal"]["would_require_approval"] is True
    assert cost["mutation"] == "none"
    assert _tree_snapshot(tmp_path) == before


def test_missing_cost_evidence_does_not_invent_estimate(tmp_path: Path) -> None:
    _project(tmp_path)
    result = _result(
        tmp_path,
        "cost",
        {"project_id": "film", "proposed_cost_usd": 0.75, "tool": "video_selector"},
    )
    assert result == {
        "current": {
            "spent_usd": None,
            "reserved_usd": None,
            "remaining_usd": None,
            "evidence": "unavailable",
        },
        "proposal": None,
        "mutation": "none",
    }


@pytest.mark.parametrize("operation", ["capabilities", "status", "preview", "cost", "approval"])
def test_all_operations_leave_project_tree_byte_identical(tmp_path: Path, operation: str) -> None:
    _project(tmp_path)
    arguments = {
        "capabilities": {},
        "status": {"project_id": "film"},
        "preview": {
            "project_id": "film",
            "pipeline_type": "cinematic",
            "target_stage": None,
            "proposed_operations": [],
        },
        "cost": {"project_id": "film"},
        "approval": {"project_id": "film", "stage": "proposal"},
    }[operation]
    before = _tree_snapshot(tmp_path)
    _result(tmp_path, operation, arguments)
    assert _tree_snapshot(tmp_path) == before
