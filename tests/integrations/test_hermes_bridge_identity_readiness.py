from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from integrations.hermes_bridge.contract import (
    BridgeError,
    handle_request,
    make_success,
    parse_request,
    validate_response,
)
from integrations.hermes_bridge.readers import EvidenceLimits, read_identity_readiness

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "integrations/hermes_bridge/__main__.py"
REQUEST_SCHEMA = json.loads(
    (ROOT / "schemas/integrations/hermes_bridge_request.schema.json").read_text()
)
RESPONSE_SCHEMA = json.loads(
    (ROOT / "schemas/integrations/hermes_bridge_response.schema.json").read_text()
)
DEFAULT = {
    "contract_version": "1.0",
    "foundation_status": "draft",
    "world_sample_approved": False,
    "character_world_sample_approved": False,
    "reviewer_approved": False,
    "bulk_generation": False,
}
READY = {
    "contract_version": "1.0",
    "foundation_status": "foundation_ready",
    "world_sample_approved": True,
    "character_world_sample_approved": True,
    "reviewer_approved": True,
    "bulk_generation": True,
}


def _request(arguments: object = None) -> dict:
    return {
        "version": "1.0",
        "request_id": "identity_1",
        "operation": "identity_readiness",
        "arguments": {"project_id": "film"} if arguments is None else arguments,
    }


def _write_evidence(root: Path, value: object) -> Path:
    project = root / "film"
    project.mkdir(parents=True, exist_ok=True)
    path = project / "identity_readiness.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _result(root: Path) -> dict:
    response = handle_request(parse_request(_request()), projects_root=root)
    assert response["ok"] is True
    return response["result"]


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


def test_identity_request_contract_is_exact_and_project_id_is_typed() -> None:
    parsed = parse_request(_request())
    assert parsed.operation == "identity_readiness"
    jsonschema.validate(_request(), REQUEST_SCHEMA)

    for arguments in (
        {},
        {"project_id": "film", "extra": False},
        {"project_id": 7},
        {"project_id": True},
        {"project_id": None},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(_request(arguments), REQUEST_SCHEMA)
        with pytest.raises(BridgeError):
            parse_request(_request(arguments))


def test_identity_result_contract_rejects_unknown_keys_types_enums_and_bulk_contradictions() -> None:
    request = parse_request(_request())
    valid = make_success(request, READY)
    validate_response(valid)
    jsonschema.validate(valid, RESPONSE_SCHEMA)

    hostile_results = [
        {**READY, "path": "/private/project"},
        {**READY, "contract_version": "2.0"},
        {**READY, "foundation_status": "approved"},
        {**READY, "world_sample_approved": 1},
        {**READY, "character_world_sample_approved": "true"},
        {**READY, "reviewer_approved": None},
        {**READY, "bulk_generation": False},
        {**DEFAULT, "bulk_generation": True},
    ]
    for result in hostile_results:
        response = make_success(request, result)
        with pytest.raises((BridgeError, jsonschema.ValidationError)):
            validate_response(response)
            jsonschema.validate(response, RESPONSE_SCHEMA)


@pytest.mark.parametrize(
    "foundation_status",
    ["draft", "review_pending", "foundation_ready", "rejected"],
)
def test_identity_projection_accepts_closed_foundation_states(
    tmp_path: Path, foundation_status: str
) -> None:
    expected = {
        **DEFAULT,
        "foundation_status": foundation_status,
    }
    _write_evidence(tmp_path, expected)
    assert _result(tmp_path) == expected


def test_identity_missing_project_or_evidence_is_conservative_default(tmp_path: Path) -> None:
    assert _result(tmp_path) == DEFAULT
    (tmp_path / "film").mkdir()
    assert _result(tmp_path) == DEFAULT


def test_identity_ready_evidence_enables_bulk_and_receipt_is_deterministic(tmp_path: Path) -> None:
    _write_evidence(tmp_path, READY)
    request = parse_request(_request())
    first = handle_request(request, projects_root=tmp_path)
    second = handle_request(request, projects_root=tmp_path)
    assert first == second
    assert first["result"] == READY
    assert set(first["result"]) == set(DEFAULT)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: {**value, "unknown": False},
        lambda value: {**value, "contract_version": 1},
        lambda value: {**value, "foundation_status": "ready"},
        lambda value: {**value, "foundation_status": ["foundation_ready"]},
        lambda value: {**value, "world_sample_approved": 1},
        lambda value: {**value, "character_world_sample_approved": "yes"},
        lambda value: {**value, "reviewer_approved": None},
        lambda value: {**value, "bulk_generation": False},
    ],
)
def test_identity_malformed_or_contradictory_evidence_fails_closed(
    tmp_path: Path, mutate
) -> None:
    _write_evidence(tmp_path, mutate(READY))
    with pytest.raises(BridgeError, match="EVIDENCE_INVALID"):
        _result(tmp_path)


def test_identity_reader_rejects_symlink_nonregular_oversize_and_malformed_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "film"
    project.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(READY), encoding="utf-8")
    evidence = project / "identity_readiness.json"

    evidence.symlink_to(outside)
    with pytest.raises(BridgeError, match="PROJECT_UNREADABLE"):
        read_identity_readiness(tmp_path, "film")
    evidence.unlink()

    evidence.mkdir()
    with pytest.raises(BridgeError, match="PROJECT_UNREADABLE"):
        read_identity_readiness(tmp_path, "film")
    evidence.rmdir()

    evidence.write_bytes(b" " * 65)
    with pytest.raises(BridgeError, match="EVIDENCE_TOO_LARGE"):
        read_identity_readiness(
            tmp_path,
            "film",
            limits=EvidenceLimits(file_bytes=64, aggregate_bytes=64),
        )

    evidence.write_text("{", encoding="utf-8")
    with pytest.raises(BridgeError, match="EVIDENCE_INVALID"):
        read_identity_readiness(tmp_path, "film")


def test_identity_stdio_rejects_multiline_and_never_changes_project_tree(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path, READY)
    before = evidence.read_bytes()

    valid = _run(json.dumps(_request()).encode(), tmp_path)
    assert valid.returncode == 0
    assert valid.stderr == b""
    assert valid.stdout.count(b"\n") == 1
    assert json.loads(valid.stdout)["result"] == READY
    assert evidence.read_bytes() == before

    multiline = _run(json.dumps(_request(), indent=2).encode(), tmp_path)
    assert multiline.returncode != 0
    assert multiline.stderr == b""
    assert multiline.stdout.count(b"\n") == 1
    response = json.loads(multiline.stdout)
    assert response["error"]["code"] == "INVALID_JSON"
    assert evidence.read_bytes() == before
