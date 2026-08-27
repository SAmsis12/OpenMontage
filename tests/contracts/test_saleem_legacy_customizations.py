"""Contracts for Saleem's preserved OpenMontage customizations."""

from __future__ import annotations

from pathlib import Path
import json

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


def test_arabic_font_is_bundled_loaded_and_used_by_text_surfaces() -> None:
    composer = REPO_ROOT / "remotion-composer"
    font = composer / "public/fonts/NotoSansArabic-Variable.woff2"
    license_file = composer / "public/fonts/OFL.txt"
    loader = (composer / "src/fonts.ts").read_text(encoding="utf-8")
    text_card = (composer / "src/components/TextCard.tsx").read_text(encoding="utf-8")
    explainer = (composer / "src/Explainer.tsx").read_text(encoding="utf-8")
    package = json.loads((composer / "package.json").read_text(encoding="utf-8"))

    assert font.is_file() and font.stat().st_size > 10_000
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_file.read_text(encoding="utf-8")
    assert package["dependencies"]["@remotion/fonts"] == "^4.0.484"
    assert 'staticFile("fonts/NotoSansArabic-Variable.woff2")' in loader
    assert "@remotion/fonts" in loader
    assert "unicodeRange:" in loader
    assert "ARABIC_FONT_STACK" in text_card
    assert "fontFamily={ARABIC_FONT_STACK}" in explainer
    assert "arabicFontStack(theme.headingFont || fontFamily)" in explainer
    assert "fonts.googleapis.com" not in loader
    assert "fonts.gstatic.com" not in loader


def test_every_arabic_capable_explainer_surface_uses_the_bundled_font_stack() -> None:
    """Representative scene, overlay, chart, and utility text surfaces are covered."""
    composer = REPO_ROOT / "remotion-composer/src"
    surfaces = (
        "components/TextCard.tsx",
        "components/StatCard.tsx",
        "components/CalloutBox.tsx",
        "components/ComparisonCard.tsx",
        "components/ProgressBar.tsx",
        "components/SectionTitle.tsx",
        "components/StatReveal.tsx",
        "components/HeroTitle.tsx",
        "components/ProviderChip.tsx",
        "components/CaptionOverlay.tsx",
        "components/TerminalScene.tsx",
        "components/ScreenshotScene.tsx",
        "components/charts/BarChart.tsx",
        "components/charts/LineChart.tsx",
        "components/charts/PieChart.tsx",
        "components/charts/KPIGrid.tsx",
    )

    for relative in surfaces:
        source = (composer / relative).read_text(encoding="utf-8")
        assert ("ARABIC_FONT" in source or "arabicFontStack" in source), (\
            f"{relative} bypasses the bundled Arabic font"\
        )


def test_hero_title_does_not_split_arabic_into_unjoinable_characters() -> None:
    source = (
        REPO_ROOT / "remotion-composer/src/components/HeroTitle.tsx"
    ).read_text(encoding="utf-8")
    assert "containsArabicScript(title) ? [title] : title.split(\"\")" in source


def test_elevenlabs_accepts_official_and_legacy_key_names(monkeypatch) -> None:
    tool = ElevenLabsTTS()

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("ELEVEN_API_KEY", "legacy-key")
    assert tool._get_api_key() == "legacy-key"

    monkeypatch.setenv("ELEVENLABS_API_KEY", "official-key")
    assert tool._get_api_key() == "official-key"
