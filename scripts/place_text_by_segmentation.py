#!/usr/bin/env python3
"""Decide per-page text placement + color from the character segmentation masks.

For each page:
  1. Take the character mask (rembg; default birefnet-general) and the story's REQUESTED
     text zone/placement. Search only within that requested zone (so we steer toward where
     the story wanted text), and find the LARGEST empty (low-character) rectangle inside it.
  2. Sample the original page pixels in that rectangle and find the MOST PROMINENT (dominant)
     color.
  3. Choose a text color that CONTRASTS with that dominant color (max WCAG contrast among the
     book's light/dark palette), and flag when even the best contrast is marginal (-> scrim).

Writes the results to outputs/story/layout_overrides.json (which the EPUB builder already
applies per page). Local + free. Dry-run by default; pass --write to save.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_fixed_layout_epub as B  # reuse hex parsing + default palette

# Search band per requested zone (x0,y0,x1,y1 in %). We keep the text box inside this band
# so it stays where the story asked, then find the emptiest rectangle within it.
ZONE_BANDS = {
    "bottom": (2, 58, 98, 98),
    "top": (2, 2, 98, 42),
    "left": (2, 6, 45, 94),
    "right": (55, 6, 98, 94),
    "center": (12, 28, 88, 72),
}
GRID_W, GRID_H = 200, 112          # downscaled mask grid for the search
BLOCK_THRESHOLD = 60               # mask value (0-255) above which a cell counts as "character"
MIN_W_PCT, MIN_H_PCT = 34.0, 7.0   # a usable text box must be at least this big


# ---- geometry: largest all-empty rectangle in a boolean "blocked" grid --------

def largest_empty_rect(blocked: np.ndarray) -> tuple[int, int, int, int, int]:
    """Return (area, r0, c0, r1, c1) of the largest rectangle containing no blocked cell."""
    rows, cols = blocked.shape
    heights = [0] * cols
    best = (0, 0, 0, 0, 0)
    for r in range(rows):
        for c in range(cols):
            heights[c] = 0 if blocked[r, c] else heights[c] + 1
        stack: list[tuple[int, int]] = []  # (start_col, height)
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] > h:
                sc, sh = stack.pop()
                area = sh * (c - sc)
                if area > best[0]:
                    best = (area, r - sh + 1, sc, r, c - 1)
                start = sc
            stack.append((start, h))
    return best


# ---- color: dominant color + contrasting choice -------------------------------

def region_colors(image: Image.Image, box_pct: tuple[float, float, float, float]):
    """Return (dominant_rgb, mean_rgb) for the box. Dominant = most frequent quantized color
    (the 'most prominent' hue); mean = average color (robust for the light/dark decision on a
    multi-tone strip, where a single dominant bin can be misleading)."""
    w, h = image.size
    x, y, bw, bh = box_pct
    left, top = int(x / 100 * w), int(y / 100 * h)
    right, bottom = int((x + bw) / 100 * w), int((y + bh) / 100 * h)
    crop = image.crop((left, top, max(right, left + 1), max(bottom, top + 1))).convert("RGB")
    crop = crop.resize((64, 36))
    arr = np.asarray(crop).reshape(-1, 3)
    mean = tuple(int(v) for v in arr.mean(axis=0))
    quant = (arr // 24 * 24 + 12).astype(np.uint8)          # coarse quantize
    colors, counts = np.unique(quant, axis=0, return_counts=True)
    r, g, b = colors[counts.argmax()]
    return (int(r), int(g), int(b)), mean


def rel_luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = rel_luminance(a), rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def pick_contrasting(dominant: tuple[int, int, int], candidates: dict[str, str]) -> tuple[str, float]:
    """Return (hex, contrast_ratio) of the candidate with the highest contrast vs dominant."""
    best_hex, best_cr = None, -1.0
    for hex_value in candidates.values():
        cr = contrast_ratio(dominant, hex_to_rgb(hex_value))
        if cr > best_cr:
            best_hex, best_cr = hex_value, cr
    return best_hex, best_cr


# ---- per page -----------------------------------------------------------------

def mask_for(page_no: int, masks_dir: Path, images_dir: Path, model: str, session_holder: list):
    mask_path = masks_dir / f"page-{page_no:03d}.mask.png"
    if mask_path.exists():
        return Image.open(mask_path).convert("L")
    # compute on the fly (cache the session)
    from rembg import new_session, remove
    if not session_holder:
        session_holder.append(new_session(model))
    img_path = images_dir / f"page-{page_no:03d}.jpg"
    data = remove(img_path.read_bytes(), only_mask=True, session=session_holder[0])
    return Image.open(__import__("io").BytesIO(data)).convert("L")


def choose_box(mask: Image.Image, zone: str, requested: dict,
               bottom_margin: float, max_text_h: float):
    """Largest empty rect within the requested zone, then ANCHOR the text to the bottom edge
    (~bottom_margin from the page bottom) so it hugs the bottom instead of floating in a tall
    empty area. Returns (box (x,y,w,h %), coverage_or_None, verticalAlign, is_fallback)."""
    band = ZONE_BANDS.get(zone, ZONE_BANDS["bottom"])
    small = np.asarray(mask.resize((GRID_W, GRID_H)))
    character = small > BLOCK_THRESHOLD

    bx0 = int(band[0] / 100 * GRID_W); by0 = int(band[1] / 100 * GRID_H)
    bx1 = int(band[2] / 100 * GRID_W); by1 = int(band[3] / 100 * GRID_H)
    blocked = np.ones((GRID_H, GRID_W), dtype=bool)
    blocked[by0:by1, bx0:bx1] = character[by0:by1, bx0:bx1]  # empty only inside the band

    area, r0, c0, r1, c1 = largest_empty_rect(blocked)
    if area > 0:
        x = c0 / GRID_W * 100
        w = (c1 - c0 + 1) / GRID_W * 100
        rect_top = r0 / GRID_H * 100
        rect_bottom = (r1 + 1) / GRID_H * 100
        coverage = float(character[r0:r1 + 1, c0:c1 + 1].mean())
        if w >= MIN_W_PCT and (rect_bottom - rect_top) >= MIN_H_PCT:
            if zone == "bottom":
                box_bottom = min(rect_bottom, 100.0 - bottom_margin)   # hug the page bottom
                box_top = max(rect_top, box_bottom - max_text_h)       # snug strip within the clear area
                return (round(x, 1), round(box_top, 1), round(w, 1), round(box_bottom - box_top, 1)), coverage, "bottom", False
            return (round(x, 1), round(rect_top, 1), round(w, 1), round(rect_bottom - rect_top, 1)), coverage, requested.get("verticalAlign", "middle"), False

    # Fallback: a bottom strip (still hugging the bottom), even if the mid-zone was occupied.
    if zone == "bottom":
        top = max(0.0, 100.0 - bottom_margin - max_text_h)
        return (4.0, round(top, 1), 92.0, round(100.0 - bottom_margin - top, 1)), None, "bottom", True
    rx, ry = requested.get("xPercent"), requested.get("yPercent")
    rw, rh = requested.get("widthPercent"), requested.get("heightPercent")
    if None not in (rx, ry, rw, rh):
        return (rx, ry, rw, rh), None, requested.get("verticalAlign", "middle"), True
    return (4.0, 78.0, 92.0, 18.0), None, "bottom", True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--story", type=Path, default=Path("outputs/story/story.json"))
    p.add_argument("--images-dir", type=Path, default=Path("outputs/story/page_images"))
    p.add_argument("--masks-dir", type=Path, default=Path("outputs/story/segmentation/birefnet-general"))
    p.add_argument("--model", default="birefnet-general", help="rembg model if a mask is missing.")
    p.add_argument("--out", type=Path, default=Path("outputs/story/layout_overrides.json"))
    p.add_argument("--bottom-margin", type=float, default=3.5,
                   help="Gap (%% of page height) between the text and the page bottom (snug to the bottom).")
    p.add_argument("--max-text-height", type=float, default=17.0,
                   help="Max height (%%) of the bottom text strip.")
    p.add_argument("--pages", help="Only these page numbers, e.g. 1,6,12.")
    p.add_argument("--write", action="store_true", help="Write layout_overrides.json (else dry-run).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    story = json.loads(args.story.read_text(encoding="utf-8"))
    text_style = {**B.DEFAULT_TEXT_STYLE, **(story.get("textStyle") or {})}
    candidates = {"light": text_style["lightColor"], "dark": text_style["darkColor"]}
    selected = {int(x) for x in args.pages.split(",") if x.strip()} if args.pages else None
    session_holder: list = []

    overrides = {"pages": {}}
    if args.out.exists():
        try:
            overrides = json.loads(args.out.read_text(encoding="utf-8"))
            overrides.setdefault("pages", {})
        except Exception:
            overrides = {"pages": {}}

    print(f"model={args.model} masks={args.masks_dir} | palette light={candidates['light']} dark={candidates['dark']}")
    print(f"{'page':>4}  {'zone':<7} {'chosen box (x,y,w,h %)':<26} {'cover':>6}  {'domin.':<9}{'mean':<9} {'text':<9} {'contrast':>8}  note")
    for page in story["pages"]:
        n = int(page["pageNumber"])
        if selected is not None and n not in selected:
            continue
        zone = page.get("textZone", "bottom")
        requested = page.get("textPlacement", {})
        mask = mask_for(n, args.masks_dir, args.images_dir, args.model, session_holder)
        box, coverage, valign, is_fallback = choose_box(mask, zone, requested, args.bottom_margin, args.max_text_height)
        note = "fallback: bottom strip (zone occupied)" if is_fallback else ""

        image = Image.open(args.images_dir / f"page-{n:03d}.jpg").convert("RGB")
        dom, mean = region_colors(image, box)
        text_hex, cr = pick_contrasting(mean, candidates)   # decide by mean luminance (robust)
        if cr < 3.0:
            note = (note + "; " if note else "") + "low contrast -> scrim recommended"

        placement = {
            "xPercent": box[0], "yPercent": box[1], "widthPercent": box[2], "heightPercent": box[3],
            "textAlign": requested.get("textAlign", "center"),
            "verticalAlign": valign,
            "fontRole": requested.get("fontRole", "body"),
            "colorRecommendation": text_hex,
        }
        overrides["pages"][str(n)] = {"textPlacement": placement, "textColor": text_hex}

        dom_hex = "#%02x%02x%02x" % dom
        mean_hex = "#%02x%02x%02x" % mean
        cov = f"{coverage * 100:5.1f}%" if coverage is not None else "   -  "
        boxs = f"({box[0]},{box[1]},{box[2]},{box[3]})"
        print(f"{n:>4}  {zone:<7} {boxs:<26} {cov}  {dom_hex:<9}{mean_hex:<9} {text_hex:<9} {cr:7.2f}:1  {note}")

    if args.write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(overrides, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.out}")
    else:
        print("\n[dry-run] pass --write to save layout_overrides.json")


if __name__ == "__main__":
    main()
