#!/usr/bin/env python3
"""Build a fixed-layout EPUB from story JSON and generated page images."""

from __future__ import annotations

import argparse
import html
import json
import math
import mimetypes
import shutil
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prompt_zones import TEXT_ZONE_PRESETS


EPUB_MIME = "application/epub+zip"
CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
DEFAULT_TEXT_STYLE = {
    "fontFamily": 'Georgia, "Times New Roman", serif',
    "fontWeight": "600",
    "lightColor": "#fffaf0",
    "darkColor": "#1f2933",
    "defaultColor": "#fffaf0",
}
TEXT_BOX_FALLBACKS = {
    zone: (preset["x"], preset["y"], preset["width"], preset["height"])
    for zone, preset in TEXT_ZONE_PRESETS.items()
}


def merged_text_style(story: dict[str, Any], args: argparse.Namespace) -> dict[str, str]:
    configured = {}
    for source in [story.get("textStyle"), story.get("book", {}).get("textStyle")]:
        if isinstance(source, dict):
            configured.update(source)

    style = dict(DEFAULT_TEXT_STYLE)
    for key in ["fontFamily", "fontWeight", "lightColor", "darkColor", "defaultColor"]:
        if configured.get(key):
            style[key] = str(configured[key])

    if args.font_family:
        style["fontFamily"] = args.font_family
    if args.font_weight:
        style["fontWeight"] = args.font_weight
    if args.light_text_color:
        style["lightColor"] = args.light_text_color
    if args.dark_text_color:
        style["darkColor"] = args.dark_text_color
    if args.default_text_color:
        style["defaultColor"] = args.default_text_color

    return style


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def load_layout_overrides(path: Path | None) -> dict[int, dict[str, Any]]:
    if path is None or not path.exists():
        return {}

    data = load_json(path)
    raw_pages = data.get("pages", data)
    overrides: dict[int, dict[str, Any]] = {}

    if isinstance(raw_pages, dict):
        for key, value in raw_pages.items():
            if not isinstance(value, dict):
                continue
            overrides[int(key)] = value
    elif isinstance(raw_pages, list):
        for value in raw_pages:
            if not isinstance(value, dict) or "pageNumber" not in value:
                continue
            overrides[int(value["pageNumber"])] = value

    return overrides


def apply_layout_overrides(
    pages: list[dict[str, Any]],
    overrides: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    patched_pages: list[dict[str, Any]] = []
    for page in pages:
        patched = dict(page)
        override = overrides.get(int(page["pageNumber"]))
        if override:
            for key in ["textZone", "textColor"]:
                if key in override:
                    patched[key] = override[key]

            if isinstance(override.get("textPlacement"), dict):
                placement = dict(patched.get("textPlacement", {}))
                placement.update(override["textPlacement"])
                patched["textPlacement"] = placement
                patched["_forceTextPlacement"] = True

        patched_pages.append(patched)
    return patched_pages


def slugify(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or "book"


def media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if path.suffix.lower() == ".png":
        return "image/png"
    raise ValueError(f"Cannot determine media type for {path}")


def image_size(path: Path) -> tuple[int, int]:
    suffix = path.suffix.lower()
    data = path.read_bytes()

    if suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return width, height

    if suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8"):
        offset = 2
        while offset < len(data):
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9}:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset : offset + 2], "big")
            if segment_length < 2:
                break
            segment_start = offset + 2
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                height = int.from_bytes(data[segment_start + 1 : segment_start + 3], "big")
                width = int.from_bytes(data[segment_start + 3 : segment_start + 5], "big")
                return width, height
            offset += segment_length

    raise ValueError(f"Unsupported or unreadable image dimensions: {path}")


def find_page_image(images_dir: Path, page_number: int) -> Path:
    candidates = [
        images_dir / f"page-{page_number:03d}.jpg",
        images_dir / f"page-{page_number:03d}.jpeg",
        images_dir / f"page-{page_number:03d}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Missing image for page {page_number:03d} in {images_dir}")


def page_text(page: dict[str, Any]) -> str:
    return str(page.get("storyText", "")).strip()


def percent(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def text_box_geometry(
    page: dict[str, Any],
    use_story_placement: bool,
) -> dict[str, Any]:
    placement = page.get("textPlacement", {})
    if not isinstance(placement, dict):
        placement = {}

    zone = page.get("textZone", "bottom")
    fallback = TEXT_BOX_FALLBACKS.get(str(zone), TEXT_BOX_FALLBACKS["bottom"])

    if use_story_placement or page.get("_forceTextPlacement"):
        x = percent(placement.get("xPercent"), fallback[0])
        y = percent(placement.get("yPercent"), fallback[1])
        width = percent(placement.get("widthPercent"), fallback[2])
        height = percent(placement.get("heightPercent"), fallback[3])
        align = html.escape(str(placement.get("textAlign", "center")))
        vertical = str(placement.get("verticalAlign", "middle"))
    else:
        x, y, width, height = fallback
        align = "left" if zone in {"left", "right"} else "center"
        vertical = "middle"

    align_items = {
        "top": "flex-start",
        "middle": "center",
        "bottom": "flex-end",
    }.get(vertical, "center")

    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "align": align,
        "align_items": align_items,
    }


def text_box_style(
    page: dict[str, Any],
    image_file: Path,
    viewport_width: int,
    viewport_height: int,
    max_font_size: int,
    min_font_size: int,
    font_scale: float,
    use_story_placement: bool,
    text_style: dict[str, str],
    auto_text_color: bool,
) -> str:
    geometry = text_box_geometry(page, use_story_placement)
    x = float(geometry["x"])
    y = float(geometry["y"])
    width = float(geometry["width"])
    height = float(geometry["height"])
    align = str(geometry["align"])
    align_items = str(geometry["align_items"])

    font_size = fitted_font_size(
        page_text(page),
        box_width=viewport_width * width / 100 * 0.94,
        box_height=viewport_height * height / 100 * 0.9,
        max_font_size=max_font_size,
        min_font_size=min_font_size,
        font_scale=font_scale,
    )
    text_color, shadow = resolve_text_color_and_shadow(
        page,
        image_file,
        viewport_width,
        viewport_height,
        geometry,
        text_style,
        auto_text_color,
    )
    font_family = css_value(text_style["fontFamily"])
    font_weight = css_value(text_style["fontWeight"])

    return (
        f"left:{x:.4g}%;top:{y:.4g}%;width:{width:.4g}%;height:{height:.4g}%;"
        f"text-align:{align};align-items:{align_items};font-size:{font_size}px;"
        f"font-family:{font_family};font-weight:{font_weight};"
        f"color:{css_value(text_color)};text-shadow:{shadow};"
    )


def fitted_font_size(
    text: str,
    box_width: float,
    box_height: float,
    max_font_size: int,
    min_font_size: int,
    font_scale: float,
) -> int:
    scaled_max = max(min_font_size, int(max_font_size * font_scale))
    scaled_min = max(10, int(min_font_size * font_scale))

    for font_size in range(scaled_max, scaled_min - 1, -1):
        chars_per_line = max(int(box_width / (font_size * 0.62)), 1)
        line_count = wrapped_line_count(text, chars_per_line)
        if line_count * font_size * 1.2 <= box_height:
            return font_size
    return scaled_min


def wrapped_line_count(text: str, chars_per_line: int) -> int:
    lines = 1
    current = 0
    for word in text.split():
        word_length = len(word)
        if word_length > chars_per_line:
            if current:
                lines += 1
                current = 0
            lines += max(math.ceil(word_length / chars_per_line) - 1, 0)
            current = word_length % chars_per_line
            continue
        if current == 0:
            current = word_length
        elif current + 1 + word_length <= chars_per_line:
            current += 1 + word_length
        else:
            lines += 1
            current = word_length
    return lines


def css_value(value: str) -> str:
    allowed = set("#(),.% -_\"'0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return "".join(character for character in str(value) if character in allowed).strip() or "inherit"


def resolve_text_color_and_shadow(
    page: dict[str, Any],
    image_file: Path,
    viewport_width: int,
    viewport_height: int,
    geometry: dict[str, Any],
    text_style: dict[str, str],
    auto_text_color: bool,
) -> tuple[str, str]:
    page_color = page.get("textColor")
    if page_color:
        color = str(page_color)
        return color, shadow_for_color(color)

    placement = page.get("textPlacement", {})
    if isinstance(placement, dict) and placement.get("textColor"):
        color = str(placement["textColor"])
        return color, shadow_for_color(color)

    if auto_text_color:
        luminance = sampled_zone_luminance(image_file, viewport_width, viewport_height, geometry)
        if luminance is not None:
            if luminance >= 0.58:
                return text_style["darkColor"], "0 2px 5px rgba(255,255,255,0.72)"
            return text_style["lightColor"], (
                "0 3px 7px rgba(0,0,0,0.86),0 0 2px rgba(0,0,0,0.92)"
            )

    return text_style["defaultColor"], shadow_for_color(text_style["defaultColor"])


def shadow_for_color(color: str) -> str:
    parsed = parse_hex_color(color)
    if parsed is None:
        return "0 3px 7px rgba(0,0,0,0.86),0 0 2px rgba(0,0,0,0.92)"
    luminance = relative_luminance(*parsed)
    if luminance >= 0.5:
        return "0 3px 7px rgba(0,0,0,0.86),0 0 2px rgba(0,0,0,0.92)"
    return "0 2px 5px rgba(255,255,255,0.72)"


def parse_hex_color(color: str) -> tuple[float, float, float] | None:
    value = color.strip()
    if not value.startswith("#"):
        return None
    value = value[1:]
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    if len(value) != 6:
        return None
    try:
        red = int(value[0:2], 16) / 255
        green = int(value[2:4], 16) / 255
        blue = int(value[4:6], 16) / 255
    except ValueError:
        return None
    return red, green, blue


def relative_luminance(red: float, green: float, blue: float) -> float:
    def channel(value: float) -> float:
        if value <= 0.03928:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def sampled_zone_luminance(
    image_file: Path,
    viewport_width: int,
    viewport_height: int,
    geometry: dict[str, Any],
) -> float | None:
    if shutil.which("magick") is None:
        return None

    crop_width = max(int(viewport_width * float(geometry["width"]) / 100), 1)
    crop_height = max(int(viewport_height * float(geometry["height"]) / 100), 1)
    crop_x = max(int(viewport_width * float(geometry["x"]) / 100), 0)
    crop_y = max(int(viewport_height * float(geometry["y"]) / 100), 0)

    try:
        result = subprocess.run(
            [
                "magick",
                str(image_file),
                "-crop",
                f"{crop_width}x{crop_height}+{crop_x}+{crop_y}",
                "-resize",
                "1x1!",
                "-colorspace",
                "sRGB",
                "-format",
                "%[fx:0.2126*r+0.7152*g+0.0722*b]",
                "info:",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def page_xhtml(
    page: dict[str, Any],
    page_number: int,
    image_filename: str,
    image_file: Path,
    image_alt: str,
    viewport_width: int,
    viewport_height: int,
    language: str,
    max_font_size: int,
    min_font_size: int,
    font_scale: float,
    use_story_placement: bool,
    text_style: dict[str, str],
    auto_text_color: bool,
) -> str:
    escaped_text = html.escape(page_text(page))
    escaped_alt = html.escape(image_alt)
    style = text_box_style(
        page,
        image_file,
        viewport_width,
        viewport_height,
        max_font_size=max_font_size,
        min_font_size=min_font_size,
        font_scale=font_scale,
        use_story_placement=use_story_placement,
        text_style=text_style,
        auto_text_color=auto_text_color,
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{html.escape(language)}" xml:lang="{html.escape(language)}">
  <head>
    <title>Page {page_number}</title>
    <meta name="viewport" content="width={viewport_width},height={viewport_height}"/>
    <link rel="stylesheet" type="text/css" href="../styles/book.css"/>
  </head>
  <body>
    <section class="page" aria-label="Page {page_number}">
      <img class="page-image" src="../images/{html.escape(image_filename)}" alt="{escaped_alt}"/>
      <div class="story-text" style="{html.escape(style, quote=True)}">
        <p>{escaped_text}</p>
      </div>
      <span id="pagebreak-{page_number:03d}" class="pagebreak" epub:type="pagebreak" title="{page_number}"></span>
    </section>
  </body>
</html>
"""


def title_page_xhtml(
    story: dict[str, Any],
    image_filename: str,
    viewport_width: int,
    viewport_height: int,
    language: str,
) -> str:
    book = story["book"]
    title = html.escape(str(book.get("title", "Untitled")))
    subtitle = html.escape(str(book.get("subtitle", "")))
    subtitle_markup = f"\n        <p class=\"title-subtitle\">{subtitle}</p>" if subtitle else ""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{html.escape(language)}" xml:lang="{html.escape(language)}">
  <head>
    <title>{title}</title>
    <meta name="viewport" content="width={viewport_width},height={viewport_height}"/>
    <link rel="stylesheet" type="text/css" href="../styles/book.css"/>
  </head>
  <body>
    <section class="page title-page" epub:type="cover titlepage" aria-label="Title Page">
      <img class="page-image title-image" src="../images/{html.escape(image_filename)}" alt="Cover illustration for {title}"/>
      <div class="title-scrim"></div>
      <div class="title-lockup">
        <h1>{title}</h1>{subtitle_markup}
      </div>
    </section>
  </body>
</html>
"""


def css(viewport_width: int, viewport_height: int, text_style: dict[str, str]) -> str:
    font_family = css_value(text_style["fontFamily"])
    font_weight = css_value(text_style["fontWeight"])
    default_color = css_value(text_style["defaultColor"])
    title_font = max(1, round(viewport_width * 0.065))
    subtitle_font = max(1, round(viewport_width * 0.030))
    title_margin = max(1, round(viewport_width * 0.019))
    return f"""html,
body {{
  margin: 0;
  padding: 0;
  width: {viewport_width}px;
  height: {viewport_height}px;
}}

body {{
  overflow: hidden;
  background: #111;
}}

.page {{
  position: relative;
  width: {viewport_width}px;
  height: {viewport_height}px;
  overflow: hidden;
}}

.page-image {{
  position: absolute;
  inset: 0;
  width: {viewport_width}px;
  height: {viewport_height}px;
  object-fit: fill;
}}

.story-text {{
  position: absolute;
  z-index: 2;
  display: flex;
  justify-content: center;
  box-sizing: border-box;
  padding: 0 1.5%;
  overflow: hidden;
  color: {default_color};
  font-family: {font_family};
  font-weight: {font_weight};
  line-height: 1.16;
  overflow-wrap: break-word;
  hyphens: auto;
  text-shadow:
    0 3px 7px rgba(0, 0, 0, 0.82),
    0 0 2px rgba(0, 0, 0, 0.9);
}}

.story-text p {{
  margin: 0;
  max-width: 100%;
  max-height: 100%;
}}

.pagebreak {{
  display: none;
}}

.title-page {{
  background: #111;
}}

.title-image {{
  opacity: 0.72;
}}

.title-scrim {{
  position: absolute;
  inset: 0;
  z-index: 1;
  background: rgba(0, 0, 0, 0.28);
}}

.title-lockup {{
  position: absolute;
  left: 9%;
  right: 9%;
  bottom: 9%;
  z-index: 2;
  color: #fff;
  font-family: {font_family};
  text-align: center;
  text-shadow:
    0 4px 11px rgba(0, 0, 0, 0.85),
    0 0 2px rgba(0, 0, 0, 0.9);
}}

.title-lockup h1 {{
  margin: 0;
  font-size: {title_font}px;
  line-height: 1.05;
  font-weight: 700;
}}

.title-subtitle {{
  margin: {title_margin}px 0 0;
  font-size: {subtitle_font}px;
  line-height: 1.15;
  font-weight: 500;
}}
"""


def nav_xhtml(story: dict[str, Any], language: str) -> str:
    title = html.escape(story["book"]["title"])
    toc_items = ['        <li><a href="pages/title.xhtml">Title Page</a></li>']
    page_items = []
    for page in story["pages"]:
        page_number = int(page["pageNumber"])
        href = f"pages/page-{page_number:03d}.xhtml"
        toc_items.append(f'        <li><a href="{href}">Page {page_number}</a></li>')
        page_items.append(
            f'        <li><a href="{href}#pagebreak-{page_number:03d}">{page_number}</a></li>'
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{html.escape(language)}" xml:lang="{html.escape(language)}">
  <head>
    <title>{title}</title>
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>{title}</h1>
      <ol>
{chr(10).join(toc_items)}
      </ol>
    </nav>
    <nav epub:type="page-list" id="page-list">
      <h2>Pages</h2>
      <ol>
{chr(10).join(page_items)}
      </ol>
    </nav>
    <nav epub:type="landmarks" hidden="hidden">
      <h2>Landmarks</h2>
      <ol>
        <li><a epub:type="cover" href="pages/title.xhtml">Cover</a></li>
        <li><a epub:type="bodymatter" href="pages/page-001.xhtml">Start</a></li>
      </ol>
    </nav>
  </body>
</html>
"""


def package_opf(
    story: dict[str, Any],
    image_files: list[Path],
    language: str,
    identifier: str,
    modified: str,
    cover_image_name: str | None = None,
) -> str:
    title = html.escape(story["book"]["title"])
    subtitle = story["book"].get("subtitle", "")
    creator = "Generated Storybook Prototype"

    metadata_lines = [
        f'    <dc:identifier id="book-id">urn:uuid:{identifier}</dc:identifier>',
        f'    <dc:title id="title">{title}</dc:title>',
        '    <meta property="title-type" refines="#title">main</meta>',
    ]
    if subtitle:
        metadata_lines.append(f'    <dc:title id="subtitle">{html.escape(str(subtitle))}</dc:title>')
        metadata_lines.append('    <meta property="title-type" refines="#subtitle">subtitle</meta>')
    metadata_lines.extend(
        [
            f"    <dc:language>{html.escape(language)}</dc:language>",
            f"    <dc:creator>{html.escape(creator)}</dc:creator>",
            f"    <meta property=\"dcterms:modified\">{modified}</meta>",
            "    <meta property=\"rendition:layout\">pre-paginated</meta>",
            "    <meta property=\"rendition:orientation\">landscape</meta>",
            "    <meta property=\"rendition:spread\">none</meta>",
        ]
    )
    if cover_image_name:
        # Legacy EPUB 2 cover hint (Kindle / older reading systems still read this).
        metadata_lines.append('    <meta name="cover" content="cover-image"/>')

    manifest = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="css" href="styles/book.css" media-type="text/css"/>',
        '    <item id="title-page" href="pages/title.xhtml" media-type="application/xhtml+xml"/>',
    ]
    if cover_image_name:
        # Dedicated cover image carries the EPUB 3 cover-image property (at most one item may).
        manifest.append(
            f'    <item id="cover-image" href="images/{html.escape(cover_image_name)}" '
            f'media-type="{media_type(Path(cover_image_name))}" properties="cover-image"/>'
        )
    spine = ['    <itemref idref="title-page"/>']
    for page, image_file in zip(story["pages"], image_files, strict=True):
        page_number = int(page["pageNumber"])
        # Only fall back to page 1 for cover-image if there is no dedicated cover.
        image_properties = ' properties="cover-image"' if (cover_image_name is None and page_number == 1) else ""
        manifest.append(
            f'    <item id="page-{page_number:03d}" href="pages/page-{page_number:03d}.xhtml" media-type="application/xhtml+xml"/>'
        )
        manifest.append(
            f'    <item id="img-{page_number:03d}" href="images/{html.escape(image_file.name)}" media-type="{media_type(image_file)}"{image_properties}/>'
        )
        spine.append(f'    <itemref idref="page-{page_number:03d}"/>')

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0"
  xmlns="http://www.idpf.org/2007/opf"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  unique-identifier="book-id"
  prefix="dcterms: http://purl.org/dc/terms/ rendition: http://www.idpf.org/vocab/rendition/#">
  <metadata>
{chr(10).join(metadata_lines)}
  </metadata>
  <manifest>
{chr(10).join(manifest)}
  </manifest>
  <spine page-progression-direction="ltr">
{chr(10).join(spine)}
  </spine>
</package>
"""


def write_epub(build_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w") as archive:
        mimetype_path = build_dir / "mimetype"
        archive.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)

        for path in sorted(build_dir.rglob("*")):
            if path.is_dir() or path == mimetype_path:
                continue
            archive.write(
                path,
                path.relative_to(build_dir).as_posix(),
                compress_type=zipfile.ZIP_DEFLATED,
            )


def smoke_check_epub(path: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names or names[0] != "mimetype":
            errors.append("First ZIP entry must be mimetype")
        else:
            info = archive.getinfo("mimetype")
            if info.compress_type != zipfile.ZIP_STORED:
                errors.append("mimetype must be stored uncompressed")
            if archive.read("mimetype").decode("ascii") != EPUB_MIME:
                errors.append("mimetype content is incorrect")
        for required in ["META-INF/container.xml", "EPUB/package.opf", "EPUB/nav.xhtml"]:
            if required not in names:
                errors.append(f"Missing {required}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story", type=Path, default=Path("outputs/story.json"))
    parser.add_argument("--images-dir", type=Path, default=Path("outputs/page_images"))
    parser.add_argument(
        "--cover-image",
        type=Path,
        help="Dedicated cover illustration (default: <images-dir>/../title_page.jpg if present). "
             "Used as the EPUB cover (properties=\"cover-image\" + legacy <meta name=cover>) and the "
             "title page. If absent, page 1 is used as the cover (legacy behavior).",
    )
    parser.add_argument("--layout-overrides", type=Path, default=Path("outputs/layout_overrides.json"))
    parser.add_argument("--build-dir", type=Path, default=Path("outputs/epub_build"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--max-font-size",
        type=int,
        help="Absolute max body font in px. Overrides --max-font-percent when set.",
    )
    parser.add_argument(
        "--min-font-size",
        type=int,
        help="Absolute min body font in px. Overrides --min-font-percent when set.",
    )
    parser.add_argument(
        "--max-font-percent",
        type=float,
        default=3.0,
        help="Max body font as %% of viewport width (default 3.0 = 48px at 1600px, ~115px at 4K).",
    )
    parser.add_argument(
        "--min-font-percent",
        type=float,
        default=1.5,
        help="Min body font as %% of viewport width (default 1.5).",
    )
    parser.add_argument("--font-scale", type=float, default=1.0)
    parser.add_argument("--font-family")
    parser.add_argument("--font-weight")
    parser.add_argument("--light-text-color")
    parser.add_argument("--dark-text-color")
    parser.add_argument("--default-text-color")
    parser.add_argument(
        "--auto-text-color",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Sample each page's text zone and choose light or dark text automatically.",
    )
    parser.add_argument(
        "--use-story-placement",
        action="store_true",
        help="Use textPlacement values from story JSON instead of builder zone presets.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    story = load_json(args.story)
    pages = story.get("pages", [])
    if not isinstance(pages, list) or not pages:
        raise ValueError("Story JSON must contain a non-empty pages array")
    overrides = load_layout_overrides(args.layout_overrides)
    pages = apply_layout_overrides(pages, overrides)
    story = dict(story)
    story["pages"] = pages

    image_files = [find_page_image(args.images_dir, int(page["pageNumber"])) for page in pages]
    sizes = [image_size(path) for path in image_files]

    # Dedicated cover: --cover-image, else <images-dir>/../title_page.jpg if present.
    cover_path = args.cover_image or (args.images_dir.parent / "title_page.jpg")
    cover_name = cover_path.name if cover_path.exists() else None
    if len(set(sizes)) != 1:
        raise ValueError(f"All page images must have the same dimensions; found {sorted(set(sizes))}")
    viewport_width, viewport_height = sizes[0]
    max_font_px = (
        args.max_font_size
        if args.max_font_size is not None
        else max(1, round(viewport_width * args.max_font_percent / 100))
    )
    min_font_px = (
        args.min_font_size
        if args.min_font_size is not None
        else max(1, round(viewport_width * args.min_font_percent / 100))
    )

    book = story["book"]
    title = str(book["title"])
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(book, sort_keys=True)))
    modified = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    output_path = args.out or Path("outputs") / f"{slugify(title)}.epub"
    text_style = merged_text_style(story, args)

    if args.build_dir.exists():
        shutil.rmtree(args.build_dir)

    (args.build_dir / "META-INF").mkdir(parents=True)
    (args.build_dir / "EPUB" / "pages").mkdir(parents=True)
    (args.build_dir / "EPUB" / "images").mkdir(parents=True)
    (args.build_dir / "EPUB" / "styles").mkdir(parents=True)

    if cover_name:
        shutil.copy2(cover_path, args.build_dir / "EPUB" / "images" / cover_name)

    (args.build_dir / "mimetype").write_text(EPUB_MIME, encoding="ascii")
    (args.build_dir / "META-INF" / "container.xml").write_text(CONTAINER_XML, encoding="utf-8")
    (args.build_dir / "EPUB" / "styles" / "book.css").write_text(
        css(viewport_width, viewport_height, text_style),
        encoding="utf-8",
    )
    (args.build_dir / "EPUB" / "nav.xhtml").write_text(
        nav_xhtml(story, args.language),
        encoding="utf-8",
    )
    (args.build_dir / "EPUB" / "package.opf").write_text(
        package_opf(story, image_files, args.language, identifier, modified, cover_image_name=cover_name),
        encoding="utf-8",
    )
    (args.build_dir / "EPUB" / "pages" / "title.xhtml").write_text(
        title_page_xhtml(
            story,
            cover_name or image_files[0].name,
            viewport_width,
            viewport_height,
            args.language,
        ),
        encoding="utf-8",
    )

    text_style_report: list[dict[str, Any]] = []
    for page, image_file in zip(pages, image_files, strict=True):
        page_number = int(page["pageNumber"])
        shutil.copy2(image_file, args.build_dir / "EPUB" / "images" / image_file.name)
        alt = str(page.get("accessibilityAltText") or f"Illustration for page {page_number}")
        geometry = text_box_geometry(page, args.use_story_placement)
        color, shadow = resolve_text_color_and_shadow(
            page,
            image_file,
            viewport_width,
            viewport_height,
            geometry,
            text_style,
            args.auto_text_color,
        )
        luminance = sampled_zone_luminance(image_file, viewport_width, viewport_height, geometry)
        text_style_report.append(
            {
                "pageNumber": page_number,
                "textZone": page.get("textZone"),
                "color": color,
                "shadow": shadow,
                "sampledLuminance": luminance,
                "placement": geometry,
            }
        )
        (args.build_dir / "EPUB" / "pages" / f"page-{page_number:03d}.xhtml").write_text(
            page_xhtml(
                page,
                page_number,
                image_file.name,
                image_file,
                alt,
                viewport_width,
                viewport_height,
                args.language,
                max_font_px,
                min_font_px,
                args.font_scale,
                args.use_story_placement,
                text_style,
                args.auto_text_color,
            ),
            encoding="utf-8",
        )
    text_style_report_path = output_path.with_suffix(".text-style-report.json")
    text_style_report_path.parent.mkdir(parents=True, exist_ok=True)
    text_style_report_path.write_text(
        json.dumps(text_style_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    write_epub(args.build_dir, output_path)
    errors = smoke_check_epub(output_path)
    if errors:
        for error in errors:
            print(f"EPUB smoke check error: {error}")
        raise SystemExit(1)

    print(f"Wrote {output_path}")
    print(f"Wrote {text_style_report_path}")
    print(f"Pages: {len(pages)}")
    print(f"Layout overrides: {len(overrides)}")
    print(f"Viewport: {viewport_width}x{viewport_height}")
    print("Smoke check: passed")


if __name__ == "__main__":
    main()
