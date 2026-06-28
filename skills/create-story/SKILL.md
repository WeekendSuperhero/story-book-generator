---
name: create-story
description: >
  Turn a story prompt (which characters to use plus what the story is about) into a
  finished, validated story and all of its illustrations, including the cover/title
  art. Generates the story JSON from a brief, then one illustration per page using
  the character reference sheets. Use when the user wants to write or generate a
  story, create the pages, or make the illustrations for a book. Requires characters
  to already exist (see the create-character skill) and a GEMINI_API_KEY for images.
---

# Create Story

From a prompt naming the cast and the story idea, produce `outputs/story.json` and
`outputs/page_images/` (page 1's illustration doubles as the cover art).

## Input you need

- Which characters to use — their slug ids, e.g. `bridget`, `shane`.
- The story idea / plot, theme, and tone.
- Read length (5 or 10 minutes) and target age range.

## Steps

1. Write the brief. Copy `brief.example.json` to `brief.json` and set
   `targetReadTimeMinutes` (5 or 10), `targetAgeRange`, `theme`, `setting`, `tone`,
   `coreLesson`, and `characterSelection.includeCharacterIds` to the cast. Leave
   `includeCharacterIds` empty to let the story choose the best cast.

2. Build the story prompt:
   ```bash
   python3 scripts/build_story_prompt.py --brief brief.json --out outputs/story_prompt.md
   ```

3. Write the story. Read `outputs/story_prompt.md` and produce `outputs/story.json`
   conforming to `schemas/story_output.schema.json`. Rules the checker enforces:
   - per-page `wordCount` equals the real word count of `storyText`;
   - `book.pageCount` equals the number of pages; `book.actualWordCount` equals the
     sum of page word counts;
   - each page has `textZone`, a full `textPlacement`, `charactersPresent[]` whose
     `referenceSheet` paths point at the real sheets, plus `illustrationPrompt`,
     `imageNegativePrompt`, `negativeSpaceInstruction`, `accessibilityAltText`, and
     `continuityNotes`;
   - keep faces and action out of each page's live-text-safe zone;
   - make page 1's illustration cover-worthy and set `book.title` / `book.subtitle`.

   Validate and fix until it passes:
   ```bash
   python3 scripts/check_story_output.py outputs/story.json
   ```

4. Generate the illustrations (one per page; page 1 = cover art):
   ```bash
   python3 scripts/generate_page_images.py --pages 1 --limit 1        # dry-run a single page
   GEMINI_API_KEY=... python3 scripts/generate_page_images.py --send --sleep-seconds 1
   ```
   The script attaches each page's character sheets, uses the `gemini-3-pro-image`
   model by default (pass `--model gemini-3.1-flash-image` to downgrade), and writes
   `outputs/page_images/page-XXX.jpg` (all pages share one resolution).

## About the title page

The title page/cover is assembled at publish time from page 1's illustration with the
book title and subtitle laid over it (handled by the **publish-story** skill). So
ensure page 1 reads well as a cover and that `book.title` / `book.subtitle` are set.
A *separate* dedicated cover illustration is a small enhancement — ask if you want it.

## Output

`outputs/story.json` (validated) and `outputs/page_images/page-XXX.jpg`.

## Next

Hand off to the **publish-story** skill.
