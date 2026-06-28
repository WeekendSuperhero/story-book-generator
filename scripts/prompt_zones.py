"""Shared prompt wording for live-text-safe illustration zones."""

from __future__ import annotations

from typing import Any


TEXT_ZONE_PRESETS: dict[str, dict[str, float]] = {
    "bottom": {"x": 8, "y": 76, "width": 84, "height": 20},
    "top": {"x": 8, "y": 3, "width": 84, "height": 20},
    "left": {"x": 3, "y": 18, "width": 30, "height": 64},
    "right": {"x": 67, "y": 18, "width": 30, "height": 64},
    "center": {"x": 18, "y": 34, "width": 64, "height": 32},
}

ZONE_DESCRIPTIONS = {
    "bottom": "the lower caption band from x=8% to x=92% and y=76% to y=96%",
    "top": "the upper caption band from x=8% to x=92% and y=3% to y=23%",
    "left": "the left vertical caption band from x=3% to x=33% and y=18% to y=82%",
    "right": "the right vertical caption band from x=67% to x=97% and y=18% to y=82%",
    "center": "the centered title-safe area from x=18% to x=82% and y=34% to y=66%",
}


def text_zone(page: dict[str, Any]) -> str:
    zone = str(page.get("textZone", "bottom")).lower()
    if zone not in TEXT_ZONE_PRESETS:
        return "bottom"
    return zone


def reserved_zone_instruction(page: dict[str, Any]) -> str:
    zone = text_zone(page)
    description = ZONE_DESCRIPTIONS[zone]
    return (
        f"Reserve {description} as a clean live-text safe area. Keep all faces, hands, "
        "bodies, important props, bright focal effects, logos, signs, readable marks, "
        "and story action completely outside that rectangle. The reserved area should "
        "be continuous and low-detail, using simple texture, smooth shadow, smooth sky, "
        "wall, floor, blanket, grass, or a single uninterrupted soft panel. If you add "
        "a visible panel or box, make it fill the safe area cleanly and do not place it "
        "partly behind characters or props."
    )
