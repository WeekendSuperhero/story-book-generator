#!/usr/bin/env python3
"""Create characters' full canon via multi-turn Interactions API chains.

Each character is a 4-turn chain that carries context forward with
`previous_interaction_id` (skill + templates + facts + photo cut-outs are uploaded
ONCE, in turn 1). Model + params change per turn:

  Turn 1  gemini-3.5-flash     thinking=high              -> <NAME>_DESCRIPTION.md
  Turn 2  gemini-3.5-flash     thinking=high              -> <NAME>_STYLES.md
  Turn 3  gemini-3-pro-image   16:9 / 4K, thinking=high   -> <name>_character_sheet.jpg  (canon)
  Turn 4  gemini-3-pro-image   16:9 / 4K, thinking=high   -> <name>_styles_sheet.jpg

Both TEXT turns run before both IMAGE turns on purpose: a text model cannot resume an
image-output interaction (image->text = HTTP 400). The styles.md (turn 2) then DRIVES
the styles sheet (turn 4).

Concurrency: characters run at the SAME time with a BARRIER per step. For each step we
fire every active character's request concurrently (one worker thread each) and wait for
them ALL to finish before starting the next step. (Note: `background=true` is NOT usable
here — the text model's background variant rejects image inputs, and gemini-3-pro-image
does not support background at all — so we parallelize synchronous calls with threads.)

`store=true` on every turn (required for previous_interaction_id). Each interaction id is
logged the moment it is created, so a crashed run can still be cleaned up. On completion
every interaction is DELETEd (nothing left server-side) unless --no-delete.

Local preprocessing (rembg cut-outs in reference/ and styles/) must already be done.

Cleanup only:
    python3 scripts/run_character_multiturn.py --cleanup outputs/multiturn/<cid>-run.json

Dry-run by default; pass --send to execute.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from build_story_prompt import read_text, slugify
from generate_character_sheets import images_in_subdir
from generate_page_images import encode_image, image_blocks
from generate_character_text import find_facts_file, extract_text

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
TEXT_MODEL = "gemini-3.5-flash"
IMAGE_MODEL = "gemini-3-pro-image"
TRANSIENT_CODES = {404, 408, 425, 429, 500, 502, 503, 504}
_print_lock = threading.Lock()

# Accuracy requirements. Two age-aware variants: the child variant deliberately avoids any
# anatomical/figure wording (which, next to a child's photos, can trip the content filter);
# both enforce even teeth (no gaps), correct arm/limb length, and true-to-reference age.
FIDELITY_ADULT = (
    " Fidelity: match the person's real adult proportions from the reference photos — correct "
    "height, build, arm length and limb proportions, and an accurate adult figure true to their "
    "real body and sex. Teeth are natural, even, and closed together with NO gaps."
    " Avoid: gaps between teeth, gap teeth, or a diastema; distorted, elongated, or mismatched "
    "arm/limb length; changing the person's age; and proportions that do not match the reference."
)
FIDELITY_CHILD = (
    " Fidelity: use accurate, age-appropriate proportions (correct height, head-to-body ratio, and "
    "arm/limb length) faithful to the reference photos. Teeth are natural, even, and closed with NO gaps."
    " Avoid: gaps between teeth or a diastema; distorted or mismatched arm/limb length; making the "
    "person look older; and proportions that do not match the reference."
)


def fidelity_clause(run: dict[str, Any]) -> str:
    return FIDELITY_CHILD if run.get("is_child") else FIDELITY_ADULT


def detect_child(facts_text: str) -> bool:
    """True if the facts mention an age of 12 or younger."""
    import re
    for m in re.finditer(r"age[^0-9]{0,12}(\d{1,2})|(\d{1,2})\s*years?\s*old", facts_text, re.IGNORECASE):
        num = m.group(1) or m.group(2)
        if num and int(num) <= 12:
            return True
    return False


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# ---- REST helpers -----------------------------------------------------------

def _headers(api_key: str | None, allow_proxy_auth: bool) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-goog-api-key"] = api_key
    elif not allow_proxy_auth:
        raise RuntimeError("GEMINI_API_KEY is not set. Use --allow-proxy-auth only inside a configured Antigravity environment.")
    return headers


def create_interaction(payload: dict[str, Any], api_key: str | None, allow_proxy_auth: bool,
                       max_transient: int = 6, max_content: int = 5, base_delay: float = 3.0,
                       label: str = "") -> dict[str, Any]:
    """POST an interaction synchronously (blocks until complete).

    Retries transient failures (404/429/5xx). The content-safety filter over-triggers
    probabilistically on wholesome child-character renders, so a "prohibited content" 400
    is retried a bounded number of times (a fresh sample usually passes); it is NOT an
    unbounded loop, and if it never clears the error propagates.
    """
    data = json.dumps(payload).encode("utf-8")
    transient = content = 0
    last = ""
    while True:
        req = urllib.request.Request(API_URL, data=data, method="POST",
                                     headers=_headers(api_key, allow_proxy_auth))
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            last = error.read().decode("utf-8", errors="replace")
            low = last.lower()
            if error.code == 429 and ("credit" in low or "billing" in low or "depleted" in low):
                # Hard billing stop — not transient; don't burn retries.
                raise RuntimeError(f"BILLING: prepayment credits depleted (top up at ai.studio): {last}") from error
            if error.code in TRANSIENT_CODES:
                transient += 1
                if transient < max_transient:
                    time.sleep(base_delay * transient); continue
                raise RuntimeError(f"HTTP {error.code}: {last}") from error
            if error.code == 400 and "prohibited content" in last.lower():
                content += 1
                if content < max_content:
                    log(f"  [{label}] content filter tripped (attempt {content}/{max_content - 1}); resampling ...")
                    time.sleep(base_delay * content); continue
                raise RuntimeError(f"content blocked after {content} attempts: {last}") from error
            raise RuntimeError(f"HTTP {error.code}: {last}") from error
        except urllib.error.URLError as error:
            transient += 1
            if transient < max_transient:
                time.sleep(base_delay * transient); continue
            raise RuntimeError(f"URLError: {error}") from error


def delete_interaction(interaction_id: str, api_key: str | None, allow_proxy_auth: bool) -> bool:
    req = urllib.request.Request(f"{API_URL}/{interaction_id}", method="DELETE",
                                 headers=_headers(api_key, allow_proxy_auth))
    try:
        with urllib.request.urlopen(req, timeout=120):
            return True
    except urllib.error.HTTPError as error:
        log(f"  [delete] {interaction_id[:24]}: HTTP {error.code}")
        return False


# ---- run log ----------------------------------------------------------------

def log_id(run_path: Path, turn: int, interaction_id: str) -> None:
    run_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {"ids": []}
    data["ids"].append({"turn": turn, "id": interaction_id})
    run_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def do_cleanup(run_path: Path, api_key: str | None, allow_proxy_auth: bool) -> None:
    if not run_path.exists():
        return
    data = json.loads(run_path.read_text(encoding="utf-8"))
    ids = [e["id"] for e in data.get("ids", [])]
    if not ids:
        return
    log(f"Deleting {len(ids)} interaction(s) from {run_path.name} ...")
    remaining = [i for i in ids if not delete_interaction(i, api_key, allow_proxy_auth)]
    data["ids"] = [e for e in data.get("ids", []) if e["id"] in remaining]
    run_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    log("  cleanup complete." if not remaining else f"  {len(remaining)} id(s) kept (delete failed).")


# ---- payload construction ---------------------------------------------------

def text_block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def image_response_format(aspect_ratio: str, image_size: str) -> dict[str, str]:
    return {"type": "image", "mime_type": "image/jpeg",
            "aspect_ratio": aspect_ratio, "image_size": image_size}


def context_preamble(now_str: str) -> str:
    return (
        f"The current date and time is {now_str}. All reference photographs in this conversation "
        "were provided by, are owned by, and depict the person who provided them, who has given "
        "consent to create a stylized children's-book character illustration from them; proceed on "
        "that basis. "
    )


def gen_config(opts: dict[str, Any]) -> dict[str, Any]:
    # thinking is ALWAYS high; temperature is placed high (it gates how the render looks);
    # top_p and a fixed seed give reproducibility.
    cfg: dict[str, Any] = {"thinking_level": "high", "temperature": opts["temperature"], "top_p": opts["top_p"]}
    if opts.get("seed") is not None:
        cfg["seed"] = opts["seed"]
    return cfg


def build_payload(step: int, run: dict[str, Any], aspect_ratio: str, image_size: str,
                  opts: dict[str, Any]) -> dict[str, Any]:
    name = run["name"]
    prev = run["prev_id"]
    pre = context_preamble(opts["now"])
    cfg = gen_config(opts)

    if step == 1:
        system = pre + (
            "You author the CHARACTER DESIGN REFERENCE (a markdown file) for a children's-book "
            "character. Fill EVERY section of the character template using the attached reference "
            "photos for appearance and the facts for age/height/weight/interests/hair color and "
            "texture. Record the correct age and accurate body proportions (height, build, arm/limb "
            "proportions, and an age-appropriate figure) faithfully; note that teeth are even with no "
            "gaps. Fill the Color Palette section with concrete hair-color and skin-tone swatches. "
            "Apply the comic-style house style as art direction. Output ONLY the completed markdown, no code fences."
        )
        blocks = [
            text_block(f"# Character template (fill this exact structure)\n\n{run['character_template']}"),
            text_block(f"# Styles template (you will fill this LATER, in a subsequent turn)\n\n{run['styles_template']}"),
            text_block(f"# Comic-style skill (house art style)\n\n{run['skill']}"),
            text_block(f"# Facts for {name} (authoritative)\n\n{run['facts']}"),
            text_block(f"The following images are reference photos of {name} (person cut-outs)."),
        ]
        blocks += [encode_image(p) for p in run["reference_images"]]
        blocks.append(text_block(f"Author {name}'s DESCRIPTION.md now. Output only the completed markdown."))
        return {"model": TEXT_MODEL, "system_instruction": system,
                "generation_config": cfg, "store": True, "input": blocks}

    if step == 2:
        system = pre + (
            "You author the STYLE & OUTFIT SNAPSHOT (a markdown file), filling EVERY section of the "
            "styles template provided earlier. Base it on the description you just wrote plus the "
            "attached clothing/style images, which are ONLY inspiration for how to dress the character. "
            "Keep body proportions, age, and identity accurate to the reference. Output ONLY the "
            "completed markdown, no code fences."
        )
        blocks = [text_block(
            f"The following images are clothing/style cut-outs — use them ONLY as inspiration for how "
            f"to dress {name}, not for body or face."
        )]
        blocks += [encode_image(p) for p in run["styles_images"]]
        blocks.append(text_block(
            f"Author {name}'s STYLES.md now, filling the styles template from the description above and "
            "these garment cut-outs. Output only the completed markdown."
        ))
        return {"model": TEXT_MODEL, "previous_interaction_id": prev, "system_instruction": system,
                "generation_config": cfg, "store": True, "input": blocks}

    if step == 3:
        system = pre + (
            "You render a character REFERENCE / CANON sheet for a children's book in the comic-style "
            "house style described earlier. Use the description and the reference photos already in this "
            "conversation to keep the likeness exact. Produce a clean labeled sheet: front, side, "
            "three-quarter and full-body views, a close-up face, and 2-3 expressions. The sheet MUST "
            "also include a small labeled COLOR-PALETTE panel showing the character's hair color(s) and "
            "skin-tone swatches. No story scene."
            + fidelity_clause(run)
        )
        return {"model": IMAGE_MODEL, "previous_interaction_id": prev, "system_instruction": system,
                "generation_config": cfg,
                "response_format": image_response_format(aspect_ratio, image_size),
                "store": True,
                "input": [text_block(
                    f"Render {name}'s character reference/canon sheet now, on-model with the description "
                    "and reference photos above, including the hair/skin color-palette panel."
                )]}

    # step == 4
    system = pre + (
        "You render a character STYLE / WARDROBE sheet for a children's book in the comic-style house "
        "style referenced earlier. CRITICAL: keep the character's face, hair, skin tone, body "
        "proportions, design, and overall identity IDENTICAL to the canon reference sheet above — do "
        "NOT redesign the person. Use the attached clothing/style images ONLY as inspiration for how to "
        "dress the character (outfit ideas), never as a source for their body, face, or proportions. "
        "Lay out the signature outfit plus 2-3 outfit variations and key accessories, following the "
        "STYLES.md wardrobe you wrote."
        + fidelity_clause(run)
    )
    return {"model": IMAGE_MODEL, "previous_interaction_id": prev, "system_instruction": system,
            "generation_config": cfg,
            "response_format": image_response_format(aspect_ratio, image_size),
            "store": True,
            "input": [text_block(
                f"Render {name}'s style/wardrobe sheet now — same person as the canon sheet above, "
                "identity identical, using the clothing images only as dress inspiration."
            )]}


STEP_KIND = {1: "text", 2: "text", 3: "image", 4: "image"}
STEP_LABEL = {1: "description", 2: "styles.md", 3: "canon sheet", 4: "styles sheet"}


def save_output(step: int, run: dict[str, Any], response: dict[str, Any]) -> None:
    path = run["paths"][step]
    if STEP_KIND[step] == "text":
        text = extract_text(response)
        if not text:
            raise RuntimeError("no text in response")
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
    else:
        images = image_blocks(response)
        if not images:
            raise RuntimeError("no image in response")
        path.write_bytes(base64.b64decode(images[-1]["data"]))


# ---- character setup --------------------------------------------------------

def build_run(name: str, characters_dir: Path, skill: str, character_template: str,
              styles_template: str) -> dict[str, Any]:
    cid = slugify(name)
    char_dir = characters_dir / name
    if not char_dir.is_dir():
        raise SystemExit(f"No character folder: {char_dir}")
    facts_file = find_facts_file(char_dir)
    if facts_file is None:
        raise SystemExit(f"No FACTS.md in {char_dir}")
    facts_text = read_text(facts_file)
    reference_images = images_in_subdir(char_dir, "reference")
    styles_images = images_in_subdir(char_dir, "styles")
    if not reference_images:
        raise SystemExit(f"No reference/ cut-outs in {char_dir}.")
    if not styles_images:
        raise SystemExit(f"No styles/ cut-outs in {char_dir}.")
    return {
        "name": name, "cid": cid, "char_dir": char_dir,
        "run_log": Path("outputs/multiturn") / f"{cid}-run.json",
        "skill": skill, "character_template": character_template, "styles_template": styles_template,
        "facts": facts_text, "is_child": detect_child(facts_text),
        "reference_images": reference_images, "styles_images": styles_images,
        "prev_id": None, "created_ids": [], "failed": False, "error": None,
        "paths": {
            1: char_dir / f"{name.upper()}_DESCRIPTION.md",
            2: char_dir / f"{name.upper()}_STYLES.md",
            3: char_dir / f"{cid}_character_sheet.jpg",
            4: char_dir / f"{cid}_styles_sheet.jpg",
        },
    }


def step_worker(run: dict[str, Any], step: int, api_key: str | None, allow_proxy_auth: bool,
                aspect_ratio: str, image_size: str, opts: dict[str, Any]) -> tuple[dict[str, Any], bool, str | None]:
    try:
        payload = build_payload(step, run, aspect_ratio, image_size, opts)
        resp = create_interaction(payload, api_key, allow_proxy_auth, label=f"{run['name']} step {step}")
        iid = resp["id"]
        run["created_ids"].append(iid)
        log_id(run["run_log"], step, iid)
        save_output(step, run, resp)
        run["prev_id"] = iid
        log(f"  [{run['name']}] step {step} DONE -> {run['paths'][step].name}")
        return run, True, None
    except Exception as exc:  # noqa: BLE001 - report and continue with other characters
        log(f"  [{run['name']}] step {step} FAILED: {type(exc).__name__}: {exc}")
        return run, False, f"step {step}: {exc}"


# ---- main -------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--character", action="append", dest="characters", help="Character folder name (repeatable).")
    p.add_argument("--all", action="store_true", help="Every character folder that has FACTS.md + reference/ cut-outs.")
    p.add_argument("--characters-dir", type=Path, default=Path("characters"))
    p.add_argument("--skill", type=Path, default=Path("skills/comic-style/SKILL.md"))
    p.add_argument("--character-template", type=Path, default=Path("templates/CHARACTER_TEMPLATE.md"))
    p.add_argument("--styles-template", type=Path, default=Path("templates/STYLES_TEMPLATE.md"))
    p.add_argument("--aspect-ratio", default="16:9")
    p.add_argument("--image-size", default="4K")
    p.add_argument("--temperature", type=float, default=1.4, help="Sampling temperature (placed high; gates how the render looks).")
    p.add_argument("--top-p", type=float, default=0.97, help="Nucleus sampling cumulative probability.")
    p.add_argument("--seed", type=int, default=42, help="Decoding seed for reproducibility (use --seed -1 to omit).")
    p.add_argument("--now", help="Override the current date/time string embedded in prompts (default: system clock).")
    p.add_argument("--max-turn", type=int, default=4, help="Run turns 1..N (default 4).")
    p.add_argument("--max-workers", type=int, default=8, help="Max characters generated concurrently per step.")
    p.add_argument("--send", action="store_true", help="Actually call the API (otherwise dry-run).")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-delete", action="store_true", help="Keep interactions server-side.")
    p.add_argument("--cleanup", type=Path, help="Delete all interaction ids in this run-log, then exit.")
    p.add_argument("--allow-proxy-auth", action="store_true")
    return p.parse_args()


def selected_characters(args: argparse.Namespace) -> list[str]:
    if args.all:
        return [d.name for d in sorted(p for p in args.characters_dir.iterdir() if p.is_dir())
                if find_facts_file(d) and images_in_subdir(d, "reference")]
    return args.characters or []


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("GEMINI_API_KEY")

    if args.cleanup:
        do_cleanup(args.cleanup, api_key, args.allow_proxy_auth)
        return

    names = selected_characters(args)
    if not names:
        raise SystemExit("Select characters with --character NAME (repeatable) or --all.")

    skill = read_text(args.skill)
    character_template = read_text(args.character_template)
    styles_template = read_text(args.styles_template)
    runs = [build_run(n, args.characters_dir, skill, character_template, styles_template) for n in names]

    from datetime import datetime
    now_str = args.now or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z (%A)")
    opts = {"temperature": args.temperature, "top_p": args.top_p,
            "seed": (None if args.seed is not None and args.seed < 0 else args.seed), "now": now_str}
    print(f"gen: thinking=high temperature={opts['temperature']} top_p={opts['top_p']} "
          f"seed={opts['seed']} | now={now_str}")

    print(f"=== {len(runs)} character(s) | turns 1..{args.max_turn} | concurrent (barrier/step) | "
          f"send={args.send} | delete={not args.no_delete} ===")
    for run in runs:
        print(f"  {run['name']:<10} ref={len(run['reference_images'])} styles={len(run['styles_images'])} -> {run['run_log']}")
    for step in range(1, args.max_turn + 1):
        print(f"  step {step}: {STEP_LABEL[step]} ({TEXT_MODEL if STEP_KIND[step]=='text' else IMAGE_MODEL})")

    if not args.send:
        print("\n[dry-run] pass --send to execute.")
        return

    for run in runs:
        for step in range(1, args.max_turn + 1):
            path = run["paths"][step]
            if path.exists() and not args.overwrite:
                raise SystemExit(f"{path} exists (use --overwrite).")

    # Barrier per step: fire all active characters concurrently, wait for all, then next step.
    for step in range(1, args.max_turn + 1):
        active = [r for r in runs if not r["failed"]]
        if not active:
            print("All characters failed; stopping.")
            break
        print(f"\n===== STEP {step} ({STEP_LABEL[step]}) — {len(active)} character(s) concurrently =====")
        with ThreadPoolExecutor(max_workers=min(args.max_workers, len(active))) as executor:
            futures = [executor.submit(step_worker, r, step, api_key, args.allow_proxy_auth,
                                       args.aspect_ratio, args.image_size, opts) for r in active]
            for future in as_completed(futures):
                run, ok, err = future.result()
                if not ok:
                    run["failed"] = True
                    run["error"] = err
        # with-block joins here -> barrier reached before the next step

    # Summary + cleanup.
    print("\n===== SUMMARY =====")
    for run in runs:
        done = sum(1 for s in range(1, args.max_turn + 1) if run["paths"][s].exists())
        state = "OK" if not run["failed"] else f"FAILED ({run['error']})"
        print(f"  {run['name']:<10} {done}/{args.max_turn} artifacts  {state}")

    if args.no_delete:
        total = sum(len(r["created_ids"]) for r in runs)
        print(f"\n--no-delete: {total} interaction(s) kept server-side (logged per character).")
    else:
        print()
        for run in runs:
            do_cleanup(run["run_log"], api_key, args.allow_proxy_auth)


if __name__ == "__main__":
    main()
