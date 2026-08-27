from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "integrations/hermes_bridge/__main__.py"


def _run(payload: bytes, projects_root: Path) -> subprocess.CompletedProcess[bytes]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "OPENMONTAGE_PROJECTS_DIR": str(projects_root),
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-I", str(ENTRYPOINT)],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env=env,
        timeout=3,
        check=False,
    )


def test_stdio_emits_exactly_one_json_line_and_no_stderr(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "version": "1.0",
            "request_id": "stdio_1",
            "operation": "capabilities",
            "arguments": {},
        }
    ).encode()
    completed = _run(payload, tmp_path)
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n")
    assert completed.stdout.count(b"\n") == 1
    response = json.loads(completed.stdout)
    assert response["ok"] is True


def test_stdio_sanitizes_errors_to_one_line(tmp_path: Path) -> None:
    completed = _run(b'{"secret":"do-not-echo"}\n{}', tmp_path)
    assert completed.returncode != 0
    assert completed.stderr == b""
    assert completed.stdout.count(b"\n") == 1
    assert b"do-not-echo" not in completed.stdout
    response = json.loads(completed.stdout)
    assert response["ok"] is False
    assert response["error"]["code"] == "INVALID_JSON"
