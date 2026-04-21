# LiteLLM removal — deferred refactor

Drop LiteLLM from the dependency graph by moving Scout (and the Editor's tool
wiring) from NAT's ADK wrapper to its LangChain wrapper. Keeps NAT as the
framework, keeps NIM as the endpoint, keeps the OpenAI Chat Completions
protocol — just swaps the NAT-side adapter.

## Context

NAT's ADK plugin always returns `google.adk.models.lite_llm.LiteLlm`, regardless
of the `_type:` value (confirmed in `nvidia_nat_adk/src/nat/plugins/adk/llm.py`:
every branch of the plugin — `nim`, `openai`, `azure_openai`, `litellm`,
`dynamo` — yields a `LiteLlm` instance). So as long as we use
`wrapper_type=LLMFrameworkEnum.ADK`, the `litellm` Python package stays in the
dependency graph.

To honor "**NAT + OpenAI + NVIDIA NIM-compatible, remove LiteLLM**":

- Keep NAT (`@register_function`, `builder.get_llm`, YAML workflow).
- Switch wrapper from ADK → **LangChain**.
- Use `_type: openai` in YAML with `base_url: <NIM endpoint>` (NIM is OpenAI-
  compatible at the wire level).
- Under LangChain wrapper, NAT returns `langchain_openai.ChatOpenAI`, which
  uses the `openai` Python SDK directly — no LiteLLM.

There's a latent tool-binding issue this same change fixes: every tool is
decorated `framework_wrappers=[LLMFrameworkEnum.ADK]`, but Editor's
`tool_calling_agent` is LangChain-native, so it couldn't actually bind the
tools today. Setting `[LANGCHAIN]` on tools makes the Editor path viable.

Apply changes to both branches consistently: `sergey_agent_toolkit` and `ui-design`.

## Why ChatOpenAI, not ChatNVIDIA

- `ChatNVIDIA` (in `langchain_nvidia_ai_endpoints/chat_models.py:175-217`)
  normalizes multimodal content through a handler that recognizes only
  `image_url` — it will silently drop or reshape `video_url` parts. Scout
  needs `video_url`.
- `ChatOpenAI` forwards content parts as-is to the OpenAI-compat endpoint,
  preserving `video_url`, and supports `with_structured_output(schema,
  method="json_schema")` that translates to
  `response_format={"type":"json_schema", ...}` — identical to what NIM already
  accepts (confirmed earlier when LiteLlm was the client).
- `model_kwargs={"extra_body": {"media_io_kwargs": {"video": {"fps": 2, "num_frames": 128}}}}`
  at construction time carries the NIM-specific sampling knob through
  `openai.AsyncClient` via its `extra_body` parameter.

## Changes (same on both branches)

### 1. `src/cutpilot/configs/cutpilot.yml`

Swap both LLM blocks from `_type: nim` → `_type: openai`. `_type: nim` under
NAT's LangChain wrapper routes through `ChatNVIDIA` (strips `video_url`).
`_type: openai` with `base_url` routes through `ChatOpenAI` and preserves
multimodal content. NIM still serves the endpoint — only the NAT-side adapter
changes.

```yaml
llms:
  nemotron_text:
    _type: openai
    model_name: ${NIM_TEXT_MODEL:-nvidia/nemotron-3-nano}
    base_url: ${NIM_TEXT_BASE_URL:-http://0.0.0.0:8000/v1}
    api_key: ${NVIDIA_API_KEY:-dummy}
    temperature: 0.0

  nemotron_vl:
    _type: openai
    model_name: ${NIM_VL_MODEL:-nvidia/nemotron-nano-12b-v2-vl}
    base_url: ${NIM_VL_BASE_URL:-http://0.0.0.0:9000/v1}
    api_key: ${NVIDIA_API_KEY:-dummy}
    temperature: 0.2
```

### 2. `src/cutpilot/agents/scout.py`

- Decorator: `framework_wrappers=[LLMFrameworkEnum.LANGCHAIN]`.
- `builder.get_llm(config.llm, wrapper_type=LLMFrameworkEnum.LANGCHAIN)` →
  returns `ChatOpenAI`.
- `scout_core`: drop `from google.adk.*` and `from google.genai.*` imports;
  use `langchain_core.messages.{HumanMessage, SystemMessage}` +
  `llm.with_structured_output(CandidatesResult, method="json_schema")`.
- Message shape:

  ```python
  [
      SystemMessage(content=system_prompt),
      HumanMessage(content=[
          {"type": "text", "text": user_text},
          {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}},
      ]),
  ]
  ```

- `ainvoke` returns the Pydantic instance directly — no text parsing, no
  `_parse_candidates` JSON decode. Keep `_repair_candidate` as a post-hoc pass
  on `result.candidates` (the 15–20 s pad guard is still load-bearing).
- Type of `llm` param on `scout_core` changes from `LiteLlm` to `ChatOpenAI`.

### 3. `src/cutpilot/clients/nim.py`

Rewrite `make_vl_llm()`:

```python
from langchain_openai import ChatOpenAI

def make_vl_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.nim_vl_model,
        base_url=settings.nim_vl_base_url,
        api_key=settings.nvidia_api_key or "dummy",
        temperature=0.2,
        model_kwargs={"extra_body": {"media_io_kwargs": {"video": {"fps": 2, "num_frames": 128}}}},
    )
```

No `litellm` import, no env var hopping, no `nvidia_nim/` prefix.

### 4. `src/cutpilot/tools/*.py` (8 files)

Swap each `framework_wrappers=[LLMFrameworkEnum.ADK]` →
`framework_wrappers=[LLMFrameworkEnum.LANGCHAIN]`. Fixes the latent
Editor-can't-bind-tools issue. Files: `cut.py`, `crop.py`, `captions.py`,
`transcript_window.py`, plus `splice.py`, `merge.py`, `save.py`, `probe.py`
(the latter four only on `ui-design`).

### 5. `scripts/scout_smoke.py`

No structural change — still calls `make_vl_llm()` from the updated factory.
The `litellm.drop_params = True` line vanishes with the factory rewrite.

### 6. `pyproject.toml`

`nvidia-nat[langchain,adk,mcp]>=1.6` → `nvidia-nat[langchain,mcp]>=1.6`. The
`adk` extra pulls in `nvidia-nat-adk` which requires `google-adk` which
requires `litellm`. Dropping it severs the last LiteLLM path.

### 7. Tests

- `tests/integration/test_scout_live.py` +
  `tests/integration/test_editor_live.py` (ui-design only): their local LLM
  factories (if any duplicate `make_vl_llm` logic) swap `LiteLlm` →
  `ChatOpenAI`.
- Unit tests are insulated from this change — they mock structured output via
  `_parse_candidates` / `_repair_candidate` on raw dicts.

## Branches

Both branches get the same logical change but different surface (ui-design has
4 extra tool files to flip). Sequence:

1. **`sergey_agent_toolkit`**: apply and commit first (narrower surface), push.
2. **`ui-design`**: apply the same change on the existing merged state (not a
   cherry-pick — surface differs). Commit, push.

Both commits use conventional-commit format and carry no AI-author trailer.

## Verification (run on each branch after commit)

1. `pyenv shell nvidia && pip install -e ".[dev]"` — resolves without `adk`
   extra.
2. `pip list | grep -iE "litellm|google-adk|google-genai"` — **empty** (these
   are the casualties we want).
3. `COLUMNS=200 nat info components | grep cutpilot_` — 5 components on
   sergey, 9 on ui-design, same as before.
4. `pytest tests/unit/` — 54 pass (unit tests don't touch the LLM path).
5. `pytest -m integration tests/integration/test_scout_live.py` — if VL NIM
   is up, passes in ~25 s. If tunnel is down, skips.
6. `python scripts/scout_smoke.py <video> <run_id>` — live end-to-end via
   `ChatOpenAI`.
7. On `ui-design`: `cutpilot <source>` full pipeline.

## Risk / rollback

- If NIM's OpenAI-compat endpoint rejects the `extra_body.media_io_kwargs` path
  when carried by the openai SDK (unlikely; OpenAI SDK supports `extra_body`
  natively): flip back to `_type: nim` + `ChatNVIDIA` and accept that we'd
  need `ChatNVIDIA` to grow a `video_url` handler.
- If `ChatOpenAI` doesn't preserve `video_url` content parts (unexpected —
  it's a dict-through forwarder): same fallback as above.
- Rollback: `git revert <commit>` on each branch. No schema migrations, no
  state changes.

## Files touched (per branch, net)

- `src/cutpilot/agents/scout.py`
- `src/cutpilot/clients/nim.py`
- `src/cutpilot/configs/cutpilot.yml`
- `src/cutpilot/tools/*.py` (4 on sergey, 8 on ui-design)
- `scripts/scout_smoke.py`
- `pyproject.toml`
- `tests/integration/*.py` (ui-design only; sergey's scout_live still uses
  the pattern)
