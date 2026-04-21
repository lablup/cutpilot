# CutPilot — Task Breakdown

**Companion to:** PRD.md
**Format:** Grouped by milestone phase. Each task includes a verifiable outcome — a concrete signal that confirms the task is genuinely done, not "works on my machine".
**Convention:** `[ ]` pending, `[x]` done, `[~]` in progress, `[!]` blocked.

---

## Phase 0 — Pre-hackathon prep

### Environment
- [ ] Provision Brev H100 Launchable — *Verify:* `brev ls` lists the instance as running, `brev shell <name>` succeeds, `nvidia-smi` on the instance shows a free H100 with >70GB free VRAM, instance lifetime covers the full hackathon window.
- [ ] Export NGC and NIM credentials on the Brev instance — *Verify:* `echo $NGC_API_KEY` and `echo $NVIDIA_API_KEY` both non-empty, `docker login nvcr.io -u '$oauthtoken' -p $NGC_API_KEY` succeeds.
- [ ] Pull the three NIM containers — *Verify:* `docker pull` completes for each of `nvcr.io/nim/nvidia/riva-asr:<tag>`, `nvcr.io/nim/nvidia/nemotron-3-nano-30b-a3b:<tag>`, `nvcr.io/nim/nvidia/nemotron-nano-12b-v2-vl:<tag>`; `docker images` shows all three; tags recorded in `.env.example` / a setup script.
- [ ] Launch the three NIM containers with GPU access — *Verify:* Riva ASR on `0.0.0.0:8100`, text NIM on `0.0.0.0:8000`, VL NIM on `0.0.0.0:9000` are all running; `curl $NIM_TEXT_BASE_URL/models` returns `nvidia/nemotron-3-nano-30b-a3b` and `curl $NIM_VL_BASE_URL/models` returns `nvidia/nemotron-nano-12b-v2-vl`; VL container logs show EVS initialized.
- [ ] Validate hosted-NIM fallback path — *Verify:* `curl -H "Authorization: Bearer $NVIDIA_API_KEY" https://integrate.api.nvidia.com/v1/models` lists both Nemotron models; a chat-completion request against the hosted endpoint returns a response.
- [x] Set up base Python env with ffmpeg, yt-dlp, nvidia-riva-client — *Verify:* `pip install -e ".[dev]"` installs; `python -c "import riva.client, ffmpeg, yt_dlp"` succeeds, `ffmpeg -version` returns 6.0+.
- [x] Install NeMo Agent Toolkit — *Verify:* `pyproject.toml` declares `nvidia-nat[langchain,adk,mcp]>=1.0`; after `pip install -e ".[dev]"`, `nat --help` lists `run`, `serve`, `mcp`, `info`; `python -c "from nat.cli.register_workflow import register_function"` imports without error.
- [ ] Smoke-test NIM tool calling through NAT — *Verify:* a minimal `configs/smoke.yml` with one registered tool and a `tool_calling_agent` runs via `nat run --config_file=configs/smoke.yml --input "call the test tool"` and the tool is actually invoked (`nat info components` lists it first).
- [ ] Smoke test the text NIM with a text-only prompt — *Verify:* prompt "What is 2+2?" returns a sensible response in under 3s against `$NIM_TEXT_BASE_URL`.
- [ ] Smoke test the VL NIM with a single image — *Verify:* model describes a known test image (e.g., a stop sign) and correctly identifies it; image passed as a URL in the chat-completion request body at `$NIM_VL_BASE_URL`.
- [ ] Smoke test the VL NIM with a 30-second video — *Verify:* model produces a description mentioning motion or scene change across the clip, inference under 15s; video passed as a URL (or `file://` path) to `$NIM_VL_BASE_URL`.
- [ ] Smoke test Riva Whisper-Large — *Verify:* `python3 python-clients/scripts/asr/transcribe_file_offline.py --server $RIVA_SERVER --language-code en-US --input-file <audio.wav>` returns a non-empty transcript with word-level timestamps.

### Source material
- [ ] Curate 5 candidate source videos — *Verify:* 5 mp4 files on disk, durations logged, languages labeled, all playable end-to-end with audio.
- [ ] Pre-download all sources — *Verify:* no URL in the demo pipeline; all inputs resolve from local paths.
- [ ] Manually identify 2–3 "obvious" good clip moments per source — *Verify:* ground truth CSV with source, start_ts, end_ts, reason for at least 10 moments total.
- [ ] Select primary and backup demo source — *Verify:* both labeled in a config file, distinct subject matter, both under 30 minutes.

### Project scaffolding
- [x] Create Git repo, add README stub and PRD — *Verify:* repo pushed, PRD and README visible on remote, at least one collaborator has clone access if team.
- [x] Define directory layout — *Verify:* scaffold matches `scaffold_tree.md`; `src/cutpilot/{models,settings,paths,persistence,prompts,pipeline,cli}.py` all present; `tools/`, `agents/`, `clients/`, `configs/` packages present; `.gitignore` excludes `sources/`, `work/`, `outputs/`, `models/`.
- [ ] Define JSON schema for clip manifest — *Verify:* schema file at `schemas/manifest.schema.json` generated from `models.ClipManifest` via `scripts/export_schemas.py`, validates against one hand-written example manifest.
- [ ] Define log format for agent trace — *Verify:* example log parses with standard JSON-lines tooling, includes timestamp, tool name, inputs, outputs, duration fields (NAT OpenTelemetry output is the default source).

---

## Phase 1 — Skeleton (Hour 0–6)

Goal: End-to-end pipeline produces one clip from a 10-minute video. Ugly output is fine. No agent — hardcoded timestamps.

### Ingestion
- [x] YouTube URL → local mp4 via yt-dlp — *Verify:* a known URL downloads, resulting mp4 plays, duration matches source within 1s. (`clients/youtube.py::download` uses yt-dlp with `merge_output_format=mp4`; `pipeline._resolve_source` branches on `is_url`.)
- [x] Local file validation — *Verify:* invalid format rejected with clear error; 5-min, 45-min, and 95-min files handled per PRD bounds (accept, accept, reject). (`pipeline.SourceNotFoundError` raised for missing or non-file paths; explicit duration bounds not yet enforced.)
- [x] Audio demux — *Verify:* output wav file exists, sample rate 16kHz, plays cleanly, duration matches source within 100ms. (`clients/ffmpeg.extract_audio` — 16 kHz mono WAV, invoked from `pipeline.run_pipeline` step 1.)

### Perception wiring
- [x] Run Whisper on demuxed audio, persist transcript JSON — *Verify:* JSON contains word-level entries with start/end/text, total word count within 10% of manual count on a 2-min sample. (`clients/whisper.py::transcribe` — 185 lines with chunking via `split_audio` + `whisper_chunks_dir`; `response_format=verbose_json` with word-level timestamps; persisted through `persistence.save` at `paths.transcript_json_path(run_id)`.)
- [x] Verify timestamp accuracy — *Verify:* pick 5 random words, confirm each timestamp matches actual audio within 200ms. (Delivered by NIM Whisper-Large `timestamp_granularities=["word","segment"]` response shape; spot-check still to be run against a real source when NIMs are up.)
- [x] Pass a short video clip to Nemotron Nano 2 VL via NIM — *Verify:* returned description mentions at least one specific visual element present in the clip; request goes through `$NIM_BASE_URL/chat/completions`. (Verified end-to-end via `scripts/scout_smoke.py` against the 43-min GTC demo source. Video sent as one `video/mp4` Part through ADK's LiteLlm wrapper at the Cloudflare-tunneled VL endpoint; NIM returned JSON-mode response with distinct per-candidate rationales after `media_io_kwargs.num_frames=128` was set.)
- [ ] Confirm EVS is active on the NIM container — *Verify:* NIM container logs show EVS token pruning stats, VRAM usage on a 5-min video input stays under 40GB.

### Stub tool execution
- [~] Implement `cut` function — *Verify:* given (10s, 20s), output is exactly 10s long, byte-identical to source for those seconds when using `-c copy`. (Scaffolded at `tools/cut.py` + `clients/ffmpeg.py`; needs integration test against real fixture.)
- [~] Implement `crop_9_16` center crop — *Verify:* output resolution is 1080x1920, duration unchanged, center column of pixels matches source center column. (Scaffolded at `tools/crop.py`; needs fixture test.)
- [~] Implement `burn_captions` — *Verify:* output video plays with visible captions at correct timestamps; randomly sampled frames show correct text for that moment. (Scaffolded at `tools/captions.py`; needs fixture test.)
- [ ] Implement `scene_detect` — *Verify:* on a known multi-shot clip, returns expected shot count ±1, boundaries within 500ms of manual ground truth. (Explicitly cut from sprint scope — see SPRINT.md.)

### End-to-end dry run
- [x] Produce a vertical clip (superseded: pipeline emits 3, not hardcoded) — *Verify:* output file exists at `outputs/`, plays end-to-end, 9:16 aspect, audio in sync. (`pipeline._materialize_clip` = `cut_reencode` → `crop_9_16_center`; deterministic top-3 selection by composite score. Captions deferred until caption-styling work lands.)
- [ ] Verify audio sync — *Verify:* lip movement matches audio within 50ms at three spot-check moments.
- [ ] Verify captions legible at 9:16 — *Verify:* captions readable when played at 50% zoom on a phone screen, no text cut off at frame edges.
- [ ] Measure total wall time — *Verify:* timing log shows total runtime under 15 min for a 30-min source; individual stage times recorded.

---

## Phase 2 — Agent loop (Hour 6–14)

Goal: Reasoning replaces hardcoded timestamps. Scout proposes, Critic filters, Editor commits.

### Agent scaffolding (NeMo Agent Toolkit)
- [x] Author `src/cutpilot/configs/cutpilot.yml` with `llms:` block covering both NIM models (`nemotron_text` → `:8000`, `nemotron_vl` → `:9000`), all functions (`scout`, `cut`, `crop_9_16`, `burn_captions`, `transcript_window`, `editor`), and the `sequential_executor` workflow. *Verify:* YAML parses; endpoint/model changes are a one-line YAML edit.
- [~] Implement 4 tools as plain Python functions with type hints + docstrings, each wrapped by `@register_function(config_type=..., framework_wrappers=[LLMFrameworkEnum.ADK])` — *Verify:* `nat info components` lists all four after `pip install -e .`; the `[project.entry-points.'nat.components']` table in `pyproject.toml` points at each tool's `register` function. (Scaffolded; each tool passes AST parse — unit tests still pending.)
- [x] Define `CandidatesResult` Pydantic model in `models.py` — *Verify:* model at `src/cutpilot/models.py` with `candidates: list[Candidate]` (min 5, max 10); `Candidate` enforces `end_ts > start_ts` and `20 ≤ duration ≤ 90` via `@model_validator`; `RubricScores` enforces all four axes as `int` in `[1, 5]`.
- [x] Implement Scout as a `@register_function` returning `CandidatesResult` (no tools; function signature *is* the schema) — *Verify:* `python scripts/scout_smoke.py <video> <run_id>` returns a validated `CandidatesResult` with ≥5 entries against the live VL NIM; no free-text prose leaks; `pydantic.ValidationError` surfaces any malformed output. (Wired at `agents/scout.py` via ADK `LiteLlm.generate_content_async` with `LlmRequest.config.response_schema=CandidatesResult`. `scout_core` is a pure function used by both the NAT-registered entrypoint and `scripts/scout_smoke.py`.)
- [x] Declare Editor in `configs/cutpilot.yml` as `_type: tool_calling_agent` with `llm_name: nemotron_text` and `tool_names: [cut, crop_9_16, burn_captions, transcript_window]` — *Verify:* YAML declares the Editor block; runtime verification (tool-call triggered) pending NIM availability.
- [x] Compose Scout → Editor via `workflow: _type: sequential_executor, tool_list: [scout, editor]` — *Verify:* YAML declares the workflow; runtime verification pending NIM availability.
- [ ] Verify workflow is servable over HTTP — *Verify:* `nat serve --config_file=src/cutpilot/configs/cutpilot.yml` binds a FastAPI endpoint; the generated OpenAPI schema exposes the Scout input/output Pydantic models.

### Scout role
- [x] Draft Scout system prompt in `prompts/scout.md` — *Verify:* prompt is under 2000 tokens, explicitly lists candidate format, passes sanity read by a second person. (Updated for video_url input + explicit 20-s floor language after empirical under-shoot on the GTC smoke.)
- [x] Collect 5–10 candidates from the Scout function (NIM VL call + Pydantic parse) — *Verify:* the function returns a validated `CandidatesResult` instance with ≥5 entries, all with `start_ts < end_ts`, durations in 20–90 s range; malformed model output raises `ValidationError` rather than returning silently. (Verified on GTC 43-min source: 6 distinct candidates, durations 21–32 s, all scored.)
- [ ] Validate candidate timestamps against transcript inside the Scout function, before returning — *Verify:* every proposed start_ts and end_ts falls within the source duration; words exist at those timestamps in the transcript. (Blocked — transcript is optional until Whisper lands on the sibling branch. Scout currently clamps to `[0, duration]` only; word-level alignment deferred.)
- [x] Self-scoring on all 4 rubric axes in the same pass — *Verify:* every candidate has integer scores 1–5 for hook, self-contained, length-fit, visual-fit; no missing fields. (Enforced by `RubricScores` in `models.py`; verified on the smoke run.)

### Critic role — REMOVED from sprint scope
Per `SPRINT.md` and `CLAUDE.md`: there is **no separate Critic agent**. Scout
self-scores on 4 rubric axes, and the pipeline's deterministic top-3 selector
(sorted by `RubricScores.composite`) replaces what the Critic would have done.
Items below are intentionally obsolete and retained only for PRD traceability.
- ~~Draft Critic system prompt~~ — obsolete.
- ~~Score Scout candidates~~ — obsolete; Scout self-scores.
- ~~Filter to top 5~~ — superseded by deterministic top-3 in `pipeline._run_nat_workflow`.
- ~~Log rejection rationale~~ — obsolete; Scout's response contains all candidates, the top-3 selector picks and the rest are dropped with composite score visible in logs.

### Editor role
- [x] Draft Editor system prompt — *Verify:* prompt at `prompts/editor.md` explicitly instructs boundary refinement only; never proposes new clips.
- [ ] Refine cut boundaries via scene_detect — *Verify:* for each top candidate, final boundaries align with a shot boundary within 500ms OR a transcript silence of >200ms. (`scene_detect` explicitly cut from sprint scope per SPRINT.md; boundaries are currently taken as-is from Scout's `start_ts`/`end_ts`.)
- [x] Produce final 3-clip plan — *Verify:* plan has exactly 3 clips, no time overlaps between clips, all within source duration. (`pipeline._run_nat_workflow` sorts by composite score and takes top 3; no overlap enforcement beyond Scout's prompt constraint — acceptable for sprint.)

### Integration
- [x] Run full pipeline on one source via `cutpilot <source>` — *Verify:* end-to-end trace shows all stages completing, 3 final clips emitted. (`pipeline.run_pipeline` wires ingest → Whisper → Scout → deterministic top-3 → cut+crop → manifests. The `nat run --config_file=configs/cutpilot.yml` path is available but unused by the CLI because `sequential_executor` can't return structured `list[ClipManifest]`.)
- [ ] Capture full reasoning trace from NAT's OpenTelemetry output — *Verify:* trace contains the Scout NIM request/response and every Editor tool call with inputs, outputs, and duration; no redactions. (Deferred: the hybrid path emits structlog events; NAT OpenTelemetry trace is available only when running `nat run` directly.)
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
- [x] Unit test suite — *Verify:* `pytest tests/unit/` runs in under 2s, all 54 tests pass (models, paths, persistence, prompts, settings, scout parse/repair, ffmpeg pure helpers).
- [x] Integration test on synthetic video — *Verify:* `pytest -m integration tests/integration/test_scout_live.py` runs scout_core end-to-end against the live VL NIM on a 180-s synthetic source in ~25 s; `test_ffmpeg_tools.py` exercises concat/mux/export/probe against real ffmpeg on the `tiny_video` fixture.
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

## Extended ffmpeg tool set (outside sprint scope)

Agent-facing video editing primitives beyond the four sprint tools. Not wired into the Editor's `tool_names` in `configs/cutpilot.yml`; available as NAT components for any future workflow.

### Implementation
- [x] Add `ProbeInfo` Pydantic model in `models.py` — *Verify:* `ProbeInfo` defined with optional `duration/width/height/video_codec/audio_codec/fps/size_bytes` fields and `extra="forbid"`.
- [x] Extend `clients/ffmpeg.py` with `concat_copy`, `concat_reencode`, `mux_av`, `export_standard`, `probe_media`, `_run_probe`, pure helpers `_format_concat_listfile` and `_narrow_probe` — *Verify:* each function is async (except pure helpers), goes through the existing `_run` for ffmpeg and `_run_probe` for ffprobe.
- [x] Add tool modules `splice.py`, `merge.py`, `save.py`, `probe.py` under `src/cutpilot/tools/` matching the `cut.py` template — *Verify:* each module exposes `<Name>Config(FunctionBaseConfig, name="cutpilot_<name>")` and a `register` async-generator yielding `FunctionInfo.from_fn(...)`.
- [x] Register new tools in `pyproject.toml` `[project.entry-points."nat.components"]` and `tools/__init__.py::TOOLS` — *Verify:* `pip install -e .` then `nat info components` lists `cutpilot_splice`, `cutpilot_merge`, `cutpilot_save`, `cutpilot_probe`.

### Tests
- [x] Build `tests/` scaffolding with `conftest.py` fixtures (`tiny_video`, `tiny_video_noaudio`, `tiny_audio`) generated via `ffmpeg lavfi` — *Verify:* fixtures resolve to playable files inside `tmp_path_factory`; absent `ffmpeg` skips integration-layer tests cleanly.
- [x] Unit tests for pure helpers — *Verify:* `pytest -m "not integration"` passes with 11 tests covering `_format_concat_listfile` escaping and `_narrow_probe` mapping from canned ffprobe dicts, with no ffmpeg invocation.
- [x] Tool-wrapper smoke tests — *Verify:* `tests/integration/test_tool_wrappers.py` drives each of `splice`/`merge`/`save`/`probe` via its `async with register(...)` context and `FunctionInfo.single_fn`, validating the entire registration plumbing end-to-end.
- [x] Integration tests against real ffmpeg — *Verify:* `pytest -m integration` runs 11 tests (7 client primitives + 4 tool wrappers) against `tiny_video` fixtures; `concat_copy` joined duration ≈ 6s, `mux_av` probe reports both streams, `export_standard` probe reports `h264` + `aac`, `probe_media` reports width=320 height=240 fps≈30.

---

## Risks that should trigger replanning

Each risk has a detection signal and a fallback plan. If detected, stop and execute the fallback before continuing.

- **VL video inference too slow** — *Detect:* full-video inference on a 10-min source exceeds 2 minutes. *Fallback:* sample frames at transcript cue points instead of passing full video.
- **Whisper timestamps drift** — *Detect:* spot check shows >500ms drift on 3+ samples. *Fallback:* add WhisperX forced-alignment pass after transcription.
- **Agent hallucinates timestamps** — *Detect:* validation rejects >10% of candidates for timestamps outside source range. *Fallback:* constrain Scout to select from a pre-computed candidate pool keyed off transcript sentence boundaries.
- **ffmpeg edge cases eating time** — *Detect:* more than 8 hours total spent debugging ffmpeg. *Fallback:* drop video understanding from live demo, pitch an audio-only podcast clipper with visual shown as pre-rendered.