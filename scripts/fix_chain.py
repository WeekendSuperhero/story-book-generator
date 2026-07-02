#!/usr/bin/env python3
"""One-off: a FRESH chain that corrects page 12 then finishes pages 14-16.

Chain (previous_interaction_id, sheets-only):
  page 12 (corrected, chain ROOT) -> page 14 -> page 15 -> page 16

Turn 1 (page 12) additionally attaches the CURRENT page-012.jpg as `bad_page12.jpg` so the
model can correct it (the current page 12 wrongly duplicates a character). Nothing else is
attached beyond bad_page12 + the normal character sheets + this page's storyline. Pages
14-16 then chain off the corrected page 12 via previous_interaction_id (no prev-page image;
only the two sheet images per present character are re-attached).

store=true on every interaction; all ids logged and DELETED at the end. Dry-run by default.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_story_multiturn as R


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-delete", action="store_true")
    args = ap.parse_args()
    api_key = os.environ.get("GEMINI_API_KEY")

    out = Path("outputs/story")
    pages_dir = out / "page_images"
    story = json.loads(R.read_text(out / "story.json"))
    materials = R.load_materials(Path("characters"))
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z (%A)")
    opts = {"temperature": args.temperature, "top_p": 0.97, "seed": args.seed,
            "now": now, "image_model": "gemini-3-pro-image"}
    ar, size = "16:9", "4K"
    run_log = out / "fixchain-run.json"
    bad = out / "bad_page12.jpg"

    def pget(n):
        return next(p for p in story["pages"] if int(p["pageNumber"]) == n)

    p12 = pget(12)
    r12, _ = R.resolve_present(p12.get("charactersPresent", []), materials)

    print(f"=== FIX CHAIN: page12(corrected) -> 14 -> 15 -> 16 | send={args.send} ===")
    print(f"gen: thinking=high temperature={opts['temperature']} top_p=off seed={opts['seed']} image={opts['image_model']}")
    print("\n[page 12 correction] attaches (chain ROOT):")
    print(f"    page 12    bad_reference   <- {bad}  (copy of current page-012.jpg)")
    R.print_manifest("page 12", r12, [])
    for n in (14, 15, 16):
        r, _ = R.resolve_present(pget(n).get("charactersPresent", []), materials)
        print(f"[page {n}] chained via previous_interaction_id (sheets-only, no prev-page image):")
        R.print_manifest(f"page {n}", r, [])

    if not args.send:
        print("\n[dry-run] pass --send to execute.")
        return

    if run_log.exists():
        run_log.unlink()

    # Preserve the current (bad) page 12 as the correction reference.
    shutil.copyfile(pages_dir / "page-012.jpg", bad)
    print(f"\nSaved bad reference -> {bad}")

    # --- Turn 1: corrected page 12 (chain root) ---
    child = any(R.detect_child(R.read_text(m["description_path"]))
                for _c, m in r12 if m.get("description_path"))
    system = R.context_preamble(opts["now"]) + (
        "You render ONE full-page children's-book illustration in the comic-style house style. The "
        "attached image 'bad_page12' is a FLAWED version of THIS page — it wrongly DUPLICATES a "
        "character (the same person appears twice). Regenerate a CORRECTED version of the same scene "
        "and story beat: each listed character appears EXACTLY ONCE — no duplicates, clones, twins, or "
        "repeated people. Keep every character IDENTICAL to their attached character sheet. Render "
        "TEXT-FREE; keep faces/hands/action out of the reserved text area; draw no box/label/text there. "
        + R.fidelity_clause({"is_child": child})
    )
    blocks = [
        R.text_block("Attached 'bad_page12' is the FLAWED version to CORRECT (it duplicates a character — do NOT repeat that mistake):"),
        R.encode_image(bad),
        R.text_block(
            f"THIS PAGE — page 12.\nStory text (context only, do NOT draw it): {p12.get('storyText','')}\n"
            f"Illustration: {p12.get('illustrationPrompt','')}\n"
            f"Keep the {p12.get('textZone','')} area low-detail for later text — no box/label: {p12.get('negativeSpaceInstruction','')}\n"
            f"Avoid (do not render any of this): {p12.get('imageNegativePrompt','')}\n"
            "Every character present appears EXACTLY ONCE."
        ),
    ]
    for c, m in r12:
        blocks.append(R.text_block(
            f"Character in scene (exactly one of each): {m['name']} — expression {c.get('expression','')}, "
            f"pose {c.get('pose','')}, action {c.get('action','')}."
        ))
        blocks += R.character_blocks(m)
    blocks.append(R.text_block("Render the corrected page 12 now — text-free, on-model, each character exactly once."))
    payload12 = {"model": opts["image_model"], "system_instruction": system,
                 "generation_config": R.gen_config(opts),
                 "response_format": R.image_response_format(ar, size), "store": True, "input": blocks}
    prev_id = R.create_image(payload12, api_key, False, "page12-fix", pages_dir / "page-012.jpg", run_log, 12)
    print(f"page 12 corrected; id={str(prev_id)[:22]}")

    # --- Chain 14 -> 15 -> 16 off the corrected page 12 ---
    prev_desc = R.page_info(pget(13))
    for n in (14, 15, 16):
        p = pget(n)
        r, _ = R.resolve_present(p.get("charactersPresent", []), materials)
        payload = R.page_payload(story, p, None, prev_desc, r, opts, ar, size, chain_prev_id=prev_id)
        prev_id = R.create_image(payload, api_key, False, f"page {n}", pages_dir / f"page-{n:03d}.jpg", run_log, n)
        print(f"page {n} done; id={str(prev_id)[:22]}")
        prev_desc = R.page_info(p)

    if args.no_delete:
        print(f"--no-delete: interactions kept (logged in {run_log}).")
    else:
        R.do_cleanup(run_log, api_key, False)
    print("DONE")


if __name__ == "__main__":
    main()
