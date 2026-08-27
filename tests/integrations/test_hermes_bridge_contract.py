from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from integrations.hermes_bridge.contract import (
    INPUT_LIMIT_BYTES,
    BridgeError,
    make_success,
    parse_request,
    process_request_bytes,
    validate_response,
)

ROOT = Path(__file__).resolve().parents[2]
REQUEST_SCHEMA = json.loads(
    (ROOT / "schemas/integrations/hermes_bridge_request.schema.json").read_text()
)
RESPONSE_SCHEMA = json.loads(
    (ROOT / "schemas/integrations/hermes_bridge_response.schema.json").read_text()
)


def request(operation: str = "capabilities", arguments: dict | None = None) -> dict:
    return {
        "version": "1.0",
        "request_id": "req_01",
        "operation": operation,
        "arguments": arguments or {},
    }


def test_request_schema_and_runtime_reject_extra_keys_at_every_depth() -> None:
    extra_top = {**request(), "surprise": True}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(extra_top, REQUEST_SCHEMA)
    with pytest.raises(BridgeError, match="INVALID_REQUEST"):
        parse_request(extra_top)

    extra_nested = request("status", {"project_id": "film", "surprise": True})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(extra_nested, REQUEST_SCHEMA)
    with pytest.raises(BridgeError, match="INVALID_REQUEST"):
        parse_request(extra_nested)


def test_input_limit_is_utf8_bytes_and_checked_before_json_parse() -> None:
    payload = ('{"version":"1.0","request_id":"' + "é" * INPUT_LIMIT_BYTES + '"}').encode()
    response = json.loads(process_request_bytes(payload, projects_root=Path("/unused")))
    assert response["ok"] is False
    assert response["error"]["code"] == "INPUT_TOO_LARGE"


def test_malformed_multiple_or_multiline_json_is_rejected() -> None:
    malformed = json.loads(process_request_bytes(b"{", projects_root=Path("/unused")))
    assert malformed["error"]["code"] == "INVALID_JSON"

    multiple = json.loads(
        process_request_bytes(b"{}\n{}\n", projects_root=Path("/unused"))
    )
    assert multiple["error"]["code"] == "INVALID_JSON"

    pretty_printed = json.loads(
        process_request_bytes(b'{\n"version":"1.0"\n}', projects_root=Path("/unused"))
    )
    assert pretty_printed["error"]["code"] == "INVALID_JSON"

    valid_line = json.dumps(request()).encode() + b"\n"
    assert json.loads(process_request_bytes(valid_line, projects_root=Path("/unused")))["ok"]


def test_success_receipt_is_deterministic_and_bound_to_request_and_result() -> None:
    parsed = parse_request(request())
    first = make_success(parsed, {"value": 1})
    second = make_success(parsed, {"value": 1})
    changed = make_success(parsed, {"value": 2})

    assert first == second
    assert first["receipt"]["digest"] != changed["receipt"]["digest"]
    canonical = json.dumps(
        {
            "version": "1.0",
            "request_id": "req_01",
            "operation": "capabilities",
            "arguments": {},
            "result": {"value": 1},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    assert first["receipt"]["digest"] == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_response_schema_rejects_impossible_receipt_and_runtime_matches_schema() -> None:
    from integrations.hermes_bridge.projector import capabilities_result

    response = make_success(parse_request(request()), capabilities_result())
    validate_response(response)
    jsonschema.validate(response, RESPONSE_SCHEMA)

    response["receipt"]["execution"] = "completed"
    with pytest.raises((BridgeError, jsonschema.ValidationError)):
        validate_response(response)
        jsonschema.validate(response, RESPONSE_SCHEMA)
