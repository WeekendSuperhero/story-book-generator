# Fixed-Layout Children's Book Story Prompt

You are writing a fixed-layout illustrated children's book for quick-turn EPUB
production.

Return valid JSON only. Do not wrap the JSON in Markdown.

## Book Brief

```json
{{BOOK_BRIEF_JSON}}
```

## Production Intent

This book will become a fixed-layout EPUB. Each story page will become one XHTML
page with one generated illustration and live text placed over reserved negative
space.

The illustration model must not render the story text. The final EPUB generator
will place the text as live, selectable text.

## Reading-Length Targets

Use these targets unless the book brief overrides them:

| Read Time | Target Words | Page Count | Words Per Page |
|---|---:|---:|---:|
| 5 minutes | 600-800 | 16 | 35-55 |
| 10 minutes | 1,200-1,500 | 24-32 | 45-65 |

## Character Canon

Use the following character descriptions as fixed canon. Do not change names,
ages, relationships, personalities, visual anchors, canonical outfits, body
proportions, or art-style rules.

For adult characters, keep all descriptions and illustration prompts
family-friendly. Do not emphasize sexualized anatomy or physical traits unless
they are strictly needed for identity continuity.

For image-generation continuity, each character also has a character sheet path.
When a page includes that character, the later image-generation stage should use
the listed character sheet as visual reference.

{{CHARACTER_REFERENCES}}

## Story Requirements

- Write a complete story with a clear beginning, middle, and ending.
- Make the story feel cohesive, not like disconnected page ideas.
- Use simple, vivid language appropriate for the target age.
- Design the text to be read aloud by a parent, caregiver, or teacher.
- Use gentle repetition only when it improves the read-aloud rhythm.
- Avoid sarcasm, cynicism, cruelty, heavy danger, or frightening material.
- Avoid long paragraphs.
- Do not rhyme unless the brief explicitly requests rhyme.
- Give every page a clear visual subject for illustration.
- Keep each page's text short enough to fit in the requested text zone.
- Do not include text, signs, labels, letters, captions, or readable words in
  illustration prompts.

## Fixed-Layout Requirements

Every page must include:

- `pageNumber`
- `storyText`
- `wordCount`
- `textZone`
- `textPlacement`
- `charactersPresent`
- `illustrationPrompt`
- `imageNegativePrompt`
- `negativeSpaceInstruction`
- `accessibilityAltText`
- `continuityNotes`

Use one of these standard text zones. Prefer `bottom` or `top` for most pages;
use `left` or `right` when the composition naturally leaves that side open. Use
`center` only for quiet title-like pages.

Match `textPlacement` to these default rectangles unless a page truly requires a
small adjustment:

- `bottom`: x=8, y=76, width=84, height=20
- `top`: x=8, y=3, width=84, height=20
- `left`: x=3, y=18, width=30, height=64
- `right`: x=67, y=18, width=30, height=64
- `center`: x=18, y=34, width=64, height=32

The `negativeSpaceInstruction` must name the selected zone and explicitly tell
the image model to leave that exact rectangle calm, continuous, uncluttered,
low-detail, and free of faces, hands, bodies, important props, bright focal
effects, readable text, signs, labels, and story action. If the page suggests a
visible text panel or box, keep it single, uninterrupted, edge-aligned with the
selected zone, and not partly behind characters or props.

Use `colorRecommendation` to describe whether the reserved area is expected to
need light or dark live text. If the generated image will make that impossible
to know in advance, say that the EPUB builder should choose by sampling the
final image.

## Character Use Per Page

For each page, list the characters who appear visually:

```json
{
  "id": "character-id",
  "name": "Character Name",
  "expression": "curious and hopeful",
  "pose": "kneeling beside the window",
  "action": "holding a silver hat",
  "referenceSheet": "characters/name/name_character_sheet.jpg"
}
```

If a character is mentioned in the story text but not visible in the image, do
not include them in `charactersPresent`; mention that in `continuityNotes`
instead.

## Output Shape

Match this top-level shape:

```json
{
  "book": {
    "title": "",
    "subtitle": "",
    "targetAgeRange": "",
    "targetReadTimeMinutes": 5,
    "targetWordCount": 700,
    "actualWordCount": 0,
    "pageCount": 16,
    "summary": "",
    "theme": "",
    "coreLesson": "",
    "tone": ""
  },
  "charactersUsed": [],
  "styleGuide": {
    "visualStyle": "",
    "textRenderingRule": "Do not include any readable text in generated images. EPUB text is live HTML text.",
    "continuityRule": "Use character reference sheets for every page where a character appears."
  },
  "textStyle": {
    "fontFamily": "Georgia, \"Times New Roman\", serif",
    "fontWeight": 600,
    "lightColor": "#fffaf0",
    "darkColor": "#1f2933",
    "defaultColor": "#fffaf0"
  },
  "pages": []
}
```

If a page clearly requires a specific live-text color, add optional
`textColor` to that page. Otherwise omit it and let the EPUB builder choose
light or dark text by sampling the reserved text zone in the generated image.

The complete JSON should validate against this schema:

```json
{{OUTPUT_SCHEMA_JSON}}
```
