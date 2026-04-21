# CutPilot — Task Breakdown

**Companion to:** PRD.md
**Format:** Grouped by milestone phase. Each task includes a verifiable outcome — a concrete signal that confirms the task is genuinely done, not "works on my machine".
**Convention:** `[ ]` pending, `[x]` done, `[~]` in progress, `[!]` blocked.

---

## Phase 0 — Pre-hackathon prep

### Environment
- [ ] Confirm GPU allocation on Backend.AI — *Verify:* `nvidia-smi` on allocated node shows one free H100 with >70GB free VRAM, reservation visible in Backend.AI UI for the full hackathon window.
- [ ] Pre-pull Nemotron Nano 2 VL weights — *Verify:* model directory exists locally, SHA checksum matches Hugging Face, total size matches expected ~24GB.
- [ ] Pre-pull faster-whisper large-v3 weights — *Verify:* weights load from local path without network access.
- [ ] Verify vLLM version compatible with Nemotron Nano 2 VL — *Verify:* vLLM launches the model without errors and logs confirm EVS is initialized.
- [ ] Set up base Python env with ffmpeg, yt-dlp, faster-whisper, PySceneDetect — *Verify:* `python -c "import all_deps"` succeeds, `ffmpeg -version` returns 6.0+.
- [ ] Install Google ADK and LiteLLM — *Verify:* `pip show google-adk` returns a version, `from google.adk.agents import LlmAgent` imports without error.
- [ ] Configure vLLM with tool-calling flags — *Verify:* vLLM started with `--enable-auto-tool-choice` and a working `--tool-call-parser`; a test tool call through ADK's `LiteLlm` wrapper executes successfully.
- [ ] Smoke test vLLM with a text-only prompt — *Verify:* prompt "What is 2+2?" returns a sensible response in under 3s.
- [ ] Smoke test Nemotron Nano 2 VL with a single image — *Verify:* model describes a known test image (e.g., a stop sign) and correctly identifies it.
- [ ] Smoke test Nemotron Nano 2 VL with a 30-second video — *Verify:* model produces a description mentioning motion or scene change across the clip, inference under 15s.

### Source material
- [ ] Curate 5 candidate source videos — *Verify:* 5 mp4 files on disk, durations logged, languages labeled, all playable end-to-end with audio.
- [ ] Pre-download all sources — *Verify:* no URL in the demo pipeline; all inputs resolve from local paths.
- [ ] Manually identify 2–3 "obvious" good clip moments per source — *Verify:* ground truth CSV with source, start_ts, end_ts, reason for at least 10 moments total.
- [ ] Select primary and backup demo source — *Verify:* both labeled in a config file, distinct subject matter, both under 30 minutes.

### Project scaffolding
- [ ] Create Git repo, add README stub and PRD — *Verify:* repo pushed, PRD and README visible on remote, at least one collaborator has clone access if team.
- [ ] Define directory layout — *Verify:* empty directories exist, `.gitkeep` committed, gitignore excludes `work/` and `outputs/`.
- [ ] Define JSON schema for clip manifest — *Verify:* schema file checked in, validates against one hand-written example manifest.
- [ ] Define log format for agent trace — *Verify:* example log parses with standard JSON-lines tooling, includes timestamp, tool name, inputs, outputs, duration fields.

---

## Phase 1 — Skeleton (Hour 0–6)

Goal: End-to-end pipeline produces one clip from a 10-minute video. Ugly output is fine. No agent — hardcoded timestamps.

### Ingestion
- [ ] YouTube URL → local mp4 via yt-dlp — *Verify:* a known URL downloads, resulting mp4 plays, duration matches source within 1s.
- [ ] Local file validation — *Verify:* invalid format rejected with clear error; 5-min, 45-min, and 95-min files handled per PRD bounds (accept, accept, reject).
- [ ] Audio demux — *Verify:* output wav file exists, sample rate 16kHz, plays cleanly, duration matches source within 100ms.

### Perception wiring
- [ ] Run Whisper on demuxed audio, persist transcript JSON — *Verify:* JSON contains word-level entries with start/end/text, total word count within 10% of manual count on a 2-min sample.
- [ ] Verify timestamp accuracy — *Verify:* pick 5 random words, confirm each timestamp matches actual audio within 200ms.
- [ ] Pass a short video clip to Nemotron Nano 2 VL — *Verify:* returned description mentions at least one specific visual element present in the clip.
- [ ] Confirm EVS is active — *Verify:* vLLM logs show EVS token pruning stats, VRAM usage on a 5-min video input stays under 40GB.

### Stub tool execution
- [ ] Implement `cut` function — *Verify:* given (10s, 20s), output is exactly 10s long, byte-identical to source for those seconds when using `-c copy`.
- [ ] Implement `crop_9_16` center crop — *Verify:* output resolution is 1080x1920, duration unchanged, center column of pixels matches source center column.
- [ ] Implement `burn_captions` — *Verify:* output video plays with visible captions at correct timestamps; randomly sampled frames show correct text for that moment.
- [ ] Implement `scene_detect` — *Verify:* on a known multi-shot clip, returns expected shot count ±1, boundaries within 500ms of manual ground truth.

### End-to-end dry run
- [ ] Produce one 30-second vertical clip from hardcoded timestamps — *Verify:* output file exists at `outputs/`, plays end-to-end, 9:16 aspect, captions visible, audio in sync.
- [ ] Verify audio sync — *Verify:* lip movement matches audio within 50ms at three spot-check moments.
- [ ] Verify captions legible at 9:16 — *Verify:* captions readable when played at 50% zoom on a phone screen, no text cut off at frame edges.
- [ ] Measure total wall time — *Verify:* timing log shows total runtime under 15 min for a 30-min source; individual stage times recorded.

---

## Phase 2 — Agent loop (Hour 6–14)

Goal: Reasoning replaces hardcoded timestamps. Scout proposes, Critic filters, Editor commits.

### Agent scaffolding (Google ADK)
- [ ] Shared `LiteLlm` instance in `agents/llm.py` pointing at vLLM endpoint — *Verify:* Scout and Editor both import the same instance; changing endpoint requires a one-line edit.
- [ ] Implement 4 tools as plain Python functions with type hints + docstrings — *Verify:* each function passes its own unit test; ADK auto-wraps via `FunctionTool` at agent instantiation.
- [ ] Define `CandidatesResult` Pydantic model in `models.py` — *Verify:* model matches the schema shape; validates a hand-written example.
- [ ] Instantiate Scout as `LlmAgent(output_schema=CandidatesResult, ...)` — *Verify:* running the agent returns parseable JSON matching the schema, no free-text prose.
- [ ] Instantiate Editor as `LlmAgent(tools=TOOLS, ...)` — *Verify:* a test run triggers at least one tool call with valid arguments.
- [ ] Compose Scout → Editor via `SequentialAgent` in `agents/orchestrator.py` — *Verify:* running the sequential agent on a sample produces Scout output then Editor cuts, in order, with session state passed between them.
- [ ] Expose `root_agent` from `agents/__init__.py` — *Verify:* `adk web` can discover and load the agent graph.

### Scout role
- [ ] Draft Scout system prompt in `prompts/scout.md` — *Verify:* prompt is under 2000 tokens, explicitly lists candidate format, passes sanity read by a second person.
- [ ] Collect 5–10 candidates from Scout via ADK `output_schema` — *Verify:* ADK returns a validated `CandidatesResult` instance with ≥5 entries, all with start_ts < end_ts, durations in 20–90s range.
- [ ] Validate candidate timestamps against transcript — *Verify:* every proposed start_ts and end_ts falls within the source duration; words exist at those timestamps in the transcript.
- [ ] Self-scoring on all 4 rubric axes in the same pass — *Verify:* every candidate has integer scores 1–5 for hook, self-contained, length-fit, visual-fit; no missing fields.

### Critic role
- [ ] Draft Critic system prompt with rubric — *Verify:* rubric explicitly covers hook, self-contained, length-fit, visual-fit; each criterion produces a 1–5 score.
- [ ] Score Scout candidates — *Verify:* every candidate receives scores on all rubric axes; no NaN or missing fields.
- [ ] Filter to top 5 — *Verify:* output contains exactly 5 entries, sorted by composite score descending.
- [ ] Log rejection rationale — *Verify:* every rejected candidate has a one-sentence reason in the log.

### Editor role
- [ ] Draft Editor system prompt — *Verify:* prompt explicitly instructs boundary refinement only; never proposes new clips.
- [ ] Refine cut boundaries via scene_detect — *Verify:* for each top candidate, final boundaries align with a shot boundary within 500ms OR a transcript silence of >200ms.
- [ ] Produce final 3-clip plan — *Verify:* plan has exactly 3 clips, no time overlaps between clips, all within source duration.

### Integration
- [ ] Run full Scout → Critic → Editor on one source — *Verify:* end-to-end trace log shows all three stages completing, 3 final clips emitted.
- [ ] Capture full reasoning trace — *Verify:* log file contains every agent turn, every tool call, every intermediate output, no redactions.
- [ ] Verify three distinct clips — *Verify:* manual review confirms clips are about different moments, not minor variations of the same segment.

---

## Phase 3 — Polish (Hour 14–20)

Goal: Clips look genuinely postable. Captions styled, framing correct, boundaries clean.

### Caption styling
- [ ] Pick caption font and size — *Verify:* captions legible on a 6-inch phone display at arm's length (real device test, not emulator).
- [ ] Word-level highlighting synced to transcript — *Verify:* on a 10-word sample, every highlighted word is the word being spoken within 100ms.
- [ ] Position in lower third — *Verify:* captions never overlap with Instagram/TikTok/Shorts UI overlay zones (tested against reference overlay templates).
- [ ] 200ms padding on cut boundaries — *Verify:* no clip starts or ends mid-word; first and last words fully audible.

### Cropping
- [ ] VL-guided subject-aware crop — *Verify:* on a source with speaker off-center, speaker's face appears in the vertical frame in ≥90% of sampled frames.
- [ ] Center crop fallback — *Verify:* when VL confidence is low, crop defaults to center; logged as such; no crash.
- [ ] First-frame check — *Verify:* no output clip has a cut-off face in frame 0 across the 3 demo clips.

### Audio
- [ ] Normalize to −14 LUFS — *Verify:* `ffmpeg -af loudnorm=print_format=summary` on each output clip reports integrated loudness within ±1 LUFS of −14.
- [ ] 50ms fade in/out — *Verify:* spectrogram of first and last 100ms shows smooth envelope, no click artifact.

### Output polish
- [ ] Per-clip JSON manifest — *Verify:* each clip has a manifest, manifest validates against schema, rationale field is non-empty and human-readable.
- [ ] Reasoning-trace overlay variant — *Verify:* variant video shows the agent's selection rationale as text during the first 3s of each clip.

### Review UI (web app)
- [ ] Scaffold single HTML file with Tailwind CDN, dark mode, Nemotron purple accent — *Verify:* file opens via `file://`, renders styled page, no console errors.
- [ ] Header view with source metadata and pipeline stats — *Verify:* source filename, duration, runtime, model versions all populated from manifest, no hardcoded values.
- [ ] Before/after row component (16:9 source + 9:16 output side by side) — *Verify:* both players load their respective media files, controls work, layout holds at 1080p and 1440p.
- [ ] Lead-in / tail-out context on source player (3s each side) — *Verify:* source player starts 3s before the cut start and ends 3s after the cut end, boundaries visible on the timeline band.
- [ ] Timeline band showing clip position within full source — *Verify:* band correctly highlights the [start, end] range, labeled with timestamps that match the manifest.
- [ ] Clip review card component — *Verify:* card shows thumbnail, hook title, rationale prose, 4 Critic scores as bars, timestamps, download button.
- [ ] Hook title and rationale populated from manifest — *Verify:* text content matches the JSON manifest byte-for-byte; no lorem ipsum anywhere.
- [ ] Critic score bars render correctly for all 4 rubric axes — *Verify:* scores between 1 and 5 display as proportional bars; missing scores render as "—" not zero.
- [ ] Download-clip button wires to the local mp4 file — *Verify:* clicking triggers a download of the correct clip, file opens and plays.
- [ ] Copy-manifest-JSON button — *Verify:* clicking copies valid JSON to clipboard, paste into a JSON validator succeeds.
- [ ] Collapsible reasoning trace panel — *Verify:* starts collapsed, `e` key and click both toggle, expansion reveals all Scout candidates and Critic rationale.
- [ ] Clicking a rejected Scout candidate highlights it in context — *Verify:* visual indicator shows "rejected: <reason>" for that candidate.
- [ ] Clicking any timestamp seeks the relevant player — *Verify:* on 5 random timestamp clicks across views, the correct player jumps to that time within 200ms.
- [ ] Keyboard shortcuts (space, arrows, `e`) — *Verify:* all three shortcuts work on focused elements; documented in an on-page hint.
- [ ] CLI emits review URL on completion — *Verify:* terminal output includes a clickable `file://` URL to the review HTML.
- [ ] CLI `--open` flag auto-launches browser — *Verify:* on Linux and macOS, flag triggers default browser to open the review page.
- [ ] Self-contained output directory — *Verify:* zipping the output dir and opening on a different machine still renders correctly.

---

## Phase 4 — Demo readiness (Hour 20–28)

Goal: Multiple sources produce great output. Fallbacks ready. Everything reproducible.

### Multi-source validation
- [ ] Run pipeline on all 5 sources — *Verify:* 15 output clips exist, all play, all have manifests.
- [ ] Manually grade each output clip — *Verify:* grading spreadsheet complete with post/no-post decision and one-line reason per clip.
- [ ] Rank sources by output quality — *Verify:* top 2 sources identified and noted in demo config.
- [ ] Pre-render outputs from all sources as fallback — *Verify:* pre-rendered outputs stored, labeled, playable without running the pipeline.

### Rubric tuning
- [ ] Identify top 2 failure modes across sources — *Verify:* written list of failure modes with 2+ example clips each.
- [ ] Adjust Critic rubric — *Verify:* prompt diff committed, before/after comparison on the same source shows measurable improvement in grading spreadsheet.
- [ ] Re-run and verify improvement — *Verify:* at least 1 clip that previously graded "no-post" now grades "post" on the tuned rubric.

### Reliability
- [ ] Retry logic on ffmpeg failures — *Verify:* simulated failure (corrupt a file mid-run) causes a retry log and either recovers or skips cleanly.
- [ ] Korean-language source handled — *Verify:* Korean source produces 3 clips with Korean captions correctly rendered (Hangul not garbled).
- [ ] Multi-speaker source handled — *Verify:* source with 2+ speakers produces clips without cropping the active speaker off-frame.
- [ ] Worst-case runtime capped — *Verify:* longest source in the set completes in under 15 minutes across 3 consecutive runs.

### Observability for demo
- [ ] Live reasoning dashboard — *Verify:* during a demo run, a screen-shareable view shows agent turns streaming in real time.
- [ ] Tool calls visible on screen — *Verify:* tool name, inputs (truncated), and outputs render legibly at 1080p screen-share resolution.
- [ ] Screen-share visibility test — *Verify:* view captured via Zoom/Meet at target resolution is readable by a remote viewer.

### UI polish for demo
- [ ] Projector test on actual venue display (or equivalent 1080p projector) — *Verify:* all text legible from 5 meters, no elements clipped, color contrast passes on projected output.
- [ ] Browser zoom level locked for demo — *Verify:* demo machine has browser at a known zoom level, layout pre-verified at that level.
- [ ] Demo-mode flag hides any dev noise — *Verify:* `?demo=1` query param hides debug panels, console logs, and any placeholder states.
- [ ] All clip thumbnails pre-generated — *Verify:* no "loading thumbnail" state during the demo; thumbnails load instantly.
- [ ] Smooth scrolling between sections — *Verify:* anchor-link navigation between header, before/after, clip review, and reasoning trace scrolls smoothly with no layout shift.
- [ ] Reasoning trace reveal rehearsed — *Verify:* expanding the reasoning panel during rehearsal produces the intended "aha" moment timing.

---

## Phase 5 — Pitch (Hour 28–36)

Goal: Win the showcase. Clear narrative, crisp demo, no dead air.

### Deck
- [ ] Opening slide, one-sentence problem — *Verify:* a non-technical reader understands the problem in under 10 seconds.
- [ ] Agentic-vs-classifier slide — *Verify:* slide contains a concrete example where classifier approach fails and agent succeeds.
- [ ] Architecture slide — *Verify:* uses the approved architecture diagram; labels match the PRD.
- [ ] Reasoning trace example slide — *Verify:* shows a real trace from a real demo source, not a mock.
- [ ] Before/after slide — *Verify:* shows 2 pairs of source frame + output clip thumbnail with timestamps.
- [ ] Metrics slide — *Verify:* runtime, post/no-post grade, and failure rate all populated with measured numbers, not estimates.
- [ ] Closing/next-steps slide — *Verify:* lists 3 concrete post-hackathon extensions, not vague aspirations.

### Demo script
- [ ] Opening hook written — *Verify:* hook is under 30 seconds when read aloud, timed with a stopwatch.
- [ ] Live-demo narration synced to expected runtime — *Verify:* narration length matches actual pipeline runtime within ±30s on rehearsal.
- [ ] Fallback narration for stalled demo — *Verify:* fallback triggers cleanly on a simulated stall, no awkward silence.
- [ ] Full pitch + demo rehearsed twice — *Verify:* both rehearsals completed end-to-end, timing logged, issues noted.
- [ ] Pitch trimmed to fit slot — *Verify:* final timed rehearsal fits slot with at least 30 seconds of buffer.

### Contingency
- [ ] Golden-path backup video pre-rendered — *Verify:* video plays from a local file, covers the full demo, no network dependency.
- [ ] Q&A answers prepared — *Verify:* written one-paragraph answer each for: vs Opus Clip/Vizard, why Nemotron, scaling/cost, content rights.

### Showcase day
- [ ] Arrive early, verify venue setup — *Verify:* laptop connects to venue display, screen-share works, audio routes correctly.
- [ ] Warm up GPU and pre-load models 30 min before slot — *Verify:* models resident in VRAM, first inference under 2s.
- [ ] Backup video queued — *Verify:* backup playable in one keystroke from the demo machine.
- [ ] Deliver pitch — *Verify:* pitch completed within slot, demo ran.
- [ ] Post-pitch feedback captured — *Verify:* written notes from at least 3 judges/attendees within 2 hours of the slot.

---

## Cross-cutting

### Testing
- [ ] Unit test each tool — *Verify:* pytest suite runs in under 60s, all 4 tool tests pass.
- [ ] Integration test on 2-min video — *Verify:* pipeline produces exactly 1 clip (smaller source, reduced target), runtime under 2 min.
- [ ] Regression test after each major change — *Verify:* primary demo source still produces 3 acceptable clips; checksum of manifest matches or is deliberately updated.

### Documentation
- [ ] README with setup — *Verify:* a teammate following only the README can run the pipeline end-to-end on a fresh machine.
- [ ] Architecture diagram in repo — *Verify:* diagram renders correctly in GitHub's markdown view.
- [ ] Example command lines — *Verify:* every documented command, copied verbatim, executes without modification.

### Stretch goals (only if genuinely ahead)
- [ ] Face-tracking smart crop — *Verify:* on a multi-person source, active speaker's face is in frame in ≥95% of sampled frames.
- [ ] Bilingual caption generation — *Verify:* a Korean source produces clips with dual Korean + English captions, both timed correctly.
- [ ] A/B hook variants per clip — *Verify:* output contains 2 variants per clip with distinguishably different first 3 seconds.
- [ ] Hook text overlay in first 2s — *Verify:* overlay text is present, readable, and matches the clip's rationale.
- [ ] Creator-style fine-tune — *Verify:* fine-tuned model's output on a held-out source is rated "more on-brand" than base by 2+ reviewers.

---

## Risks that should trigger replanning

Each risk has a detection signal and a fallback plan. If detected, stop and execute the fallback before continuing.

- **VL video inference too slow** — *Detect:* full-video inference on a 10-min source exceeds 2 minutes. *Fallback:* sample frames at transcript cue points instead of passing full video.
- **Whisper timestamps drift** — *Detect:* spot check shows >500ms drift on 3+ samples. *Fallback:* add WhisperX forced-alignment pass after transcription.
- **Agent hallucinates timestamps** — *Detect:* validation rejects >10% of candidates for timestamps outside source range. *Fallback:* constrain Scout to select from a pre-computed candidate pool keyed off transcript sentence boundaries.
- **ffmpeg edge cases eating time** — *Detect:* more than 8 hours total spent debugging ffmpeg. *Fallback:* drop video understanding from live demo, pitch an audio-only podcast clipper with visual shown as pre-rendered.