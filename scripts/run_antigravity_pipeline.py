#!/usr/bin/env python3
"""Run the character-sheet pipeline in a Google Antigravity managed sandbox.

Local orchestrator (uses the google-genai SDK; install with `uv sync --extra
antigravity`). It uploads a character's source photos via the Files API, runs the
pipeline inside a forked sandbox, and downloads the generated sheet back.

Auth is supplied via the environment's egress-proxy `transform` (the key is injected
at the proxy and never exposed inside the sandbox); the in-sandbox script runs with
`env -u GEMINI_API_KEY ... --allow-proxy-auth`.

VERIFIED LIVE: clone + `uv sync` + a real `--send` generation in the sandbox via this
transform injection. NEEDS LIVE VALIDATION: the Files-API photo upload as interaction
input and the upload-then-download retrieval leg. Dry-run by default; pass --send.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

REPO_DEFAULT = "https://github.com/WeekendSuperhero/story-book-generator"
BRANCH_DEFAULT = "weekendsuperhero/character_build"
BASE_AGENT = "antigravity-preview-05-2026"
GEMINI_DOMAIN = "generativelanguage.googleapis.com"
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def egress_environment(key: str) -> dict:
    """Per-interaction environment that injects the API key at the egress proxy."""
    return {
        "type": "remote",
        "network": {"allowlist": [
            {"domain": GEMINI_DOMAIN, "transform": [{"x-goog-api-key": key}]},
            {"domain": "*"},  # catch-all so git/pip/uv still work
        ]},
    }


def source_photos(characters_dir: Path, name: str) -> list[Path]:
    source = characters_dir / name / "source"
    if not source.is_dir():
        return []
    return sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS)


def sandbox_task(name: str, repo: str, branch: str, photo_names: list[str], model: str, image_size: str, use_agent: bool) -> str:
    repo_dir = "/workspace/story-book-generator"
    lines: list[str] = [
        f"Run every command from {repo_dir}. Report each step and paste errors verbatim.",
    ]
    if not use_agent:
        lines += [
            "If uv is missing: pip install --break-system-packages uv.",
            f"cd /workspace && git clone {repo} && cd story-book-generator && "
            f"git checkout {branch} && uv sync.",
        ]
    else:
        lines.append(f"The persisted base environment already has the repo + deps + model at {repo_dir}.")
    if photo_names:
        joined = ", ".join(photo_names)
        lines.append(
            f"Save the attached photo(s) ({joined}) into characters/{name}/source/ using their "
            "original filenames, then run: uv run scripts/prep_source_photos.py "
            f"--character {name} --apply."
        )
    lines += [
        f"Run EXACTLY: env -u GEMINI_API_KEY uv run scripts/generate_character_sheets.py "
        f"--character {name} --send --allow-proxy-auth --overwrite --model {model} --image-size {image_size}",
        f"Upload the generated characters/{name}/<id>_character_sheet.* to the Gemini Files API "
        "(the key is injected by the egress proxy — do NOT add a key).",
        "Print the returned file resource name on its own line EXACTLY as: FILE_NAME=files/xxxx",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--character", help="Character name (folder under --characters-dir).")
    parser.add_argument("--characters-dir", type=Path, default=Path("characters"))
    parser.add_argument("--repo", default=REPO_DEFAULT)
    parser.add_argument("--branch", default=BRANCH_DEFAULT)
    parser.add_argument("--agent-id", help="Use a persisted managed agent (forks its base env) instead of cloning per run.")
    parser.add_argument("--setup", action="store_true", help="Create+persist a managed agent from the repo, print its id, and exit.")
    parser.add_argument("--out", type=Path, help="Where to write the downloaded sheet (default: characters/<Name>/<name>_character_sheet.jpg).")
    parser.add_argument("--model", default="gemini-3-pro-image")
    parser.add_argument("--image-size", default="4K")
    parser.add_argument("--send", action="store_true", help="Actually call Antigravity. Without it, print the plan (key redacted).")
    parser.add_argument("--poll-seconds", type=float, default=12.0)
    parser.add_argument("--max-polls", type=int, default=60)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set (needed to authenticate and to inject into the sandbox).")

    if args.setup:
        if not args.send:
            print("[dry-run] Would create a managed agent from", args.repo, "(pass --send to create).")
            return
        from google import genai
        client = genai.Client()
        agent = client.agents.create(
            id="storybook-image-env",
            base_agent=BASE_AGENT,
            system_instruction="You run the story-book-generator pipeline in /workspace/repo.",
            base_environment={"type": "remote", "sources": [
                {"type": "repository", "source": args.repo, "target": "/workspace/repo"},
            ]},
        )
        print("Created agent:", getattr(agent, "id", agent))
        print("Re-run with --agent-id storybook-image-env to fork it per run.")
        return

    if not args.character:
        raise SystemExit("--character is required (or use --setup).")

    name = args.character
    photos = source_photos(args.characters_dir, name)
    out_path = args.out or (args.characters_dir / name / f"{name.lower()}_character_sheet.jpg")
    task = sandbox_task(name, args.repo, args.branch, [p.name for p in photos], args.model, args.image_size, bool(args.agent_id))

    if not args.send:
        print("=== DRY RUN (pass --send to execute) ===")
        print("character:", name, "| photos:", [p.name for p in photos] or "none (house-style)")
        print("agent:", args.agent_id or f"{BASE_AGENT} (clone+uv sync per run)")
        print("environment: egress transform injects x-goog-api-key=<redacted> for", GEMINI_DOMAIN)
        print("output ->", out_path)
        print("--- sandbox task ---")
        print(task)
        return

    from google import genai
    client = genai.Client()

    input_blocks: list[dict] = []
    for photo in photos:
        f = client.files.upload(file=str(photo))
        input_blocks.append({"type": "image", "file_uri": f.uri, "mime_type": f.mime_type})
    input_blocks.append({"type": "text", "text": task})

    run = client.interactions.create(
        agent=args.agent_id or BASE_AGENT,
        environment=egress_environment(key),
        input=input_blocks,
        background=True,
    )
    print("interaction:", run.id)

    for _ in range(args.max_polls):
        run = client.interactions.get(run.id)
        if run.status in ("completed", "failed", "cancelled"):
            break
        time.sleep(args.poll_seconds)
    print("status:", run.status)

    output = getattr(run, "output_text", None) or ""
    match = re.search(r"FILE_NAME=(files/[A-Za-z0-9_-]+)", output)
    if run.status == "completed" and match:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(client.files.download(file=match.group(1)))
        print("Wrote", out_path, f"({out_path.stat().st_size} bytes)")
    else:
        print("No sheet retrieved. Agent report tail:")
        print(output[-800:])


if __name__ == "__main__":
    main()
