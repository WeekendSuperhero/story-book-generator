#!/usr/bin/env python3
"""Generate page images from story JSON using Gemini image models.

The script is intentionally REST-based so it can run locally or inside an
Antigravity Linux sandbox without requiring the Google GenAI SDK.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from prompt_zones import reserved_zone_instruction


API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_MODEL = "gemini-3.1-flash-image"
DEFAULT_PRO_MODEL = "gemini-3-pro-image"
DEFAULT_MIME_TYPE = "image/jpeg"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def parse_page_selection(value: str | None, total_pages: int) -> set[int]:
    if not value:
        return set(range(1, total_pages + 1))

    selected: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            selected.update(range(start, end + 1))
        else:
            selected.add(int(part))

    invalid = sorted(page for page in selected if page < 1 or page > total_pages)
    if invalid:
        raise ValueError(f"Invalid page numbers: {invalid}")
    return selected


def mime_type_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "image/jpeg"


def extension_for_mime_type(mime_type: str) -> str:
    if mime_type == "image/jpeg":
        return "jpg"
    if mime_type == "image/png":
        return "png"
    guessed = mimetypes.guess_extension(mime_type)
    return guessed.lstrip(".") if guessed else "img"


def encode_image(path: Path) -> dict[str, str]:
    return {
        "type": "image",
        "mime_type": mime_type_for(path),
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def character_reference_paths(page: dict[str, Any], root: Path) -> list[Path]:
    paths: list[Path] = []
    for character in page.get("charactersPresent", []):
        if not isinstance(character, dict) or not character.get("referenceSheet"):
            continue
        path = Path(character["referenceSheet"])
        if not path.is_absolute():
            path = root / path
        paths.append(path)
    return paths


def page_prompt(story: dict[str, Any], page: dict[str, Any]) -> str:
    book = story["book"]
    characters = page.get("charactersPresent", [])
    character_lines: list[str] = []
    for character in characters:
        character_lines.append(
            "\n".join(
                [
                    f"- {character.get('name', '')} (`{character.get('id', '')}`)",
                    f"  Expression: {character.get('expression', '')}",
                    f"  Pose: {character.get('pose', '')}",
                    f"  Action: {character.get('action', '')}",
                ]
            )
        )

    return "\n\n".join(
        [
            "Generate one full-page fixed-layout children's book illustration.",
            f"Book title: {book.get('title', '')}",
            f"Page number: {page.get('pageNumber')}",
            "Target format: landscape 4:3 image for a 1600x1200 fixed-layout EPUB page.",
            "Do not render any story text, page numbers, signs, labels, captions, speech bubbles, or readable letters in the image.",
            "The EPUB generator will place live HTML text over the reserved space later.",
            "Use the attached character reference sheets for visual consistency. Preserve age, face shape, hairstyle, outfit, proportions, and expression language.",
            "Create a single story scene, not a character sheet or collage.",
            "Story text for context:",
            page.get("storyText", ""),
            "Characters visible in this image:",
            "\n".join(character_lines) if character_lines else "No recurring characters visible.",
            "Scene prompt:",
            page.get("illustrationPrompt", ""),
            "Fixed live-text safe area:",
            reserved_zone_instruction(page),
            "Story-specific negative-space requirement:",
            page.get("negativeSpaceInstruction", ""),
            "Negative prompt:",
            page.get("imageNegativePrompt", ""),
        ]
    )


def model_for_page(page: dict[str, Any], default_model: str, pro_model: str, pro_threshold: int) -> str:
    reference_count = len(page.get("charactersPresent", []))
    if reference_count >= pro_threshold:
        return pro_model
    return default_model


def build_request(
    story: dict[str, Any],
    page: dict[str, Any],
    root: Path,
    model: str,
    aspect_ratio: str,
    image_size: str,
    mime_type: str,
    skip_missing_references: bool,
) -> dict[str, Any]:
    input_blocks: list[dict[str, str]] = [{"type": "text", "text": page_prompt(story, page)}]

    missing: list[Path] = []
    for reference_path in character_reference_paths(page, root):
        if reference_path.exists():
            input_blocks.append(encode_image(reference_path))
        else:
            missing.append(reference_path)

    if missing and not skip_missing_references:
        raise FileNotFoundError(
            "Missing character reference sheets:\n" + "\n".join(f"- {path}" for path in missing)
        )

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


def post_json(payload: dict[str, Any], api_key: str | None, allow_proxy_auth: bool) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-goog-api-key"] = api_key
    elif not allow_proxy_auth:
        raise RuntimeError("GEMINI_API_KEY is not set. Use --allow-proxy-auth only inside a configured Antigravity environment.")

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def image_blocks(response: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    output_image = response.get("output_image")
    if isinstance(output_image, dict) and output_image.get("data"):
        blocks.append(output_image)

    for step in response.get("steps", []):
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content", []):
            if isinstance(block, dict) and block.get("type") == "image" and block.get("data"):
                blocks.append(block)

    return blocks


def redact_image_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            if key == "data" and value.get("type") == "image":
                redacted[key] = f"<base64 image data: {len(str(child))} chars>"
            else:
                redacted[key] = redact_image_data(child)
        return redacted
    if isinstance(value, list):
        return [redact_image_data(child) for child in value]
    return value


def write_dry_run_request(path: Path, payload: dict[str, Any]) -> None:
    redacted = redact_image_data(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redacted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story", type=Path, default=Path("outputs/story.json"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/page_images"))
    parser.add_argument("--request-dir", type=Path, default=Path("outputs/image_requests"))
    parser.add_argument("--response-dir", type=Path, default=Path("outputs/image_responses"))
    parser.add_argument("--pages", help="Pages to generate, e.g. 1,3,5-8. Defaults to all pages.")
    parser.add_argument("--limit", type=int, help="Limit number of selected pages processed.")
    parser.add_argument("--send", action="store_true", help="Call the Gemini image API. Without this, write redacted request JSON only.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-missing-references", action="store_true")
    parser.add_argument("--allow-proxy-auth", action="store_true", help="Allow requests without GEMINI_API_KEY when Antigravity injects the API key header.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--pro-model", default=DEFAULT_PRO_MODEL)
    parser.add_argument("--force-pro", action="store_true", help="Use --pro-model for every selected page.")
    parser.add_argument("--pro-reference-threshold", type=int, default=5)
    parser.add_argument("--aspect-ratio", default="4:3")
    parser.add_argument("--image-size", default="2K")
    parser.add_argument("--mime-type", default=DEFAULT_MIME_TYPE)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    story = load_json(args.story)
    pages = story.get("pages", [])
    if not isinstance(pages, list) or not pages:
        raise ValueError("Story JSON must contain a non-empty pages array")

    selected = parse_page_selection(args.pages, len(pages))
    selected_pages = [page for page in pages if page["pageNumber"] in selected]
    if args.limit is not None:
        selected_pages = selected_pages[: args.limit]

    api_key = os.environ.get("GEMINI_API_KEY")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.request_dir.mkdir(parents=True, exist_ok=True)
    args.response_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0

    for page in selected_pages:
        page_number = int(page["pageNumber"])
        image_extension = extension_for_mime_type(args.mime_type)
        image_path = args.out_dir / f"page-{page_number:03d}.{image_extension}"
        request_path = args.request_dir / f"page-{page_number:03d}.json"
        response_path = args.response_dir / f"page-{page_number:03d}.json"

        if image_path.exists() and not args.overwrite:
            print(f"Skipping page {page_number:03d}: {image_path} exists")
            skipped += 1
            continue

        if args.force_pro:
            model = args.pro_model
        else:
            model = model_for_page(
                page,
                default_model=args.model,
                pro_model=args.pro_model,
                pro_threshold=args.pro_reference_threshold,
            )
        payload = build_request(
            story,
            page,
            root=args.root,
            model=model,
            aspect_ratio=args.aspect_ratio,
            image_size=args.image_size,
            mime_type=args.mime_type,
            skip_missing_references=args.skip_missing_references,
        )
        write_dry_run_request(request_path, payload)

        if not args.send:
            print(f"Prepared page {page_number:03d} request: {request_path}")
            continue

        print(f"Generating page {page_number:03d} with {model}")
        response = post_json(payload, api_key=api_key, allow_proxy_auth=args.allow_proxy_auth)
        response_path.write_text(
            json.dumps(redact_image_data(response), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        images = image_blocks(response)
        if not images:
            raise RuntimeError(f"No image returned for page {page_number:03d}; see {response_path}")

        image_path.write_bytes(base64.b64decode(images[-1]["data"]))
        print(f"Wrote {image_path}")
        generated += 1

        if args.sleep_seconds:
            time.sleep(args.sleep_seconds)

    if args.send:
        print(f"Generated {generated} images. Skipped {skipped}.")
    else:
        print(f"Dry run prepared {len(selected_pages) - skipped} requests. Pass --send to generate images.")


if __name__ == "__main__":
    main()
