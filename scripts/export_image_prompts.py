#!/usr/bin/env python3
"""Export one image-generation prompt file per story page."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from prompt_zones import reserved_zone_instruction


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def page_prompt(book: dict[str, Any], page: dict[str, Any]) -> str:
    characters = page.get("charactersPresent", [])
    reference_sheets = [
        character["referenceSheet"]
        for character in characters
        if isinstance(character, dict) and character.get("referenceSheet")
    ]

    lines = [
        f"# Page {page['pageNumber']:03d} Image Prompt",
        "",
        f"Book: {book.get('title', '')}",
        f"Text zone: {page.get('textZone', '')}",
        "",
        "## Attach Character Reference Sheets",
        "",
    ]

    if reference_sheets:
        lines.extend(f"- {sheet}" for sheet in reference_sheets)
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Story Text For Context",
            "",
            page.get("storyText", ""),
            "",
            "## Characters Present",
            "",
        ]
    )

    if characters:
        for character in characters:
            lines.extend(
                [
                    f"- {character.get('name', '')} (`{character.get('id', '')}`)",
                    f"  - Expression: {character.get('expression', '')}",
                    f"  - Pose: {character.get('pose', '')}",
                    f"  - Action: {character.get('action', '')}",
                ]
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Prompt",
            "",
            page.get("illustrationPrompt", ""),
            "",
            "## Fixed Live-Text Safe Area",
            "",
            reserved_zone_instruction(page),
            "",
            "## Story-Specific Negative Space Requirement",
            "",
            page.get("negativeSpaceInstruction", ""),
            "",
            "## Negative Prompt",
            "",
            page.get("imageNegativePrompt", ""),
            "",
            "## Accessibility Alt Text",
            "",
            page.get("accessibilityAltText", ""),
            "",
        ]
    )

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story", type=Path, default=Path("outputs/story.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/image_prompts"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    story = load_json(args.story)
    book = story.get("book", {})
    pages = story.get("pages", [])

    if not isinstance(book, dict) or not isinstance(pages, list) or not pages:
        raise ValueError("Story JSON must contain book and pages")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page["pageNumber"])
        filename = f"page-{page_number:03d}.md"
        output_path = args.out_dir / filename
        output_path.write_text(page_prompt(book, page), encoding="utf-8")
        index.append(
            {
                "pageNumber": page_number,
                "promptFile": str(output_path),
                "textZone": page.get("textZone"),
                "referenceSheets": [
                    character.get("referenceSheet")
                    for character in page.get("charactersPresent", [])
                    if isinstance(character, dict) and character.get("referenceSheet")
                ],
            }
        )

    (args.out_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Exported {len(index)} image prompts to {args.out_dir}")


if __name__ == "__main__":
    main()
