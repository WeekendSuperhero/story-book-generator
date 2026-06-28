# Fixed-Layout Children's Book Story Prototype

This prototype turns a reusable book brief plus a folder of character references
into a story-generation prompt for fixed-layout EPUB production.

It is intentionally generic. A new user can place private files under
`characters/` and keep the same prompt builder. The `characters/` contents are
ignored by Git because character descriptions and reference sheets are user
data.

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
character descriptions + character sheets
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

The script uses `gemini-3.1-flash-image` by default and automatically switches
to `gemini-3-pro-image` for pages with five or more visible recurring
characters.

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
python3 scripts/build_fixed_layout_epub.py --max-font-size 44
```

The default maximum font size is `48px`; the first EPUB pass used much larger
text and could overflow in some readers.

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
