#!/usr/bin/env python3
"""Build a character-sheet image-generation prompt from a character description.

This is the front of the pipeline: it turns a character description (authored
from `templates/CHARACTER_TEMPLATE.md`) plus the `comic-style` house style into
the exact prompt and reference-attachment list needed to generate that
character's reference sheet in any image model.

It does not call an image model. Run it, then feed each prompt (with the listed
reference pictures attached) into whatever image tool is available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from build_story_prompt import (
    find_description_file,
    read_text,
    relative_display_path,
    slugify,
)


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
SOURCE_SUBDIRS = ("source", "sources", "references", "refs")

DEFAULT_HOUSE_STYLE = (
    "Comic-book / graphic-novel illustration. Thick bold black ink outlines on "
    "clothing, objects, and backgrounds; thinner, softer outlines on the face and "
    "skin. Flat cel-shading with vibrant but slightly muted colors and soft "
    "airbrush-like gradients on skin with subtle rosy cheeks. Halftone dot textures "
    "and light cross-hatching for depth, used sparingly on the face. Hand-drawn, "
    "textured-paper feel. Slightly exaggerated, expressive, youthful, flawless faces. "
    "High detail, clean lines, no photorealism."
)
DEFAULT_NEGATIVE_PROMPT = (
    "Do not make the character photorealistic, anime-styled, or give them sharp "
    "angular features. Keep the face youthful, smooth, and flawless. Do not drift "
    "from the described hair, eye color, skin tone, outfit, or proportions. "
    "Teeth are natural, even, and closed together — do NOT draw gaps between teeth, "
    "gap teeth, or a diastema. Do not distort, elongate, or mismatch arm length or "
    "limb proportions. Keep the character's age and build accurate to the reference "
    "(no aging up or down), with age-appropriate proportions."
)


def looks_like_placeholder(text: str) -> bool:
    """A template field is unfilled if it is empty or still has [bracketed] hints."""
    stripped = text.strip()
    return not stripped or ("[" in stripped and "]" in stripped)


def parse_sections(markdown: str) -> dict[str, str]:
    """Split a description into {lowercased '## ' heading: body text}."""
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[3:].strip().lower()
            current_lines = []
        elif current_key is not None:
            current_lines.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()
    return sections


def section_body(sections: dict[str, str], *keywords: str) -> str:
    """Return the body of the first section whose heading contains all keywords."""
    for key, body in sections.items():
        if all(keyword in key for keyword in keywords):
            return body
    return ""


def extract_blockquote(text: str) -> str:
    """Join consecutive Markdown blockquote (`>`) lines into one string."""
    quote_lines = [
        line.lstrip(">").strip()
        for line in text.splitlines()
        if line.lstrip().startswith(">")
    ]
    return " ".join(line for line in quote_lines if line).strip().strip('"').strip()


def attribute_summary(sections: dict[str, str]) -> str:
    """Flatten the physical-attribute sections into a single descriptor line."""
    fragments: list[str] = []
    for keywords in (("core", "physical"), ("canonical", "outfit"), ("proportion",), ("expression",)):
        body = section_body(sections, *keywords)
        for line in body.splitlines():
            cleaned = line.strip().lstrip("*-").strip()
            if cleaned and not looks_like_placeholder(cleaned):
                fragments.append(cleaned)
    return " ".join(fragments)


def synthesize_sheet_instruction(name: str, sections: dict[str, str]) -> str:
    summary = attribute_summary(sections)
    base = (
        f"Create a clean character reference sheet for {name} showing a front view, "
        "side view, three-quarter view, full-body pose, a close-up face, and three "
        "expressions: joyful, curious, and surprised. Keep the character identical "
        "across every view."
    )
    return f"{base} Character details: {summary}" if summary else base


def find_reference_images(character_dir: Path) -> list[Path]:
    """User-provided source/reference photos (everything except the generated sheet)."""
    images: list[Path] = []
    for subdir_name in SOURCE_SUBDIRS:
        subdir = character_dir / subdir_name
        if subdir.is_dir():
            images.extend(
                sorted(
                    path
                    for path in subdir.iterdir()
                    if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
                )
            )
    for path in sorted(character_dir.iterdir()):
        if (
            path.is_file()
            and path.suffix.lower() in PHOTO_EXTENSIONS
            and "character_sheet" not in path.name.lower()
        ):
            images.append(path)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in images:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def find_existing_sheet(character_dir: Path) -> Path | None:
    candidates = sorted(
        path
        for path in character_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in PHOTO_EXTENSIONS
        and "character_sheet" in path.name.lower()
    )
    return candidates[0] if candidates else None


def load_house_style(skill_path: Path) -> str:
    """Pull the 'Defined House Style' bullets out of the comic-style SKILL.md."""
    if not skill_path.exists():
        return DEFAULT_HOUSE_STYLE

    text = skill_path.read_text(encoding="utf-8")
    sections = parse_sections(text)
    body = section_body(sections, "house style")
    fragments: list[str] = []
    for line in body.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("-"):
            cleaned = cleaned.lstrip("-").strip().replace("**", "")
            if cleaned:
                fragments.append(cleaned if cleaned.endswith(".") else cleaned + ".")
    return " ".join(fragments) if fragments else DEFAULT_HOUSE_STYLE


def build_sheet_prompt(
    name: str,
    sections: dict[str, str],
    house_style: str,
    references: list[Path],
    style_reference: Path | None,
) -> str:
    sheet_instruction = extract_blockquote(section_body(sections, "reference sheet"))
    if looks_like_placeholder(sheet_instruction):
        sheet_instruction = synthesize_sheet_instruction(name, sections)

    style = extract_blockquote(section_body(sections, "art style"))
    if looks_like_placeholder(style):
        style = house_style

    negative = extract_blockquote(section_body(sections, "negative prompt"))
    if looks_like_placeholder(negative):
        negative = DEFAULT_NEGATIVE_PROMPT

    if references:
        likeness = (
            f"Base {name}'s likeness on the attached reference photo(s): faithfully "
            "preserve their real facial features, hair, skin tone, and body "
            "proportions, then stylize them in the house style below."
        )
    else:
        likeness = (
            f"No source photo is attached, so design {name} from the written "
            "description, applying the house style below."
        )

    parts = [sheet_instruction, likeness, f"House style: {style}"]
    if style_reference is not None:
        parts.append(
            "Match the line work, coloring, shading, and texture of the attached "
            "style-reference illustration so this character matches the rest of the cast."
        )
    parts.extend(
        [
            "Lay the sheet out on a clean white background with neatly labeled views, "
            "consistent character identity across every view, and even, flattering lighting.",
            f"Negative prompt: {negative}",
            "High detail, clean lines, no photorealism.",
        ]
    )
    return "\n\n".join(part for part in parts if part)


def styles_summary(sections: dict[str, str]) -> str:
    """Flatten the wardrobe/preference sections of a STYLES doc into one line."""
    fragments: list[str] = []
    for keywords in (("signature", "outfit"), ("wardrobe",), ("colors",), ("accessories",), ("style", "preferences")):
        body = section_body(sections, *keywords)
        for line in body.splitlines():
            cleaned = line.strip().lstrip("*-").strip()
            if cleaned and not looks_like_placeholder(cleaned):
                fragments.append(cleaned)
    return " ".join(fragments)


def build_styles_prompt(
    name: str,
    sections: dict[str, str],
    house_style: str,
    references: list[Path],
    style_reference: Path | None,
) -> str:
    """Prompt for the Pass-2 styles/wardrobe sheet (built from a STYLES doc)."""
    instruction = extract_blockquote(section_body(sections, "styles sheet instruction"))
    if looks_like_placeholder(instruction):
        instruction = (
            f"Create a character style/wardrobe sheet for {name}: a clean, labeled layout of "
            "the signature outfit plus two or three outfit variations and key accessories on "
            "the same on-model character."
        )

    style = extract_blockquote(section_body(sections, "art style"))
    if looks_like_placeholder(style):
        style = house_style

    negative = extract_blockquote(section_body(sections, "avoid"))
    if looks_like_placeholder(negative):
        negative = DEFAULT_NEGATIVE_PROMPT

    parts = [instruction]
    summary = styles_summary(sections)
    if summary:
        parts.append("Wardrobe details: " + summary)
    parts.append(
        "Keep the character strictly on-model with the attached reference sheet — identical "
        "face, hair, skin tone, and proportions; only the clothing and styling vary."
    )
    if references:
        parts.append("Use the attached clothing reference images to ground the outfits and colors.")
    parts.append(f"House style: {style}")
    parts.append(
        "Lay the sheet out on a clean white background with each outfit neatly labeled and "
        "consistent character identity across every look."
    )
    parts.append(f"Negative prompt: {negative}")
    parts.append("High detail, clean lines, no photorealism.")
    return "\n\n".join(part for part in parts if part)


def character_markdown(
    name: str,
    character_id: str,
    description_path: Path,
    existing_sheet: Path | None,
    references: list[Path],
    style_reference: Path | None,
    prompt: str,
    root: Path,
) -> str:
    mode = "reference (photo-based)" if references else "house-style (text-only)"
    lines = [
        f"# {name} — Character Sheet Prompt",
        "",
        f"- id: `{character_id}`",
        f"- mode: {mode}",
        f"- description: `{relative_display_path(description_path, root)}`",
        f"- existing sheet: `{relative_display_path(existing_sheet, root) if existing_sheet else 'none'}`",
        "",
        "## Attach Reference Pictures",
        "",
    ]
    if references:
        lines.extend(f"- `{relative_display_path(path, root)}`" for path in references)
    else:
        lines.append("- None (generate from the written description using the house style)")
    if style_reference is not None:
        lines.append(f"- `{relative_display_path(style_reference, root)}` (style reference)")

    lines.extend(["", "## Reference Sheet Prompt", "", prompt, ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--characters-dir", type=Path, default=Path("characters"))
    parser.add_argument(
        "--character",
        action="append",
        dest="characters",
        help="Character name or id to include (repeatable). Defaults to all characters.",
    )
    parser.add_argument("--skill", type=Path, default=Path("skills/comic-style/SKILL.md"))
    parser.add_argument(
        "--style-reference",
        type=Path,
        help="Optional style-reference illustration attached to every prompt for cast consistency.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/character_prompts"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path.cwd()

    if not args.characters_dir.exists():
        raise FileNotFoundError(f"Character directory not found: {args.characters_dir}")

    house_style = load_house_style(args.skill)
    style_reference = args.style_reference
    if style_reference is not None and not style_reference.exists():
        raise FileNotFoundError(f"Style reference not found: {style_reference}")

    wanted = {slugify(value) for value in args.characters} if args.characters else None

    args.out_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, Any]] = []
    for character_dir in sorted(path for path in args.characters_dir.iterdir() if path.is_dir()):
        description_path = find_description_file(character_dir)
        if description_path is None:
            continue

        name = character_dir.name
        character_id = slugify(name)
        if wanted is not None and character_id not in wanted:
            continue

        sections = parse_sections(read_text(description_path))
        references = find_reference_images(character_dir)
        existing_sheet = find_existing_sheet(character_dir)
        prompt = build_sheet_prompt(name, sections, house_style, references, style_reference)

        output_path = args.out_dir / f"{character_id}.md"
        output_path.write_text(
            character_markdown(
                name,
                character_id,
                description_path,
                existing_sheet,
                references,
                style_reference,
                prompt,
                root,
            ),
            encoding="utf-8",
        )
        index.append(
            {
                "id": character_id,
                "name": name,
                "promptFile": str(output_path),
                "mode": "reference" if references else "house-style",
                "descriptionFile": relative_display_path(description_path, root),
                "existingSheet": relative_display_path(existing_sheet, root) if existing_sheet else None,
                "referenceImages": [relative_display_path(path, root) for path in references],
                "styleReference": relative_display_path(style_reference, root) if style_reference else None,
            }
        )

    if not index:
        raise ValueError(f"No character descriptions found in {args.characters_dir}")

    (args.out_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(index)} character sheet prompts to {args.out_dir}")
    for entry in index:
        attached = len(entry["referenceImages"])
        print(f"- {entry['id']}: {entry['mode']} ({attached} reference picture(s))")


if __name__ == "__main__":
    main()
