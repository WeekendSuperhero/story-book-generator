---
name: storybook-builder
description: >
  Build a finished fixed-layout EPUB children's book end to end from premade
  characters and a short brief. The pipeline: generate comic-style character
  reference sheets, turn a brief into a story-generation prompt, write the story
  JSON, generate one illustration per page, and assemble the EPUB. Use this skill
  whenever the user wants to create or build a children's book, picture book, or
  storybook from characters, "make a book", or run the storybook pipeline.
  Requires a GEMINI_API_KEY for image generation; pairs with the comic-style
  skill for art direction.
---

# Storybook Builder

Drive the whole children's-book pipeline. You (the agent) run the scripts for the
mechanical steps and do the one creative step — writing the story JSON — yourself.

```text
character description (templates/) + comic-style house style
  -> character reference sheet   (scripts/generate_character_sheets.py)
  -> story prompt                (scripts/build_story_prompt.py)
  -> story JSON                  (you write it, validated by check_story_output.py)
  -> page images                 (scripts/generate_page_images.py)
  -> fixed-layout EPUB           (scripts/build_fixed_layout_epub.py)
```

## Composed of three skills

This end-to-end flow is the composition of three focused skills you can also run on
their own:

1. **create-character** (Step 1) — cast details + photos → comic-style reference sheets.
2. **create-story** (Steps 2–6) — a prompt (cast + story idea) → story JSON → page
   images, including the cover art.
3. **publish-story** (Steps 7–8) — assemble and QA the readable EPUB.

The steps below are the full walkthrough; each step notes which script it runs.

## Prerequisites

- Python 3.11+. No third-party packages (standard library only).
- `GEMINI_API_KEY` in the environment for any image generation step.
- Optional: ImageMagick (`magick`) for the EPUB's automatic light/dark text color.
- Characters live in `characters/<Name>/` with a `*_DESCRIPTION.md` authored from
  `templates/CHARACTER_TEMPLATE.md`. For likeness, drop source photos in
  `characters/<Name>/source/`.

Run every command from the repository root. `characters/` and `outputs/` are
git-ignored (private inputs and generated artifacts).

## Step 1 — Create character reference sheets

Make sure every character has a description. To start a new one from the template:

```bash
python3 scripts/new_character.py "Shane"
```

That scaffolds `characters/Shane/SHANE_DESCRIPTION.md` (from
`templates/CHARACTER_TEMPLATE.md`) and `characters/Shane/source/`. Fill in the
description (`templates/EXAMPLE_CHARACTER_DESCRIPTION.md` is a worked example); the
`comic-style` skill supplies the house art style at generation time.

Dry run first (writes redacted request JSON, skips characters that already have a
sheet):

```bash
python3 scripts/generate_character_sheets.py
```

Generate for real:

```bash
GEMINI_API_KEY=... python3 scripts/generate_character_sheets.py --send --sleep-seconds 1
```

- `--character Bridget` targets one character; `--overwrite` regenerates an
  existing sheet. Sheets use the `gemini-3-pro-image` model by default (pass
  `--model gemini-3.1-flash-image` for a faster, cheaper run).
- `--style-reference characters/Bridget/bridget_character_sheet.jpg` keeps a new
  character visually consistent with the existing cast.

Result: `characters/<Name>/<id>_character_sheet.jpg`. Open each sheet and confirm
the views and identity are consistent before continuing.

## Step 2 — Write the brief

Copy `brief.example.json` (or `brief.10-minute.example.json`) to `brief.json` and set:

- `targetReadTimeMinutes`: `5` or `10` (drives page/word targets).
- `targetAgeRange`, `theme`, `setting`, `tone`, `coreLesson`.
- `characterSelection.includeCharacterIds`: slugified folder names, e.g.
  `["bridget", "dad"]`. Leave empty to let the story model choose the best cast.

## Step 3 — Generate the story prompt

```bash
python3 scripts/build_story_prompt.py --brief brief.json --out outputs/story_prompt.md
```

This injects the brief, the selected character canon (descriptions + sheet paths),
and the output schema into one prompt.

## Step 4 — Write the story JSON (your creative step)

Read `outputs/story_prompt.md` and write `outputs/story.json` conforming to
`schemas/story_output.schema.json`. Requirements that the checker enforces:

- Top-level objects: `book`, `charactersUsed`, `styleGuide`, `pages`.
- `book.pageCount` equals `len(pages)`; `book.actualWordCount` equals the sum of
  every page's `wordCount`.
- Each page needs: sequential `pageNumber` (1..N), `storyText`, `wordCount`
  **exactly equal to the real word count of `storyText`**, `textZone`
  (`bottom|top|left|right|center`), `textPlacement`
  (`xPercent,yPercent,widthPercent,heightPercent,textAlign,verticalAlign,fontRole,colorRecommendation`),
  `charactersPresent[]` (each `id,name,expression,pose,action,referenceSheet`),
  `illustrationPrompt`, `imageNegativePrompt`, `negativeSpaceInstruction`,
  `accessibilityAltText`, `continuityNotes`.
- `referenceSheet` values point at the real sheets, e.g.
  `characters/Bridget/bridget_character_sheet.jpg`. Character `id`s are slugs
  (`^[a-z0-9][a-z0-9_-]*$`).
- In each `illustrationPrompt` / `negativeSpaceInstruction`, keep faces, hands, and
  action out of the page's live-text-safe band so the EPUB can lay selectable text
  on top (the zone rectangles are defined in `scripts/prompt_zones.py`).

Validate and fix until it passes:

```bash
python3 scripts/check_story_output.py outputs/story.json
```

## Step 5 — (Optional) Export per-page image prompts

```bash
python3 scripts/export_image_prompts.py
```

Writes human-readable `outputs/image_prompts/page-XXX.md` listing the exact
reference sheets to attach and the live-text-safe rectangle per page.

## Step 6 — Generate page images

Dry-run one page without spending a call, then generate all pages:

```bash
python3 scripts/generate_page_images.py --pages 1 --limit 1
GEMINI_API_KEY=... python3 scripts/generate_page_images.py --send --sleep-seconds 1
```

The script attaches each page's character sheets, uses the `gemini-3-pro-image`
model by default (pass `--model gemini-3.1-flash-image` to downgrade), and writes
`outputs/page_images/page-XXX.jpg` (all pages must share one resolution).

## Step 7 — Build the EPUB

```bash
python3 scripts/build_fixed_layout_epub.py
```

Produces `outputs/<title>.epub` with one XHTML page per image, live HTML story
text over each illustration, and fixed-layout (`pre-paginated`) metadata. It runs a
ZIP smoke check and writes a text-style report. Tune with `--max-font-size`, or
per-page fixes in `outputs/layout_overrides.json`.

## Step 8 — QA and finish

Open the EPUB and check text legibility and placement. For any page that needs a
nudge, add an entry to `outputs/layout_overrides.json` (text placement and/or
color) and rerun Step 7. For distribution, also validate with EPUBCheck.

Final deliverable: `outputs/<title>.epub`.

## Notes

- For remote/background execution, `scripts/run_antigravity_flow.py` packages the
  text stages for a Google Antigravity environment.
- Inside Antigravity (key injected via header), add `--allow-proxy-auth` to the
  image steps instead of setting `GEMINI_API_KEY`.
