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

Two agents, both Nemotron Nano 2 VL with different prompts:

- **Scout** — reads video + transcript in one pass, outputs 5–10 candidate moments with self-scored rubric (hook, self-contained, length-fit, visual-fit, 1–5 each). No separate critic.
- **Editor** — takes top 3 by composite score, validates timestamps against transcript, calls tools to cut/crop/caption.

Four tools available to Editor only: `cut`, `crop_9_16_center`, `burn_captions`, `get_transcript_window`.

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

## Hour-by-hour plan

### Hour 0–1: Setup and sync

**Both devs together.**
- Confirm GPU access, vLLM serving Nemotron Nano 2 VL, Whisper loaded. *Verify:* both models respond to a test prompt.
- Agree on manifest JSON schema (Dev A writes, Dev B reviews). *Verify:* schema file committed, one hand-written example validates.
- Agree on output directory layout. *Verify:* both devs can name the path to every artifact without checking.
- Split tracks and start.

### Hour 1–3: Foundation

**Dev A — Perception pipeline**
- Ingest: local mp4 file validation + audio demux. *Verify:* 10-min test file produces 16kHz wav in under 10s.
- Whisper transcript with word timestamps persisted to JSON. *Verify:* 5 random words match audio within 200ms.
- Smoke-call Nemotron VL on a 2-min video clip, confirm it returns a visual description. *Verify:* description mentions a specific on-screen element.

**Dev B — UI scaffold with mock data**
- Single HTML file, Tailwind CDN, dark mode, purple accent. *Verify:* opens via `file://`, no console errors.
- Load a hand-written mock manifest, render header + 3 placeholder clip cards. *Verify:* all manifest fields visible, no hardcoded values.
- Before/after row layout with two `<video>` elements side by side. *Verify:* layout holds at 1080p and 1440p.

### Hour 3–6: Core functionality

**Dev A — Scout agent**
- Tool schemas defined for all 4 tools. *Verify:* JSON Schema validates, vLLM accepts them.
- Scout system prompt: instructs model to return 5–10 candidates with self-scores in strict JSON. *Verify:* 3 runs on the same source each return parseable JSON with ≥5 candidates.
- Timestamp validation: reject any candidate with timestamps outside source or words missing from transcript. *Verify:* unit test with deliberately bad candidates all get rejected.

**Dev B — Clip review cards**
- Card component with thumbnail, hook title, rationale prose, 4 score bars, download button. *Verify:* card matches mockup, scores render as proportional bars.
- Thumbnail auto-generated from clip (via ffmpeg or `<video>` poster). *Verify:* thumbnails show correct moment, no broken images.
- Timeline band showing clip position in source. *Verify:* band correctly highlights [start, end] for each clip.

### Hour 6–8: Sync point + Editor agent

**Integration sync at hour 6.** Dev A and Dev B exchange: real Scout output manifest, real clip mp4 for UI testing.

**Dev A — Editor agent + tools**
- `cut` tool: ffmpeg with `-c copy` where possible. *Verify:* output duration within 100ms of requested, byte-identical content.
- `crop_9_16_center` tool: ffmpeg crop filter. *Verify:* output is 1080x1920, center column matches source.
- `burn_captions` tool: ffmpeg subtitles filter with basic styling. *Verify:* captions visible, timed correctly at spot checks.
- Editor prompt: receives candidates, picks top 3, calls tools sequentially. *Verify:* full loop produces 3 clips with no overlap.

**Dev B — Reasoning trace panel**
- Collapsible panel showing all Scout candidates with scores and rejection reasons. *Verify:* starts collapsed, `e` key toggles, expansion shows all candidates.
- Visual distinction between selected and rejected candidates. *Verify:* 3 selected candidates visually different from rejected ones.
- Clicking a timestamp seeks the relevant player. *Verify:* 5 random timestamp clicks each seek correctly.

### Hour 8–10: Full integration

**Both devs.**
- Dev A pipeline produces real output; Dev B's UI loads the real manifest. *Verify:* UI renders correctly with real pipeline output end to end.
- Run on primary demo source (unedited, fresh input). *Verify:* 3 clips produced, all play, all have rationale.
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

- **Hour 4: Scout not returning parseable JSON reliably** → switch to heavily-structured prompt with JSON-mode forced decoding, or fall back to a simpler "score each of these 10 transcript windows" approach.
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
