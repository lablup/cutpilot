# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Hackathon scaffold, end-to-end wired. `cutpilot <source>` (local file or URL) now runs ingest → Whisper → Scout → top-3 → ffmpeg → manifest to completion on a live NIM, and `cutpilot-serve` exposes the same pipeline over HTTP for the review UI at `ui/index.html`. `tests/unit/` and `tests/integration/` exist and are wired into `pytest`. Still missing: `schemas/manifest.schema.json` and its generator `scripts/export_schemas.py`. The full NAT `sequential_executor` path (via `nat run`) is declared in `configs/cutpilot.yml` but **not** what `cutpilot` actually drives — see "Non-agent pipeline" below. The target layout is in `scaffold_tree.md`; the execution plan is in `SPRINT.md` (12-hour authoritative cut of the full `PRD.md`). When scope conflicts arise, `SPRINT.md` wins over `PRD.md`. `litellm_refactor.md` describes a deferred refactor to drop LiteLLM by swapping Scout's NAT wrapper from ADK to LangChain — don't act on it without a deliberate sprint decision.

## What CutPilot is

An agentic pipeline that turns a long-form video (5–90 min) into 3 vertical 30–60s clips with burned-in captions, each with a reasoning trace. Whisper (audio → word-level transcript) + Nemotron Nano 2 VL (video understanding + agent reasoning + tool calling), served by NVIDIA NIM on an NVIDIA Brev H100 Launchable, orchestrated by the NVIDIA NeMo Agent Toolkit (`nvidia-nat`).

## Commands

`pyproject.toml` with hatchling; no requirements.txt. `requires-python = ">=3.11"`, but `ruff` and `mypy` both target `py313` and `.python-version` pins the pyenv `nvidia` env (Python 3.13.9) — develop on 3.13 to match lint/type targets.

- Install (editable + dev extras): `pip install -e ".[dev]"`
- Run CLI (single-command Typer, no subcommand): `cutpilot <source> [--run-id NAME]` where `<source>` is a local path *or* an `http(s)://` URL (yt-dlp handles ingest). Entry point `cutpilot.cli:app`.
- Run the HTTP server backing the review UI: `cutpilot-serve [--host 127.0.0.1] [--port 8080] [--reload]`. Entry point `cutpilot.cli:serve` → uvicorn → `cutpilot.server:app`. Exposes `POST /runs` (source URL/path), `POST /runs/upload` (multipart), `GET /runs/{id}`, static mounts at `/outputs` and `/`.
- Run the NAT workflow directly (bypasses the Python shortcut — see Architecture §1): `nat run --config_file=src/cutpilot/configs/cutpilot.yml --input <source>`.
- Expose the NAT workflow over HTTP (separate from `cutpilot-serve`): `nat serve --config_file=src/cutpilot/configs/cutpilot.yml`.
- Verify tool registrations were discovered: `nat info components` (if a CutPilot function is missing, reinstall with `pip install -e .`).
- Lint: `ruff check .` — autofix: `ruff check --fix .` — format: `ruff format .`
- Type check: `mypy src` (strict mode, pydantic plugin enabled).
- Tests: `pytest` — unit only: `pytest -m "not integration and not gpu"` — integration: `pytest -m integration` — single: `pytest tests/unit/test_scout_parse.py::test_name -v`. Integration suites exist for the live Scout call (`test_scout_live.py`) and ffmpeg tool wrappers; they skip automatically when their endpoints/binaries aren't reachable.
- Coverage is auto-enabled (`--cov=cutpilot`) via `addopts`.

`ruff` line-length is 100 and `E501` is ignored — the formatter owns wrapping. Markers `integration` and `gpu` are declared; use them for anything that needs real ffmpeg files, a live NIM endpoint, or GPU hardware.

## Architecture — the big picture

Three layers stacked vertically. Don't reshape this without a reason — it's designed so non-agent stages are plain Python and agent orchestration stays inside the NeMo Agent Toolkit workflow.

### 1. Non-agent pipeline (`pipeline.py`)

A thin functional async sequence in `run_pipeline`: `_resolve_source (URL via yt-dlp or local path) → extract_audio → transcribe (Whisper) → _run_nat_workflow → save manifests`. Each stage takes/returns Pydantic models from `models.py`. No agent logic lives here.

**`_run_nat_workflow` deliberately does not invoke the NAT `sequential_executor` declared in `configs/cutpilot.yml`.** Instead it: (a) builds a VL LLM handle via `clients/nim.make_vl_llm()`, (b) calls `scout_core(...)` directly, (c) picks the top 3 by `scores.composite`, (d) re-encodes each window (`cut_reencode` → `crop_9_16_center`) into `outputs/<run>/clip_N.mp4`, and (e) writes a `ClipManifest` per clip. Reason the Python shortcut exists: `sequential_executor` returns the last agent's text (not `list[ClipManifest]`), and it takes a single string input, so passing `(run_id, source_path)` to Scout would need a JSON-dispatcher layer we don't yet need. The YAML workflow remains the entry point for `nat run` — treat it as the canonical declarative view for the judges, not as what the CLI executes.

`run_pipeline` also accepts an `on_stage: Callable[[PipelineStage], None] | None` callback so `server.py` can surface stage transitions (`downloading | transcribing | scouting | editing`) without coupling the pipeline to HTTP concerns. Callback errors are swallowed — observability must never break the pipeline.

### 2. Agent layer — NAT workflow declared in `src/cutpilot/configs/cutpilot.yml`

The entire agent graph is config-driven YAML. There is no Python agent-class hierarchy.

- **Scout** — a NAT `@register_function` in `agents/scout.py` that takes `(run_id, source_path)` and returns a validated `CandidatesResult`. The function's return type *is* the schema — there is no agent loop because Scout has no tools. The framework is **NAT + Google ADK** (not LangChain): the LLM handle comes from `builder.get_llm(config.llm, wrapper_type=LLMFrameworkEnum.ADK)` and resolves to a `google.adk.models.lite_llm.LiteLlm`. Internal flow: `clients/ffmpeg.prepare_video_for_vl` transcodes the source to 480p/CRF30 (audio dropped) → bytes wrapped in `google.genai.types.Part.from_bytes(mime_type="video/mp4")` → `LlmRequest` with `config.response_schema=CandidatesResult` → `generate_content_async`. LiteLlm encodes the video as a `data:video/mp4;base64,…` URL and translates `response_schema` to NIM's `response_format={"type": "json_schema", "json_schema": {...}}`. Transcript is **optional at the Scout boundary**: `scout_core` takes `transcript: Transcript | None`; the pipeline always runs Whisper first but passes `None` when `transcript.segments` is empty so Scout degrades gracefully on audio-free sources. Near-miss candidates (15 ≤ duration < 20 s) are symmetrically padded to 20 s before validation; anything outside `[12, 90]` is dropped. `pydantic.ValidationError` is still the fail-closed contract at the `CandidatesResult` level (`min_length=5`).
- **Editor** — declared as `_type: tool_calling_agent` with `tool_names: [cut, crop_9_16, burn_captions, transcript_window]` and `llm_name: nemotron_text`. Only component with write access. Picks top 3 by composite score, validates timestamps against transcript, calls tools to refine boundaries and materialize clips. Must emit exactly 3 non-overlapping clips. Prompt in `prompts/editor.md`.
- **Orchestrator** — `workflow: _type: sequential_executor, tool_list: [scout, editor]` in the same YAML. Runs via `nat run --config_file=src/cutpilot/configs/cutpilot.yml --input <source>`; `nat serve` exposes it over HTTP. The `cutpilot` CLI does **not** drive this orchestrator — it calls `scout_core` + deterministic top-3 + ffmpeg directly (see Architecture §1 for why).
- Model config SSoT is the `llms:` block. **Two NIM chat endpoints**, not one: `nemotron_text` (bound to the Editor, `NIM_TEXT_BASE_URL` / `NIM_TEXT_MODEL`, default `nvidia/nemotron-3-nano`, `temperature: 0.0`) and `nemotron_vl` (bound to Scout, `NIM_VL_BASE_URL` / `NIM_VL_MODEL`, default `nvidia/nemotron-nano-12b-v2-vl`, `temperature: 0.2`). Endpoint/model changes are one-line YAML/env edits; no Python LLM-wrapper file exists.
- Whisper is **not** in the `llms:` block (NAT's `llms:` is for chat models). It's a third NIM endpoint (`WHISPER_BASE_URL` / `WHISPER_MODEL`) called directly from `pipeline.py` through `clients/whisper.py` using the async OpenAI audio API (`/v1/audio/transcriptions`, `response_format=verbose_json`, `timestamp_granularities=["word","segment"]`).

Per-request VL knobs (`media_io_kwargs={"video": {"fps": 2, "num_frames": 128}}`) are passed via LiteLlm's `extra_body=...` at construction time. The canonical place that knob lives is `clients/nim.py::make_vl_llm()` — both `pipeline._run_nat_workflow` and `scripts/scout_smoke.py` call it, so the sampling config can't drift between the two. The `nvidia_nat_adk` plugin doesn't thread `extra_body` through its YAML NIM client, so if/when the pipeline switches to the YAML `sequential_executor` path, the same knob must be injected onto the YAML-sourced LiteLlm (open question — likely via a subclass or a patch to the NAT-ADK NIM plugin). **num_frames=128 is load-bearing**: without it NIM defaults to ~8 frames and Scout returns 5+ near-identical "woman on stage" candidates because it can't see past the title slide.

Key simplification vs. the full PRD: there is **no separate Critic agent**. Scout self-scores; Editor filters. `SPRINT.md` is authoritative on this.

### 3. Tool layer (`tools/` + `clients/ffmpeg.py`)

Plain Python functions with type hints and docstrings, each decorated with `@register_function(config_type=...)` and registered as a NAT component. NAT derives the tool schema from the function signature — do not hand-roll tool schemas. Each tool has its own entry point in `pyproject.toml`'s `[project.entry-points."nat.components"]` table; `nat info components` lists all nine (`cutpilot_scout` plus eight tools).

The Editor has access to these eight tools:

- `cut` (`tools/cut.py`) — ffmpeg time-range extraction, prefer `-c copy` for speed; fall back to re-encode on any artifact.
- `crop_9_16` (`tools/crop.py`) — center crop to 1080×1920. No VL-guided smart crop in the sprint scope.
- `burn_captions` (`tools/captions.py`) — ffmpeg subtitles filter; full-segment captions, no word-level highlighting in the sprint scope.
- `transcript_window` (`tools/transcript_window.py`) — read-only access to a transcript slice for boundary refinement.
- `splice`, `merge`, `save`, `probe` (`tools/{splice,merge,save,probe}.py`) — boundary-tweaking and diagnostic helpers. Source-file names may differ from registered tool names; always check the decorator, not the filename.

**All subprocess ffmpeg invocation goes through `clients/ffmpeg.py`** (`extract_audio`, `cut_copy`, `cut_reencode`, `crop_9_16_center`, `burn_captions`). Tools are thin adapters — they should not shell out directly.

`tools/__init__.py` exports `TOOLS = [...]` as the canonical name list. The Editor's YAML `tool_names` must not reference anything outside this list. Scene detection and audio normalization are explicitly cut from sprint scope — see `SPRINT.md` § "What we cut from the full PRD".

### Data flow

`source (local path | URL | multipart upload)` → (yt-dlp if URL) → `video file` → (ffmpeg demux) → `wav` → (Whisper) → `Transcript` → (Scout, via `clients/nim.make_vl_llm()`) → `CandidatesResult` → (deterministic top-3 in `pipeline._run_nat_workflow`; Editor + tools live only on the `nat run` branch) → `outputs/<run>/clip_{1,2,3}.mp4` + per-clip `ClipManifest` JSON → rendered by `ui/index.html` (loaded directly from `file://` or served by `cutpilot-serve` at `/`).

### Review UI (`ui/index.html`) and HTTP server (`server.py`)

Single static HTML file, Tailwind via CDN, dark theme, Nemotron purple accent. Reads the manifest written by the pipeline and renders three video players + rationale panels. Keyboard shortcuts: space/arrows on focused player, `e` toggles the reasoning trace panel. `?demo=1` hides dev noise.

Two ways to deliver it:

- **`file://`** — open `ui/index.html` directly; it reads manifests written under `outputs/<run>/`.
- **`cutpilot-serve`** — the FastAPI app in `server.py` mounts `ui/` at `/` and `outputs/` at `/outputs`, and exposes `POST /runs` (URL/path), `POST /runs/upload` (multipart), `GET /runs/{id}` (status + manifests). Run state lives in an in-memory dict — single-worker, single-user, lost on restart. `RunStatus` is a superset of the pipeline's `PipelineStage` (adds `pending`, `done`, `failed`); transitions come from `run_pipeline`'s `on_stage` callback.

## SSoT files — keep them single

- `src/cutpilot/configs/cutpilot.yml` — the NAT workflow: `llms:` (models + endpoints), `functions:` (Scout + tools + Editor), `workflow:` (orchestrator). Framework-level changes (model, endpoint, agent type, tool list) happen here, not in Python.
- `models.py` — every Pydantic domain type (Word, TranscriptSegment, Transcript, RubricScores, Candidate, CandidatesResult, ClipManifest). Anything serialized to disk or crossing an agent boundary must be defined here. All models use `ConfigDict(extra="forbid")` and `Candidate` enforces 20–90s duration in a `model_validator`.
- `settings.py` — pydantic-settings. All config reads go through this. Reads `NIM_TEXT_BASE_URL`, `NIM_TEXT_MODEL`, `NIM_VL_BASE_URL`, `NIM_VL_MODEL`, `WHISPER_BASE_URL`, `WHISPER_MODEL`, `WHISPER_LANGUAGE`, `NVIDIA_API_KEY`, `NGC_API_KEY`, and the three `CUTPILOT_*_DIR` paths. `.env.example` is the canonical contract.
- `paths.py` — path computation. No `pathlib` path math anywhere else; no string path concatenation anywhere.
- `persistence.py` — `save`/`load` via Pydantic JSON roundtrip (`model_dump_json` / `model_validate_json`). No manual dict plumbing.
- `clients/ffmpeg.py` — the only module that invokes the `ffmpeg` subprocess. Tools delegate here.
- `clients/whisper.py` — the only caller of the Whisper NIM audio API.
- `clients/nim.py` — `make_vl_llm()` is the only place the VL `LiteLlm` handle is constructed. Both `pipeline._run_nat_workflow` and `scripts/scout_smoke.py` go through it so `media_io_kwargs` can't drift.
- `clients/youtube.py` — the only caller of `yt-dlp`. Invoked by `pipeline._resolve_source` when the CLI argument passes `is_url()`.
- `server.py` — the only FastAPI app; owns `RunStatus` (a superset of `PipelineStage`), `RunState`, and the in-memory `_RUNS` dict. Any persistence / scaling work starts here, not in `pipeline.py`.
- `prompts/scout.md` and `prompts/editor.md` at **repo root** (not under `src/cutpilot/`), loaded through `src/cutpilot/prompts.py` which walks three parents up. Don't inline system prompts in agent code.
- `schemas/manifest.schema.json` — will be generated from `models.py` via `scripts/export_schemas.py` (neither exists yet). Never hand-edit.
- `pyproject.toml` `[project.entry-points."nat.components"]` — the registration point that makes `@register_function` components discoverable by `nat`. If `nat info components` doesn't list a tool, the entry-point table is the first place to check.

## Working within sprint scope

Before implementing anything, check `SPRINT.md` "What we cut from the full PRD". These are deliberately deferred: VL-guided crop, scene detection tool, audio normalization/fades, word-level caption highlighting, Korean support, multi-speaker handling, retry logic beyond skip-and-continue, reasoning-trace overlay video variant, `--open` flag. Don't build them unless `SPRINT.md` changes.

`TASKS.md` has more detail than the sprint actually delivers — treat it as a reference for how to *verify* each piece, not as a to-do list.

## Things that commonly go wrong here

- **Scout returning free text instead of JSON** — the Scout function's return type is `CandidatesResult`; let `pydantic.ValidationError` fail loudly rather than silently parsing prose.
- **NIM cap of 5 images per request** — Nemotron Nano VL rejects `image_url` lists of more than 5, so client-side frame extraction can't be the strategy. Send one `video/mp4` Part and let NIM sample server-side (see `media_io_kwargs` above).
- **NIM VL returning identical candidates** — the default `num_frames` is tiny; always pass `extra_body={"media_io_kwargs": {"video": {"fps": 2, "num_frames": 128}}}` on the LiteLlm handle. If candidates still repeat, raise `num_frames` toward 128 (the model's max) or reduce video duration.
- **Nemotron VL undershooting duration** — it reliably returns 15–19 s clips despite the prompt. `_repair_candidate` in `agents/scout.py` pads 15–20 s candidates up to 20 s; don't remove this guard without re-running the smoke driver.
- **Editor hallucinating timestamps** — every proposed `start_ts`/`end_ts` must be validated against the transcript *before* the tool call, not after ffmpeg produces garbage.
- **ffmpeg `-c copy` producing drift** — first fallback is to re-encode, don't chase the copy optimization.
- **NIM endpoint 401 / 429 / connection refused** — three independent endpoints (`NIM_TEXT_BASE_URL`, `NIM_VL_BASE_URL`, `WHISPER_BASE_URL`) each referencing `${NVIDIA_API_KEY}`. Self-hosted NIM on Brev without auth accepts any key (code falls back to `"dummy"`); hosted NIM returns 401 on a bad/missing key. Each URL is the one-knob switch for that service.
- **Rotated Cloudflare tunnel URLs** — dev endpoints in `.env` are Cloudflare `trycloudflare.com` tunnels that rotate. When requests suddenly fail with DNS or connection errors, check `.env` against the current tunnel URLs before chasing a code bug.
- **`@register_function` not discovered** — if `nat info components` doesn't list a CutPilot tool, the `[project.entry-points.'nat.components']` table in `pyproject.toml` is missing or the package wasn't reinstalled (`pip install -e .`). Entry-points are read at install time, not at import time.
- **LiteLLM still in the graph** — the `nvidia-nat[adk]` extra transitively pulls `google-adk` → `litellm`. This is deliberate: Scout currently uses NAT's ADK wrapper. `litellm_refactor.md` sketches the LangChain-wrapper migration that would let us drop it; don't start that refactor without a sprint decision.

Docs for NAT: /Users/sergeyleksikov/Documents/GitHub/nvidia_repos/NeMo-Agent-Toolkit/docs

Examples for NAT: /Users/sergeyleksikov/Documents/GitHub/nvidia_repos/NeMo-Agent-Toolkit/examples


