---
name: create-character
description: >
  Create one or more storybook characters from basic facts plus reference photos.
  gemini-3.5-flash fills templates/CHARACTER_TEMPLATE.md from the photos to
  generate the character description (the canon), then the comic-style reference sheet
  is rendered from it. Use when the user wants to create or add a character, build a
  cast, or turn a person/photo into a storybook character. Builds on the comic-style
  skill and the character scripts. Requires a GEMINI_API_KEY.
---

# Create Character

Turn character details (and optional photos) into finished comic-style reference
sheets under `characters/<Name>/`. Works for a single character or a whole cast in
one pass.

## Input you need

Per character:
- **Name** — becomes the folder name.
- **Basic facts** — at least age (plus height/birthday if known), and anything else
  you want fixed.
- **Reference photos** (recommended) — the model derives skin tone, hair, eyes,
  distinctive features, and proportions from these. Without photos, provide those
  visual details yourself.

## Steps (repeat per character, or batch them)

1. Scaffold from the template:
   ```bash
   python3 scripts/new_character.py "Shane"
   ```
   Creates `characters/Shane/SHANE_DESCRIPTION.md` (from
   `templates/CHARACTER_TEMPLATE.md`) and `characters/Shane/source/`.

2. Add the reference photos, then clean them (extract the person):
   ```bash
   cp /path/to/photos/*.jpg characters/Shane/source/
   uv sync                                          # one-time dependency setup
   uv run scripts/prep_source_photos.py --character "Shane" --apply
   ```
   This replaces each `source/` photo with a tight person cut-out (originals kept in
   `source/originals/`, which the generators ignore), giving the model a cleaner
   subject and smaller files. Skip if you didn't add photos.

3. Generate the description with `gemini-3.5-flash`. Send the **same** inputs we
   already use for the sheet — the blank template + `comic-style` skill in
   `system_instruction`, and the basic facts + `source/` photos in the input — but with
   a text model and an instruction to *fill the template from the photos* (text output,
   not an image). Save the result over `characters/Shane/SHANE_DESCRIPTION.md`. This is
   the canon the sheet and pages reuse, so skim it before continuing.

   Interactions request:
   - `model`: `gemini-3.5-flash`
   - `system_instruction`: "You are a character designer. Fill the character template
     below from the attached photos and facts; derive the art-style section from the
     comic-style skill." + the blank `CHARACTER_TEMPLATE.md` + the `comic-style` skill
   - `input`: the basic facts (name, age, height, …) + each `source/` photo as an image block
   - `store: false`; read the filled template from `output_text`

   With no photos, write the details into the template by hand instead.

4. Generate the sheet(s) — dry-run first (no API cost), then send:
   ```bash
   python3 scripts/generate_character_sheets.py                       # preview ALL characters
   GEMINI_API_KEY=... python3 scripts/generate_character_sheets.py --send --sleep-seconds 1
   ```
   - With no `--character`, it processes **every** character folder, so a whole cast
     is created in one run. Existing sheets are skipped unless `--overwrite`.
   - `--character "Shane"` targets one; sheets use the `gemini-3-pro-image` model by
     default (pass `--model gemini-3.1-flash-image` for a faster, cheaper run);
     `--style-reference characters/<Name>/<id>_character_sheet.jpg` keeps a new
     character visually consistent with the existing cast.

5. Open each `characters/<Name>/<id>_character_sheet.jpg` and confirm the views and
   identity are consistent before moving on. Regenerate with `--overwrite` if needed.

Both steps use the same Interactions API pattern — the character template and the full
`comic-style` skill in `system_instruction`, the reference photos in the input — so
generating the description (step 3) is just a prompt + model swap
(`gemini-3.5-flash`, text output) over the inputs we already send. The image step
adds `thinking_level=high` (override with `--thinking-level minimal`); every request
uses `store=false` (override with `--store`) so personal photos are not retained
server-side.

## Output

`characters/<Name>/<id>_character_sheet.jpg` for every character — the references
the story stage attaches for visual continuity.

## Next

Hand the cast to the **create-story** skill.
