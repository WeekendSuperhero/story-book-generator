#!/usr/bin/env python3
"""Scaffold a new character from the template.

Creates `characters/<Name>/<NAME>_DESCRIPTION.md` (copied from
`templates/CHARACTER_TEMPLATE.md`) and an empty `source/` folder for reference
photos. This is the on-ramp from the character template + comic-style skill to a
generated character sheet:

    python3 scripts/new_character.py "Shane"
    # fill in the description, drop photos in characters/Shane/source/, then:
    GEMINI_API_KEY=... python3 scripts/generate_character_sheets.py --character Shane --send
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def description_filename(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    return f"{slug or 'CHARACTER'}_DESCRIPTION.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help='Character name, e.g. "Shane". Becomes the folder name.')
    parser.add_argument("--characters-dir", type=Path, default=Path("characters"))
    parser.add_argument("--template", type=Path, default=Path("templates/CHARACTER_TEMPLATE.md"))
    parser.add_argument("--force", action="store_true", help="Overwrite an existing description.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.template.exists():
        raise FileNotFoundError(f"Template not found: {args.template}")

    character_dir = args.characters_dir / args.name
    source_dir = character_dir / "source"
    description_path = character_dir / description_filename(args.name)

    if description_path.exists() and not args.force:
        raise SystemExit(f"{description_path} already exists. Pass --force to overwrite.")

    character_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    template_text = args.template.read_text(encoding="utf-8")
    template_text = template_text.replace("[Character Name]", args.name, 1)
    description_path.write_text(template_text, encoding="utf-8")

    print(f"Created {description_path}")
    print(f"Created {source_dir}/ (drop reference photos here for photo-based likeness)")
    print()
    print("Next:")
    print(f"  1. Fill in {description_path} (age, skin tone, hair, outfit, proportions).")
    print(f"  2. Optional: copy reference photos into {source_dir}/")
    print("  3. Generate the sheet:")
    print(f'       GEMINI_API_KEY=... python3 scripts/generate_character_sheets.py --character "{args.name}" --send')


if __name__ == "__main__":
    main()
