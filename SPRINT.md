# CutPilot — 12-hour sprint plan

**Team:** 2 developers + Claude Code
**Budget:** 12 hours wall time (24 dev-hours + AI assist)
**Goal:** Working demo with polished UI. Not everything from the full PRD — the must-haves for a winning pitch.

This is the pragmatic cut-down of TASKS.md. When there's a conflict, this file wins during the sprint.

---

## Team structure

| Role | Owner | Focus |
|---|---|---|
| Pipeline | Dev A | Models, 2 agents, tools, ffmpeg |
| UI | Dev B | HTML review app, demo polish |
| Claude Code | Both | Pair on both tracks; use for boilerplate, schema, prompt iteration |

Dev A and Dev B work on parallel tracks. They sync at hours 1, 6, 10, and 11.

## Architecture (simplified from PRD)

Stack: **NVIDIA NeMo Agent Toolkit** (`nvidia-nat`, CLI `nat`) orchestrates a two-step workflow declared in `src/cutpilot/configs/cutpilot.yml`. Three NIM services run on a single **NVIDIA Brev** H100 Launchable:

| Port | Service | Purpose |
|---|---|---|
| `8100` | Riva Whisper-Large (gRPC) | Audio transcription (pipeline stage, not a NAT tool) |
| `8000` | NIM Nemotron-3 Nano 30B A3B | Editor — text reasoning + tool calling |
| `9000` | NIM Nemotron Nano 12B V2 VL | Scout — whole-video + frame analysis |

- **Scout** — NAT `@register_function` (framework wrapper: Google ADK). Transcodes the source to a compact 480p mp4 via ffmpeg, sends it as one `video/mp4` Part through ADK's `LiteLlm.generate_content_async`, with `response_schema=CandidatesResult` for Pydantic-structured output. `media_io_kwargs={"video": {"fps": 2, "num_frames": 128}}` is passed via `extra_body` on the LiteLlm handle so NIM samples enough frames server-side to distinguish moments — **without this, candidates come back as near-duplicates.** Transcript is optional (Whisper is on a sibling branch). Per-candidate repair pads 15–20 s responses up to 20 s; `CandidatesResult.min_length=5` is the fail-closed contract.
- **Editor** — NAT `tool_calling_agent` wired to the text NIM. Takes top 3 by composite score, validates timestamps against transcript, calls tools to cut/crop/caption.
- **Orchestrator** — NAT `sequential_executor` composing `[scout, editor]`. Runs via `nat run --config_file=src/cutpilot/configs/cutpilot.yml` (or the `cutpilot` CLI).
- Tools decorated `framework_wrappers=[LLMFrameworkEnum.ADK]` so they're portable to NAT's `_type: adk` workflow if we ever switch; focus stays on NAT.

Four tools available to Editor only, each registered via `@register_function`: `cut`, `crop_9_16_center`, `burn_captions`, `get_transcript_window`.

## What we cut from the full PRD

Explicitly deferred to post-hackathon. Do not build these.

- VL-guided smart crop — center crop only.
- Scene detection tool — rely on transcript sentence boundaries for cut points.
- Audio normalization and fades — use source audio as-is.
- Word-level caption highlighting — burn full segment captions instead.
- Korean-language support — English sources only for demo.
- Multi-speaker handling — pick sources without this challenge.
- Retry logic and elaborate recovery — basic try/except, skip-and-continue.
- Reasoning-trace overlay video variant — reasoning lives in the UI only.
- Reliability testing on many sources — 2 demo sources, 1 backup.
- CLI `--open` flag — manual browser open is fine.
- MCP server exposure of CutPilot tools (`nat mcp serve`) — stretch only; skip unless the core demo is green by hour 10.

## Hour-by-hour plan

### Hour 0–1: Setup and sync

**Both devs together.**
- Provision the Brev H100 Launchable and confirm SSH access. *Verify:* `nvidia-smi` on the instance shows a free H100, `brev ls` lists the instance, ports `8000` (text NIM), `9000` (VL NIM), and `8100` (Riva) are reachable from the laptop via `brev port-forward`.
- Set env from `.env.example`: `NIM_TEXT_BASE_URL`, `NIM_VL_BASE_URL`, `RIVA_SERVER`, `WHISPER_LANGUAGE`, `NGC_API_KEY`, `NVIDIA_API_KEY`. *Verify:* `.env` on the instance mirrors `.env.example`; `python -c "from cutpilot.settings import settings; print(settings)"` prints the full contract.
- Launch the three NIM containers on Brev:
  - `docker run --gpus all -p 8100:50051 -e NGC_API_KEY nvcr.io/nim/nvidia/riva-asr:<tag>` (Whisper-Large ASR, gRPC).
  - `docker run --gpus all -p 8000:8000 -e NGC_API_KEY nvcr.io/nim/nvidia/nemotron-3-nano-30b-a3b:<tag>` (text reasoning).
  - `docker run --gpus all -p 9000:8000 -e NGC_API_KEY nvcr.io/nim/nvidia/nemotron-nano-12b-v2-vl:<tag>` (vision-language).
  *Verify:* `curl $NIM_TEXT_BASE_URL/models` and `curl $NIM_VL_BASE_URL/models` each return their expected model; `python3 python-clients/scripts/asr/transcribe_file_offline.py --server $RIVA_SERVER --language-code en-US --input-file tests/fixtures/sample.wav` returns a non-empty transcript.
- Install NeMo Agent Toolkit + Riva client: `pip install -e ".[dev]"`. *Verify:* `nat --help` lists `run`, `serve`, `mcp`; `from nat.cli.register_workflow import register_function` imports; `import riva.client` imports.
- Agree on manifest JSON schema (Dev A writes, Dev B reviews). *Verify:* schema file committed, one hand-written example validates.
- Agree on output directory layout. *Verify:* both devs can name the path to every artifact without checking.
- Split tracks and start.

### Hour 1–3: Foundation

**Dev A — Perception pipeline**
- Ingest: local mp4 file validation + audio demux. *Verify:* 10-min test file produces 16kHz wav in under 10s.
- Whisper transcript with word timestamps persisted to JSON. *Verify:* 5 random words match audio within 200ms.
- Smoke-call NIM Nemotron VL on a 2-min video clip via `/v1/chat/completions` at `$NIM_BASE_URL`, confirm it returns a visual description. *Verify:* description mentions a specific on-screen element.

**Dev B — UI scaffold with mock data**
- Single HTML file, Tailwind CDN, dark mode, purple accent. *Verify:* opens via `file://`, no console errors.
- Load a hand-written mock manifest, render header + 3 placeholder clip cards. *Verify:* all manifest fields visible, no hardcoded values.
- Before/after row layout with two `<video>` elements side by side. *Verify:* layout holds at 1080p and 1440p.

### Hour 3–6: Core functionality

**Dev A — Scout function**
- `src/cutpilot/configs/cutpilot.yml` with `llms: _type: nim` block and `functions:` / `workflow:` declared. *Verify:* `nat info components` lists `cutpilot_scout` and the four tool functions after `pip install -e .`.
- Scout registered as `@register_function(config_type=ScoutConfig, framework_wrappers=[LLMFrameworkEnum.ADK])` returning a `CandidatesResult` Pydantic model. Path: `clients/ffmpeg.prepare_video_for_vl` → `Part.from_bytes(mime_type="video/mp4")` → `LlmRequest(config.response_schema=CandidatesResult)` → `generate_content_async`. *Verify:* `python scripts/scout_smoke.py <video> <run_id>` returns a validated `CandidatesResult` with ≥5 entries end-to-end against the live VL NIM.
- Scout system prompt: 5–10 candidates with self-scores in strict JSON (loaded from `prompts/scout.md`), explicit "reject <20s / >90s" language because Nemotron VL otherwise undershoots. *Verify:* smoke run on the GTC demo source returns ≥5 distinct candidates, no free-text prose leaks.
- `media_io_kwargs` on LiteLlm `extra_body`: `{"video": {"fps": 2, "num_frames": 128}}`. *Verify:* candidates describe different moments (not the same "woman on stage" every time). Without this knob NIM defaults to ~8 frames and content collapses.

**Dev B — Clip review cards**
- Card component with thumbnail, hook title, rationale prose, 4 score bars, download button. *Verify:* card matches mockup, scores render as proportional bars.
- Thumbnail auto-generated from clip (via ffmpeg or `<video>` poster). *Verify:* thumbnails show correct moment, no broken images.
- Timeline band showing clip position in source. *Verify:* band correctly highlights [start, end] for each clip.

### Hour 6–8: Sync point + Editor agent

**Integration sync at hour 6.** Dev A and Dev B exchange: real Scout output manifest, real clip mp4 for UI testing.

**Dev A — Editor agent + tools**
- Four tools registered via `@register_function`: `cut`, `crop_9_16_center`, `burn_captions`, `get_transcript_window`. Entry-point table added in `pyproject.toml` under `[project.entry-points.'nat.components']`. *Verify:* `nat info components` lists all four under the CutPilot package.
- `cut` tool: ffmpeg with `-c copy` where possible. *Verify:* output duration within 100ms of requested, byte-identical content.
- `crop_9_16_center` tool: ffmpeg crop filter. *Verify:* output is 1080x1920, center column matches source.
- `burn_captions` tool: ffmpeg subtitles filter with basic styling. *Verify:* captions visible, timed correctly at spot checks.
- Editor declared in `configs/cutpilot.yml` as `_type: tool_calling_agent` with `tool_names: [cut, crop_9_16_center, burn_captions, get_transcript_window]` and `llm_name: nemotron_vl`. Prompt in `prompts/editor.md`. *Verify:* full loop produces 3 clips with no overlap.
- Orchestrator declared as `workflow: _type: sequential_executor, tool_list: [scout, editor]`. *Verify:* `nat run --config_file=configs/cutpilot.yml --input <source>` runs Scout then Editor in order; session state is passed between steps.

**Dev B — Reasoning trace panel**
- Collapsible panel showing all Scout candidates with scores and rejection reasons. *Verify:* starts collapsed, `e` key toggles, expansion shows all candidates.
- Visual distinction between selected and rejected candidates. *Verify:* 3 selected candidates visually different from rejected ones.
- Clicking a timestamp seeks the relevant player. *Verify:* 5 random timestamp clicks each seek correctly.

### Hour 8–10: Full integration

**Both devs.**
- Dev A pipeline produces real output; Dev B's UI loads the real manifest. *Verify:* UI renders correctly with real pipeline output end to end.
- Run on primary demo source: `cutpilot <source.mp4>` (which internally invokes the NAT workflow) and cross-check with `nat run --config_file=configs/cutpilot.yml --input <source.mp4>`. *Verify:* both entrypoints produce the same 3 clips and the same manifest.
- Run on backup source. *Verify:* same result on a second source.
- Pre-render outputs from both sources as fallback videos. *Verify:* fallbacks play from local file without running the pipeline.

### Hour 10–11: Polish for demo

- Demo-mode query param (`?demo=1`) hides any debug noise. *Verify:* nothing dev-looking visible in demo mode.
- Projector or laptop-to-big-screen test. *Verify:* all text legible, color contrast holds.
- Browser zoom level locked. *Verify:* demo machine has known zoom, layout pre-verified.
- Pre-generate all thumbnails so demo has no loading states. *Verify:* thumbnails load instantly.
- README with setup steps so teammates can reproduce. *Verify:* a fresh clone runs end to end with documented commands.

### Hour 11–12: Rehearsal

- Full pitch + live demo, rehearsed once. *Verify:* timing logged, issues captured.
- Fixes for rehearsal issues. *Verify:* second rehearsal clean.
- Backup video one-keystroke ready. *Verify:* hotkey or bookmark tested.
- Team decides: live demo or pre-rendered based on rehearsal confidence. *Verify:* decision written down, no on-stage debate.

## What must work for the demo (the non-negotiables)

If any of these is broken 2 hours before the pitch, switch to the pre-rendered backup and pitch the reasoning:

1. Pipeline produces 3 clips from the primary demo source.
2. UI renders those clips with rationale text readable on projector.
3. Before/after comparison shows the source moment next to the output clip.
4. Reasoning trace panel expands to show Scout's candidates and self-scores.
5. At least one clip is genuinely postable (the "wow" clip).

## Risk triggers (revised for 12h scope)

Tighter thresholds than the full PRD because there's no recovery time.

- **Hour 1: Self-hosted NIM container on Brev won't start or can't pull** → flip `NIM_BASE_URL` to `https://integrate.api.nvidia.com/v1` and use `NVIDIA_API_KEY`. Hosted-NIM rate limits are the new failure mode; cache responses where possible.
- **Hour 4: Scout not returning parseable JSON reliably** → tighten the Scout prompt's JSON contract and add a single retry on `pydantic.ValidationError` inside the Scout function; if that fails, fall back to a simpler "score each of these 10 transcript windows" approach.
- **Hour 6: VL video inference above 3 minutes for a 20-min source** → stop passing full video. Sample frames at transcript cue points only.
- **Hour 8: Editor cuts producing artifacts (drift, glitches)** → drop the `-c copy` optimization, re-encode everything. Slower but reliable.
- **Hour 10: Integration not clean** → ship with static example manifest in UI, run pipeline offline, pre-render the live-demo flow.

## Claude Code usage patterns

Where Claude Code earns its keep in this sprint:

- **Boilerplate**: HTML scaffold, Tailwind class application, ffmpeg command chains.
- **Schema and validation**: JSON schema for manifest, pydantic models for agent outputs.
- **Prompt iteration**: Scout and Editor prompts — paste a failing output, ask for a prompt diff.
- **Tool implementations**: each of the 4 tools as isolated functions with tests.
- **Debugging**: when ffmpeg fails cryptically, paste the error and let Claude suggest the fix.

Where Claude Code is not a substitute:

- Judging clip quality (human ear/eye only).
- Deciding what to cut from scope.
- Rehearsing the pitch.
