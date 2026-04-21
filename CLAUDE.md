# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is a hackathon scaffold. Only planning documents and `pyproject.toml` exist — `src/cutpilot/` has not been created yet. The target layout is in `scaffold_tree.md`; the execution plan is in `SPRINT.md` (12-hour authoritative cut of the full `PRD.md`). When scope conflicts arise, `SPRINT.md` wins over `PRD.md` during the hackathon.

## What CutPilot is

An agentic pipeline that turns a long-form video (5–90 min) into 3 vertical 30–60s clips with burned-in captions, each with a reasoning trace. Whisper (audio → word-level transcript) + Nemotron Nano 2 VL (video understanding + agent reasoning + tool calling), served by NVIDIA NIM on an NVIDIA Brev H100 Launchable, orchestrated by the NVIDIA NeMo Agent Toolkit (`nvidia-nat`).

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

`ruff` line-length is 100 and `E501` is ignored — the formatter owns wrapping. Markers `integration` and `gpu` are declared; use them for anything that needs ffmpeg real files, NIM, or Whisper weights.

## Architecture — the big picture

Three layers stacked vertically. Don't reshape this without a reason — it's designed so non-agent stages are plain Python and agent orchestration stays inside the NeMo Agent Toolkit workflow.

### 1. Non-agent pipeline (`pipeline.py`)

A thin functional sequence: `ingest → transcribe → run_workflow → save`. Each stage takes/returns Pydantic models from `models.py`. No agent logic lives here. This is where ffmpeg demux, Whisper invocation, NAT workflow invocation, and manifest persistence happen.

### 2. Agent layer — NAT workflow declared in `configs/cutpilot.yml`

The entire agent graph is config-driven YAML. There is no Python agent-class hierarchy.

- **Scout** — a NAT `@register_function` in `agents/scout.py` that takes `(video_path, transcript)`, calls the NIM VL endpoint once, and returns a validated `CandidatesResult`. The function's return type *is* the schema — there is no agent loop because Scout has no tools. Outputs 5–10 candidates with `start_ts < end_ts`, 20–90s duration, plus self-scores on 4 rubric axes (hook, self-contained, length-fit, visual-fit, integer 1–5). `pydantic.ValidationError` is the fail-closed contract.
- **Editor** — declared as `_type: tool_calling_agent` in `configs/cutpilot.yml` with `tool_names: [cut, crop_9_16, burn_captions, transcript_window]`. Only component with write access. Picks top 3 by composite score, validates timestamps against transcript, calls tools to refine boundaries and materialize clips. Must emit exactly 3 non-overlapping clips. Prompt in `prompts/editor.md`.
- **Orchestrator** — `workflow: _type: sequential_executor, tool_list: [scout, editor]` in the same YAML. Runs via `nat run --config_file=configs/cutpilot.yml --input <source>`; `nat serve` exposes it over HTTP; the `cutpilot` CLI loads the workflow programmatically via `nat.runtime`.
- Model config SSoT is the `llms:` block in `configs/cutpilot.yml` (`_type: nim`, `model_name: nvidia/nemotron-nano-12b-v2-vl`, `base_url: ${NIM_BASE_URL}`, `api_key: ${NVIDIA_API_KEY}`). Endpoint changes are a one-line YAML edit; no Python LLM-wrapper file exists.

Key simplification vs. the full PRD: there is **no separate Critic agent**. Scout self-scores; Editor filters. `SPRINT.md` is authoritative on this.

### 3. Tool layer (`tools/`)

Plain Python functions with type hints and docstrings, each decorated with `@register_function(config_type=...)` and registered as a NAT component via the `[project.entry-points.'nat.components']` table in `pyproject.toml`. NAT derives the tool schema from the function signature — do not hand-roll tool schemas. The Editor has access to these four and only these four:

- `cut` — ffmpeg time-range extraction, prefer `-c copy` for speed; fall back to re-encode on any artifact.
- `crop_9_16` — center crop to 1080×1920. No VL-guided smart crop in the sprint scope.
- `burn_captions` — ffmpeg subtitles filter; full-segment captions, no word-level highlighting in the sprint scope.
- `transcript_window` — read-only access to a transcript slice for boundary refinement.

`tools/__init__.py` exports `TOOLS = [...]` as the canonical list. The Editor's YAML `tool_names` must not reference anything outside this list. Scene detection and audio normalization are explicitly cut from sprint scope — see `SPRINT.md` § "What we cut from the full PRD".

### Data flow

`video` → (ffmpeg demux) → `wav` → (Whisper) → `Transcript` → (Scout) → `CandidatesResult` → (Editor + tools) → `outputs/<run>/clip_{1,2,3}.mp4` + per-clip manifest JSON → `ui/index.html` (reads manifest, renders review page).

### Review UI (`ui/index.html`)

Single static HTML file, Tailwind via CDN, dark theme, Nemotron purple accent. Runs from `file://`. Reads the manifest written by the pipeline. Keyboard shortcuts: space/arrows on focused player, `e` toggles the reasoning trace panel. `?demo=1` hides dev noise. No backend service.

## SSoT files — keep them single

- `configs/cutpilot.yml` — the NAT workflow: `llms:` (model + endpoint), `functions:` (Scout + tools), `workflow:` (orchestrator). Framework-level changes (model, endpoint, agent type, tool list) happen here, not in Python.
- `models.py` — every Pydantic domain type (Transcript, Candidate, CandidatesResult, ClipManifest). Anything serialized to disk or crossing an agent boundary must be defined here.
- `settings.py` — pydantic-settings. All config reads go through this. Reads `NIM_BASE_URL`, `NVIDIA_API_KEY`, `NGC_API_KEY`, `WHISPER_MODEL_PATH`.
- `paths.py` — path computation. No `pathlib` path math anywhere else; no string path concatenation anywhere.
- `prompts/scout.md` and `prompts/editor.md` — loaded through `prompts.py`. Don't inline system prompts in agent code.
- `schemas/manifest.schema.json` — generated from `models.py` via `scripts/export_schemas.py`. Never hand-edit.
- `pyproject.toml` `[project.entry-points.'nat.components']` — the registration point that makes `@register_function` components discoverable by `nat`. If `nat info components` doesn't list a tool, the entry-point table is the first place to check.

## Working within sprint scope

Before implementing anything, check `SPRINT.md` "What we cut from the full PRD". These are deliberately deferred: VL-guided crop, scene detection tool, audio normalization/fades, word-level caption highlighting, Korean support, multi-speaker handling, retry logic beyond skip-and-continue, reasoning-trace overlay video variant, `--open` flag. Don't build them unless `SPRINT.md` changes.

`TASKS.md` has more detail than the sprint actually delivers — treat it as a reference for how to *verify* each piece, not as a to-do list.

## Things that commonly go wrong here

- **Scout returning free text instead of JSON** — the Scout function's return type is `CandidatesResult`; let `pydantic.ValidationError` fail loudly rather than silently parsing prose.
- **Editor hallucinating timestamps** — every proposed `start_ts`/`end_ts` must be validated against the transcript *before* the tool call, not after ffmpeg produces garbage.
- **ffmpeg `-c copy` producing drift** — first fallback is to re-encode, don't chase the copy optimization.
- **NIM endpoint 401 / 429** — the `llms:` block references `${NVIDIA_API_KEY}`; if it's unset or expired, self-hosted NIM on Brev gives a connection error and hosted NIM gives 401. `NIM_BASE_URL` is the one-knob switch between the two.
- **`@register_function` not discovered** — if `nat info components` doesn't list a CutPilot tool, the `[project.entry-points.'nat.components']` table in `pyproject.toml` is missing or the package wasn't reinstalled (`pip install -e .`). Entry-points are read at install time, not at import time.

Docs for NAT: /Users/sergeyleksikov/Documents/GitHub/nvidia_repos/NeMo-Agent-Toolkit/docs

Examples for NAT: /Users/sergeyleksikov/Documents/GitHub/nvidia_repos/NeMo-Agent-Toolkit/examples


