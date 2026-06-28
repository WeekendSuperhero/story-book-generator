#!/usr/bin/env python3
"""Build a story-generation prompt from a brief and character references."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


READING_PRESETS: dict[int, dict[str, Any]] = {
    5: {
        "targetWordCount": 700,
        "pageCount": 16,
        "wordsPerPage": "35-55",
    },
    10: {
        "targetWordCount": 1350,
        "pageCount": 24,
        "wordsPerPage": "45-65",
    },
}


@dataclass(frozen=True)
class CharacterReference:
    character_id: str
    display_name: str
    description_path: Path
    description: str
    sheet_path: Path | None


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "character"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def find_description_file(character_dir: Path) -> Path | None:
    candidates = sorted(character_dir.glob("*_DESCRIPTION.md"))
    if candidates:
        return candidates[0]

    candidates = sorted(
        path
        for path in character_dir.glob("*.md")
        if path.name.lower().endswith("_description.md")
    )
    if candidates:
        return candidates[0]

    candidates = sorted(character_dir.glob("*.md"))
    return candidates[0] if candidates else None


def find_character_sheet(character_dir: Path) -> Path | None:
    extensions = {".jpg", ".jpeg", ".png", ".webp"}
    preferred = [
        path
        for path in character_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in extensions
        and "character_sheet" in path.name.lower()
    ]
    if preferred:
        return sorted(preferred)[0]

    images = [
        path
        for path in character_dir.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    ]
    return sorted(images)[0] if images else None


def discover_characters(characters_dir: Path) -> list[CharacterReference]:
    if not characters_dir.exists():
        raise FileNotFoundError(f"Character directory not found: {characters_dir}")

    references: list[CharacterReference] = []

    for character_dir in sorted(path for path in characters_dir.iterdir() if path.is_dir()):
        description_path = find_description_file(character_dir)
        if description_path is None:
            continue

        references.append(
            CharacterReference(
                character_id=slugify(character_dir.name),
                display_name=character_dir.name,
                description_path=description_path,
                description=read_text(description_path),
                sheet_path=find_character_sheet(character_dir),
            )
        )

    if not references:
        raise ValueError(f"No character description markdown files found in {characters_dir}")

    return references


def apply_reading_defaults(brief: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(brief)
    minutes = int(normalized.get("targetReadTimeMinutes", 5))
    if minutes not in READING_PRESETS:
        raise ValueError("targetReadTimeMinutes must be 5 or 10")

    preset = READING_PRESETS[minutes]
    normalized.setdefault("targetWordCount", preset["targetWordCount"])
    normalized.setdefault("pageCount", preset["pageCount"])
    normalized["wordsPerPage"] = preset["wordsPerPage"]
    return normalized


def selected_characters(
    references: list[CharacterReference], brief: dict[str, Any]
) -> list[CharacterReference]:
    selection = brief.get("characterSelection", {})
    include_ids = selection.get("includeCharacterIds", []) if isinstance(selection, dict) else []
    include_ids = {slugify(str(character_id)) for character_id in include_ids}

    if not include_ids:
        return references

    by_id = {reference.character_id: reference for reference in references}
    missing = sorted(include_ids - set(by_id))
    if missing:
        available = ", ".join(sorted(by_id))
        raise ValueError(f"Unknown character IDs: {', '.join(missing)}. Available: {available}")

    return [reference for reference in references if reference.character_id in include_ids]


def format_character_references(references: list[CharacterReference], root: Path) -> str:
    sections: list[str] = []

    index_lines = ["### Character Index", ""]
    for reference in references:
        sheet = relative_display_path(reference.sheet_path, root) if reference.sheet_path else ""
        index_lines.append(f"- `{reference.character_id}`: {reference.display_name}")
        index_lines.append(f"  - description: `{relative_display_path(reference.description_path, root)}`")
        index_lines.append(f"  - characterSheet: `{sheet}`")

    sections.append("\n".join(index_lines))

    for reference in references:
        sheet = relative_display_path(reference.sheet_path, root) if reference.sheet_path else ""
        sections.append(
            "\n".join(
                [
                    f"### Character: {reference.display_name}",
                    "",
                    f"- id: `{reference.character_id}`",
                    f"- referenceSheet: `{sheet}`",
                    "",
                    "```markdown",
                    reference.description,
                    "```",
                ]
            )
        )

    return "\n\n".join(sections)


def relative_display_path(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", type=Path, default=Path("brief.example.json"))
    parser.add_argument("--characters-dir", type=Path, default=Path("characters"))
    parser.add_argument("--template", type=Path, default=Path("prompts/story_generation_prompt.md"))
    parser.add_argument("--schema", type=Path, default=Path("schemas/story_output.schema.json"))
    parser.add_argument("--out", type=Path, default=Path("outputs/story_prompt.md"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path.cwd()

    brief = apply_reading_defaults(load_json(args.brief))
    references = selected_characters(discover_characters(args.characters_dir), brief)

    template = read_text(args.template)
    schema = json.loads(read_text(args.schema))
    schema_json = json.dumps(schema, indent=2, ensure_ascii=False)
    brief_json = json.dumps(brief, indent=2, ensure_ascii=False)

    rendered = render_template(
        template,
        {
            "BOOK_BRIEF_JSON": brief_json,
            "CHARACTER_REFERENCES": format_character_references(references, root),
            "OUTPUT_SCHEMA_JSON": schema_json,
        },
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered + "\n", encoding="utf-8")

    print(f"Wrote {args.out}")
    print(f"Characters included: {', '.join(reference.character_id for reference in references)}")


if __name__ == "__main__":
    main()

