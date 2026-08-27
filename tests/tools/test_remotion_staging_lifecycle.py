"""Lifecycle guarantees for the Remotion render-scoped public dir.

Two defects this locks down:

1. A public dir named only after the output stem is shared by every render of
   that output — concurrent renders overwrite each other's staged media, the
   first to finish deletes the other's inputs, and cleanup can erase a
   pre-existing directory that happens to match the name.
2. Staging that runs *before* the try/finally leaves staged user media on disk
   when setup fails after the directory was created but before the render.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose


def _mp4(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return path


def _inputs(tmp_path: Path, source: Path) -> dict:
    return {
        "output_path": str(tmp_path / "renders" / "final.mp4"),
        "composition_data": {
            "renderer_family": "explainer-data",
            "cuts": [
                {"id": "c1", "source": str(source), "in_seconds": 0, "out_seconds": 2}
            ],
        },
    }


@pytest.fixture
def render(monkeypatch):
    """Drive _remotion_render with the Remotion CLI stubbed out.

    Returns (tool, calls) where calls collects each argv the render would run.
    """
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    tool = VideoCompose()
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

    monkeypatch.setattr(tool, "run_command", fake_run)
    return tool, calls


def _public_dir_arg(argv: list[str]) -> str | None:
    for arg in argv:
        if arg.startswith("--public-dir="):
            return arg.split("=", 1)[1]
    return None


def _staging_dirs(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "renders").glob(".remotion-public-*"))


def test_public_dir_is_unique_per_render(tmp_path, render):
    """Two renders of the same output must not share a staging dir."""
    tool, calls = render
    source = _mp4(tmp_path / "assets" / "clip.mp4")

    tool._remotion_render(_inputs(tmp_path, source))
    tool._remotion_render(_inputs(tmp_path, source))

    dirs = [_public_dir_arg(c) for c in calls]
    assert len(calls) == 2
    assert all(d is not None for d in dirs), "staged media but no --public-dir passed"
    assert dirs[0] != dirs[1], f"concurrent renders would collide on {dirs[0]}"


def test_staged_media_cleaned_up_after_render(tmp_path, render):
    tool, _ = render
    source = _mp4(tmp_path / "assets" / "clip.mp4")

    tool._remotion_render(_inputs(tmp_path, source))

    assert _staging_dirs(tmp_path) == []
    assert source.is_file(), "cleanup must not touch the original media"


def test_pre_render_failure_still_cleans_staged_media(tmp_path, monkeypatch, render):
    """The regression: failure *after* staging but *before* the render.

    Staging created the directory and copied user media into it; if the guard
    only wraps run_command, that media is left behind.
    """
    tool, _ = render
    source = _mp4(tmp_path / "assets" / "clip.mp4")

    real_open = open

    def exploding_open(file, mode="r", *args, **kwargs):
        if str(file).endswith(".remotion_props.json") and "w" in mode:
            raise OSError("disk full")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", exploding_open)

    result = tool._remotion_render(_inputs(tmp_path, source))

    assert not result.success
    assert _staging_dirs(tmp_path) == [], "staged user media leaked after setup failure"


def test_cleanup_leaves_preexisting_dirs_alone(tmp_path, render):
    """A dir matching the old stem-only name is not ours to delete."""
    tool, _ = render
    source = _mp4(tmp_path / "assets" / "clip.mp4")
    squatter = tmp_path / "renders" / ".remotion-public-final"
    squatter.mkdir(parents=True)
    (squatter / "keep.txt").write_text("not ours")

    tool._remotion_render(_inputs(tmp_path, source))

    assert (squatter / "keep.txt").read_text() == "not ours"


@pytest.mark.parametrize("render_raises", [False, True])
def test_caller_public_dir_is_overlaid_with_bundled_assets_without_mutation(
    tmp_path, monkeypatch, render_raises
):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    caller_public = tmp_path / "caller-public"
    (caller_public / "fonts").mkdir(parents=True)
    (caller_public / "caller.txt").write_text("caller asset")
    (caller_public / "fonts" / "OFL.txt").write_text("hostile caller license")
    (caller_public / "fonts" / "PROVENANCE.md").write_text("hostile caller provenance")
    (caller_public / "fonts" / "NotoSansArabic-Variable.woff2").write_bytes(
        b"hostile caller font"
    )
    before = {
        path.relative_to(caller_public): path.read_bytes()
        for path in caller_public.rglob("*")
        if path.is_file()
    }
    observed: dict[str, object] = {}
    tool = VideoCompose()

    def fake_run(cmd, **kwargs):
        overlay = Path(_public_dir_arg(cmd) or "")
        observed["overlay"] = overlay
        observed["caller"] = (overlay / "caller.txt").read_text()
        bundled_fonts = Path(__file__).resolve().parents[2] / "remotion-composer/public/fonts"
        observed["reserved"] = {
            name: (overlay / "fonts" / name).read_bytes()
            for name in ("OFL.txt", "PROVENANCE.md", "NotoSansArabic-Variable.woff2")
        }
        observed["expected_reserved"] = {
            name: (bundled_fonts / name).read_bytes()
            for name in ("OFL.txt", "PROVENANCE.md", "NotoSansArabic-Variable.woff2")
        }
        if render_raises:
            raise RuntimeError("render failed")

    monkeypatch.setattr(tool, "run_command", fake_run)
    result = tool._remotion_render(
        {
            "output_path": str(tmp_path / "renders" / "final.mp4"),
            "public_dir": str(caller_public),
            "composition_data": {"renderer_family": "explainer-data", "cuts": []},
        }
    )

    assert not result.success
    assert observed["caller"] == "caller asset"
    assert observed["reserved"] == observed["expected_reserved"]
    overlay = observed["overlay"]
    assert isinstance(overlay, Path)
    assert not overlay.exists()
    after = {
        path.relative_to(caller_public): path.read_bytes()
        for path in caller_public.rglob("*")
        if path.is_file()
    }
    assert after == before
