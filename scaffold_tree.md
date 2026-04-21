cutpilot/
├── pyproject.toml
├── .python-version                   # pyenv convention (3.11.x)
├── README.md
├── PRD.md
├── TASKS.md
├── SPRINT.md
├── .gitignore
├── .env.example
│
├── src/cutpilot/
│   ├── __init__.py
│   ├── __main__.py
│   │
│   ├── models.py                     # SSoT: Pydantic domain types
│   ├── settings.py                   # SSoT: Pydantic Settings
│   ├── paths.py                      # SSoT: path computation
│   ├── persistence.py                # load/save domain objects ↔ disk
│   ├── prompts.py                    # loader for prompts/*.md
│   ├── pipeline.py                   # non-agent stages: ingest → transcribe → run_agents → save
│   ├── cli.py                        # typer entry
│   │
│   ├── clients/                      # only non-LLM external adapters
│   │   ├── __init__.py
│   │   ├── whisper.py                # audio → Transcript
│   │   └── ffmpeg.py                 # safe ffmpeg invocation
│   │
│   ├── configs/
│   │   └── cutpilot.yml              # SSoT: NAT workflow (llms + functions + workflow)
│   │
│   ├── tools/                        # @register_function per file; NAT auto-derives schemas
│   │   ├── __init__.py               # TOOLS = [cut, crop_9_16, burn_captions, transcript_window]
│   │   ├── cut.py                    # exports `register` (hooked via pyproject entry-points)
│   │   ├── crop.py
│   │   ├── captions.py
│   │   └── transcript_window.py
│   │
│   └── agents/
│       ├── __init__.py
│       ├── scout.py                  # @register_function returning CandidatesResult (NIM VL call)
│       └── runtime.py                # thin loader: invokes nat workflow from configs/cutpilot.yml
│
├── prompts/
│   ├── scout.md
│   └── editor.md
│
├── schemas/                          # generated from models.py
│   └── manifest.schema.json
│
├── ui/
│   ├── index.html
│   ├── demo-manifest.json
│   └── README.md
│
├── tests/
│   ├── unit/
│   │   ├── test_models.py
│   │   ├── test_tools_cut.py
│   │   ├── test_tools_crop.py
│   │   └── test_paths.py
│   ├── integration/
│   │   └── test_pipeline.py
│   └── fixtures/
│       ├── sample_2min.mp4
│       └── sample_transcript.json
│
├── scripts/
│   ├── smoke_test.sh
│   ├── run_demo.sh
│   ├── prerender_backup.sh
│   └── export_schemas.py
│
├── sources/                          # gitignored
├── work/                             # gitignored
└── outputs/                          # gitignored