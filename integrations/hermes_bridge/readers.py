from __future__ import annotations

import errno
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contract import BridgeError

PROJECT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
CHECKPOINT_RE = re.compile(r"^checkpoint_([A-Za-z0-9_-]{1,96})\.json$")


@dataclass(frozen=True)
class EvidenceLimits:
    file_bytes: int = 256 * 1024
    aggregate_bytes: int = 1024 * 1024
    max_checkpoints: int = 64


@dataclass
class ProjectEvidence:
    project_id: str
    exists: bool = False
    initialized: bool = False
    pipeline_type: str | None = None
    marker: dict[str, Any] | None = None
    checkpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
    cost_log: dict[str, Any] | None = None
    degraded: bool = False


def validate_project_id(project_id: str) -> None:
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise BridgeError("INVALID_PROJECT_ID")


def _flags(*, directory: bool = False) -> int:
    value = os.O_RDONLY
    value |= getattr(os, "O_CLOEXEC", 0)
    value |= getattr(os, "O_NOFOLLOW", 0)
    if directory:
        value |= getattr(os, "O_DIRECTORY", 0)
    return value


def _public_open_error(exc: OSError) -> BridgeError:
    if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
        return BridgeError("PROJECT_UNREADABLE")
    return BridgeError("PROJECT_UNREADABLE")


def _read_json_at(
    directory_fd: int,
    name: str,
    *,
    limits: EvidenceLimits,
    aggregate: list[int],
    required: bool = False,
) -> dict[str, Any] | None:
    try:
        fd = os.open(name, _flags(), dir_fd=directory_fd)
    except FileNotFoundError:
        if required:
            raise BridgeError("PROJECT_UNREADABLE")
        return None
    except OSError as exc:
        raise _public_open_error(exc) from None

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise BridgeError("PROJECT_UNREADABLE")
        if info.st_size > limits.file_bytes:
            raise BridgeError("EVIDENCE_TOO_LARGE")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, min(65536, limits.file_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limits.file_bytes:
                raise BridgeError("EVIDENCE_TOO_LARGE")
        aggregate[0] += size
        if aggregate[0] > limits.aggregate_bytes:
            raise BridgeError("EVIDENCE_TOO_LARGE")
    finally:
        os.close(fd)

    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BridgeError("EVIDENCE_INVALID") from None
    if not isinstance(value, dict):
        raise BridgeError("EVIDENCE_INVALID")
    return value


def read_project(
    projects_root: Path,
    project_id: str,
    *,
    limits: EvidenceLimits | None = None,
) -> ProjectEvidence:
    """Read only fixed, direct-child JSON evidence without following symlinks."""
    validate_project_id(project_id)
    limits = limits or EvidenceLimits()
    evidence = ProjectEvidence(project_id=project_id)

    try:
        root_fd = os.open(os.fspath(projects_root), _flags(directory=True))
    except FileNotFoundError:
        return evidence
    except OSError as exc:
        raise _public_open_error(exc) from None

    try:
        try:
            project_fd = os.open(project_id, _flags(directory=True), dir_fd=root_fd)
        except FileNotFoundError:
            return evidence
        except OSError as exc:
            raise _public_open_error(exc) from None
    finally:
        os.close(root_fd)

    evidence.exists = True
    aggregate = [0]
    try:
        marker = _read_json_at(
            project_fd, "project.json", limits=limits, aggregate=aggregate
        )
        if marker is not None:
            if (
                marker.get("project_id") != project_id
                or not isinstance(marker.get("pipeline_type"), str)
            ):
                raise BridgeError("EVIDENCE_INVALID")
            evidence.initialized = True
            evidence.marker = marker
            evidence.pipeline_type = marker["pipeline_type"]

        try:
            names = os.listdir(project_fd)
        except OSError:
            raise BridgeError("PROJECT_UNREADABLE") from None
        checkpoint_names = sorted(name for name in names if CHECKPOINT_RE.fullmatch(name))
        if len(checkpoint_names) > limits.max_checkpoints:
            raise BridgeError("EVIDENCE_TOO_LARGE")

        for name in checkpoint_names:
            checkpoint = _read_json_at(
                project_fd, name, limits=limits, aggregate=aggregate, required=True
            )
            assert checkpoint is not None
            filename_stage = CHECKPOINT_RE.fullmatch(name).group(1)  # type: ignore[union-attr]
            stage = checkpoint.get("stage")
            if (
                not evidence.initialized
                or checkpoint.get("project_id") != project_id
                or checkpoint.get("pipeline_type") != evidence.pipeline_type
                or stage != filename_stage
                or not isinstance(stage, str)
            ):
                evidence.degraded = True
                continue
            evidence.checkpoints[stage] = checkpoint

        evidence.cost_log = _read_json_at(
            project_fd, "cost_log.json", limits=limits, aggregate=aggregate
        )
    finally:
        os.close(project_fd)
    return evidence
