#!/usr/bin/env python3
"""Segment storybook pages with rembg to see where the characters are (for text placement).

Uses the illustration-trained `isnet-anime` model by default (better on comic art than the
photo-trained portrait/human models). For each page it saves, into outputs/story/segmentation/:

  <name>.mask.png    grayscale foreground mask (white = character/foreground, black = background)
  <name>.cutout.png  characters isolated on a transparent background

Local + free (no API). First run downloads the model once, then it's cached.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from rembg import new_session, remove


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--images-dir", type=Path, default=Path("outputs/story/page_images"))
    p.add_argument("--title", type=Path, default=Path("outputs/story/title_page.jpg"))
    p.add_argument("--out", type=Path, default=Path("outputs/story/segmentation"))
    p.add_argument("--model", default="isnet-anime", help="rembg model (default isnet-anime).")
    p.add_argument("--pages", help="Only these page numbers, e.g. 1,6,12. Default: all.")
    p.add_argument("--post-process", action="store_true", help="post_process_mask=True (cleaner edges).")
    p.add_argument("--mask-only", action="store_true", help="Save only the mask (skip the cutout) — faster.")
    p.add_argument("--no-title", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    session = new_session(args.model)

    selected = {int(x) for x in args.pages.split(",") if x.strip()} if args.pages else None
    targets: list[tuple[str, Path]] = []
    if args.title.exists() and not args.no_title and selected is None:
        targets.append(("title", args.title))
    for img in sorted(args.images_dir.glob("page-*.jpg")):
        n = int(img.stem.split("-")[1])
        if selected is not None and n not in selected:
            continue
        targets.append((img.stem, img))

    print(f"segmenting {len(targets)} image(s) with rembg '{args.model}' -> {args.out}")
    for name, path in targets:
        data = path.read_bytes()
        mask = remove(data, only_mask=True, post_process_mask=args.post_process, session=session)
        (args.out / f"{name}.mask.png").write_bytes(mask)
        if args.mask_only:
            print(f"  {name}: mask saved")
        else:
            cutout = remove(data, post_process_mask=args.post_process, session=session)
            (args.out / f"{name}.cutout.png").write_bytes(cutout)
            print(f"  {name}: mask + cutout saved")
    print("done")


if __name__ == "__main__":
    main()
