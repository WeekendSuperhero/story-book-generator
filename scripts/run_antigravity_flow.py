#!/usr/bin/env python3
"""Run the children's book flow inside a Google Antigravity remote environment.

By default this script builds the Interactions API request and writes it to disk
without sending it. Use --send when GEMINI_API_KEY is available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
AGENT_ID = "antigravity-preview-05-2026"
REMOTE_ROOT = "/workspace/bookgen"
MAX_INLINE_FILE_BYTES = 1_000_000
MAX_INLINE_TOTAL_BYTES = 2_000_000

SOURCE_FILES = [
    "README.md",
    "brief.example.json",
    "brief.10-minute.example.json",
    "prompts/story_generation_prompt.md",
    "schemas/story_output.schema.json",
    "scripts/build_story_prompt.py",
    "scripts/check_story_output.py",
    "scripts/prompt_zones.py",
    "scripts/export_image_prompts.py",
    "scripts/generate_page_images.py",
    "scripts/build_fixed_layout_epub.py",
    "outputs/story.json",
    "outputs/layout_overrides.json",
]


def discover_character_descriptions(root: Path) -> list[Path]:
    characters_dir = root / "characters"
    if not characters_dir.exists():
        return []
    return sorted(
        path
        for path in characters_dir.glob("*/*.md")
        if path.name.lower().endswith("_description.md")
    )


def read_inline_source(path: Path, root: Path) -> dict[str, str]:
    raw = path.read_bytes()
    if len(raw) > MAX_INLINE_FILE_BYTES:
        raise ValueError(f"{path} is too large for Antigravity inline source")

    relative = path.relative_to(root)
    return {
        "type": "inline",
        "content": raw.decode("utf-8"),
        "target": f"{REMOTE_ROOT}/{relative.as_posix()}",
    }


def build_sources(root: Path) -> list[dict[str, str]]:
    paths = [root / path for path in SOURCE_FILES]
    paths.extend(discover_character_descriptions(root))

    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing required source files:\n" + "\n".join(f"- {path}" for path in missing)
        )

    sources = [read_inline_source(path, root) for path in paths]
    total = sum(len(source["content"].encode("utf-8")) for source in sources)
    if total > MAX_INLINE_TOTAL_BYTES:
        raise ValueError(
            f"Inline sources total {total} bytes, above Antigravity's {MAX_INLINE_TOTAL_BYTES} byte limit"
        )
    return sources


def build_task() -> str:
    return f"""
You are testing a fixed-layout children's book production prototype in a remote
Linux sandbox.

Project root: {REMOTE_ROOT}

Important context:
- Character sheet JPG files are intentionally not mounted in this test because
  inline Antigravity sources are text-only. The JSON should still preserve their
  relative paths for the later image-generation handoff.
- Do not rewrite the project unless a command fails because of a simple path
  issue. The goal is to verify the current flow.

Run these commands from {REMOTE_ROOT}:

1. `python3 --version`
2. `python3 scripts/build_story_prompt.py --brief brief.10-minute.example.json --characters-dir characters --out outputs/antigravity_story_prompt_10min.md`
3. `python3 scripts/check_story_output.py outputs/story.json`
4. `python3 scripts/export_image_prompts.py --story outputs/story.json --out-dir outputs/image_prompts_antigravity`
5. Print a concise report with:
   - whether each command passed
   - the generated story title, page count, and actual word count from outputs/story.json
   - the number of exported image prompt files
   - the first 40 lines of outputs/image_prompts_antigravity/page-001.md
""".strip()


def build_request(root: Path) -> dict[str, Any]:
    return {
        "agent": AGENT_ID,
        "input": [{"type": "text", "text": build_task()}],
        "environment": {
            "type": "remote",
            "sources": build_sources(root),
        },
    }


def request_json(method: str, url: str, api_key: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "Api-Revision": "2026-05-20",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def send_request(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    return request_json("POST", API_URL, api_key, payload)


def poll_interaction(interaction_id: str, api_key: str, interval_seconds: int) -> dict[str, Any]:
    url = f"{API_URL}/{interaction_id}"
    while True:
        response = request_json("GET", url, api_key)
        status = response.get("status")
        print(f"Antigravity status: {status}")
        if status not in {"in_progress", "queued"}:
            return response
        time.sleep(interval_seconds)


def extract_model_output(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for step in response.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for item in step.get("content", []):
            if item.get("type") == "text" and item.get("text"):
                chunks.append(item["text"])
    return "\n\n".join(chunks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--request-out", type=Path, default=Path("outputs/antigravity_request.json"))
    parser.add_argument("--response-out", type=Path, default=Path("outputs/antigravity_response.json"))
    parser.add_argument("--send", action="store_true", help="Send the request to the Gemini Interactions API")
    parser.add_argument("--background", action="store_true", help="Run the Antigravity interaction in background mode")
    parser.add_argument("--poll", action="store_true", help="Poll a background interaction until it finishes")
    parser.add_argument("--poll-interval", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    payload = build_request(root)

    if args.background:
        payload["background"] = True

    args.request_out.parent.mkdir(parents=True, exist_ok=True)
    args.request_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    source_count = len(payload["environment"]["sources"])
    source_bytes = sum(
        len(source["content"].encode("utf-8"))
        for source in payload["environment"]["sources"]
    )
    print(f"Wrote request payload to {args.request_out}")
    print(f"Inline sources: {source_count} files, {source_bytes} bytes")

    if not args.send:
        print("Dry run only. Set GEMINI_API_KEY and pass --send to run Antigravity.")
        return

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not set.", file=sys.stderr)
        raise SystemExit(2)

    response = send_request(payload, api_key)
    if args.background and args.poll and response.get("id"):
        response = poll_interaction(response["id"], api_key, args.poll_interval)

    args.response_out.parent.mkdir(parents=True, exist_ok=True)
    args.response_out.write_text(json.dumps(response, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote Antigravity response to {args.response_out}")

    output_text = response.get("output_text") or extract_model_output(response)
    if output_text:
        print("\n--- Antigravity output ---")
        print(output_text)


if __name__ == "__main__":
    main()
