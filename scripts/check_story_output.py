#!/usr/bin/env python3
"""Lightweight checks for generated story JSON.

This is not a full JSON Schema validator. It catches common production issues
without adding third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ALLOWED_ZONES = {"bottom", "top", "left", "right", "center"}
BRIEF_ONLY_KEYS = {
    "targetReadTimeMinutes",
    "targetAgeRange",
    "theme",
    "setting",
    "canvas",
    "textLayout",
    "storyConstraints",
}


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Story output must be a JSON object")
    return data


def check_story(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    book = data.get("book")
    pages = data.get("pages")

    if book is None and pages is None and BRIEF_ONLY_KEYS.intersection(data):
        return [
            "This looks like a book brief, not generated story output.",
            "Use the brief to build a prompt, send that prompt to a story model, then save the model's JSON response as outputs/story.json.",
        ]

    if not isinstance(book, dict):
        errors.append("Missing object: book")
        book = {}

    if not isinstance(pages, list) or not pages:
        errors.append("Missing non-empty array: pages")
        return errors

    expected_page_count = book.get("pageCount")
    if isinstance(expected_page_count, int) and expected_page_count != len(pages):
        errors.append(f"book.pageCount is {expected_page_count}, but pages has {len(pages)} items")

    total_words = 0
    seen_numbers: set[int] = set()

    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            errors.append(f"pages[{index - 1}] must be an object")
            continue

        page_number = page.get("pageNumber")
        if page_number != index:
            errors.append(f"page {index}: pageNumber should be {index}, got {page_number!r}")
        if isinstance(page_number, int):
            seen_numbers.add(page_number)

        story_text = page.get("storyText")
        if not isinstance(story_text, str) or not story_text.strip():
            errors.append(f"page {index}: storyText is required")
            continue

        actual_words = count_words(story_text)
        total_words += actual_words

        declared_words = page.get("wordCount")
        if declared_words != actual_words:
            errors.append(
                f"page {index}: wordCount is {declared_words!r}, actual count is {actual_words}"
            )

        zone = page.get("textZone")
        if zone not in ALLOWED_ZONES:
            errors.append(f"page {index}: invalid textZone {zone!r}")

        for field in ["illustrationPrompt", "imageNegativePrompt", "negativeSpaceInstruction"]:
            value = page.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"page {index}: {field} is required")

        characters = page.get("charactersPresent")
        if not isinstance(characters, list):
            errors.append(f"page {index}: charactersPresent must be an array")
            continue

        for character_index, character in enumerate(characters, start=1):
            if not isinstance(character, dict):
                errors.append(f"page {index}: character {character_index} must be an object")
                continue
            for field in ["id", "name", "expression", "pose", "action", "referenceSheet"]:
                if not isinstance(character.get(field), str):
                    errors.append(f"page {index}: character {character_index} missing {field}")

    declared_total = book.get("actualWordCount")
    if isinstance(declared_total, int) and declared_total != total_words:
        errors.append(f"book.actualWordCount is {declared_total}, actual total is {total_words}")

    if len(seen_numbers) != len(pages):
        errors.append("Duplicate or missing pageNumber values found")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("story_json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    errors = check_story(load_json(args.story_json))

    if errors:
        print("Story output has issues:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print("Story output passed lightweight checks.")


if __name__ == "__main__":
    main()
