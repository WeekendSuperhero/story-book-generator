# Character Design Reference: [Character Name]

This template is the master reference guide for generating consistent images of a
character for storybook creation. Fill in the brackets `[ ]` from the person's
reference photos and the facts they provided.

**The person must always provide:** a character name, a rough age estimate, an approximate
height, hair color, hair texture, and one or two sentences about what they like / who they
are. Everything else is derived from the reference photos.

> Ownership & consent: the reference photographs were provided by, are owned by, and depict
> the person who provided them, with their consent to create this stylized character.

## 1. Core Physical Attributes (Age ~[X])

*   **Age:** [rough age, e.g., 37]
*   **Height:** [approximate height, e.g., 6 ft 3 in]
*   **Skin Tone:** [describe skin tone, e.g., warm medium-brown with golden undertones]
*   **Hair Color:** [e.g., primarily black]
*   **Hair Texture & Style:** [e.g., tightly coiled/curly, short with a clean fade]
*   **Eyes:** [e.g., big, expressive, dark brown eyes]
*   **Distinctive Visual Anchor:** [one memorable feature — e.g., a dimple when smiling,
    a freckle pattern, a tattoo. Teeth are even and natural with NO gaps.]

## 2. Color Palette (must appear on the character sheet)

List the exact colors so the reference sheet can show a labeled swatch panel:

*   **Skin-tone swatches:** [base tone + shadow + highlight, e.g., warm medium-brown base]
*   **Hair color swatch(es):** [e.g., near-black with cooler highlights]
*   **Eye color swatch:** [e.g., dark brown]

The rendered character sheet **must include** a small labeled color-palette panel showing
these hair-color and skin-tone swatches.

## 3. Canonical Outfit
*   **Default Outfit:** [outfit that appears in most scenes]

## 4. Proportion Notes
*   **Proportions (age [X]):** [concrete guidance — head-to-body ratio, limb length, build.
    Keep proportions accurate and true to the person's real age and body.]

## 5. Expression Range
*   **Default/Smiling:** [e.g., warm, joyful smile with even teeth]
*   **Other Expressions:** [e.g., curious, surprised — keep the face soft and expressive]

## 6. Art Style Guidelines (Comic-Style)

Render in the house style supplied by the `comic-style` skill (provided in the generation
request); do not restate it here.

## 7. Consistency / Negative Prompt

To prevent drift between generations, state what should NOT happen:
> "Do not make the character photorealistic, anime-styled, or sharply angular. Keep the
> face youthful, smooth, and flawless. Do not drift from the described hair color, hair
> texture, eye color, skin tone, or proportions. Teeth are even and closed — no gaps
> between teeth. Do not change the character's age or body proportions."

## 8. Reference Sheet Instruction

> "Create a character reference sheet showing front view, side view, three-quarter view,
> full-body pose, close-up face, and three expressions (joyful, curious, surprised) of
> [Character Description]. Include a labeled color-palette panel with the hair color(s) and
> skin-tone swatches. Comic-style house style; teeth even with no gaps; proportions accurate
> to the person's real age and body."

## 9. Master Prompt Template

To generate future scenes, fill the `[BRACKETS]`:
> "[Art Style Prefix] of a [Age]-year-old [Gender], [Height], with [Skin Tone], [Hair Color]
> [Hair Texture], [Eye Description], and [Distinctive Visual Anchor]. [Proportion Notes].
> Wearing [Outfit]. [Expression] and [Action/Pose/Environment]. Comic-style house style.
> Even teeth, no gaps; accurate age and proportions."
