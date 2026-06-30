#!/usr/bin/env python3
"""Generate a character's reference sheet or styles sheet using Gemini.

Two-pass character creation:
  - --kind reference (default): reads the reference/ portrait cut-outs (or source/)
    + <NAME>_DESCRIPTION.md -> <id>_character_sheet.<ext>.
  - --kind styles: reads the styles/ clothing cut-outs + the reference sheet (as a
    style anchor) + <NAME>_STYLES.md -> <id>_styles_sheet.<ext>.

Mirrors generate_page_images.py: REST-based (no SDK), dry-run by default, --send to
call the image API. The matching template (CHARACTER_TEMPLATE / STYLES_TEMPLATE) and
the comic-style skill go in the request's system_instruction; the description + cut-out
images go in the input. Requests set thinking_level=high and store=false.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from build_character_sheet_prompt import (
    PHOTO_EXTENSIONS,
    build_sheet_prompt,
    build_styles_prompt,
    find_reference_images,
    load_house_style,
    parse_sections,
)
from build_story_prompt import find_description_file, read_text, slugify
from generate_page_images import (
    DEFAULT_MIME_TYPE,
    DEFAULT_MODEL,
    DEFAULT_PRO_MODEL,
    encode_image,
    extension_for_mime_type,
    image_blocks,
    post_json,
    redact_image_data,
    write_dry_run_request,
)


def styles_description_file(character_dir: Path) -> Path | None:
    candidates = sorted(character_dir.glob("*_STYLES.md"))
    return candidates[0] if candidates else None


def images_in_subdir(character_dir: Path, subdir: str) -> list[Path]:
    directory = character_dir / subdir
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS)


def selected_character_dirs(characters_dir: Path, wanted: set[str] | None, kind: str) -> list[Path]:
    finder = styles_description_file if kind == "styles" else find_description_file
    dirs: list[Path] = []
    for character_dir in sorted(path for path in characters_dir.iterdir() if path.is_dir()):
        if finder(character_dir) is None:
            continue
        if wanted is not None and slugify(character_dir.name) not in wanted:
            continue
        dirs.append(character_dir)
    return dirs


def build_request(
    prompt: str,
    references: list[Path],
    style_reference: Path | None,
    model: str,
    aspect_ratio: str,
    image_size: str,
    mime_type: str,
    thinking_level: str,
    system_instruction: str,
    store: bool,
) -> dict[str, Any]:
    input_blocks: list[dict[str, str]] = [{"type": "text", "text": prompt}]
    for path in references:
        input_blocks.append(encode_image(path))
    if style_reference is not None:
        input_blocks.append(encode_image(style_reference))

    payload: dict[str, Any] = {
        "model": model,
        "input": input_blocks,
        # Opt out of server-side retention; requests carry personal photos.
        "store": store,
        # Let the reasoning-driven image model plan the composition before rendering.
        "generation_config": {"thinking_level": thinking_level},
        "response_format": {
            "type": "image",
            "mime_type": mime_type,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
        },
    }
    if system_instruction:
        payload["system_instruction"] = system_instruction
    return payload


def compose_system_instruction(template_text: str, skill_text: str, kind: str = "reference") -> str:
    """Governing context: the matching template structure + the comic-style skill."""
    if kind == "styles":
        intro = (
            "You generate a character style/wardrobe sheet for a children's book. Honor the "
            "styles template structure and apply the comic-style art skill below; keep the "
            "character on-model with the attached reference sheet."
        )
        template_label = "# Styles template (structure to honor)"
    else:
        intro = (
            "You generate character reference sheets for a children's book. Honor the "
            "character template structure and apply the comic-style art skill below to the "
            "specific character described in the input."
        )
        template_label = "# Character template (structure and canon to honor)"

    sections: list[str] = [intro]
    if template_text:
        sections.append(f"{template_label}\n\n{template_text}")
    if skill_text:
        sections.append("# Comic-style skill (apply this house style)\n\n" + skill_text)
    return "\n\n---\n\n".join(sections)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--characters-dir", type=Path, default=Path("characters"))
    parser.add_argument(
        "--character",
        action="append",
        dest="characters",
        help="Character name or id to generate (repeatable). Defaults to all characters.",
    )
    parser.add_argument(
        "--kind",
        choices=["reference", "styles"],
        default="reference",
        help="reference = likeness sheet (default); styles = outfits/wardrobe sheet (Pass 2).",
    )
    parser.add_argument(
        "--source-subdir",
        help="Subfolder of cut-out images to attach (default: reference/ for --kind reference, styles/ for --kind styles).",
    )
    parser.add_argument("--skill", type=Path, default=Path("skills/comic-style/SKILL.md"))
    parser.add_argument(
        "--template",
        type=Path,
        help="Template to honor (default: CHARACTER_TEMPLATE.md for reference, STYLES_TEMPLATE.md for styles).",
    )
    parser.add_argument(
        "--style-reference",
        type=Path,
        help="Extra style-reference illustration attached to every request (styles uses the reference sheet automatically).",
    )
    parser.add_argument("--request-dir", type=Path, default=Path("outputs/character_requests"))
    parser.add_argument("--response-dir", type=Path, default=Path("outputs/character_responses"))
    parser.add_argument(
        "--send",
        action="store_true",
        help="Call the Gemini image API. Without this, write redacted request JSON only.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Regenerate sheets that already exist.")
    parser.add_argument(
        "--allow-proxy-auth",
        action="store_true",
        help="Allow requests without GEMINI_API_KEY when Antigravity injects the API key header.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_PRO_MODEL,
        help=f"Image model (default: {DEFAULT_PRO_MODEL}). Pass {DEFAULT_MODEL} for a faster, cheaper run.",
    )
    parser.add_argument("--pro-model", default=DEFAULT_PRO_MODEL)
    parser.add_argument("--force-pro", action="store_true", help="Force --pro-model (already the default).")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--image-size", default="4K")
    parser.add_argument("--mime-type", default=DEFAULT_MIME_TYPE)
    parser.add_argument(
        "--thinking-level",
        choices=["minimal", "high"],
        default="high",
        help="How much the model reasons before rendering (image models support minimal|high). Default: high.",
    )
    parser.add_argument(
        "--store",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Store the interaction server-side (default: --no-store for zero retention of personal photos).",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.characters_dir.exists():
        raise FileNotFoundError(f"Character directory not found: {args.characters_dir}")
    if args.style_reference is not None and not args.style_reference.exists():
        raise FileNotFoundError(f"Style reference not found: {args.style_reference}")

    if args.kind == "styles":
        template_path = args.template or Path("templates/STYLES_TEMPLATE.md")
        images_subdir = args.source_subdir or "styles"
        out_suffix = "styles_sheet"
        prompt_builder = build_styles_prompt
    else:
        template_path = args.template or Path("templates/CHARACTER_TEMPLATE.md")
        images_subdir = args.source_subdir or "reference"
        out_suffix = "character_sheet"
        prompt_builder = build_sheet_prompt

    house_style = load_house_style(args.skill)
    skill_text = args.skill.read_text(encoding="utf-8").strip() if args.skill.exists() else ""
    template_text = template_path.read_text(encoding="utf-8").strip() if template_path.exists() else ""
    system_instruction = compose_system_instruction(template_text, skill_text, kind=args.kind)

    wanted = {slugify(value) for value in args.characters} if args.characters else None
    character_dirs = selected_character_dirs(args.characters_dir, wanted, kind=args.kind)
    if not character_dirs:
        needed = "*_STYLES.md" if args.kind == "styles" else "*_DESCRIPTION.md"
        raise ValueError(f"No characters with {needed} found in {args.characters_dir}")

    api_key = os.environ.get("GEMINI_API_KEY")
    args.request_dir.mkdir(parents=True, exist_ok=True)
    args.response_dir.mkdir(parents=True, exist_ok=True)

    model = args.pro_model if args.force_pro else args.model
    extension = extension_for_mime_type(args.mime_type)

    generated = 0
    skipped = 0

    for character_dir in character_dirs:
        name = character_dir.name
        character_id = slugify(name)
        out_path = character_dir / f"{character_id}_{out_suffix}.{extension}"
        request_path = args.request_dir / f"{character_id}-{args.kind}.json"
        response_path = args.response_dir / f"{character_id}-{args.kind}.json"

        if out_path.exists() and not args.overwrite:
            print(f"Skipping {character_id} ({args.kind}): {out_path} exists (use --overwrite)")
            skipped += 1
            continue

        if args.kind == "styles":
            description_path = styles_description_file(character_dir)
        else:
            description_path = find_description_file(character_dir)
        if description_path is None:
            print(f"Skipping {character_id}: no {'styles' if args.kind == 'styles' else 'description'} file")
            skipped += 1
            continue
        sections = parse_sections(read_text(description_path))

        references = images_in_subdir(character_dir, images_subdir)
        if not references and args.kind == "reference" and not args.source_subdir:
            references = find_reference_images(character_dir)  # back-compat: cut-outs in source/

        if args.kind == "styles":
            reference_sheet = character_dir / f"{character_id}_character_sheet.{extension}"
            style_reference = reference_sheet if reference_sheet.exists() else args.style_reference
        else:
            style_reference = args.style_reference

        prompt = prompt_builder(name, sections, house_style, references, style_reference)
        payload = build_request(
            prompt,
            references,
            style_reference,
            model=model,
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
            mime_type=args.mime_type,
            thinking_level=args.thinking_level,
            system_instruction=system_instruction,
            store=args.store,
        )
        write_dry_run_request(request_path, payload)

        if not args.send:
            extra = " + reference sheet" if (args.kind == "styles" and style_reference) else ""
            print(f"Prepared {character_id} {args.kind} request ({len(references)} image(s){extra}): {request_path}")
            continue

        print(f"Generating {character_id} {out_suffix} with {model}")
        response = post_json(payload, api_key=api_key, allow_proxy_auth=args.allow_proxy_auth)
        response_path.write_text(
            json.dumps(redact_image_data(response), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        images = image_blocks(response)
        if not images:
            raise RuntimeError(f"No image returned for {character_id} ({args.kind}); see {response_path}")

        out_path.write_bytes(base64.b64decode(images[-1]["data"]))
        print(f"Wrote {out_path}")
        generated += 1

        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)

    if args.send:
        print(f"Generated {generated} {out_suffix}(s). Skipped {skipped}.")
    else:
        print(f"Dry run prepared {len(character_dirs) - skipped} {args.kind} request(s). Pass --send to generate.")


if __name__ == "__main__":
    main()
