#!/usr/bin/env python3
"""Run the character-sheet pipeline in a Google Antigravity managed sandbox.

Local orchestrator (uses the google-genai SDK; install with `uv sync --extra
antigravity`). It uploads a character's source photos via the Files API, runs the
pipeline inside a forked sandbox, and downloads the generated sheet back.

Auth is supplied via the environment's egress-proxy `transform` (the key is injected
at the proxy and never exposed inside the sandbox); the in-sandbox script runs with
`env -u GEMINI_API_KEY ... --allow-proxy-auth`.

VERIFIED LIVE: clone + `uv sync` + a real `--send` generation in the sandbox via this
transform injection.

KNOWN BLOCKER (proven 2026-06): the Gemini **Files API cannot transport private bytes
to/from the sandbox**. Uploaded files are *viewable context only* — they are NOT written
to the sandbox filesystem (the agent searched the whole tree and found nothing), and the
API rejects downloading them ("Only GENERATED files can be downloaded"). That kills BOTH
the inbound photo leg and the outbound sheet-retrieval leg below. A real sandbox run needs
a byte transport that works over the catch-all egress — e.g. signed GCS URLs (private) the
sandbox can curl in/out. Until that is wired, run the pipeline locally (`uv sync` installs
rembg locally). Dry-run by default; pass --send.
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


def _bootstrap_lines(repo: str, branch: str, repo_dir: str, use_agent: bool) -> list[str]:
    if use_agent:
        return [f"The persisted base environment already has the repo + deps + model at {repo_dir}."]
    return [
        "If uv is missing: pip install --break-system-packages uv.",
        f"cd /workspace && git clone {repo} && cd story-book-generator && "
        f"git checkout {branch} && uv sync.",
    ]


def sandbox_task(name: str, repo: str, branch: str, photo_names: list[str], model: str, image_size: str, use_agent: bool) -> str:
    repo_dir = "/workspace/story-book-generator"
    lines: list[str] = [
        f"Run every command from {repo_dir}. Report each step and paste errors verbatim.",
    ]
    lines += _bootstrap_lines(repo, branch, repo_dir, use_agent)
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


def two_pass_task(
    name: str,
    repo: str,
    branch: str,
    photo_names: list[str],
    description_text: str,
    model: str,
    image_size: str,
    use_agent: bool,
) -> str:
    """Drive BOTH passes (reference + styles) in one interaction.

    characters/ is git-ignored, so the clone has no canon: we inline the reviewed
    DESCRIPTION.md. The styles pass needs a *_STYLES.md, which doesn't exist yet — the
    managed agent (a multimodal LLM) authors it by reading the styles template and the
    garment cut-outs directly (no extra API call). Both sheets are uploaded back.
    """
    repo_dir = "/workspace/story-book-generator"
    slug = name.lower()
    desc_name = f"{name.upper()}_DESCRIPTION.md"
    styles_name = f"{name.upper()}_STYLES.md"
    joined = ", ".join(photo_names) if photo_names else "(none)"

    lines: list[str] = [
        f"Run every command from {repo_dir}. Report each step and paste errors verbatim.",
    ]
    lines += _bootstrap_lines(repo, branch, repo_dir, use_agent)
    lines += [
        "",
        f"## Setup canon (characters/ is git-ignored, so write these files)",
        f"1. Save the attached photo(s) ({joined}) into characters/{name}/source/ using their original filenames.",
        f"2. Write characters/{name}/{desc_name} with EXACTLY this content between the markers:",
        "<<<DESCRIPTION",
        description_text.strip(),
        "DESCRIPTION>>>",
        "",
        "## PASS 1 — reference (likeness)",
        f"3. uv run scripts/prep_source_photos.py --character {name} --model birefnet-portrait --out-subdir reference --apply --post-process",
        f"4. env -u GEMINI_API_KEY uv run scripts/generate_character_sheets.py --character {name} "
        f"--kind reference --send --allow-proxy-auth --overwrite --model {model} --image-size {image_size}",
        f"5. Upload characters/{name}/{slug}_character_sheet.* to the Gemini Files API (key is proxy-injected — do NOT add a key). "
        "Print its resource name on its own line EXACTLY as: REFERENCE_FILE=files/xxxx",
        "",
        "## PASS 2 — styles (wardrobe), fed by the reference",
        f"6. uv run scripts/prep_source_photos.py --character {name} --model u2net_cloth_seg --out-subdir styles --apply",
        f"7. Author characters/{name}/{styles_name} yourself: read templates/STYLES_TEMPLATE.md for the structure, "
        f"skills/comic-style/SKILL.md for the house style, characters/{name}/{desc_name} for canon, the just-generated "
        f"reference sheet characters/{name}/{slug}_character_sheet.*, and the garment cut-outs in characters/{name}/styles/*.png. "
        "Fill every section of the styles template from the garments you SEE in those cut-outs (signature outfit, wardrobe, "
        "colors, accessories, preferences, style features). Save it as that markdown file.",
        f"8. env -u GEMINI_API_KEY uv run scripts/generate_character_sheets.py --character {name} "
        f"--kind styles --send --allow-proxy-auth --overwrite --model {model} --image-size {image_size}",
        f"9. Upload characters/{name}/{slug}_styles_sheet.* to the Gemini Files API. "
        "Print its resource name on its own line EXACTLY as: STYLES_FILE=files/xxxx",
        f"10. Also upload characters/{name}/{styles_name}. Print it EXACTLY as: STYLES_MD_FILE=files/xxxx",
        "",
        "Finally print DONE on its own line.",
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
    parser.add_argument("--two-pass", action="store_true", help="Drive both passes (reference + styles) in one run and retrieve both sheets.")
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
        repo_dir = "/workspace/story-book-generator"
        agent = client.agents.create(
            id="storybook-image-env",
            base_agent=BASE_AGENT,
            system_instruction=f"You run the story-book-generator pipeline in {repo_dir}.",
            base_environment={"type": "remote", "sources": [
                {"type": "repository", "source": args.repo, "target": repo_dir},
            ]},
        )
        print("Created agent:", getattr(agent, "id", agent))
        print("Re-run with --agent-id storybook-image-env to fork it per run.")
        return

    if not args.character:
        raise SystemExit("--character is required (or use --setup).")

    name = args.character
    char_dir = args.characters_dir / name
    photos = source_photos(args.characters_dir, name)
    slug = name.lower()

    # What we expect to retrieve, keyed by the marker the sandbox agent prints.
    if args.two_pass:
        description_path = char_dir / f"{name.upper()}_DESCRIPTION.md"
        if not description_path.exists():
            raise SystemExit(f"--two-pass needs an existing {description_path} to inline into the sandbox.")
        description_text = description_path.read_text(encoding="utf-8")
        task = two_pass_task(name, args.repo, args.branch, [p.name for p in photos],
                             description_text, args.model, args.image_size, bool(args.agent_id))
        retrievals = {
            "REFERENCE_FILE": char_dir / f"{slug}_character_sheet.jpg",
            "STYLES_FILE": char_dir / f"{slug}_styles_sheet.jpg",
            "STYLES_MD_FILE": char_dir / f"{name.upper()}_STYLES.md",
        }
    else:
        task = sandbox_task(name, args.repo, args.branch, [p.name for p in photos],
                            args.model, args.image_size, bool(args.agent_id))
        out_path = args.out or (char_dir / f"{slug}_character_sheet.jpg")
        retrievals = {"FILE_NAME": out_path}

    if not args.send:
        print("=== DRY RUN (pass --send to execute) ===")
        print("mode:", "two-pass (reference + styles)" if args.two_pass else "single-pass (reference)")
        print("character:", name, "| photos:", [p.name for p in photos] or "none (house-style)")
        print("agent:", args.agent_id or f"{BASE_AGENT} (clone+uv sync per run)")
        print("environment: egress transform injects x-goog-api-key=<redacted> for", GEMINI_DOMAIN)
        print("will retrieve:", {k: str(v) for k, v in retrievals.items()})
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
    wrote_any = False
    for marker, dest in retrievals.items():
        match = re.search(rf"{marker}=(files/[A-Za-z0-9_-]+)", output)
        if not match:
            print(f"[miss] no {marker} in agent output")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(client.files.download(file=match.group(1)))
        print(f"Wrote {dest} ({dest.stat().st_size} bytes)")
        wrote_any = True
    if not wrote_any:
        print("No files retrieved. Agent report tail:")
        print(output[-1200:])


if __name__ == "__main__":
    main()
