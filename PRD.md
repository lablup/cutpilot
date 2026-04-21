# CutPilot — Product Requirements Document

**Version:** 0.1 (Hackathon draft)
**Author:** Sergey
**Last updated:** April 21, 2026
**Event:** NVIDIA Nemotron Developer Days Seoul 2026 — Track A (Creative Agentic Systems)

---

## 1. Overview

CutPilot is an agentic system that turns a single long-form video (podcast, lecture, interview, keynote) into a small set of publish-ready vertical short clips. The system combines an audio perception model (Whisper) with a video-capable multimodal reasoning model (Nemotron Nano 2 VL) acting as the decision-making agent. The agent has access to a constrained set of editing tools and is responsible for selecting moments, justifying the selection, and producing the final artifacts.

The name signals the core positioning: an autopilot for editorial cut decisions, not a cutting utility.

## 2. Problem statement

Creators with long-form content face a content-multiplication problem: every long video should produce 3–10 shorts for social distribution, but manual editing takes hours per source and requires editorial judgment (where does the hook start, where does the payoff land, what crops well to 9:16). Existing automated tools either (a) pick clips by naive heuristics like loudness spikes or caption keyword matches, or (b) summarize without actually cutting. Neither explains *why* a moment was chosen, and neither produces broadcast-ready output.

Jjal closes this gap by treating short-form selection as an agentic reasoning task rather than a one-shot generation task.

## 3. Goals

- Convert a 20–60 minute source video into 3 vertical shorts (30–60 seconds each) without human intervention.
- Produce a reasoning trace for each clip explaining why the moment was selected.
- Deliver clips with burned-in captions, correct 9:16 framing, and normalized audio.
- Run end-to-end on a single GPU using open models only (Whisper + Nemotron Nano 2 VL).
- Be demonstrable live during the hackathon final showcase.

## 4. Non-goals

- Multi-person smart-crop with face tracking (v2).
- Music/sound effect addition, transitions, or motion graphics.
- Real-time or streaming operation.
- Multi-language output translation (source language is preserved).
- Social platform publishing integration.
- Editing of existing shorts or re-editing based on user feedback loops.

## 5. Target users

Primary: Independent creators, podcast producers, and educators who publish long-form content and need short-form derivatives.

Secondary: Marketing teams repurposing webinars, conference organizers clipping talks, journalists generating social teasers from interviews.

For the hackathon, the demo audience is NVIDIA judges and the K-AI community — so the demo source videos should be technically interesting (conference talks, interviews).

## 6. User stories

- As a podcaster, I drop a 45-minute episode into the system and receive three 45-second vertical clips with captions, each with an explanation of why it was selected, so I can post them to TikTok and Shorts without editing time.
- As a conference organizer, I batch-process recorded talks and get social-ready clips per session, so speakers receive promotional material the day after the event.
- As a hackathon judge, I watch a live demo where a raw video goes in, reasoning appears on screen, and finished shorts come out — so I can evaluate whether the agentic approach is genuinely better than a classifier.

## 7. Functional requirements

### 7.1 Input

- Accept a local video file (mp4, mov, mkv) or a YouTube URL.
- Source duration range: 5–90 minutes. Outside this range, reject with a clear message.
- Source audio must be intelligible speech in a single primary language (Korean or English for the demo).

### 7.2 Perception layer

- The audio track must be transcribed with word-level timestamps.
- Transcription accuracy on clean English speech must be sufficient for caption burn-in (target: well under one visible error per 30-second clip on demo material).
- The video track must be made available to the reasoning model in a form suitable for semantic understanding of visual content, supporting the model's native video input.

### 7.3 Reasoning and selection layer

The system uses a two-agent workflow built on the **NVIDIA NeMo Agent Toolkit** (`nvidia-nat`). The toolkit is config-driven: agents, tools, and their composition are declared in a single YAML workflow (`configs/cutpilot.yml`). Both agents are backed by Nemotron Nano 2 VL served through NVIDIA NIM; NAT's `_type: nim` LLM provider handles tool-calling and schema validation, so no custom agent runtime is written.

- **Scout** — a deterministic NAT function (`@register_function`) that receives the source video and transcript, calls the NIM VL endpoint once, and returns a validated `CandidatesResult`. In that single pass it proposes 5–10 candidate moments with start/end timestamps, a hook description, a rationale, and self-scored ratings on four axes (hook strength, self-containedness, length fit, visual fit). Scout has no tools — its return type is the schema. Self-critique replaces a separate critic pass.
- **Editor** — a NAT `tool_calling_agent` that receives Scout's ranked candidates, selects the top 3, uses tools to validate timestamps and refine cut boundaries, then commits the final cuts. Editor is the only component with write-access tools.
- **Orchestrator** — a NAT `sequential_executor` workflow composing `[scout, editor]`, runnable via `nat run --config_file=configs/cutpilot.yml` or through the `cutpilot` CLI, which loads the same workflow programmatically.

Requirements:

- Scout must produce at least 5 valid candidates. Candidates must have start_ts < end_ts, both within source duration, and duration in the 20–90s range.
- Self-scoring must cover all four rubric axes with integer values 1–5.
- Editor must reject any candidate whose timestamps cannot be validated against the transcript.
- Editor must produce exactly 3 final clips with no time overlap.
- The complete reasoning trace from both agents must be retained and surfaced in the output.

### 7.4 Tool layer

The agent must have access to, and only to, the following tools:

- **Scene detection** — returns shot boundaries around a given timestamp, used to avoid cutting mid-gesture or mid-shot.
- **Cut** — extracts a time range from the source video without re-encoding where possible.
- **Caption burn-in** — renders transcript text onto a video file with styling parameters (font size, position, highlight style).
- **Crop to 9:16** — reframes horizontal source video to vertical output.

The agent must not have access to arbitrary shell execution, arbitrary HTTP, or any write access outside a designated working directory.

### 7.5 Output

- Three mp4 files, each 30–60 seconds, 9:16 aspect ratio, with burned-in captions.

- A sidecar JSON manifest per clip containing: source timestamps, rationale, caption text, confidence score.
- A combined HTML review app (see 7.6) summarizing all three clips.

### 7.6 Review UI

The review UI is the primary surface judges see during the pitch. It is a single-page static web app, loaded from a local file or localhost, that reads the pipeline's output manifest and renders a polished before/after + clip review experience.

**Form factor and constraints**

- Single HTML file, runnable via `file://` or a trivial local server. No backend service, no authentication, no external API calls beyond CDN-hosted assets.
- Tailwind via CDN for styling; vanilla JS or Alpine.js; no build step required.
- Dark mode by default. Nemotron purple as the accent color.
- Must render correctly on a 1080p projector and on a laptop at 1440p.
- Load time under 2 seconds on a local file system.

**Views (stacked vertically on a single page)**

1. **Header** — project name, source video filename, source duration, processing timestamp, total pipeline runtime, model versions (Whisper and Nemotron Nano 2 VL).
2. **Before/after comparison** — one row per output clip. Left side plays the source segment at 16:9 with 3 seconds of lead-in and tail-out context. Right side plays the final 9:16 clip with burned captions. Both players synchronized scrubbing where practical. A thin timeline band above each row shows where in the full source this clip came from.
3. **Clip review** — one card per clip. Each card contains: a thumbnail with play button, the AI-generated hook title, a two-to-three sentence rationale in prose, Critic rubric scores (hook, self-contained, length-fit, visual-fit) rendered as small bar indicators, source start/end/duration timestamps, and actions (download clip, copy manifest JSON).
4. **Reasoning trace (collapsible)** — expandable panel showing the full agent log: Scout candidates with rationale, Critic scores and rejection reasons, Editor boundary adjustments. Collapsed by default; expanded during the demo as the reveal moment.

**Interaction**

- Clicking any timestamp in any view seeks the relevant player to that timestamp.
- Clicking a Scout candidate in the reasoning trace highlights the corresponding final clip (or indicates it was rejected and why).
- Keyboard: space to play/pause the focused player, arrow keys to scrub, `e` to expand the reasoning trace. Used during live demo to avoid fumbling with the mouse.

**CLI integration**

- The CLI command that runs the pipeline emits a local URL to open the review UI when processing completes.
- The CLI has a `--open` flag that automatically launches the default browser on the review page.
- The review HTML file is self-contained within the output directory — it can be zipped and shared without the pipeline.

## 8. Non-functional requirements

### 8.1 Performance

- Processing a 30-minute source video to finished output must complete in under 15 minutes on a single H100-class GPU.
- The reasoning and tool-calling phase alone must complete in under 5 minutes for a 30-minute input.

### 8.2 Reliability

- The system must degrade gracefully when a tool fails (e.g., ffmpeg error on a specific cut): skip that candidate and continue with the next.
- Transcription errors must not crash the pipeline.

### 8.3 Observability

- Every tool call by the agent must be logged with inputs, outputs, and duration.
- The final output must include the full reasoning trace per selected clip, suitable for live demonstration.

### 8.4 Resource constraints

- Single GPU deployment.
- No external API dependencies beyond optional YouTube download at ingest time.
- All models must be open-weight.

## 9. Technical architecture

### 9.1 Models and framework

- **NVIDIA NeMo Agent Toolkit (`nvidia-nat`)** — Python toolkit (CLI: `nat`) that defines agents, tools, and multi-agent workflows via YAML. Agent types used here: `tool_calling_agent` for the Editor and `sequential_executor` for the orchestrator. Tools register with `@register_function` and are discovered via the `[project.entry-points.'nat.components']` table in `pyproject.toml`. Tools are decorated with `framework_wrappers=[LLMFrameworkEnum.ADK]` so they remain portable to NAT's `_type: adk` workflow if we ever switch. Extras in use: `nvidia-nat[langchain]`, `nvidia-nat[adk]`, `nvidia-nat[mcp]`.
- **Whisper-Large via NVIDIA Riva NIM** — audio transcription and word-level timestamps. Served by the Riva ASR NIM at `0.0.0.0:8100` and consumed through `nvidia-riva-client` (gRPC, not OpenAI-compatible). Riva lives outside the NAT `llms:` block — it's a perception-stage client called from `pipeline.py`.
- **Nemotron-3 Nano 30B A3B (text reasoning)** — powers the Editor's tool-calling loop. Served by NIM as `nvidia/nemotron-3-nano-30b-a3b` on `0.0.0.0:8000` (OpenAI-compatible `/v1/chat/completions`). Text-only is sufficient for the Editor — it receives Scout's structured candidates, validates timestamps, and calls ffmpeg tools.
- **Nemotron Nano 12B V2 VL (vision-language)** — powers Scout and any on-demand frame analysis. Served by NIM as `nvidia/nemotron-nano-12b-v2-vl` on `0.0.0.0:9000`. Natively supports image URLs and video URLs in the request body, benefits from Efficient Video Sampling (EVS), and is purpose-built for video curation workloads.

### 9.2 Infrastructure

- Deployed on an **NVIDIA Brev** H100 Launchable (provisioned via the Brev CLI; single node co-locates all three NIM containers to avoid cross-node I/O).
- Three NIM containers run on the instance:
  - Riva Whisper-Large ASR → port `8100` (gRPC).
  - Nemotron-3 Nano 30B A3B (text) → port `8000` (OpenAI-compat).
  - Nemotron Nano 12B V2 VL (vision-language) → port `9000` (OpenAI-compat).
- Tool-calling is handled natively by NIM — no vLLM tool-choice flags to configure.
- Agent orchestration is handled by the NeMo Agent Toolkit. A thin Python pipeline (`src/cutpilot/pipeline.py`) wraps non-agent stages (ingest, Riva transcribe, persist) and delegates agent execution to the NAT workflow in `src/cutpilot/configs/cutpilot.yml`.

### 9.3 Data flow

Source video enters the pipeline. Audio is demuxed and passed to Whisper, producing a transcript with word-level timestamps. The original video and the transcript are both passed to Scout (Nemotron Nano 2 VL), which proposes and self-scores 5–10 candidates. Editor (same model, different prompt and tool access) receives the ranked candidates, calls tools to validate timestamps and refine cut boundaries, and commits the final three cuts. Outputs are written to a clip directory and summarized in a manifest that powers the Review UI.

## 10. Success metrics

### 10.1 Hackathon demo metrics

- Three finished clips produced live on stage from a previously unseen source video within the allotted demo window.
- Reasoning trace visible and legible on each clip.
- Judges can identify at least one clip they would consider posting without further editing.

### 10.2 Quality metrics (measured offline on a curated test set)

- Clip self-containedness: a viewer watching only the clip understands what is happening (subjective 1–5 rating).
- Hook strength: first 3 seconds of the clip contains a question, claim, or visual intrigue.
- Caption accuracy: transcript matches audio, timed within 200ms.
- Cut cleanliness: clips do not start or end mid-word or mid-gesture.

## 11. Constraints and assumptions

- Hackathon development window is approximately 36 hours.
- Source videos for development and demo are available in advance.
- Nemotron Nano 2 VL inference is available through a self-hosted NIM container on a Brev H100 Launchable, with hosted NIM (`build.nvidia.com`, `NVIDIA_API_KEY`) as fallback.
- Source videos have a clear primary speaker and are not heavily edited montages.

## 12. Risks

- **Inference latency risk**: if VL processing of full video is too slow, the agent loop blows the time budget. Mitigation: down-sample video resolution before passing to the model and rely on EVS, and cap analyzed video length to a sliding window guided by the transcript.
- **NIM endpoint availability**: self-hosted NIM container on Brev may fail to start or rate-limit under load; hosted NIM may return 401/429. Mitigation: `NIM_BASE_URL` defaults to the self-hosted container but can be pointed at `integrate.api.nvidia.com/v1` with `NVIDIA_API_KEY` in a single env-var flip; smoke-test both paths in Phase 0.
- **ffmpeg edge cases**: audio drift, codec mismatch, or container issues can corrupt output. Mitigation: use copy-codec cuts where possible and re-encode only when adding captions or cropping.
- **Cut boundary quality**: word-level timestamps drift around laughter and music. Mitigation: pad cuts by 200ms on both sides and use scene detection as a secondary signal.
- **Crop quality for multi-person scenes**: a naive center crop fails when speakers are off-center. Mitigation: acknowledged as v2 scope; demo material selected to be compatible with center crop.
- **Agent hallucinates timestamps**: the VL model cites moments that do not exist or cuts into dead air. Mitigation: validate every proposed timestamp against the transcript and scene detection output before cutting.

## 13. Milestones

- **Hour 0–6**: End-to-end skeleton with a hardcoded single clip on a 10-minute source. Both models wired, ugly output, working pipeline.
- **Hour 6–14**: Agent reasoning loop with Scout and Critic roles. Tool calls functional. Rationale capture working.
- **Hour 14–20**: Caption styling, 9:16 cropping, final Editor pass for precise cut boundaries.
- **Hour 20–28**: Polish on 3–5 demo source videos. Tune rubric. Generate before/after pairs for the pitch.
- **Hour 28–36**: Pitch deck, live demo rehearsal, contingency plan for live demo failure (pre-rendered backup).

## 14. Out of scope for the hackathon

The following are explicitly deferred to any post-hackathon work:

- Face-tracking smart crop.
- Multi-language translation.
- User feedback loop for clip selection.
- Publishing integration with social platforms.
- Batch processing across many sources.
- Evaluation harness with human raters.
- Fine-tuning Nemotron Nano 2 VL on creator-specific style.