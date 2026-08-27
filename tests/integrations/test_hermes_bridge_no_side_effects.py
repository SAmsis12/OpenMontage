from __future__ import annotations

import builtins
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from integrations.hermes_bridge.contract import handle_request, parse_request


def _request(operation: str, arguments: dict) -> dict:
    return {
        "version": "1.0",
        "request_id": f"safe_{operation}",
        "operation": operation,
        "arguments": arguments,
    }


def test_bridge_never_discovers_tools_reads_dotenv_calls_network_shell_or_writes(
    tmp_path: Path, monkeypatch,
) -> None:
    project = tmp_path / "film"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"version": "1.0", "project_id": "film", "pipeline_type": "cinematic"}),
        encoding="utf-8",
    )
    before = {path.name: path.read_bytes() for path in project.iterdir()}
    modules_before = set(sys.modules)

    def forbidden(*args, **kwargs):
        raise AssertionError("provider, network, or subprocess call attempted")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(os, "system", forbidden)

    real_open = builtins.open
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "dotenv" or name.startswith("tools.tool_registry") or name.startswith("tools.base_tool"):
            raise AssertionError("tool registry or dotenv import attempted")
        return real_import(name, *args, **kwargs)

    def guarded_open(file, mode="r", *args, **kwargs):
        path = os.fspath(file)
        assert Path(path).name != ".env", ".env read attempted"
        assert not any(flag in mode for flag in "wax+"), "write attempted"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    calls = {
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
        "identity_readiness": {"project_id": "film"},
    }
    for operation, arguments in calls.items():
        response = handle_request(
            parse_request(_request(operation, arguments)), projects_root=tmp_path
        )
        assert response["ok"] is True

    after = {path.name: path.read_bytes() for path in project.iterdir()}
    assert after == before
    newly_loaded = set(sys.modules) - modules_before
    assert "tools.tool_registry" not in newly_loaded
    assert "tools.base_tool" not in newly_loaded
    assert "dotenv" not in newly_loaded
