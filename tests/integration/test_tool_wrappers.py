"""Integration tests that drive each new tool through its `register` entry.

`@register_function` wraps `register` as an `asynccontextmanager`, so we enter
it with `async with` to obtain the `FunctionInfo`, then invoke the tool via
`info.single_fn(input_schema(**kwargs))` — the exact path NAT uses at runtime.
This catches any wrapper bug (wrong arg names, bad forwarding) that pure
client-primitive tests would miss.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from nat.builder.builder import Builder

from cutpilot.tools import merge, probe, save, splice

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# The four new tools ignore the Builder, so a None cast is safe at runtime.
_NO_BUILDER = cast(Builder, None)


async def _invoke(ctx: Any, **kwargs: Any) -> Any:
    async with ctx as info:
        payload = info.input_schema(**kwargs)
        return await info.single_fn(payload)


async def test_splice_tool_joins_clips(tiny_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "spliced.mp4"
    result = await _invoke(
        splice.register(splice.SpliceConfig(), _NO_BUILDER),
        source_paths=[str(tiny_video), str(tiny_video)],
        output_path=str(out),
    )
    assert result == str(out)
    assert out.exists() and out.stat().st_size > 0


async def test_merge_tool_combines_tracks(
    tiny_video_noaudio: Path,
    tiny_audio: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "merged.mp4"
    result = await _invoke(
        merge.register(merge.MergeConfig(), _NO_BUILDER),
        video_path=str(tiny_video_noaudio),
        audio_path=str(tiny_audio),
        output_path=str(out),
    )
    assert result == str(out)
    assert out.exists() and out.stat().st_size > 0


async def test_save_tool_exports_mp4(tiny_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "exported.mp4"
    result = await _invoke(
        save.register(save.SaveConfig(), _NO_BUILDER),
        source_path=str(tiny_video),
        output_path=str(out),
    )
    assert result == str(out)
    assert out.exists() and out.stat().st_size > 0


async def test_probe_tool_returns_valid_json(tiny_video: Path) -> None:
    # `probe` is a single-arg tool — NAT leaves `single_fn` taking the raw value
    # rather than auto-wrapping into a Pydantic input model, so we call direct.
    async with probe.register(probe.ProbeConfig(), _NO_BUILDER) as info:
        result = await info.single_fn(str(tiny_video))
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["width"] == 320
    assert parsed["height"] == 240
    assert parsed["video_codec"] == "h264"
    assert parsed["audio_codec"] == "aac"
    assert parsed["fps"] == pytest.approx(30.0, rel=0.01)
