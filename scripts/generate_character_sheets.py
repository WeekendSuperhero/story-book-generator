#!/usr/bin/env python3
"""Generate character reference sheets from character descriptions using Gemini.

Mirrors generate_page_images.py: REST-based (no SDK), dry-run by default, and
--send to call the image API. It reuses the prompt assembly from
build_character_sheet_prompt.py and the comic-style house style, attaching any
source photos found for the character so the model can match a real likeness.

Generated sheets are written into each character folder as
`<id>_character_sheet.<ext>` and existing sheets are left alone unless
--overwrite is passed.
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
    build_sheet_prompt,
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


def selected_character_dirs(characters_dir: Path, wanted: set[str] | None) -> list[Path]:
    dirs: list[Path] = []
    for character_dir in sorted(path for path in characters_dir.iterdir() if path.is_dir()):
        if find_description_file(character_dir) is None:
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
) -> dict[str, Any]:
    input_blocks: list[dict[str, str]] = [{"type": "text", "text": prompt}]
    for path in references:
        input_blocks.append(encode_image(path))
    if style_reference is not None:
        input_blocks.append(encode_image(style_reference))

    return {
        "model": model,
        "input": input_blocks,
        "response_format": {
            "type": "image",
            "mime_type": mime_type,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--characters-dir", type=Path, default=Path("characters"))
    parser.add_argument(
        "--character",
        action="append",
        dest="characters",
        help="Character name or id to generate (repeatable). Defaults to all characters.",
    )
    parser.add_argument("--skill", type=Path, default=Path("skills/comic-style/SKILL.md"))
    parser.add_argument(
        "--style-reference",
        type=Path,
        help="Style-reference illustration attached to every request for cast consistency.",
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
    parser.add_argument("--aspect-ratio", default="4:3")
    parser.add_argument("--image-size", default="2K")
    parser.add_argument("--mime-type", default=DEFAULT_MIME_TYPE)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.characters_dir.exists():
        raise FileNotFoundError(f"Character directory not found: {args.characters_dir}")
    if args.style_reference is not None and not args.style_reference.exists():
        raise FileNotFoundError(f"Style reference not found: {args.style_reference}")

    house_style = load_house_style(args.skill)
    wanted = {slugify(value) for value in args.characters} if args.characters else None
    character_dirs = selected_character_dirs(args.characters_dir, wanted)
    if not character_dirs:
        raise ValueError(f"No matching character descriptions found in {args.characters_dir}")

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
        sheet_path = character_dir / f"{character_id}_character_sheet.{extension}"
        request_path = args.request_dir / f"{character_id}.json"
        response_path = args.response_dir / f"{character_id}.json"

        if sheet_path.exists() and not args.overwrite:
            print(f"Skipping {character_id}: {sheet_path} exists (use --overwrite)")
            skipped += 1
            continue

        description_path = find_description_file(character_dir)
        sections = parse_sections(read_text(description_path))
        references = find_reference_images(character_dir)
        prompt = build_sheet_prompt(name, sections, house_style, references, args.style_reference)
        payload = build_request(
            prompt,
            references,
            args.style_reference,
            model=model,
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
            mime_type=args.mime_type,
        )
        write_dry_run_request(request_path, payload)

        if not args.send:
            mode = "reference" if references else "house-style"
            print(f"Prepared {character_id} request ({mode}, {len(references)} photo(s)): {request_path}")
            continue

        print(f"Generating {character_id} sheet with {model}")
        response = post_json(payload, api_key=api_key, allow_proxy_auth=args.allow_proxy_auth)
        response_path.write_text(
            json.dumps(redact_image_data(response), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        images = image_blocks(response)
        if not images:
            raise RuntimeError(f"No image returned for {character_id}; see {response_path}")

        sheet_path.write_bytes(base64.b64decode(images[-1]["data"]))
        print(f"Wrote {sheet_path}")
        generated += 1

        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)

    if args.send:
        print(f"Generated {generated} sheets. Skipped {skipped}.")
    else:
        print(f"Dry run prepared {len(character_dirs) - skipped} requests. Pass --send to generate sheets.")


if __name__ == "__main__":
    main()
