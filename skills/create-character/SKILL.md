---
name: create-character
description: >
  Create one or more storybook characters from basic facts plus reference photos, in
  two passes. Pass 1 (reference): birefnet-portrait isolates the person, gemini-3.5-flash
  fills templates/CHARACTER_TEMPLATE.md, and the comic-style reference sheet is rendered.
  Pass 2 (styles): u2net_cloth_seg isolates the clothing, gemini-3.5-flash fills
  templates/STYLES_TEMPLATE.md from the reference + garments, and a styles/wardrobe
  sheet is rendered. Result: two photos per character (reference + styles). Use when the
  user wants to create or add a character or build a cast. Requires a GEMINI_API_KEY.
---

# Create Character (two passes)

From source photos to **two photos per character**:
- `<id>_character_sheet.jpg` — the **reference** (likeness)
- `<id>_styles_sheet.jpg` — the **styles** (outfits/wardrobe)

plus two canon docs: `<NAME>_DESCRIPTION.md` (likeness) and `<NAME>_STYLES.md` (styles).

## What the person must provide
For each character, collect: **character name, rough age, approximate height, hair color,
hair texture,** and **one or two sentences** about what they like / who they are. Store it in
`characters/<Name>/FACTS.md`; it is fed to Gemini as authoritative input (Gemini writes the
description — you don't hand-author it). Everything else is read from the reference photos.

## Wiring (how it fits together)
- The **comic-style skill** (`skills/comic-style/SKILL.md`) is loaded and placed in the
  **`system_instruction`** of *every* generation (both descriptions and both sheets), so
  everything stays in one house style.
- **Templates** go in `system_instruction` per pass: `CHARACTER_TEMPLATE.md` (Pass 1) and
  `STYLES_TEMPLATE.md` (Pass 2).
- `source/` holds the original photos and is never modified; each pass extracts into its
  own subfolder (`reference/`, `styles/`).
- **Every request also carries:** an ownership/consent line (the photos are owned by and
  depict the person who provided them) and the **current date/time**.
- The **character sheet must include a hair/skin color-palette** panel (see the template).
- The **styles sheet keeps identity/proportions IDENTICAL to the reference sheet**; the
  clothing/style images are used **only as inspiration for how to dress** the character.
- **Generation config on every turn:** `thinking_level` is always **high**; `temperature`
  is placed **high** (it gates how the render looks), plus `top_p` and a fixed `seed` for
  reproducibility.

## Automated (multi-turn, concurrent)
`scripts/run_character_multiturn.py --all --send` runs the whole thing: a 4-turn
Interactions chain per character (description → styles.md → canon sheet → styles sheet)
carried by `previous_interaction_id`, all characters at once with a barrier per step, and
every interaction deleted at the end. Prep the cut-outs first with `prep_source_photos.py`
(`--out-subdir reference` with `birefnet-portrait`, `--out-subdir styles` with `u2net_cloth_seg`).

## Setup (once)
```bash
uv sync                       # installs rembg + pillow
cp .env.example .env          # then paste your GEMINI_API_KEY
```

## Per character

1. **Scaffold + add photos.**
   ```bash
   python3 scripts/new_character.py "Shane"
   cp /path/to/photos/*.jpg characters/Shane/source/
   ```
   Use single-subject, uncluttered photos for the cleanest cut-outs.

### Pass 1 — Reference (made first)

2. **Extract the person** (portrait model):
   ```bash
   uv run scripts/prep_source_photos.py --character Shane \
     --model birefnet-portrait --out-subdir reference --apply --post-process
   ```
   → `characters/Shane/reference/*.png` (source/ untouched).

3. **Generate the description** with `gemini-3.5-flash`: `system_instruction` =
   `CHARACTER_TEMPLATE.md` + comic-style skill; `input` = basic facts + the `reference/`
   cut-outs. Save the filled template as `characters/Shane/SHANE_DESCRIPTION.md`.
   (`store=false`.) With no photos, fill the template by hand.

4. **Render the reference sheet:**
   ```bash
   uv run scripts/generate_character_sheets.py --character Shane --send   # dry-run without --send
   ```
   Reads `reference/` + `SHANE_DESCRIPTION.md` → `shane_character_sheet.jpg`
   (`gemini-3-pro-image`, 16:9 4K, thinking high, store false).

### Pass 2 — Styles (fed by the reference)

5. **Extract the clothing** (cloth model):
   ```bash
   uv run scripts/prep_source_photos.py --character Shane \
     --model u2net_cloth_seg --out-subdir styles --apply
   ```
   → `characters/Shane/styles/*.png` (garment cut-outs).

6. **Generate the styles snapshot** with `gemini-3.5-flash`: `system_instruction` =
   `STYLES_TEMPLATE.md` + comic-style skill; `input` = the `SHANE_DESCRIPTION.md` +
   reference sheet + the `styles/` garment PNGs. Save as
   `characters/Shane/SHANE_STYLES.md`.

7. **Render the styles sheet:**
   ```bash
   uv run scripts/generate_character_sheets.py --character Shane --kind styles --send
   ```
   Reads `styles/` + the **reference sheet** (style anchor) + `SHANE_STYLES.md` →
   `shane_styles_sheet.jpg`, on-model with the reference.

8. **Review** both sheets for consistent identity; regenerate any step with `--overwrite`.

## Output
Per character: `<id>_character_sheet.jpg` + `<id>_styles_sheet.jpg`, plus
`<NAME>_DESCRIPTION.md` + `<NAME>_STYLES.md`. The story stage attaches the reference
sheet for visual continuity.

## Notes
- Model knobs: `--model gemini-3.1-flash-image` (cheaper), `--thinking-level minimal`,
  `--store` (opt into retention). Extraction quality: `--post-process` / `--alpha-matting`.
- Batch: with no `--character`, both scripts process every character folder.

## Next
Hand the cast to the **create-story** skill.
