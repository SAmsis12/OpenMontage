from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

PROTOCOL_VERSION = "1.0"
INPUT_LIMIT_BYTES = 65_536
OUTPUT_LIMIT_BYTES = 131_072
OPERATIONS = (
    "capabilities",
    "status",
    "preview",
    "cost",
    "approval",
    "identity_readiness",
)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
PROJECT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas" / "integrations"


class BridgeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Request:
    version: str
    request_id: str
    operation: str
    arguments: dict[str, Any]


def _exact(value: object, keys: set[str], required: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys and set(value) != (required or keys):
        raise BridgeError("INVALID_REQUEST")
    if required is not None and not required.issubset(value):
        raise BridgeError("INVALID_REQUEST")
    return value


def _project_id(arguments: dict[str, Any]) -> None:
    value = arguments.get("project_id")
    if not isinstance(value, str) or not PROJECT_ID_RE.fullmatch(value):
        raise BridgeError("INVALID_PROJECT_ID")


def _bounded_name(value: object) -> bool:
    return isinstance(value, str) and bool(NAME_RE.fullmatch(value))


def _money(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1_000_000
    )


def _validate_arguments(operation: str, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BridgeError("INVALID_REQUEST")
    if operation == "capabilities":
        return _exact(value, set())
    if operation in {"status", "identity_readiness"}:
        arguments = _exact(value, {"project_id"})
        _project_id(arguments)
        return arguments
    if operation == "approval":
        arguments = _exact(value, {"project_id", "stage"})
        _project_id(arguments)
        if not _bounded_name(arguments["stage"]):
            raise BridgeError("INVALID_REQUEST")
        return arguments
    if operation == "cost":
        keys = set(value)
        if keys not in ({"project_id"}, {"project_id", "proposed_cost_usd", "tool"}):
            raise BridgeError("INVALID_REQUEST")
        arguments = value
        _project_id(arguments)
        if "proposed_cost_usd" in arguments and (
            not _money(arguments["proposed_cost_usd"]) or not _bounded_name(arguments["tool"])
        ):
            raise BridgeError("INVALID_REQUEST")
        return arguments
    if operation == "preview":
        arguments = _exact(
            value,
            {"project_id", "pipeline_type", "target_stage", "proposed_operations"},
        )
        _project_id(arguments)
        if not _bounded_name(arguments["pipeline_type"]):
            raise BridgeError("UNKNOWN_PIPELINE")
        target = arguments["target_stage"]
        if target is not None and not _bounded_name(target):
            raise BridgeError("INVALID_REQUEST")
        proposed = arguments["proposed_operations"]
        if not isinstance(proposed, list) or len(proposed) > 64:
            raise BridgeError("INVALID_REQUEST")
        for item in proposed:
            item = _exact(item, {"tool", "operation", "estimated_usd"})
            if (
                not _bounded_name(item["tool"])
                or not _bounded_name(item["operation"])
                or not _money(item["estimated_usd"])
            ):
                raise BridgeError("INVALID_REQUEST")
        return arguments
    raise BridgeError("INVALID_REQUEST")


def parse_request(value: object) -> Request:
    envelope = _exact(value, {"version", "request_id", "operation", "arguments"})
    if envelope["version"] != PROTOCOL_VERSION:
        raise BridgeError("INVALID_REQUEST")
    request_id = envelope["request_id"]
    operation = envelope["operation"]
    if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
        raise BridgeError("INVALID_REQUEST")
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise BridgeError("INVALID_REQUEST")
    arguments = _validate_arguments(operation, envelope["arguments"])
    return Request(PROTOCOL_VERSION, request_id, operation, arguments)


def _receipt_payload(request: Request, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": request.version,
        "request_id": request.request_id,
        "operation": request.operation,
        "arguments": request.arguments,
        "result": result,
    }


def make_success(request: Request, result: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(
        _receipt_payload(request, result),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {
        "version": PROTOCOL_VERSION,
        "request_id": request.request_id,
        "operation": request.operation,
        "ok": True,
        "result": result,
        "receipt": {
            "kind": "dry_run",
            "digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
            "persisted": False,
            "execution": "not_started",
            "approval_granted": False,
        },
    }


def make_error(
    code: str, *, request_id: str = "unknown", operation: str = "unknown"
) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "operation": operation,
        "ok": False,
        "error": {"code": code, "message": "request rejected", "retryable": False},
    }


def _response_schema() -> dict[str, Any]:
    path = SCHEMA_ROOT / "hermes_bridge_response.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise BridgeError("INTERNAL_FAILURE") from None


def validate_response(response: dict[str, Any]) -> None:
    try:
        jsonschema.validate(response, _response_schema())
    except jsonschema.ValidationError:
        raise BridgeError("INTERNAL_FAILURE") from None


def handle_request(request: Request, *, projects_root: Path) -> dict[str, Any]:
    from .projector import (
        approval_result,
        capabilities_result,
        cost_result,
        identity_readiness_result,
        preview_result,
        status_result,
    )

    if request.operation == "capabilities":
        result = capabilities_result()
    elif request.operation == "status":
        result = status_result(projects_root, request.arguments["project_id"])
    elif request.operation == "preview":
        result = preview_result(projects_root, request.arguments)
    elif request.operation == "cost":
        result = cost_result(projects_root, request.arguments)
    elif request.operation == "approval":
        result = approval_result(projects_root, request.arguments)
    elif request.operation == "identity_readiness":
        result = identity_readiness_result(
            projects_root, request.arguments["project_id"]
        )
    else:  # pragma: no cover - parse_request closes this branch
        raise BridgeError("INVALID_REQUEST")
    response = make_success(request, result)
    validate_response(response)
    return response


def _serialize(response: dict[str, Any]) -> bytes:
    try:
        output = json.dumps(
            response,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        output = json.dumps(make_error("INTERNAL_FAILURE"), separators=(",", ":")).encode()
    if len(output) > OUTPUT_LIMIT_BYTES:
        output = json.dumps(make_error("INTERNAL_FAILURE"), separators=(",", ":")).encode()
    return output


def process_request_bytes(payload: bytes, *, projects_root: Path) -> bytes:
    if len(payload) > INPUT_LIMIT_BYTES:
        return _serialize(make_error("INPUT_TOO_LARGE"))
    if payload.endswith(b"\r\n"):
        json_bytes = payload[:-2]
    elif payload.endswith((b"\n", b"\r")):
        json_bytes = payload[:-1]
    else:
        json_bytes = payload
    if b"\n" in json_bytes or b"\r" in json_bytes:
        return _serialize(make_error("INVALID_JSON"))
    request: Request | None = None
    try:
        value = json.loads(json_bytes.decode("utf-8"))
        request = parse_request(value)
        response = handle_request(request, projects_root=projects_root)
    except (UnicodeDecodeError, json.JSONDecodeError):
        response = make_error("INVALID_JSON")
    except BridgeError as exc:
        response = make_error(
            exc.code,
            request_id=request.request_id if request else "unknown",
            operation=request.operation if request else "unknown",
        )
    except Exception:
        response = make_error(
            "INTERNAL_FAILURE",
            request_id=request.request_id if request else "unknown",
            operation=request.operation if request else "unknown",
        )
    return _serialize(response)
