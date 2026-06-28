---
name: publish-story
description: >
  Assemble a validated story and its page images into a finished fixed-layout EPUB
  the user can open, review, and read. Builds the title page/cover (page 1 art plus a
  title overlay), lays selectable text over each illustration, runs a smoke check, and
  writes the .epub. Use when the user wants to publish, build the EPUB, export the
  book, or produce something readable. Requires outputs/story.json and
  outputs/page_images/ (see the create-story skill).
---

# Publish Story

Produce `outputs/<title>.epub` from the story JSON and page images so the user can
read and review the book.

## Prerequisites

- `outputs/story.json` (validated) and `outputs/page_images/page-XXX.jpg` for every page.
- Optional: ImageMagick (`magick`) so the builder auto-picks light or dark text per page.

## Steps

1. Build the EPUB:
   ```bash
   python3 scripts/build_fixed_layout_epub.py
   ```
   This creates the title page (page 1 art + title/subtitle lockup over a scrim), one
   fixed-layout XHTML page per illustration with selectable story text laid on top,
   fixed-layout (`pre-paginated`) metadata, runs a ZIP smoke check, and writes
   `outputs/<title>.text-style-report.json`.

2. Review. Open `outputs/<title>.epub` in any EPUB reader and check text legibility
   and placement on every page.

3. Fix problem pages without regenerating images: add entries to
   `outputs/layout_overrides.json` (text placement and/or color) and rerun step 1.
   Adjust global text size with `--max-font-size`.

4. For distribution, also validate with EPUBCheck.

## Output

`outputs/<title>.epub` — the readable, reviewable book.
