# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is a hackathon scaffold. Only planning documents and `pyproject.toml` exist — `src/cutpilot/` has not been created yet. The target layout is in `scaffold_tree.md`; the execution plan is in `SPRINT.md` (12-hour authoritative cut of the full `PRD.md`). When scope conflicts arise, `SPRINT.md` wins over `PRD.md` during the hackathon.

## What CutPilot is

An agentic pipeline that turns a long-form video (5–90 min) into 3 vertical 30–60s clips with burned-in captions, each with a reasoning trace. Whisper (audio → word-level transcript) + Nemotron Nano 2 VL (video understanding + agent reasoning + tool calling), orchestrated by Google ADK.

## Commands

The project uses `pyproject.toml` with hatchling; no requirements.txt. Python 3.11+ required. Once `src/` exists:

- Install (editable + dev extras): `pip install -e ".[dev]"`
- Run CLI: `cutpilot …` (entry point `cutpilot.cli:app`, Typer)
- Lint: `ruff check .` — autofix: `ruff check --fix .` — format: `ruff format .`
- Type check: `mypy src` (strict mode, pydantic plugin enabled)
- Tests (all): `pytest`
- Unit tests only: `pytest -m "not integration and not gpu"`
- Integration (real deps, slow): `pytest -m integration`
- Single test: `pytest tests/unit/test_tools_cut.py::test_name -v`
- Coverage is auto-enabled (`--cov=cutpilot`) via `addopts`.

`ruff` line-length is 100 and `E501` is ignored — the formatter owns wrapping. Markers `integration` and `gpu` are declared; use them for anything that needs ffmpeg real files, vLLM, or Whisper weights.

## Architecture — the big picture

Three layers stacked vertically. Don't reshape this without a reason — it's designed so non-agent stages are plain Python and agent orchestration stays inside ADK.

### 1. Non-agent pipeline (`pipeline.py`)

A thin functional sequence: `ingest → transcribe → run_agents → save`. Each stage takes/returns Pydantic models from `models.py`. No agent logic lives here. This is where ffmpeg demux, Whisper invocation, and manifest persistence happen.

### 2. Agent layer (`agents/`) — two ADK `LlmAgent`s composed via `SequentialAgent`

- **Scout** — `LlmAgent(output_schema=CandidatesResult, …)`. Single pass: reads video + transcript, outputs 5–10 candidates with `start_ts < end_ts`, 20–90s duration, plus self-scores on 4 rubric axes (hook, self-contained, length-fit, visual-fit, integer 1–5). No tools. No free-text prose — structured output only.
- **Editor** — `LlmAgent(tools=TOOLS, …)`. Only agent with write access. Picks top 3 by composite score, validates timestamps against transcript, calls tools to refine boundaries and materialize clips. Must emit exactly 3 non-overlapping clips.
- **Orchestrator** — `SequentialAgent([scout, editor])` exposed as `root_agent` from `agents/__init__.py` so `adk web` can discover it.
- Both agents share a single `LiteLlm` instance in `agents/llm.py` pointing at a vLLM endpoint via the `hosted_vllm/*` model string. That file is the SSoT for model config — endpoint changes are one-line edits.

Key simplification vs. the full PRD: there is **no separate Critic agent**. Scout self-scores; Editor filters. `SPRINT.md` is authoritative on this.

### 3. Tool layer (`tools/`)

Plain Python functions with type hints and docstrings. ADK auto-wraps them via signature inspection (`FunctionTool`) — do not hand-roll tool schemas. The Editor has access to these four and only these four:

- `cut` — ffmpeg time-range extraction, prefer `-c copy` for speed; fall back to re-encode on any artifact.
- `crop_9_16` — center crop to 1080×1920. No VL-guided smart crop in the sprint scope.
- `burn_captions` — ffmpeg subtitles filter; full-segment captions, no word-level highlighting in the sprint scope.
- `transcript_window` — read-only access to a transcript slice for boundary refinement.

`tools/__init__.py` exports `TOOLS = [...]`. The Editor agent must not see anything outside this list. Scene detection and audio normalization are explicitly cut from sprint scope — see `SPRINT.md` § "What we cut from the full PRD".

### Data flow

`video` → (ffmpeg demux) → `wav` → (Whisper) → `Transcript` → (Scout) → `CandidatesResult` → (Editor + tools) → `outputs/<run>/clip_{1,2,3}.mp4` + per-clip manifest JSON → `ui/index.html` (reads manifest, renders review page).

### Review UI (`ui/index.html`)

Single static HTML file, Tailwind via CDN, dark theme, Nemotron purple accent. Runs from `file://`. Reads the manifest written by the pipeline. Keyboard shortcuts: space/arrows on focused player, `e` toggles the reasoning trace panel. `?demo=1` hides dev noise. No backend service.

## SSoT files — keep them single

- `models.py` — every Pydantic domain type (Transcript, Candidate, CandidatesResult, ClipManifest). Anything serialized to disk or crossing an agent boundary must be defined here.
- `settings.py` — pydantic-settings. All config reads go through this.
- `paths.py` — path computation. No `pathlib` path math anywhere else; no string path concatenation anywhere.
- `prompts/scout.md` and `prompts/editor.md` — loaded through `prompts.py`. Don't inline system prompts in agent code.
- `schemas/manifest.schema.json` — generated from `models.py` via `scripts/export_schemas.py`. Never hand-edit.

## Working within sprint scope

Before implementing anything, check `SPRINT.md` "What we cut from the full PRD". These are deliberately deferred: VL-guided crop, scene detection tool, audio normalization/fades, word-level caption highlighting, Korean support, multi-speaker handling, retry logic beyond skip-and-continue, reasoning-trace overlay video variant, `--open` flag. Don't build them unless `SPRINT.md` changes.

`TASKS.md` has more detail than the sprint actually delivers — treat it as a reference for how to *verify* each piece, not as a to-do list.

## Things that commonly go wrong here

- **Scout returning free text instead of JSON** — always use ADK's `output_schema=CandidatesResult`, never parse text.
- **Editor hallucinating timestamps** — every proposed `start_ts`/`end_ts` must be validated against the transcript *before* the tool call, not after ffmpeg produces garbage.
- **ffmpeg `-c copy` producing drift** — first fallback is to re-encode, don't chase the copy optimization.
- **vLLM tool calling silently disabled** — vLLM must be launched with `--enable-auto-tool-choice` and a compatible `--tool-call-parser` or the Editor loop will be a no-op.
