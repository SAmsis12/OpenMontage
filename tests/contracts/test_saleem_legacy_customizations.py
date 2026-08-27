"""Contracts for Saleem's preserved OpenMontage customizations."""

from __future__ import annotations

from pathlib import Path

from tools.audio.elevenlabs_tts import ElevenLabsTTS


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_vertical_explainer_composition_is_registered() -> None:
    source = (REPO_ROOT / "remotion-composer/src/Root.tsx").read_text(encoding="utf-8")
    assert 'id="ExplainerVertical"' in source
    block = source[source.index('id="ExplainerVertical"') :]
    block = block[: block.index("/>")]
    assert "width={1080}" in block
    assert "height={1920}" in block
    assert "calculateMetadata={calculateMetadata}" in block


def test_text_card_supports_project_style_and_rtl_overrides() -> None:
    component = (REPO_ROOT / "remotion-composer/src/components/TextCard.tsx").read_text(
        encoding="utf-8"
    )
    explainer = (REPO_ROOT / "remotion-composer/src/Explainer.tsx").read_text(
        encoding="utf-8"
    )

    for prop in (
        "cardBackgroundColor",
        "cardBorder",
        "cardShadow",
        "textShadow",
        "textDirection",
    ):
        assert prop in component
        assert f"{prop}={{cut.{prop}}}" in explainer

    assert 'textDirection = "auto"' in component
    assert "dir={textDirection}" in component
    assert 'unicodeBidi: "plaintext"' in component
    assert 'whiteSpace: "pre-line"' in component


def test_elevenlabs_accepts_official_and_legacy_key_names(monkeypatch) -> None:
    tool = ElevenLabsTTS()

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("ELEVEN_API_KEY", "legacy-key")
    assert tool._get_api_key() == "legacy-key"

    monkeypatch.setenv("ELEVENLABS_API_KEY", "official-key")
    assert tool._get_api_key() == "official-key"
