---
name: antigravity-pipeline
description: >
  Run the storybook image pipeline inside a Google Antigravity managed environment
  instead of installing the heavy dependencies (uv, rembg, onnxruntime, the
  segmentation model) locally. Configure a base environment once, then fork it per
  run; upload the private photos per run via the Files API. Use when the user wants to
  generate character sheets or page images without local setup, or "run it in
  Antigravity". Requires Antigravity managed-agents (preview) access and a
  GEMINI_API_KEY. Pairs with create-character / create-story / publish-story.
---

# Storybook Pipeline in Antigravity

Move the heavy, stateful work (uv + rembg + onnxruntime + the 176 MB model + 4K image
generation) into a Google-hosted Linux sandbox. Configure it **once**, then **fork**
that ready environment for every run — nothing heavy installs on the local machine.

> Managed agents are **preview** and only `antigravity-preview-05-2026` is supported
> as the base agent. Confirm the exact SDK call signatures against the official docs
> (linked below) — this skill encodes the *project-specific* recipe, not the full API.

## Prerequisites

- Antigravity managed-agents (preview) access on the key, and a `GEMINI_API_KEY`.
- The local orchestrator deps: `uv sync --extra antigravity` (installs `google-genai`).
- The repo is public, so the sandbox can clone it. The **private photos are not in the
  repo** (git-ignored) — they're uploaded per run via the Files API.

## Step 1 — Configure the base environment (once)

Provision a sandbox that clones the repo, runs `uv sync`, and warms the segmentation
model, then capture its `environment_id` so it can be forked later.

```python
from google import genai
client = genai.Client()

boot = client.interactions.create(
    agent="antigravity-preview-05-2026",
    environment="remote",
    base_environment={
        "type": "remote",
        "sources": [{
            "type": "repository",
            "source": "https://github.com/WeekendSuperhero/story-book-generator",
            "target": "/workspace/repo",
        }],
    },
    input=(
        "cd /workspace/repo && uv sync && "
        "uv run python -c \"from rembg import new_session; new_session('u2net_human_seg')\" "
        "&& echo CONFIGURED"
    ),
    background=True,
)
# poll boot.id until status == completed, then:
env_id = boot.environment_id
```

## Step 2 — Persist a managed agent that forks the configured env

```python
agent = client.agents.create(
    id="storybook-image-env",
    base_agent="antigravity-preview-05-2026",
    system_instruction="You run the story-book-generator pipeline in /workspace/repo.",
    base_environment=env_id,   # fork this fully-configured snapshot every run
)
```

Now each invocation **forks** the base env, so `uv` deps and the cached model are
already present — no re-install per run.

## Step 3 — Per run: upload photos, inject the key, fork

**Auth (verified live).** The sandbox's auto-injected `GEMINI_API_KEY` is **not valid**
for our direct REST calls, and outbound key injection is **not** automatic. Supply a
valid key through the environment's egress-proxy **`transform`** — the key is injected
at the proxy and *never exposed inside the sandbox* — and drop the invalid in-sandbox
var with `env -u GEMINI_API_KEY` so the script relies on the proxy (`--allow-proxy-auth`).

```python
import os
key = os.environ["GEMINI_API_KEY"]          # used only in the transform; never print it
photo = client.files.upload(file="/local/path/shane1.jpg")   # Files API, ~48h TTL

run = client.interactions.create(
    agent="storybook-image-env",
    environment={
        "type": "remote",
        "network": {"allowlist": [
            {"domain": "generativelanguage.googleapis.com",
             "transform": [{"x-goog-api-key": key}]},   # transform is a LIST of {header: value}
            {"domain": "*"},                              # catch-all so git/pip/uv still work
        ]},
    },
    input=[
        {"type": "image", "file_uri": photo.uri, "mime_type": photo.mime_type},
        {"type": "text", "text": (
            "Save the attached photo to characters/Shane/source/shane1.jpg, then from "
            "/workspace/repo run:\n"
            "  uv run scripts/prep_source_photos.py --character Shane --apply\n"
            "  env -u GEMINI_API_KEY uv run scripts/generate_character_sheets.py "
            "--character Shane --send --allow-proxy-auth --overwrite\n"
            "Then upload the generated sheet to the Gemini Files API (the key is injected "
            "by the proxy) and print its resource name as FILE_NAME=files/...\n"
        )},
    ],
    background=True,
)
# poll run.id until completed; parse FILE_NAME from output_text, then retrieve:
#   data = client.files.download(file=file_name); open(local_path, "wb").write(data)
```

**Verified live:** clone + `uv sync` + a real `--send` generation in the sandbox via
this `transform` injection (a sheet was produced). The Files-API **retrieval** leg
(sandbox upload → `client.files.download`) uses the same auth and is wired into
`scripts/run_antigravity_pipeline.py`.

Notes that make this work:
- `--allow-proxy-auth` — inside Antigravity the API key is injected via the network
  transform header, so no key is written into the sandbox.
- `store=false` is already the default on the image requests (no server-side retention).
- For page images, run `scripts/generate_page_images.py --send --allow-proxy-auth`
  the same way (the story JSON + sheets must already be in `/workspace/repo`).

## Step 4 — Retrieve outputs

The agent writes results under `/workspace/repo/...` (e.g.
`characters/Shane/shane_character_sheet.jpg`, `outputs/<title>.epub`). Download them
from the sandbox (per the Managed Agents Quickstart) into the local repo so the rest
of the pipeline (or your review) can use them.

## Reuse vs. fresh

- Same workspace, new task: pass `environment=<environment_id>` without
  `previous_interaction_id`.
- Brand-new clean fork: invoke the saved agent again (each run forks the base).

## Privacy

The photos transit Google's managed sandbox (in addition to the Gemini calls they
already feed). `store=false` keeps the interactions from being retained, but the
uploaded Files API objects persist ~48h and the sandbox is Google-hosted — a conscious
trade for real photos.

## Official references (confirm exact signatures)

- Antigravity Agent: https://ai.google.dev/gemini-api/docs/antigravity-agent
- Environments in Managed Agents: https://ai.google.dev/gemini-api/docs/agent-environment
- Building Managed Agents (fork / base_environment): https://ai.google.dev/gemini-api/docs/custom-agents
- Managed Agents Quickstart (file upload/download): https://ai.google.dev/gemini-api/docs/managed-agents-quickstart
