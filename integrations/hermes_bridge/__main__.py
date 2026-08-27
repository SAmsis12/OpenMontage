#!/usr/bin/env python3
"""One-request/one-response stdio entrypoint for the Hermes bridge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.hermes_bridge.contract import INPUT_LIMIT_BYTES, process_request_bytes  # noqa: E402


def main() -> int:
    projects_root = Path(
        os.environ.get("OPENMONTAGE_PROJECTS_DIR", str(REPO_ROOT / "projects"))
    )
    payload = sys.stdin.buffer.read(INPUT_LIMIT_BYTES + 1)
    output = process_request_bytes(payload, projects_root=projects_root)
    sys.stdout.buffer.write(output + b"\n")
    sys.stdout.buffer.flush()
    try:
        return 0 if json.loads(output).get("ok") is True else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
