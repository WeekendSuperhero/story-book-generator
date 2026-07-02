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

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
# For storybook image turns (title + pages) each referenced character contributes ONLY its two
# sheet images — the character sheet and the styles sheet. The description.md / styles.md text is
# intentionally NOT attached to the picture turns (the sheets carry the visual canon).
ATTACH_ORDER = [("character_sheet", "sheet_path"), ("styles_sheet", "styles_sheet_path")]


def _ci_find(directory: Path, predicate) -> Path | None:
    """First file in directory whose lowercased name matches predicate (case-insensitive,
    so it works even on a case-sensitive filesystem regardless of how files were cased)."""
    if not directory.is_dir():
        return None
    for f in sorted(directory.iterdir()):
        if f.is_file() and predicate(f.name.lower()):
            return f
    return None


def resolve_materials(d: Path) -> dict[str, Any]:
    """Resolve a character's four materials by content-pattern, case-insensitively."""
    name = d.name
    cid = slugify(name)
    sheet = _ci_find(d, lambda n: "_character_sheet." in n and Path(n).suffix in IMAGE_EXTS)
    return {
        "id": cid, "name": name, "dir": d,
        "facts_path": find_facts_file(d),
        "description_path": _ci_find(d, lambda n: n.endswith("_description.md")),
        "styles_path": _ci_find(d, lambda n: n.endswith("_styles.md")),
        "sheet_path": sheet,
        "styles_sheet_path": _ci_find(d, lambda n: "_styles_sheet." in n and Path(n).suffix in IMAGE_EXTS),
        "reference_sheet": f"characters/{name}/{sheet.name}" if sheet else f"characters/{name}/{cid}_character_sheet.jpg",
    }


def load_materials(characters_dir: Path) -> dict[str, dict[str, Any]]:
    """Map character id (slug) -> its four materials, for every complete character folder."""
    out: dict[str, dict[str, Any]] = {}
    for d in sorted(p for p in characters_dir.iterdir() if p.is_dir()):
        mat = resolve_materials(d)
        if mat["description_path"] and mat["sheet_path"]:
            out[mat["id"]] = mat
    return out


def character_attachments(mat: dict[str, Any]) -> tuple[list[tuple[str, Path]], list[str]]:
    """(present [(kind, path)], missing [kind]) for one character's 4 materials."""
    items, missing = [], []
    for kind, key in ATTACH_ORDER:
        if mat.get(key):
            items.append((kind, mat[key]))
        else:
            missing.append(kind)
    return items, missing


def resolve_present(present: list[dict], materials: dict[str, dict]) -> tuple[list, list]:
    """Match each charactersPresent entry to a materials folder by id OR name, case-insensitively."""
    by_name = {slugify(m["name"]): k for k, m in materials.items()}
    resolved, unresolved = [], []
    for c in present:
        cid = slugify(c.get("id", "") or "")
        nm = slugify(c.get("name", "") or "")
        key = cid if cid in materials else (nm if nm in materials else by_name.get(nm))
        if key:
            resolved.append((c, materials[key]))
        else:
            unresolved.append(c.get("id") or c.get("name") or "?")
    return resolved, unresolved


def print_manifest(label: str, resolved: list, unresolved: list) -> None:
    """Show exactly which file (name + path) is attached, for which character, where."""
    total = 0
    for _c, mat in resolved:
        items, missing = character_attachments(mat)
        for kind, path in items:
            log(f"    [{label}] {mat['id']:<10} {kind:<15} <- {path}")
            total += 1
        for k in missing:
            log(f"    [{label}] {mat['id']:<10} {k:<15} !! MISSING — cannot attach")
    for u in unresolved:
        log(f"    [{label}] UNRESOLVED CHARACTER {u!r} — no matching character folder")
    tail = f", {len(unresolved)} UNRESOLVED" if unresolved else ""
    log(f"    [{label}] => {len(resolved)} character(s), {total} file(s) attached{tail}")


def character_blocks(mat: dict[str, Any]) -> list[dict[str, Any]]:
    """The four materials for one character as interaction input blocks (always all present ones)."""
    blocks = [text_block(f"## Character: {mat['name']} (id: {mat['id']})")]
    items, _missing = character_attachments(mat)
    for kind, path in items:
        if kind in ("description", "styles_md"):
            blocks.append(text_block(f"### {mat['name']} {kind}\n\n{read_text(path)}"))
        else:
            blocks.append(text_block(f"### {mat['name']} {kind} ({path.name}):"))
            blocks.append(encode_image(path))
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
        "with the attached character sheets. Render it strictly TEXT-FREE: NO title, letters, words, "
        "numbers, signs, posters, labels, banners, book covers, papers, or any readable text on props "
        "or clothing. Leave a clean, low-detail area near the top or center for the title to be "
        "overlaid later, but do NOT draw any box, frame, or placeholder there."
    )
    blocks = [text_block(
        f"Book: \"{book['title']}\" — {book.get('subtitle','')}\nSummary: {book.get('summary','')}\n"
        f"Theme: {book.get('theme','')}. Tone: {book.get('tone','')}."
    )]
    for mat in cast:
        blocks += character_blocks(mat)
    blocks.append(text_block("Render the text-free cover/title-page illustration now, on-model with every attached character sheet."))
    return {"model": opts["image_model"], "system_instruction": system, "generation_config": gen_config(opts),
            "response_format": image_response_format(aspect_ratio, image_size), "store": True,
            "input": blocks}


def page_info(page: dict[str, Any]) -> str:
    who = ", ".join(c.get("name", c.get("id", "")) for c in page.get("charactersPresent", []))
    return f"Page {page['pageNumber']}: {page.get('storyText','')} (characters: {who or 'none'})"


def page_payload(story, page, prev_image, prev_desc: str, present_resolved: list,
                 opts, aspect_ratio: str, image_size: str, chain_prev_id: str | None = None) -> dict[str, Any]:
    """Build a page image request.

    Two modes:
      - default (chain_prev_id=None): a fresh call primed by attaching the previous page's image.
      - chained (chain_prev_id set): links to the prior interaction via previous_interaction_id;
        NO previous-page image is attached (continuity comes from the conversation history);
        only the per-character sheet images are (re)attached each turn.
    """
    child_ctx = any(detect_child(read_text(mat["description_path"]))
                    for _c, mat in present_resolved if mat.get("description_path"))
    fake_run = {"is_child": child_ctx}
    continuity = ("Maintain visual continuity with the previous pages already in THIS conversation "
                  "(same book, same art style, same character designs)."
                  if chain_prev_id else
                  "Maintain visual continuity with the attached previous page.")
    system = context_preamble(opts["now"]) + (
        "You render ONE full-page children's-book illustration in the comic-style house style. Keep "
        "every character IDENTICAL to their attached character sheet (face, hair, skin tone, "
        "proportions, wardrobe). " + continuity + " Render "
        "the scene TEXT-FREE — no words, letters, signs, or captions. Keep faces, hands, and action "
        "out of the page's reserved text area and leave that area low-detail/uncluttered so text can "
        "be overlaid later — but do NOT draw any box, frame, rectangle, outline, label, caption, "
        "coordinates, watermark, UI, or placeholder of any kind marking that area. Output ONLY "
        "finished illustration art."
        + fidelity_clause(fake_run)
    )
    blocks: list[dict[str, Any]] = []
    if chain_prev_id:
        blocks.append(text_block(
            f"Continue the SAME storybook. The previous page was: {prev_desc}. Keep the identical art "
            "style and character designs as the earlier pages in this conversation — do not restart."
        ))
    else:
        blocks.append(text_block(f"Previous page (for visual continuity): {prev_desc}"))
        blocks.append(text_block("Previous page image:"))
        blocks.append(encode_image(prev_image))
    blocks.append(text_block(
        f"THIS PAGE to render — page {page['pageNumber']}.\n"
        f"Story text (context only, do NOT draw it): {page.get('storyText','')}\n"
        f"Illustration: {page.get('illustrationPrompt','')}\n"
        f"Keep the {page.get('textZone','')} region uncluttered and low-detail for later text "
        f"overlay — but draw NO box, label, or marking there; it must just be simpler art: "
        f"{page.get('negativeSpaceInstruction','')}\n"
        f"Avoid (do not render any of this): {page.get('imageNegativePrompt','')}"
    ))
    for c, mat in present_resolved:
        blocks.append(text_block(
            f"Character in scene: {mat['name']} — expression: {c.get('expression','')}, "
            f"pose: {c.get('pose','')}, action: {c.get('action','')}. Keep identical to the sheets below."
        ))
        blocks += character_blocks(mat)
    blocks.append(text_block(f"Render page {page['pageNumber']} now, text-free, on-model, continuous with the earlier pages."))
    payload = {"model": opts["image_model"], "system_instruction": system, "generation_config": gen_config(opts),
               "response_format": image_response_format(aspect_ratio, image_size), "store": True,
               "input": blocks}
    if chain_prev_id:
        payload["previous_interaction_id"] = chain_prev_id
    return payload


def save_image_response(resp: dict[str, Any], path: Path) -> None:
    imgs = image_blocks(resp)
    if not imgs:
        snippet = (extract_text(resp) or "")[:200]
        raise RuntimeError(f"no image in response (model returned text instead: {snippet!r})")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(imgs[-1]["data"]))


def create_image(payload: dict[str, Any], api_key, allow_proxy_auth, label: str,
                 out_path: Path, run_log: Path, turn: int, tries: int = 3) -> str | None:
    """Create an image interaction and save it, retrying if the model returns an empty
    response (occasional completed-but-no-image). Every attempt's id is logged so cleanup
    still deletes orphaned attempts. Returns the successful interaction id (for chaining)."""
    last = ""
    for attempt in range(1, tries + 1):
        resp = create_interaction(payload, api_key, allow_proxy_auth, label=f"{label} try{attempt}")
        iid = resp.get("id")
        if iid:
            log_id(run_log, turn, iid)
        try:
            save_image_response(resp, out_path)
            return iid
        except RuntimeError as exc:
            last = str(exc)
            if attempt < tries:
                log(f"  [{label}] empty response (try {attempt}/{tries}) — retrying")
    raise RuntimeError(f"{label}: {last}")


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
    p.add_argument("--temperature", type=float, default=0.4)
    p.add_argument("--top-p", type=float, default=0.97, help="(Unused — top_p is not sent.)")
    p.add_argument("--seed", type=int, default=42, help="Use -1 to omit.")
    p.add_argument("--now")
    p.add_argument("--canon-only", action="store_true", help="Stop after producing+validating story.json.")
    p.add_argument("--chain", action="store_true", help="Chain title->page1->..->pageN via previous_interaction_id (no prev-page image; re-attach only the 2 sheets per character). Regenerates title + all pages in order.")
    p.add_argument("--pages", help="Only render these page numbers (e.g. 1,2,3). Default: all. (Ignored with --chain.)")
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
    selected = {int(x) for x in args.pages.split(",") if x.strip()} if args.pages else None
    existing_story = json.loads(read_text(story_path)) if story_path.exists() else None

    if not args.send:
        print("\n[dry-run] Attachment manifest — exactly what WOULD be attached (name <- path):")
        print("  TITLE PAGE:")
        print_manifest("title", [(None, m) for m in cast], [])
        if existing_story:
            for page in existing_story["pages"]:
                n = int(page["pageNumber"])
                if selected is not None and n not in selected:
                    continue
                resolved, unresolved = resolve_present(page.get("charactersPresent", []), materials)
                print(f"  PAGE {n:03d}:")
                print_manifest(f"page {n}", resolved, unresolved)
        else:
            print("  (story.json not present yet — run --send once to generate the canon, then dry-run to preview pages.)")
        print("\nPass --send to execute.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Canon (+ validate/fix loop)
    if existing_story is not None and not args.overwrite:
        print(f"Reusing existing {story_path}")
        story = existing_story
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

    # --- Chained variant: title -> page1 -> ... -> pageN via previous_interaction_id ---
    if args.chain:
        print("\n== CHAINED RENDER (previous_interaction_id; sheets-only; no prev-page image) ==")
        print("  TITLE PAGE attachments:")
        print_manifest("title", [(None, m) for m in cast], [])
        prev_id = create_image(title_payload(story, cast, opts, args.aspect_ratio, args.image_size),
                               api_key, args.allow_proxy_auth, "title", title_path, run_log, -1)
        print(f"Wrote {title_path} ({title_path.stat().st_size} bytes) id={str(prev_id)[:20]}...")
        prev_desc = f"Title / cover of \"{story['book']['title']}\"."
        for page in story["pages"]:
            n = int(page["pageNumber"])
            page_path = pages_dir / f"page-{n:03d}.jpg"
            resolved, unresolved = resolve_present(page.get("charactersPresent", []), materials)
            print(f"  page {n:03d} (chained on prev_id={str(prev_id)[:16]}...) attachments:")
            print_manifest(f"page {n}", resolved, unresolved)
            prev_id = create_image(
                page_payload(story, page, None, prev_desc, resolved, opts, args.aspect_ratio,
                             args.image_size, chain_prev_id=prev_id),
                api_key, args.allow_proxy_auth, f"page {n}", page_path, run_log, n)
            print(f"  page {n:03d}: wrote {page_path} ({page_path.stat().st_size} bytes)")
            prev_desc = page_info(page)
        print("\n== DONE (chained) ==")
        if args.no_delete:
            print(f"--no-delete: interactions kept (logged in {run_log}).")
        else:
            do_cleanup(run_log, api_key, args.allow_proxy_auth)
        return

    # 2. Title page
    print("\n== TITLE PAGE ==")
    if title_path.exists() and not args.overwrite:
        print(f"Reusing {title_path}")
    else:
        print_manifest("title", [(None, m) for m in cast], [])
        create_image(title_payload(story, cast, opts, args.aspect_ratio, args.image_size),
                     api_key, args.allow_proxy_auth, "title", title_path, run_log, -1)
        print(f"Wrote {title_path} ({title_path.stat().st_size} bytes)")

    # 3. Pages (sequential; each primed by the previous rendered image)
    print("\n== PAGES ==")
    prev_image = title_path
    prev_desc = f"Title page / cover of \"{story['book']['title']}\"."
    for page in story["pages"]:
        n = int(page["pageNumber"])
        page_path = pages_dir / f"page-{n:03d}.jpg"
        if selected is not None and n not in selected:
            prev_image, prev_desc = (page_path if page_path.exists() else prev_image), page_info(page)
            continue
        if page_path.exists() and not args.overwrite:
            print(f"  page {n:03d}: exists, skip")
            prev_image, prev_desc = page_path, page_info(page)
            continue
        resolved, unresolved = resolve_present(page.get("charactersPresent", []), materials)
        print(f"  page {n:03d} attachments:")
        print_manifest(f"page {n}", resolved, unresolved)
        create_image(
            page_payload(story, page, prev_image, prev_desc, resolved, opts, args.aspect_ratio, args.image_size),
            api_key, args.allow_proxy_auth, f"page {n}", page_path, run_log, n)
        print(f"  page {n:03d}: wrote {page_path} ({page_path.stat().st_size} bytes)")
        prev_image, prev_desc = page_path, page_info(page)

    print("\n== DONE ==")
    if args.no_delete:
        print(f"--no-delete: interactions kept (logged in {run_log}).")
    else:
        do_cleanup(run_log, api_key, args.allow_proxy_auth)


if __name__ == "__main__":
    main()
