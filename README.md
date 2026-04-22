# CutPilot

Agentic long-video → short-clip generator. Drop in a 5–90 minute podcast, lecture, interview, or keynote and get back three 30–60 second vertical clips plus a stitched highlights reel — each with burned-in captions, a hook, scored rationale, and the full reasoning trace that picked the moment.

Built for NVIDIA Nemotron Developer Days Seoul 2026 — Track A (Creative Agentic Systems).

## How it works

A three-NIM pipeline orchestrated through the **NVIDIA NeMo Agent Toolkit** (`nvidia-nat`). Every model call hits a live NVIDIA NIM over its OpenAI-compatible `/v1` interface — no local weights, no LLM mocks:

1. **Whisper-Large ASR NIM** transcribes the source in 5-minute chunks with word-level timestamps and stitches them back into a single `Transcript`.
2. **Nemotron Nano 12B V2 VL NIM** runs **in parallel over sliding 90-second windows** covering the full video. Each window returns a 1–5 visual score and a one-sentence visual hook. Sliding scan avoids the pattern-collapse a single VL pass exhibits on long talks (everything becomes "a woman on stage").
3. **Nemotron-3 Nano text NIM** sees the full transcript + the per-window VL observations and proposes 5–10 candidate clips via `client.beta.chat.completions.parse(response_format=CandidatesResult)` — Pydantic strict-mode JSON, not free text.

The top 3 by composite score (`hook + self_contained + length_fit + visual_fit`) are materialized: `cut_reencode → crop_9_16_center → burn_captions` via `clients/ffmpeg.py`. A stitched `highlights.mp4` joins all three.

Scout is a NAT `@register_function` (`cutpilot.agents.scout`); the eight ffmpeg operations (`cut`, `crop_9_16`, `burn_captions`, `transcript_window`, `splice`, `merge`, `save`, `probe`) are registered NAT tools available to the declarative `tool_calling_agent` Editor defined in `configs/cutpilot.yml`. The CLI drives the simpler functional path (`scout_vl_sliding → scout_text_core → top-3 → materialize`) directly, because `sequential_executor` returns a text blob and takes a single string input — not the `(run_id, source_path) → list[ClipManifest]` contract the CLI needs.

Output: three `.mp4`s, per-clip JSON manifests, a stitched `highlights.mp4`, and a single-file HTML review UI with before/after, rationale, rubric scores, and reasoning trace.

## Status

Finalized on `main`. End-to-end verified against the live NIMs on a 43-minute GTC Healthcare talk: 9-chunk Whisper transcription → 15-window parallel VL scan → 6-candidate text scout → 3 content-grounded clips (e.g. *"What if AI could design life-saving drugs in minutes?"*, *"What if robots could perform surgery with human-level precision?"*) + 42 MB `highlights.mp4`, all in ~3 minutes of wall-clock.

Test coverage (all against live dependencies, no LLM mocks):

- **87 unit tests** — models, parsers, prompt rendering, sliding-window math, URL gating. <3 s.
- **16 integration tests** — real ffmpeg subprocess + live VL / text / Whisper NIMs on a 120 s slice of real content. ~25 s.
- **1 e2e test** — full `run_pipeline` on the 43-min GTC video. Opt-in via `pytest -m e2e`, ~3 min.

## Requirements

- Python 3.11+ (dev env pinned to 3.13 via `.python-version`; `ruff` and `mypy` both target `py313`)
- `ffmpeg` 6.0+ on `PATH`
- Three NVIDIA NIM endpoints reachable over HTTPS (configured in `.env`):
  - **Whisper-Large** (OpenAI-compat `/v1/audio/transcriptions`)
  - **Nemotron Nano 12B V2 VL** (OpenAI-compat `/v1/chat/completions` with video input)
  - **Nemotron-3 Nano text** (OpenAI-compat `/v1/chat/completions`)

  Hosted at `build.nvidia.com` with `NVIDIA_API_KEY`, or self-hosted on an **NVIDIA Brev** H100 Launchable (≥70 GB VRAM) with containers from `nvcr.io/nim/...` pulled via `NGC_API_KEY`. See [.env.example](.env.example) for the full contract.

## Install

```bash
pip install -e ".[dev]"
```

Then copy `.env.example` → `.env` and fill in the three NIM endpoints.

## Run

```bash
cutpilot <source.mp4>                          # local file
cutpilot https://youtu.be/<id>                 # yt-dlp handles URL ingest
cutpilot /path/to/video.mp4 --run-id demo      # custom run id (= output subdir)
```

Clips, per-clip manifests, and `highlights.mp4` land under `outputs/<run>/`; open `ui/index.html` directly (`file://`) to review.

To serve the review UI over HTTP — and accept multipart uploads from the browser:

```bash
cutpilot-serve                 # defaults to http://127.0.0.1:8080
```

The declarative NAT workflow in `src/cutpilot/configs/cutpilot.yml` can be exercised directly:

```bash
nat run --config_file=src/cutpilot/configs/cutpilot.yml --input <source>
```

## Development

```bash
pytest                                          # unit + integration (live NIMs auto-skip when down)
pytest -m "not integration and not e2e"         # unit only — fast, hermetic
pytest -m integration                           # real ffmpeg + live NIM (≈25 s)
pytest -m e2e                                   # full 43-min pipeline on the GTC video (≈3 min)
ruff check . && ruff format .                   # lint + format
mypy src                                        # strict type check
```

## Scope

**In:** one source file (`.mp4`/`.mov`/`.mkv`) or YouTube URL, English audio, single primary speaker, 3 vertical clips with burned-in captions, center-crop framing, stitched highlights reel.

**Out for the sprint** (deferred to post-hackathon): smart crop with face tracking, scene-detection tool, multi-language output, word-level caption highlighting, Korean-language sources, multi-speaker handling, social platform publishing, batch processing.

## Authors

Sergey Leksikov · Minjae Kim

## License

MIT
