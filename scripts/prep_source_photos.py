#!/usr/bin/env python3
"""Extract the person from each character's source photos before image generation.

Background removal (via rembg) gives the image model a tight, distraction-free
subject: smaller files and a cleaner likeness. For each photo in
`characters/<Name>/source/`, this writes a cut-out version back into `source/` and
preserves the original under `source/originals/` (which the generators ignore).

Requires the project dependencies (rembg + Pillow). Install and run with uv:

    uv sync
    uv run scripts/prep_source_photos.py --apply

Without --apply it only prints the plan (no files touched, no models downloaded).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from build_story_prompt import find_description_file, slugify


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ORIGINALS_DIRNAME = "originals"


def selected_character_dirs(characters_dir: Path, wanted: set[str] | None) -> list[Path]:
    dirs: list[Path] = []
    for character_dir in sorted(path for path in characters_dir.iterdir() if path.is_dir()):
        if find_description_file(character_dir) is None:
            continue
        if wanted is not None and slugify(character_dir.name) not in wanted:
            continue
        dirs.append(character_dir)
    return dirs


def source_photos(source_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
    )


def already_processed(originals_dir: Path, stem: str) -> bool:
    if not originals_dir.is_dir():
        return False
    return any(path.is_file() and path.stem == stem for path in originals_dir.iterdir())


def clean_photo(
    image_path: Path,
    out_path: Path,
    session,
    jpeg: bool,
    max_dimension: int | None,
    *,
    post_process: bool = False,
    alpha_matting: bool = False,
    am_foreground: int = 240,
    am_background: int = 10,
    am_erode: int = 10,
) -> None:
    from PIL import Image
    from rembg import remove

    image = Image.open(image_path).convert("RGBA")
    cut = remove(
        image,
        session=session,
        post_process_mask=post_process,
        alpha_matting=alpha_matting,
        alpha_matting_foreground_threshold=am_foreground,
        alpha_matting_background_threshold=am_background,
        alpha_matting_erode_size=am_erode,
    )

    bbox = cut.getchannel("A").getbbox()
    if bbox:
        cut = cut.crop(bbox)

    if max_dimension and max(cut.size) > max_dimension:
        scale = max_dimension / max(cut.size)
        cut = cut.resize((max(1, round(cut.width * scale)), max(1, round(cut.height * scale))))

    if jpeg:
        background = Image.new("RGB", cut.size, (255, 255, 255))
        background.paste(cut, mask=cut.getchannel("A"))
        background.save(out_path, quality=90)
    else:
        cut.save(out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--characters-dir", type=Path, default=Path("characters"))
    parser.add_argument(
        "--character",
        action="append",
        dest="characters",
        help="Character name or id to process (repeatable). Defaults to all characters.",
    )
    parser.add_argument(
        "--model",
        default="u2net_human_seg",
        help="rembg model (default: u2net_human_seg, tuned for people).",
    )
    parser.add_argument(
        "--jpeg",
        action="store_true",
        help="Write a JPEG on a white background instead of a transparent PNG.",
    )
    parser.add_argument(
        "--max-dimension",
        type=int,
        help="Resize so the longest edge is at most this many pixels.",
    )
    parser.add_argument(
        "--post-process",
        action="store_true",
        help="Clean the mask (rembg post_process_mask) — removes specks/holes.",
    )
    parser.add_argument(
        "--alpha-matting",
        action="store_true",
        help="Alpha matting for softer, more accurate edges (slower).",
    )
    parser.add_argument("--am-foreground", type=int, default=240, help="Alpha-matting foreground threshold (default 240).")
    parser.add_argument("--am-background", type=int, default=10, help="Alpha-matting background threshold (default 10).")
    parser.add_argument("--am-erode", type=int, default=10, help="Alpha-matting erode size (default 10).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually process photos and move originals. Without it, print the plan only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.characters_dir.exists():
        raise FileNotFoundError(f"Character directory not found: {args.characters_dir}")

    wanted = {slugify(value) for value in args.characters} if args.characters else None
    character_dirs = selected_character_dirs(args.characters_dir, wanted)
    if not character_dirs:
        raise ValueError(f"No matching characters found in {args.characters_dir}")

    extension = "jpg" if args.jpeg else "png"

    session = None
    if args.apply:
        try:
            from rembg import new_session
        except ImportError as error:
            raise SystemExit(
                "rembg is not installed. Run `uv sync` (or `pip install 'rembg[cpu]' pillow`)."
            ) from error
        session = new_session(args.model)

    planned = 0
    processed = 0
    for character_dir in character_dirs:
        source_dir = character_dir / "source"
        if not source_dir.is_dir():
            continue
        originals_dir = source_dir / ORIGINALS_DIRNAME

        for photo in source_photos(source_dir):
            if already_processed(originals_dir, photo.stem):
                continue
            out_path = source_dir / f"{photo.stem}.{extension}"
            planned += 1

            if not args.apply:
                print(f"[plan] {photo.name} -> {out_path.name}  (original -> {ORIGINALS_DIRNAME}/{photo.name})")
                continue

            originals_dir.mkdir(exist_ok=True)
            shutil.copy2(photo, originals_dir / photo.name)
            clean_photo(
                photo, out_path, session, args.jpeg, args.max_dimension,
                post_process=args.post_process, alpha_matting=args.alpha_matting,
                am_foreground=args.am_foreground, am_background=args.am_background,
                am_erode=args.am_erode,
            )
            if out_path != photo and photo.exists():
                photo.unlink()
            print(f"Cleaned {out_path.name}  (original -> {ORIGINALS_DIRNAME}/{photo.name})")
            processed += 1

    if args.apply:
        print(f"Processed {processed} photo(s).")
    else:
        print(f"Plan: {planned} photo(s). Pass --apply to process (after `uv sync`).")


if __name__ == "__main__":
    main()
