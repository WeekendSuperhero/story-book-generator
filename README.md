# Fixed-Layout Children's Book Story Prototype

This prototype turns a reusable book brief plus a folder of character references
into a story-generation prompt for fixed-layout EPUB production.

It is intentionally generic. A new user can place private files under
`characters/` and keep the same prompt builder. The `characters/` contents are
ignored by Git because character descriptions and reference sheets are user
data.

> **Want an agent to drive this?** The `storybook-builder` skill
> (`skills/storybook-builder/SKILL.md`) runs the whole pipeline. It is composed of
> three focused skills you can also use on their own (under `skills/`):
> `create-character`, `create-story`, and `publish-story`.

> **No local setup?** You can run the image pipeline in a Google Antigravity managed
> environment instead — configure it once and fork it per run (photos uploaded per run
> via the Files API). See the `antigravity-pipeline` skill and
> `uv sync --extra antigravity`.

## Prerequisites

- **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)** (`brew install uv`).
  Install dependencies once — this is the required setup step:

  ```bash
  uv sync
  ```

  uv reads `pyproject.toml` + `uv.lock` and provisions the pinned environment, then
  run scripts with `uv run` (e.g. `uv run scripts/prep_source_photos.py --apply`).
  The image-generation and EPUB scripts use only the standard library; the
  third-party deps (rembg + Pillow) are for the source-photo preprocessing step.
- **Optional:** [ImageMagick](https://imagemagick.org) (the `magick` CLI) for the
  EPUB builder's automatic light/dark text-color selection. Without it, the
  builder simply falls back to the configured default text color.
- Image generation needs a **`GEMINI_API_KEY`**. Copy the example env file and add
  your key (from [AI Studio](https://aistudio.google.com/apikey)):

  ```bash
  cp .env.example .env          # then edit .env and paste your key
  uv run --env-file .env scripts/generate_character_sheets.py --send
  ```

  `.env` is git-ignored. You can also `export GEMINI_API_KEY=...` or prefix a command
  with `GEMINI_API_KEY=...`. Inside Antigravity the key is proxy-injected, so use
  `--allow-proxy-auth` instead of setting it.

## Folder Convention

Each character should live in its own folder:

```text
characters/
  CharacterName/
    CHARACTERNAME_DESCRIPTION.md
    charactername_character_sheet.jpg
```

The description file is story and art canon. The character sheet image is not
embedded into the text prompt, but its path is listed so the image-generation
stage can attach the right reference sheet for each page.

To author a new character, copy `templates/CHARACTER_TEMPLATE.md` into your
character folder and fill in the brackets. `templates/EXAMPLE_CHARACTER_DESCRIPTION.md`
shows a completed example.

## Create Characters (two passes)

Each character produces **two sheets** — a reference (likeness) and a styles
(wardrobe) — plus two canon docs (`<NAME>_DESCRIPTION.md`, `<NAME>_STYLES.md`). The
`create-character` skill drives the full flow; the commands are:

```bash
python3 scripts/new_character.py "Shane"           # scaffold DESCRIPTION + source/
cp /path/to/photos/*.jpg characters/Shane/source/  # add original photos

# Pass 1 — reference (likeness): isolate the person, then render the reference sheet
uv run scripts/prep_source_photos.py --character Shane --model birefnet-portrait --out-subdir reference --apply --post-process
#   gemini-3.5-flash fills SHANE_DESCRIPTION.md from reference/ (see the create-character skill)
uv run scripts/generate_character_sheets.py --character Shane --send                 # -> shane_character_sheet.jpg

# Pass 2 — styles (wardrobe), fed by the reference: isolate the clothing, then render
uv run scripts/prep_source_photos.py --character Shane --model u2net_cloth_seg --out-subdir styles --apply
#   gemini-3.5-flash fills SHANE_STYLES.md from the reference + styles/ (see the create-character skill)
uv run scripts/generate_character_sheets.py --character Shane --kind styles --send   # -> shane_styles_sheet.jpg
```

- `source/` originals are never modified; each pass extracts into `reference/` / `styles/`.
- The `comic-style` house style is injected into every request's `system_instruction`;
  `CHARACTER_TEMPLATE.md` (Pass 1) and `STYLES_TEMPLATE.md` (Pass 2) define the structure.
- Sheets default to `gemini-3-pro-image` at 16:9 / 4K, `thinking_level=high`, `store=false`
  (override with `--model gemini-3.1-flash-image`, `--thinking-level minimal`, `--store`).
  Omit `--send` for a dry-run preview; with no `--character`, every character is processed.
- `scripts/build_character_sheet_prompt.py` emits the reference prompt as Markdown if you
  prefer to paste it into another image tool.

## Generate A Story Prompt

```bash
python3 scripts/build_story_prompt.py \
  --brief brief.example.json \
  --characters-dir characters \
  --out outputs/story_prompt.md
```

For a 10-minute book, use:

```bash
python3 scripts/build_story_prompt.py \
  --brief brief.10-minute.example.json \
  --characters-dir characters \
  --out outputs/story_prompt_10min.md
```

The generated prompt asks for JSON matching
`schemas/story_output.schema.json`.

## Intended Pipeline

```text
character description (templates/) + comic-style house style
  -> character sheet prompt    (scripts/build_character_sheet_prompt.py)
  -> character reference sheet  (any image model)
  -> story prompt
  -> story JSON
  -> page image prompts
  -> fixed-layout EPUB generator
```

For quick-turn production, the story JSON should carry enough page-level
metadata to drive image generation and EPUB layout without manual rewriting:

- story text per page
- character IDs present on each page
- pose, expression, and action per character
- suggested text zone
- negative-space instruction
- illustration prompt
- accessibility alt text

## Export Page Image Prompts

After `outputs/story.json` exists and passes validation, split it into one
image-generation prompt per page:

```bash
python3 scripts/export_image_prompts.py \
  --story outputs/story.json \
  --out-dir outputs/image_prompts
```

Each page prompt lists the character reference sheets that should be attached
for that specific image.

The exported prompts include exact live-text safe rectangles that match the
EPUB builder's default zones. This reduces manual fixes by making the image
model reserve a continuous low-detail area before the EPUB places selectable
HTML text on top.

## Generate Page Images

Prepare a dry-run request for one page without spending an API call:

```bash
python3 scripts/generate_page_images.py --pages 1 --limit 1
```

That writes a redacted request preview to:

```text
outputs/image_requests/page-001.json
```

Generate one page locally with attached character sheets:

```bash
GEMINI_API_KEY=... python3 scripts/generate_page_images.py \
  --pages 1 \
  --send
```

Generate all pages locally:

```bash
GEMINI_API_KEY=... python3 scripts/generate_page_images.py \
  --send \
  --sleep-seconds 1
```

Generated images are written to:

```text
outputs/page_images/page-001.jpg
outputs/page_images/page-002.jpg
...
```

The script uses `gemini-3-pro-image` by default for the highest quality. Pass
`--model gemini-3.1-flash-image` for a faster, cheaper run (it will still upgrade
to `gemini-3-pro-image` for pages with five or more visible recurring characters).

## Test In Google Antigravity

The Antigravity test runner packages the text-only parts of the project as
inline remote-environment sources:

```bash
python3 scripts/run_antigravity_flow.py
```

That command is a dry run. It writes:

```text
outputs/antigravity_request.json
```

To send the flow to Antigravity, set `GEMINI_API_KEY` and pass `--send`:

```bash
GEMINI_API_KEY=... python3 scripts/run_antigravity_flow.py --send
```

For a longer background run:

```bash
GEMINI_API_KEY=... python3 scripts/run_antigravity_flow.py --send --background --poll
```

Character sheet JPG files are not mounted in the first Antigravity test because
inline sources are meant for text payloads. The generated JSON and image prompt
files still preserve the character sheet paths for the later image-generation
stage.

To generate final images inside Antigravity, mount the project through a source
that supports binary assets, such as a Git repository or Cloud Storage bucket.
Then run the image generator in the sandbox:

```bash
cd /workspace/bookgen
python3 scripts/generate_page_images.py \
  --send \
  --allow-proxy-auth \
  --sleep-seconds 1
```

`--allow-proxy-auth` is for Antigravity environments where the Gemini API key is
injected through the environment network `transform` header. This keeps the key
out of sandbox files and environment variables.

## Build Fixed-Layout EPUB

After all page images exist, build the EPUB:

```bash
python3 scripts/build_fixed_layout_epub.py
```

To make live text smaller or larger:

```bash
python3 scripts/build_fixed_layout_epub.py --max-font-percent 2.6
```

Body text (and the title lockup) scale with the page: the default maximum is `3%` of
the viewport width — about `48px` on a 1600px page and `~115px` at 4K — so the book
reads correctly at any resolution. Pass an absolute `--max-font-size <px>` to override.

The default output path is based on the story title:

```text
outputs/the-lantern-that-remembered.epub
```

The EPUB builder uses:

- `outputs/story.json`
- `outputs/page_images/page-001.jpg` through `page-027.jpg`
- one XHTML page per story page
- live HTML story text over each illustration
- fixed-layout EPUB metadata with `rendition:layout` set to `pre-paginated`

The script performs a basic EPUB ZIP smoke check. For final distribution, also
validate with EPUBCheck.

### Text Style And Color

The EPUB builder supports book-level font and color defaults:

```json
{
  "textStyle": {
    "fontFamily": "Georgia, \"Times New Roman\", serif",
    "fontWeight": 600,
    "lightColor": "#fffaf0",
    "darkColor": "#1f2933",
    "defaultColor": "#fffaf0"
  }
}
```

It also supports per-page overrides:

```json
{
  "pageNumber": 2,
  "textColor": "#1f2933"
}
```

By default, the builder samples each page's reserved text zone and chooses
light or dark text automatically. It writes the decisions here:

```text
outputs/the-lantern-that-remembered.text-style-report.json
```

Disable automatic color selection with:

```bash
python3 scripts/build_fixed_layout_epub.py --no-auto-text-color
```

Override font or colors from the command line:

```bash
python3 scripts/build_fixed_layout_epub.py \
  --font-family 'Georgia, "Times New Roman", serif' \
  --light-text-color '#fffaf0' \
  --dark-text-color '#1f2933'
```

### Layout QA Overrides

Visual QA fixes belong in:

```text
outputs/layout_overrides.json
```

The EPUB builder loads this file automatically when it exists. Use it for pages
where the automatic text zone or automatic text color needs a manual correction.

Example:

```json
{
  "pages": {
    "24": {
      "textPlacement": {
        "xPercent": 72,
        "yPercent": 20,
        "widthPercent": 24,
        "heightPercent": 58,
        "textAlign": "left",
        "verticalAlign": "middle"
      }
    },
    "10": {
      "textColor": "#fffaf0"
    }
  }
}
```

The current book uses overrides for pages 2, 8, 10, 17, 24, and 26 based on
visual review. The builder writes the resolved placement/color decisions to:

```text
outputs/the-lantern-that-remembered.text-style-report.json
```

Debug overlays for the corrected text zones are in:

```text
outputs/layout_debug/override-zones-contact.jpg
```
