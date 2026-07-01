#!/usr/bin/env python3
"""Create one character's full canon via a multi-turn Interactions API chain.

Each turn uses `previous_interaction_id` to carry the conversation history (inputs +
outputs) forward, so the skill + templates + facts + photo cut-outs are uploaded ONCE
(turn 1) and every later turn sees them without re-uploading. The model and per-turn
parameters change between turns:

  Turn 1  gemini-3.5-flash     thinking=high              -> <NAME>_DESCRIPTION.md
  Turn 2  gemini-3.5-flash     thinking=high              -> <NAME>_STYLES.md
  Turn 3  gemini-3-pro-image   16:9 / 4K, thinking=high   -> <name>_character_sheet.jpg  (canon)
  Turn 4  gemini-3-pro-image   16:9 / 4K, thinking=high   -> <name>_styles_sheet.jpg

Both TEXT turns run before both IMAGE turns on purpose: a text model cannot resume an
image-output interaction (image->text returns HTTP 400), so keeping the two text turns
first preserves one valid previous_interaction_id chain end-to-end. As a bonus the
styles.md (turn 2) then DRIVES the styles sheet (turn 4).

Local preprocessing (rembg cut-outs in reference/ and styles/) must already be done.

`store=true` on every turn (required for previous_interaction_id). Each interaction id
is written to a run-log file the moment it is created, so a crashed run can still be
cleaned up. On success the script DELETEs every interaction it created (keeping nothing
server-side) unless --no-delete is passed.

Recovery / cleanup only:
    python3 scripts/run_character_multiturn.py --cleanup outputs/multiturn/<cid>-run.json

Dry-run by default (prints the plan, no API calls). Pass --send to execute.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from build_story_prompt import read_text, slugify
from generate_character_sheets import images_in_subdir
from generate_page_images import encode_image, image_blocks
from generate_character_text import find_facts_file, extract_text

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
TEXT_MODEL = "gemini-3.5-flash"
IMAGE_MODEL = "gemini-3-pro-image"


# ---- REST helpers -----------------------------------------------------------

def _headers(api_key: str | None, allow_proxy_auth: bool) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-goog-api-key"] = api_key
    elif not allow_proxy_auth:
        raise RuntimeError("GEMINI_API_KEY is not set. Use --allow-proxy-auth only inside a configured Antigravity environment.")
    return headers


# A just-created interaction (especially a large image one) is briefly not resolvable as
# a previous_interaction_id -> transient 404. Also retry rate-limit / 5xx. 400 is permanent.
TRANSIENT_CODES = {404, 408, 425, 429, 500, 502, 503, 504}


def create_interaction(payload: dict[str, Any], api_key: str | None, allow_proxy_auth: bool,
                       max_attempts: int = 6, base_delay: float = 3.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    last_detail = ""
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(API_URL, data=data, method="POST",
                                         headers=_headers(api_key, allow_proxy_auth))
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            last_detail = error.read().decode("utf-8", errors="replace")
            if error.code in TRANSIENT_CODES and attempt < max_attempts:
                delay = base_delay * attempt
                print(f"  [retry {attempt}/{max_attempts - 1}] HTTP {error.code}; waiting {delay:.0f}s ...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"HTTP {error.code}: {last_detail}") from error
        except urllib.error.URLError as error:
            if attempt < max_attempts:
                delay = base_delay * attempt
                print(f"  [retry {attempt}/{max_attempts - 1}] {type(error).__name__}; waiting {delay:.0f}s ...")
                time.sleep(delay)
                continue
            raise RuntimeError(f"URLError: {error}") from error
    raise RuntimeError(f"Exhausted retries: {last_detail}")


def delete_interaction(interaction_id: str, api_key: str | None, allow_proxy_auth: bool) -> bool:
    request = urllib.request.Request(
        f"{API_URL}/{interaction_id}", method="DELETE",
        headers=_headers(api_key, allow_proxy_auth),
    )
    try:
        with urllib.request.urlopen(request, timeout=120):
            return True
    except urllib.error.HTTPError as error:
        print(f"  [delete] {interaction_id}: HTTP {error.code} {error.read().decode('utf-8', errors='replace')[:200]}")
        return False


# ---- run log (so a crashed run is still cleanable) --------------------------

def log_id(run_path: Path, turn: int, interaction_id: str) -> None:
    run_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {"ids": []}
    data["ids"].append({"turn": turn, "id": interaction_id})
    run_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---- turn construction ------------------------------------------------------

def text_block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def turn1_payload(name: str, character_template: str, styles_template: str, skill: str,
                  facts: str, reference_images: list[Path]) -> dict[str, Any]:
    system_instruction = (
        "You author the CHARACTER DESIGN REFERENCE (a markdown file) for a children's-book "
        "character. Fill EVERY section of the character template using the attached reference "
        "photos for appearance and the facts for age/height/weight/interests/colors. Be concrete "
        "and consistent — this is the character's canon. Apply the comic-style house style as art "
        "direction. Output ONLY the completed markdown, no preamble and no code fences."
    )
    blocks = [
        text_block(f"# Character template (fill this exact structure)\n\n{character_template}"),
        text_block(f"# Styles template (you will fill this LATER, in a subsequent turn)\n\n{styles_template}"),
        text_block(f"# Comic-style skill (house art style)\n\n{skill}"),
        text_block(f"# Facts for {name} (authoritative)\n\n{facts}"),
        text_block(f"The following images are reference photos of {name} (person cut-outs)."),
    ]
    blocks += [encode_image(p) for p in reference_images]
    blocks.append(text_block(f"Author {name}'s DESCRIPTION.md now. Output only the completed markdown."))
    return {
        "model": TEXT_MODEL,
        "system_instruction": system_instruction,
        "generation_config": {"thinking_level": "high"},
        "store": True,
        "input": blocks,
    }


def turn2_payload(name: str, prev_id: str, styles_images: list[Path]) -> dict[str, Any]:
    # Turn 2 is TEXT (styles.md). It must come before the image turns: a text model
    # cannot resume an image-output interaction (image->text = HTTP 400), so both text
    # turns run first, then both image turns. The styles.md then DRIVES the styles sheet.
    system_instruction = (
        "You author the STYLE & OUTFIT SNAPSHOT (a markdown file), filling EVERY section of the "
        "styles template provided earlier. Base it on the description you just wrote plus the attached "
        "clothing-segmentation cut-outs of the character's real garments. Describe only what those "
        "inputs support; keep it on-model. Output ONLY the completed markdown, no code fences."
    )
    blocks = [text_block(f"The following images are clothing-segmentation cut-outs of {name}'s real garments.")]
    blocks += [encode_image(p) for p in styles_images]
    blocks.append(text_block(
        f"Author {name}'s STYLES.md now, filling the styles template from the description above and "
        "these garment cut-outs. Output only the completed markdown."
    ))
    return {
        "model": TEXT_MODEL,
        "previous_interaction_id": prev_id,
        "system_instruction": system_instruction,
        "generation_config": {"thinking_level": "high"},
        "store": True,
        "input": blocks,
    }


def turn3_payload(name: str, prev_id: str, aspect_ratio: str, image_size: str) -> dict[str, Any]:
    system_instruction = (
        "You render a character REFERENCE / CANON sheet for a children's book in the comic-style "
        "house style described earlier. Use the description and the reference photos already in this "
        "conversation to keep the likeness exact. Produce a clean labeled sheet: front, side, "
        "three-quarter and full-body views, a close-up face, and 2-3 expressions. No story scene."
    )
    return {
        "model": IMAGE_MODEL,
        "previous_interaction_id": prev_id,
        "system_instruction": system_instruction,
        "generation_config": {"thinking_level": "high"},
        "response_format": {"type": "image", "mime_type": "image/jpeg",
                            "aspect_ratio": aspect_ratio, "image_size": image_size},
        "store": True,
        "input": [text_block(
            f"Render {name}'s character reference/canon sheet now, on-model with the description and "
            "reference photos above."
        )],
    }


def turn4_payload(name: str, prev_id: str, aspect_ratio: str, image_size: str) -> dict[str, Any]:
    system_instruction = (
        "You render a character STYLE / WARDROBE sheet for a children's book in the same comic-style "
        "house style. Keep the face, hair, skin tone, and proportions IDENTICAL to the canon reference "
        "sheet above. Lay out the signature outfit plus 2-3 outfit variations and key accessories, "
        "following the STYLES.md wardrobe you wrote and the clothing cut-outs already in this conversation."
    )
    return {
        "model": IMAGE_MODEL,
        "previous_interaction_id": prev_id,
        "system_instruction": system_instruction,
        "generation_config": {"thinking_level": "high"},
        "response_format": {"type": "image", "mime_type": "image/jpeg",
                            "aspect_ratio": aspect_ratio, "image_size": image_size},
        "store": True,
        "input": [text_block(
            f"Render {name}'s style/wardrobe sheet now, on-model with the canon reference sheet above "
            "and the wardrobe you documented."
        )],
    }


# ---- artifact savers --------------------------------------------------------

def save_text(response: dict[str, Any], out_path: Path) -> None:
    text = extract_text(response)
    if not text:
        raise RuntimeError(f"No text returned; keys={list(response)}")
    out_path.write_text(text.rstrip() + "\n", encoding="utf-8")


def save_image(response: dict[str, Any], out_path: Path) -> None:
    images = image_blocks(response)
    if not images:
        raise RuntimeError(f"No image returned; keys={list(response)}")
    out_path.write_bytes(base64.b64decode(images[-1]["data"]))


# ---- main -------------------------------------------------------------------

def do_cleanup(run_path: Path, api_key: str | None, allow_proxy_auth: bool) -> None:
    data = json.loads(run_path.read_text(encoding="utf-8"))
    ids = [entry["id"] for entry in data.get("ids", [])]
    print(f"Deleting {len(ids)} interaction(s) from {run_path} ...")
    remaining = []
    for interaction_id in ids:
        if not delete_interaction(interaction_id, api_key, allow_proxy_auth):
            remaining.append(interaction_id)
        else:
            print(f"  deleted {interaction_id}")
    data["ids"] = [e for e in data.get("ids", []) if e["id"] in remaining]
    run_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("Cleanup complete." if not remaining else f"{len(remaining)} id(s) could not be deleted; kept in log.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--character", help="Character folder name under --characters-dir.")
    p.add_argument("--characters-dir", type=Path, default=Path("characters"))
    p.add_argument("--skill", type=Path, default=Path("skills/comic-style/SKILL.md"))
    p.add_argument("--character-template", type=Path, default=Path("templates/CHARACTER_TEMPLATE.md"))
    p.add_argument("--styles-template", type=Path, default=Path("templates/STYLES_TEMPLATE.md"))
    p.add_argument("--aspect-ratio", default="16:9")
    p.add_argument("--image-size", default="4K")
    p.add_argument("--max-turn", type=int, default=4, help="Run turns 1..N (default 4). Use for incremental testing.")
    p.add_argument("--run-log", type=Path, help="Where to record interaction ids (default outputs/multiturn/<cid>-run.json).")
    p.add_argument("--send", action="store_true", help="Actually call the API (otherwise dry-run).")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing artifacts.")
    p.add_argument("--no-delete", action="store_true", help="Keep interactions server-side (skip end cleanup).")
    p.add_argument("--cleanup", type=Path, help="Delete all interaction ids listed in this run-log file, then exit.")
    p.add_argument("--allow-proxy-auth", action="store_true")
    p.add_argument("--sleep-seconds", type=float, default=0.0)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("GEMINI_API_KEY")

    if args.cleanup:
        do_cleanup(args.cleanup, api_key, args.allow_proxy_auth)
        return

    if not args.character:
        raise SystemExit("--character is required (or use --cleanup <run-log>).")

    name = args.character
    cid = slugify(name)
    char_dir = args.characters_dir / name
    if not char_dir.is_dir():
        raise SystemExit(f"No character folder: {char_dir}")

    run_log = args.run_log or Path("outputs/multiturn") / f"{cid}-run.json"

    # Inputs (local).
    character_template = read_text(args.character_template)
    styles_template = read_text(args.styles_template)
    skill = read_text(args.skill)
    facts_file = find_facts_file(char_dir)
    if facts_file is None:
        raise SystemExit(f"No FACTS.md in {char_dir}")
    facts = read_text(facts_file)
    reference_images = images_in_subdir(char_dir, "reference")
    styles_images = images_in_subdir(char_dir, "styles")
    if not reference_images:
        raise SystemExit(f"No reference/ cut-outs in {char_dir} (run prep_source_photos --out-subdir reference).")
    if args.max_turn >= 2 and not styles_images:
        raise SystemExit(f"No styles/ cut-outs in {char_dir} (run prep_source_photos --out-subdir styles).")

    desc_path = char_dir / f"{name.upper()}_DESCRIPTION.md"
    canon_path = char_dir / f"{cid}_character_sheet.jpg"
    styles_sheet_path = char_dir / f"{cid}_styles_sheet.jpg"
    styles_md_path = char_dir / f"{name.upper()}_STYLES.md"

    # Order: both TEXT turns first, then both IMAGE turns (a text model cannot resume an
    # image interaction), so the chain stays valid end-to-end via previous_interaction_id.
    plan = [
        (1, TEXT_MODEL, "thinking=high", desc_path.name),
        (2, TEXT_MODEL, "thinking=high", styles_md_path.name),
        (3, IMAGE_MODEL, f"{args.aspect_ratio}/{args.image_size}", canon_path.name),
        (4, IMAGE_MODEL, f"{args.aspect_ratio}/{args.image_size}", styles_sheet_path.name),
    ]
    print(f"=== {name} (id={cid}) | turns 1..{args.max_turn} | send={args.send} | delete={not args.no_delete} ===")
    print(f"reference cut-outs: {len(reference_images)} | styles cut-outs: {len(styles_images)} | run-log: {run_log}")
    for turn, model, params, out in plan[: args.max_turn]:
        print(f"  turn {turn}: {model:<20} {params:<14} -> {out}")

    if not args.send:
        print("\n[dry-run] pass --send to execute.")
        return

    for path in (desc_path, styles_md_path, canon_path, styles_sheet_path)[: args.max_turn]:
        if path.exists() and not args.overwrite:
            raise SystemExit(f"{path} exists (use --overwrite).")

    prev_id: str | None = None
    created_ids: list[str] = []
    try:
        if args.max_turn >= 1:
            print("\nTurn 1: description (text) ...")
            payload = turn1_payload(name, character_template, styles_template, skill, facts, reference_images)
            resp = create_interaction(payload, api_key, args.allow_proxy_auth)
            prev_id = resp["id"]; created_ids.append(prev_id); log_id(run_log, 1, prev_id)
            save_text(resp, desc_path)
            print(f"  id={prev_id}  wrote {desc_path}")
            if args.sleep_seconds: time.sleep(args.sleep_seconds)

        if args.max_turn >= 2:
            print("Turn 2: styles.md (text) ...")
            payload = turn2_payload(name, prev_id, styles_images)
            resp = create_interaction(payload, api_key, args.allow_proxy_auth)
            prev_id = resp["id"]; created_ids.append(prev_id); log_id(run_log, 2, prev_id)
            save_text(resp, styles_md_path)
            print(f"  id={prev_id}  wrote {styles_md_path}")
            if args.sleep_seconds: time.sleep(args.sleep_seconds)

        if args.max_turn >= 3:
            print("Turn 3: canon reference sheet (image) ...")
            payload = turn3_payload(name, prev_id, args.aspect_ratio, args.image_size)
            resp = create_interaction(payload, api_key, args.allow_proxy_auth)
            prev_id = resp["id"]; created_ids.append(prev_id); log_id(run_log, 3, prev_id)
            save_image(resp, canon_path)
            print(f"  id={prev_id}  wrote {canon_path} ({canon_path.stat().st_size} bytes)")
            if args.sleep_seconds: time.sleep(args.sleep_seconds)

        if args.max_turn >= 4:
            print("Turn 4: styles/wardrobe sheet (image) ...")
            payload = turn4_payload(name, prev_id, args.aspect_ratio, args.image_size)
            resp = create_interaction(payload, api_key, args.allow_proxy_auth)
            prev_id = resp["id"]; created_ids.append(prev_id); log_id(run_log, 4, prev_id)
            save_image(resp, styles_sheet_path)
            print(f"  id={prev_id}  wrote {styles_sheet_path} ({styles_sheet_path.stat().st_size} bytes)")
    except Exception as exc:
        print(f"\n!! Run failed: {type(exc).__name__}: {exc}")
        print(f"   {len(created_ids)} interaction(s) recorded in {run_log}.")
        print(f"   Clean them up with: python3 scripts/run_character_multiturn.py --cleanup {run_log}")
        raise

    print(f"\nAll {args.max_turn} turn(s) complete. Interaction ids: {created_ids}")
    if args.no_delete:
        print(f"--no-delete set: {len(created_ids)} interaction(s) kept server-side (logged in {run_log}).")
    else:
        do_cleanup(run_log, api_key, args.allow_proxy_auth)


if __name__ == "__main__":
    main()
