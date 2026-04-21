# CutPilot

Agentic long-video to short-clip generator. Takes a 5–90 minute source video (podcast, lecture, interview, keynote) and produces three 30–60 second vertical clips with burned-in captions and a reasoning trace explaining why each moment was selected.

Built for NVIDIA Nemotron Developer Days Seoul 2026 — Track A (Creative Agentic Systems).

## How it works

Two steps, both backed by **Nemotron Nano 2 VL** served by **NVIDIA NIM**, orchestrated by the **NVIDIA NeMo Agent Toolkit** (`nvidia-nat`) via a single YAML workflow (`configs/cutpilot.yml`):

- **Scout** reads the full video and transcript in one pass, proposes 5–10 candidate moments, and self-scores each on four axes (hook strength, self-containedness, length fit, visual fit). Implemented as a NAT `@register_function` whose return type is the Pydantic schema.
- **Editor** (NAT `tool_calling_agent`) picks the top three, validates timestamps against the transcript, and calls a small set of tools (`cut`, `crop_9_16`, `burn_captions`, `transcript_window`) to materialize the clips.

Perception is handled by **Whisper large-v3** (word-level timestamps) running alongside the NIM container on a single **NVIDIA Brev** H100 Launchable. Hosted NIM at `build.nvidia.com` (`NVIDIA_API_KEY`) is the documented fallback.

Output is three `.mp4` files + per-clip JSON manifests, surfaced through a single-file HTML review UI that shows before/after, rationale, rubric scores, and the full agent reasoning trace.

## Status

Hackathon-era scaffold. Planning documents are authoritative until the code lands:

- **[PRD.md](PRD.md)** — full product requirements
- **[SPRINT.md](SPRINT.md)** — 12-hour execution cut (wins over PRD on scope conflicts)
- **[TASKS.md](TASKS.md)** — task breakdown with verifiable outcomes
- **[scaffold_tree.md](scaffold_tree.md)** — target directory layout
- **[CLAUDE.md](CLAUDE.md)** — guidance for Claude Code in this repo

## Requirements

- Python 3.11+
- `ffmpeg` 6.0+ on `PATH`
- An **NVIDIA Brev** H100 Launchable (≥70 GB VRAM), provisioned via the Brev CLI
- NVIDIA NIM container `nvcr.io/nim/nvidia/nemotron-nano-12b-v2-vl:<tag>` running on the Brev instance (pulled with `NGC_API_KEY`), or hosted NIM at `build.nvidia.com` with `NVIDIA_API_KEY` as fallback
- faster-whisper large-v3 weights available locally on the Brev instance

## Install

```bash
pip install -e ".[dev]"
```

## Run

```bash
cutpilot <source.mp4>
```

The CLI writes clips and a manifest under `outputs/<run>/` and prints a `file://` URL to the review UI.

## Development

```bash
pytest                          # full suite (unit + integration)
pytest -m "not integration"     # unit tests only
ruff check . && ruff format .   # lint + format
mypy src                        # strict type check
```

## Scope

**In:** one source file (`.mp4`/`.mov`/`.mkv`) or YouTube URL, English audio, single primary speaker, 3 vertical clips with burned-in captions, center-crop framing.

**Out for the sprint** (deferred to post-hackathon): smart crop with face tracking, scene-detection tool, multi-language output, word-level caption highlighting, Korean-language sources, multi-speaker handling, social platform publishing, batch processing.

## Authors

Sergey Leksikov · Minjae Kim

## License

MIT
