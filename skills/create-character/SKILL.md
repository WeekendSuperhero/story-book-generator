---
name: create-character
description: >
  Create one or more storybook characters from template information plus optional
  reference photos, producing comic-style character reference sheets. Input is the
  character details that fill templates/CHARACTER_TEMPLATE.md (name, age, skin tone,
  hair, eyes, distinctive feature, canonical outfit, proportions) and any reference
  pictures for likeness. Use when the user wants to create or add a character, build
  a cast, or turn a person/photo into a storybook character. Builds on the
  comic-style skill and the character scripts. Requires a GEMINI_API_KEY to render
  sheets.
---

# Create Character

Turn character details (and optional photos) into finished comic-style reference
sheets under `characters/<Name>/`. Works for a single character or a whole cast in
one pass.

## Input you need

Per character:
- **Name** — becomes the folder name.
- **Details** for the template — approximate age, skin tone, hair, eyes, a
  distinctive visual anchor, canonical outfit, and proportion notes.
- **Reference photos** (optional, recommended) — for a real likeness.

## Steps (repeat per character, or batch them)

1. Scaffold from the template:
   ```bash
   python3 scripts/new_character.py "Shane"
   ```
   Creates `characters/Shane/SHANE_DESCRIPTION.md` (from
   `templates/CHARACTER_TEMPLATE.md`) and `characters/Shane/source/`.

2. Fill in the description. Map the input details into sections 1–4 and, most
   importantly, section **7. Reference Sheet Instruction** — the generator uses that
   line verbatim as the image prompt. The `comic-style` skill supplies the house art
   style automatically at generation time, so you don't need to restate it.

3. If photos were provided, copy them in:
   ```bash
   cp /path/to/photos/*.jpg characters/Shane/source/
   ```
   Any image in `source/` switches that character to photo-based "reference" mode
   (preserve the real likeness, then apply the house style).

4. Generate the sheet(s) — dry-run first (no API cost), then send:
   ```bash
   python3 scripts/generate_character_sheets.py                       # preview ALL characters
   GEMINI_API_KEY=... python3 scripts/generate_character_sheets.py --send --sleep-seconds 1
   ```
   - With no `--character`, it processes **every** character folder, so a whole cast
     is created in one run. Existing sheets are skipped unless `--overwrite`.
   - `--character "Shane"` targets one; `--force-pro` helps difficult likenesses;
     `--style-reference characters/<Name>/<id>_character_sheet.jpg` keeps a new
     character visually consistent with the existing cast.

5. Open each `characters/<Name>/<id>_character_sheet.jpg` and confirm the views and
   identity are consistent before moving on. Regenerate with `--overwrite` if needed.

## Output

`characters/<Name>/<id>_character_sheet.jpg` for every character — the references
the story stage attaches for visual continuity.

## Next

Hand the cast to the **create-story** skill.
