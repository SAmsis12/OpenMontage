"""Profile-aware Remotion composition routing."""

from __future__ import annotations

import pytest

from tools.video.video_compose import VideoCompose


@pytest.mark.parametrize("profile", ["youtube_shorts", "instagram_reels", "tiktok"])
def test_explainer_portrait_profiles_use_vertical_composition(profile: str) -> None:
    assert VideoCompose._get_composition_id("explainer-data", profile) == "ExplainerVertical"


def test_explainer_landscape_profile_keeps_standard_composition() -> None:
    assert VideoCompose._get_composition_id("explainer-teacher", "youtube_landscape") == "Explainer"


@pytest.mark.parametrize(
    ("renderer_family", "expected"),
    [
        ("presenter", "TalkingHead"),
        ("cinematic-trailer", "CinematicRenderer"),
    ],
)
def test_portrait_profile_does_not_invent_vertical_non_explainer_composition(
    renderer_family: str, expected: str
) -> None:
    assert VideoCompose._get_composition_id(renderer_family, "tiktok") == expected


def test_unknown_profile_preserves_existing_composition_routing() -> None:
    assert VideoCompose._get_composition_id("explainer-data", "not-a-profile") == "Explainer"
