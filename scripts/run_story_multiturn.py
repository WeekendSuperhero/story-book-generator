#!/usr/bin/env python3
"""Generate a full storybook with Gemini via the multi-turn Interactions API.

Flow (approach 1 — "prime by attachment", the default):

  1. CANON (text, gemini-3.5-flash, thinking=high): attach the brief + the schema + rules
     + all four materials for every cast character (description, styles.md, character
     sheet.jpg, styles sheet.jpg). Gemini outputs the full story.json (title page implied
     by book.title/subtitle). Validate locally with check_story_output.check_story; on
     errors, chain a fix turn (previous_interaction_id) and retry until valid.

  2. TITLE PAGE (image, gemini-3-pro-image, 16:9/4K): a dedicated cover illustration
     featuring the cast, text-free (title overlaid later). -> title_page.jpg

  3. PAGES (image, gemini-3-pro-image), one at a time, SEQUENTIALLY. Each page is a FRESH
     call (NO previous_interaction_id). It is "primed" by attaching: the previous page's
     rendered .jpg (page 1 -> title_page.jpg), the previous page's info, this page's
     storyline, and — for every character present in the scene — that character's
     character sheet.jpg + styles sheet.jpg + description + styles.md. 1-page lookback.

store=true on every interaction; all interaction ids are logged and DELETED at the end.
Every turn carries the ownership/consent + current-date preamble and the high-thinking /
high-temperature / top_p / seed generation config.

(A second variant that chains pages with previous_interaction_id can be added later; this
script implements the attachment-priming variant first.)

Dry-run by default; pass --send to execute.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any

from build_story_prompt import read_text, slugify
from check_story_output import check_story
from generate_page_images import encode_image, image_blocks
from generate_character_text import extract_text, find_facts_file
from run_character_multiturn import (
    create_interaction, do_cleanup, log_id, gen_config, context_preamble,
    image_response_format, text_block, fidelity_clause, detect_child, log,
)

STORY_MODEL = "gemini-3.5-flash"
IMAGE_MODEL = "gemini-3-pro-image"

RULES = """\
Output requirements (a validator will check these exactly):
- Top-level object with: book, charactersUsed, styleGuide, pages (and optional textStyle).
- book: title, subtitle, targetAgeRange, targetReadTimeMinutes (5 or 10), targetWordCount,
  actualWordCount, pageCount, summary, theme, coreLesson, tone. The title/subtitle are used
  for the printed TITLE PAGE of the ebook (non-negotiable — make them strong).
- book.pageCount MUST equal the number of pages. book.actualWordCount MUST equal the sum of
  every page's wordCount. Each page's wordCount MUST equal the real word count of its storyText.
- pages: sequential pageNumber starting at 1. Each page needs: storyText, wordCount, textZone
  (bottom|top|left|right|center), textPlacement {xPercent,yPercent,widthPercent,heightPercent,
  textAlign(left|center|right),verticalAlign(top|middle|bottom),fontRole(title|body|caption|small),
  colorRecommendation}, charactersPresent[] (each: id,name,expression,pose,action,referenceSheet),
  illustrationPrompt, imageNegativePrompt, negativeSpaceInstruction, accessibilityAltText,
  continuityNotes.
- charactersUsed[]: id (slug), name, role, description, referenceSheet. Use ONLY the character
  ids and referenceSheet paths provided below; do not invent characters or paths.
- The illustrations will be rendered text-free with live HTML text overlaid later. So every
  illustrationPrompt/negativeSpaceInstruction must keep faces, hands, and action OUT of the
  chosen text zone and must NOT ask for any readable words in the image.
Output ONLY the JSON object — no prose, no markdown, no code fences.
"""


# ---- material loading -------------------------------------------------------

def load_materials(characters_dir: Path) -> dict[str, dict[str, Any]]:
    """Map character id (slug) -> its four materials, for every complete character folder."""
    out: dict[str, dict[str, Any]] = {}
    for d in sorted(p for p in characters_dir.iterdir() if p.is_dir()):
        name = d.name
        cid = slugify(name)
        desc = d / f"{name.upper()}_DESCRIPTION.md"
        styles = d / f"{name.upper()}_STYLES.md"
        sheet = d / f"{cid}_character_sheet.jpg"
        styles_sheet = d / f"{cid}_styles_sheet.jpg"
        if not (desc.exists() and sheet.exists()):
            continue
        out[cid] = {
            "id": cid, "name": name,
            "facts_path": find_facts_file(d),
            "description_path": desc, "styles_path": styles if styles.exists() else None,
            "sheet_path": sheet, "styles_sheet_path": styles_sheet if styles_sheet.exists() else None,
            "reference_sheet": f"characters/{name}/{cid}_character_sheet.jpg",
        }
    return out


def character_blocks(mat: dict[str, Any], include_styles_image: bool = True) -> list[dict[str, Any]]:
    """The four materials for one character as interaction input blocks."""
    blocks = [text_block(f"## Character: {mat['name']} (id: {mat['id']})")]
    blocks.append(text_block(f"### {mat['name']} description\n\n{read_text(mat['description_path'])}"))
    if mat["styles_path"]:
        blocks.append(text_block(f"### {mat['name']} styles\n\n{read_text(mat['styles_path'])}"))
    blocks.append(text_block(f"### {mat['name']} character sheet (canon likeness):"))
    blocks.append(encode_image(mat["sheet_path"]))
    if include_styles_image and mat["styles_sheet_path"]:
        blocks.append(text_block(f"### {mat['name']} styles sheet (wardrobe):"))
        blocks.append(encode_image(mat["styles_sheet_path"]))
    return blocks


# ---- turn 1: story canon (+ validate/fix loop) ------------------------------

def canon_payload(brief_text: str, schema_text: str, cast: list[dict[str, Any]],
                  opts: dict[str, Any], prev_id: str | None, fix_errors: list[str] | None) -> dict[str, Any]:
    system = context_preamble(opts["now"]) + (
        "You are an expert children's-book author and art director. Using the brief, the cast "
        "materials, and the schema/rules, write a complete, warm, age-appropriate story as ONE "
        "JSON object. Keep every character on-model with their sheets and descriptions. Include a "
        "strong title and subtitle for the ebook's title page. Output ONLY the JSON object."
    )
    if fix_errors:
        blocks = [text_block(
            "The JSON you produced failed validation with these errors. Fix ALL of them and output "
            "the COMPLETE corrected JSON again (recompute every wordCount, book.actualWordCount, and "
            "book.pageCount so they match exactly):\n\n- " + "\n- ".join(fix_errors)
        )]
        return {"model": STORY_MODEL, "previous_interaction_id": prev_id, "system_instruction": system,
                "generation_config": gen_config(opts), "store": True, "input": blocks}

    blocks: list[dict[str, Any]] = [
        text_block(f"# Brief\n\n{brief_text}"),
        text_block(f"# Output schema (conform exactly)\n\n{schema_text}"),
        text_block(f"# Rules\n\n{RULES}"),
        text_block("# Cast (use ONLY these characters and their referenceSheet paths)"),
    ]
    # The story-writing turn gets a LEAN cast brief (name, id, facts, referenceSheet) — not
    # the full physical descriptions/sheets. The detailed materials are attached at the image
    # turns where likeness matters; aggregating every character's full body description here
    # is unnecessary for prose and trips the input "sensitive words" filter.
    for mat in cast:
        facts = read_text(mat["facts_path"]) if mat.get("facts_path") else ""
        blocks.append(text_block(
            f"## {mat['name']} (id: {mat['id']})\n{facts}\n"
            f"referenceSheet path to use for this character: {mat['reference_sheet']}"
        ))
    blocks.append(text_block("Write the full story now as one JSON object. Output ONLY the JSON."))
    return {"model": STORY_MODEL, "system_instruction": system,
            "generation_config": gen_config(opts), "store": True, "input": blocks}


def parse_story_json(text: str) -> dict[str, Any]:
    t = text.strip()
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start:end + 1]
    return json.loads(t)


def generate_canon(brief_text, schema_text, cast, opts, api_key, allow_proxy, run_log, max_fix=5):
    prev_id = None
    errors: list[str] | None = None
    for attempt in range(1, max_fix + 1):
        payload = canon_payload(brief_text, schema_text, cast, opts, prev_id, errors)
        resp = create_interaction(payload, api_key, allow_proxy, label=f"canon try {attempt}")
        iid = resp["id"]; log_id(run_log, 0, iid); prev_id = iid
        text = extract_text(resp)
        try:
            story = parse_story_json(text)
        except Exception as exc:
            errors = [f"Output was not valid JSON: {exc}"]
            log(f"  canon try {attempt}: JSON parse failed; requesting fix")
            continue
        errors = check_story(story)
        if not errors:
            log(f"  canon try {attempt}: VALID ({story['book']['pageCount']} pages)")
            return story
        log(f"  canon try {attempt}: {len(errors)} validation error(s); requesting fix")
    raise RuntimeError(f"Story canon never validated after {max_fix} tries: {errors}")


# ---- title page + pages (images, approach 1) --------------------------------

def title_payload(story: dict[str, Any], cast: list[dict[str, Any]], opts: dict[str, Any],
                  aspect_ratio: str, image_size: str) -> dict[str, Any]:
    book = story["book"]
    system = context_preamble(opts["now"]) + (
        "You render the TITLE-PAGE / cover illustration for a children's book in the comic-style "
        "house style. Feature the main cast together in an inviting establishing scene, on-model "
        "with the attached character sheets. Render it TEXT-FREE (no title, letters, or words) and "
        "leave a clean, low-detail area near the top or center where the title will be overlaid later."
    )
    blocks = [text_block(
        f"Book: \"{book['title']}\" — {book.get('subtitle','')}\nSummary: {book.get('summary','')}\n"
        f"Theme: {book.get('theme','')}. Tone: {book.get('tone','')}."
    )]
    for mat in cast:
        blocks.append(text_block(f"{mat['name']} character sheet:"))
        blocks.append(encode_image(mat["sheet_path"]))
    blocks.append(text_block("Render the text-free cover/title-page illustration now."))
    return {"model": opts["image_model"], "system_instruction": system, "generation_config": gen_config(opts),
            "response_format": image_response_format(aspect_ratio, image_size), "store": True,
            "input": blocks}


def page_info(page: dict[str, Any]) -> str:
    who = ", ".join(c.get("name", c.get("id", "")) for c in page.get("charactersPresent", []))
    return f"Page {page['pageNumber']}: {page.get('storyText','')} (characters: {who or 'none'})"


def page_payload(story, page, prev_image: Path, prev_desc: str, materials: dict[str, dict],
                 opts, aspect_ratio: str, image_size: str) -> dict[str, Any]:
    present = [c for c in page.get("charactersPresent", []) if c.get("id") in materials]
    child_ctx = any(detect_child(read_text(materials[c["id"]]["description_path"])) for c in present)
    fake_run = {"is_child": child_ctx}
    system = context_preamble(opts["now"]) + (
        "You render ONE full-page children's-book illustration in the comic-style house style. Keep "
        "every character IDENTICAL to their attached character sheet (face, hair, skin tone, "
        "proportions, wardrobe). Maintain visual continuity with the attached previous page. Render "
        "the scene TEXT-FREE — no words, letters, signs, or captions — and keep faces, hands, and "
        "action OUT of the page's reserved text zone so live text can be overlaid there."
        + fidelity_clause(fake_run)
    )
    blocks: list[dict[str, Any]] = [
        text_block(f"Previous page (for visual continuity): {prev_desc}"),
        text_block("Previous page image:"),
        encode_image(prev_image),
        text_block(
            f"THIS PAGE to render — page {page['pageNumber']}.\n"
            f"Story text (context only, do NOT draw it): {page.get('storyText','')}\n"
            f"Illustration: {page.get('illustrationPrompt','')}\n"
            f"Reserved text zone: {page.get('textZone','')} — {page.get('negativeSpaceInstruction','')}\n"
            f"Avoid: {page.get('imageNegativePrompt','')}"
        ),
    ]
    for c in present:
        mat = materials[c["id"]]
        blocks.append(text_block(
            f"Character in scene: {mat['name']} — expression: {c.get('expression','')}, "
            f"pose: {c.get('pose','')}, action: {c.get('action','')}. Keep identical to the sheets below."
        ))
        blocks += character_blocks(mat)
    blocks.append(text_block(f"Render page {page['pageNumber']} now, text-free, on-model, continuous with the previous page."))
    return {"model": opts["image_model"], "system_instruction": system, "generation_config": gen_config(opts),
            "response_format": image_response_format(aspect_ratio, image_size), "store": True,
            "input": blocks}


def save_image_response(resp: dict[str, Any], path: Path) -> None:
    imgs = image_blocks(resp)
    if not imgs:
        raise RuntimeError("no image in response")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(imgs[-1]["data"]))


# ---- main -------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brief", type=Path, default=Path("brief.json"))
    p.add_argument("--characters-dir", type=Path, default=Path("characters"))
    p.add_argument("--schema", type=Path, default=Path("schemas/story_output.schema.json"))
    p.add_argument("--out-dir", type=Path, default=Path("outputs/story"))
    p.add_argument("--aspect-ratio", default="16:9")
    p.add_argument("--image-size", default="4K")
    p.add_argument("--image-model", default=IMAGE_MODEL, help=f"Image model (default {IMAGE_MODEL}; pass gemini-3.1-flash-image for cheaper/faster).")
    p.add_argument("--temperature", type=float, default=1.4)
    p.add_argument("--top-p", type=float, default=0.97)
    p.add_argument("--seed", type=int, default=42, help="Use -1 to omit.")
    p.add_argument("--now")
    p.add_argument("--canon-only", action="store_true", help="Stop after producing+validating story.json.")
    p.add_argument("--pages", help="Only render these page numbers (e.g. 1,2,3). Default: all.")
    p.add_argument("--send", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-delete", action="store_true")
    p.add_argument("--cleanup", type=Path, help="Delete all interaction ids in this run-log, then exit.")
    p.add_argument("--allow-proxy-auth", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("GEMINI_API_KEY")
    if args.cleanup:
        do_cleanup(args.cleanup, api_key, args.allow_proxy_auth)
        return

    from datetime import datetime
    now_str = args.now or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z (%A)")
    opts = {"temperature": args.temperature, "top_p": args.top_p,
            "seed": (None if args.seed is not None and args.seed < 0 else args.seed), "now": now_str,
            "image_model": args.image_model}

    brief_text = read_text(args.brief)
    brief = json.loads(brief_text)
    schema_text = read_text(args.schema)
    materials = load_materials(args.characters_dir)

    wanted = [slugify(x) for x in brief.get("characterSelection", {}).get("includeCharacterIds", [])]
    cast_ids = wanted or list(materials)
    cast = [materials[cid] for cid in cast_ids if cid in materials]
    if not cast:
        raise SystemExit("No cast materials found; run character generation first.")

    out_dir = args.out_dir
    story_path = out_dir / "story.json"
    title_path = out_dir / "title_page.jpg"
    pages_dir = out_dir / "page_images"
    run_log = out_dir / "story-run.json"

    print(f"=== Story run | cast: {[m['name'] for m in cast]} | send={args.send} | delete={not args.no_delete} ===")
    print(f"gen: thinking=high temperature={opts['temperature']} top_p={opts['top_p']} seed={opts['seed']} | now={now_str}")
    print(f"brief: {args.brief} | out: {out_dir}")
    if not args.send:
        print("\n[dry-run] Turn 1 canon attaches 4 materials x", len(cast), "characters + schema + rules.")
        print("Then title_page.jpg, then each page primed by prev image + materials (no interaction id).")
        print("Pass --send to execute.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Canon (+ validate/fix loop)
    if story_path.exists() and not args.overwrite:
        print(f"Reusing existing {story_path}")
        story = json.loads(read_text(story_path))
    else:
        print("\n== CANON ==")
        story = generate_canon(brief_text, schema_text, cast, opts, api_key, args.allow_proxy_auth, run_log)
        story_path.write_text(json.dumps(story, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote {story_path}")

    if args.canon_only:
        print("--canon-only: stopping after canon.")
        if not args.no_delete:
            do_cleanup(run_log, api_key, args.allow_proxy_auth)
        return

    # 2. Title page
    print("\n== TITLE PAGE ==")
    if title_path.exists() and not args.overwrite:
        print(f"Reusing {title_path}")
    else:
        resp = create_interaction(title_payload(story, cast, opts, args.aspect_ratio, args.image_size),
                                  api_key, args.allow_proxy_auth, label="title")
        log_id(run_log, -1, resp["id"])
        save_image_response(resp, title_path)
        print(f"Wrote {title_path} ({title_path.stat().st_size} bytes)")

    # 3. Pages (sequential; each primed by the previous rendered image)
    print("\n== PAGES ==")
    selected = None
    if args.pages:
        selected = {int(x) for x in args.pages.split(",") if x.strip()}
    prev_image = title_path
    prev_desc = f"Title page / cover of \"{story['book']['title']}\"."
    for page in story["pages"]:
        n = int(page["pageNumber"])
        page_path = pages_dir / f"page-{n:03d}.jpg"
        if selected is not None and n not in selected:
            prev_image, prev_desc = page_path if page_path.exists() else prev_image, page_info(page)
            continue
        if page_path.exists() and not args.overwrite:
            print(f"  page {n:03d}: exists, skip")
            prev_image, prev_desc = page_path, page_info(page)
            continue
        resp = create_interaction(
            page_payload(story, page, prev_image, prev_desc, materials, opts, args.aspect_ratio, args.image_size),
            api_key, args.allow_proxy_auth, label=f"page {n}")
        log_id(run_log, n, resp["id"])
        save_image_response(resp, page_path)
        print(f"  page {n:03d}: wrote {page_path} ({page_path.stat().st_size} bytes)")
        prev_image, prev_desc = page_path, page_info(page)

    print("\n== DONE ==")
    if args.no_delete:
        print(f"--no-delete: interactions kept (logged in {run_log}).")
    else:
        do_cleanup(run_log, api_key, args.allow_proxy_auth)


if __name__ == "__main__":
    main()
