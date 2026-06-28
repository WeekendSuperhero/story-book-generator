---
name: comic-style
description: >
  Transforms photographs into a vibrant comic-book or graphic-novel illustration style using thick bold black ink outlines on objects/clothing/backgrounds, thinner softer lines on faces, flat cel-shading with soft airbrush transitions on skin, halftone dot textures, light cross-hatching, warm skin tones with rosy cheeks, hand-drawn paper texture, and slight expressive facial exaggeration while keeping the face youthful and flawless. Supports two modes: (1) matching the exact artistic style of a provided reference illustration, or (2) applying a strong consistent house style when the user invokes the skill via text prompt only. Use when the user runs /comic-style or says comic style, graphic novel, comic-book illustration, cel-shaded, halftone, turn this photo into a comic, reference illustration style, etc. Always preserve the subject's exact pose, expression, hair, clothing, accessories, and proportions.
---

# Comic Style

Transform photographs into high-quality comic-book or graphic-novel illustrations with a consistent, stylized aesthetic.

## When to Use This Skill

Use this skill whenever a user wants to convert a photograph into comic / graphic novel style **and references the skill**, either by:
- Running `/comic-style`
- Saying phrases such as "comic style", "graphic novel", "comic-book illustration", "cel-shaded", "in that comic style", "use comic-style", "turn this into a comic", etc.

The skill supports two modes:
- **With reference illustration** (strongly preferred for best results)
- **Text-only** (apply the defined house style)

## The Defined House Style

When no reference illustration is provided, apply this complete artistic style:

- **Line work**: Thick, bold black ink outlines on objects, clothing, and background elements. Use thinner, softer outlines on the face and skin for gentler contours.
- **Shading**: Flat cel-shading with vibrant but slightly muted colors. Use soft blended gradients and airbrush-like transitions on the face and skin — avoid harsh or sharp edges.
- **Textures**: Incorporate halftone dot textures and light cross-hatching for shadows and depth. Use cross-hatching very sparingly on the face.
- **Skin**: Warm tones with brighter highlights and subtle rosy cheeks for a warm, glowing complexion.
- **Overall texture**: Hand-drawn, textured paper feel.
- **Facial treatment**: Slightly exaggerate features for expressiveness (larger eyes, wider smile). Keep the face youthful, smooth, and flawless — no wrinkles, age lines, or rough texture.
- **Fidelity**: Faithfully preserve the subject's exact pose, facial expression, hair, clothing, accessories, and any objects (e.g. drinks) from the input photo. Maintain realistic proportions overall while applying the stylized aesthetic.
- **Quality**: High detail, clean lines. No photorealism.

## Usage Modes

### Mode 1: Reference Illustration (Recommended)

The user provides both:
- A source photograph (the subject to transform)
- A reference illustration that defines the desired artistic style

**Your task**: Construct a prompt for `image_edit` (or equivalent image transformation tool) that instructs the model to match the reference's style as closely as possible while still following the core house style rules. The reference takes priority for specific stylistic choices (line character, color palette, texture treatment, etc.).

### Mode 2: No Reference (House Style)

The user provides only the source photograph and invokes the skill (e.g. "turn this into comic-style" or "/comic-style").

Apply the full **House Style** defined above unless the user's request is ambiguous. If the request is ambiguous, ask for clarification before generating.

## How to Perform the Transformation

1. Identify the input photograph (the main subject to transform).
2. Determine whether a reference illustration was provided.
3. Assess whether the request is clear enough to proceed without clarification.
4. Use the `image_edit` tool (preferred) or equivalent image-to-image tool:
   - Pass the source photo as the primary image.
   - When a reference illustration is available, include it as a style reference / conditioning image.
   - Use a carefully constructed prompt that combines the house style rules with any reference-specific matching instructions.
5. After the first generation, evaluate the result against the house style rules (especially line weight differentiation, skin rendering, halftone/cross-hatching, paper texture, and facial treatment).
6. If key stylistic elements are missing or the result is too photorealistic, re-prompt with stronger emphasis on the missing rules. A second pass often improves fidelity to the style.

## Prompt Construction Guidance

When writing the prompt for the image transformation tool, include:

- Clear instruction to convert the photo into comic-book/graphic-novel style.
- The full set of house style rules (line weights, cel-shading behavior, textures, skin treatment, paper feel, facial exaggeration rules).
- Strong fidelity language: preserve exact pose, expression, hair, clothing, accessories, and proportions from the original photo.
- When a reference is provided: "exactly matching the artistic style, line work, coloring, shading approach, and textural qualities of the reference illustration".
- Always close with: "High detail, clean lines, no photorealism."

## Best Practices

- Prioritize a good reference illustration whenever the user can provide one — matching a specific reference almost always produces better and more intentional results than the house style alone.
- When no reference is available, be strict about applying the house style defined in this document.
- For portraits and close-ups, pay special attention to the face-specific rules (thinner/softer lines + airbrush skin transitions + subtle rosy cheeks).
- If important details from the original photo are lost (clothing, accessories, pose, expression), re-run with reinforced fidelity instructions.
- When the user wants a series or multiple images in the same style, keep the same reference (or consistently use the house style) across generations for visual cohesion.

## Limitations

- The quality of the output is heavily dependent on the underlying image model's ability to follow long, detailed style instructions.
- Highly complex scenes, unusual camera angles, or images with a lot of small text may require multiple attempts or additional guidance.
- Text elements from the original photograph are rarely preserved accurately in this stylized treatment.
- Extremely dark or low-contrast source photos can sometimes result in muddy line work or lost detail.
