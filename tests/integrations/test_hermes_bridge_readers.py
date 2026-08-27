from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from integrations.hermes_bridge.contract import BridgeError
from integrations.hermes_bridge.readers import EvidenceLimits, read_project


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _marker(project: Path, project_id: str = "film", pipeline: str = "cinematic") -> None:
    project.mkdir(parents=True)
    _write(
        project / "project.json",
        {"version": "1.0", "project_id": project_id, "pipeline_type": pipeline},
    )


@pytest.mark.parametrize(
    "project_id",
    ["../film", "film/other", "film\\other", "/tmp/film", ".", "-film", "film-"],
)
def test_project_id_rejects_traversal_and_path_forms(tmp_path: Path, project_id: str) -> None:
    with pytest.raises(BridgeError, match="INVALID_PROJECT_ID"):
        read_project(tmp_path, project_id)


def test_project_directory_and_files_must_not_be_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _marker(outside)
    os.symlink(outside, tmp_path / "film")
    with pytest.raises(BridgeError, match="PROJECT_UNREADABLE"):
        read_project(tmp_path, "film")

    (tmp_path / "film").unlink()
    project = tmp_path / "film"
    project.mkdir()
    os.symlink(outside / "project.json", project / "project.json")
    with pytest.raises(BridgeError, match="PROJECT_UNREADABLE"):
        read_project(tmp_path, "film")


def test_reader_rejects_oversized_and_malformed_evidence(tmp_path: Path) -> None:
    project = tmp_path / "film"
    _marker(project)
    limits = EvidenceLimits(file_bytes=256, aggregate_bytes=1024, max_checkpoints=64)

    for name in ("project.json", "checkpoint_research.json", "cost_log.json"):
        original = (project / "project.json").read_bytes()
        target = project / name
        target.write_bytes(b" " * 257)
        with pytest.raises(BridgeError, match="EVIDENCE_TOO_LARGE"):
            read_project(tmp_path, "film", limits=limits)
        target.unlink()
        (project / "project.json").write_bytes(original)

    for name in ("project.json", "checkpoint_research.json", "cost_log.json"):
        original = (project / "project.json").read_bytes()
        target = project / name
        target.write_text("{", encoding="utf-8")
        with pytest.raises(BridgeError, match="EVIDENCE_INVALID"):
            read_project(tmp_path, "film", limits=limits)
        target.unlink()
        (project / "project.json").write_bytes(original)


def test_reader_bounds_checkpoint_count_and_aggregate_bytes(tmp_path: Path) -> None:
    project = tmp_path / "film"
    _marker(project)
    for index in range(3):
        _write(
            project / f"checkpoint_stage{index}.json",
            {"project_id": "film", "pipeline_type": "cinematic", "stage": f"stage{index}"},
        )
    with pytest.raises(BridgeError, match="EVIDENCE_TOO_LARGE"):
        read_project(
            tmp_path,
            "film",
            limits=EvidenceLimits(file_bytes=256, aggregate_bytes=1024, max_checkpoints=2),
        )

    with pytest.raises(BridgeError, match="EVIDENCE_TOO_LARGE"):
        read_project(
            tmp_path,
            "film",
            limits=EvidenceLimits(file_bytes=256, aggregate_bytes=100, max_checkpoints=64),
        )


def test_checkpoint_identity_mismatch_is_degraded_not_trusted(tmp_path: Path) -> None:
    project = tmp_path / "film"
    _marker(project)
    _write(
        project / "checkpoint_research.json",
        {
            "project_id": "other",
            "pipeline_type": "cinematic",
            "stage": "research",
            "status": "completed",
            "human_approved": False,
        },
    )
    evidence = read_project(tmp_path, "film")
    assert evidence.degraded is True
    assert evidence.checkpoints == {}
