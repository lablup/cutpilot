"""Unit tests for `_segments_from_response` and `_segments_from_verbose` — the
parsers that bridge two different NIM response shapes (`json` vs `verbose_json`)
into the same `Transcript.segments` list."""

from __future__ import annotations

from types import SimpleNamespace

from cutpilot.clients.whisper import _segments_from_response, _segments_from_verbose


class TestSegmentsFromResponse:
    def test_json_shape_becomes_single_fallback_segment(self) -> None:
        # `json` mode: response carries `text` only, no `segments` attribute.
        resp = SimpleNamespace(text="hello world", segments=None, words=None)
        segs = _segments_from_response(resp, time_offset=10.0, fallback_duration=20.0)
        assert len(segs) == 1
        assert segs[0].text == "hello world"
        assert segs[0].start == 10.0
        assert segs[0].end == 30.0
        assert segs[0].words == []

    def test_empty_text_yields_empty_list(self) -> None:
        resp = SimpleNamespace(text="   ", segments=None, words=None)
        assert _segments_from_response(resp, time_offset=0.0, fallback_duration=5.0) == []

    def test_verbose_shape_goes_through_verbose_parser(self) -> None:
        # `verbose_json` mode: response has a `segments` list.
        seg = SimpleNamespace(text="s1", start=0.0, end=2.0)
        resp = SimpleNamespace(text="dont use", segments=[seg], words=None)
        segs = _segments_from_response(resp, time_offset=5.0, fallback_duration=99.0)
        assert len(segs) == 1
        # `fallback_duration` is ignored on the verbose path.
        assert segs[0].start == 5.0
        assert segs[0].end == 7.0


class TestSegmentsFromVerbose:
    def test_words_attached_by_containment(self) -> None:
        # Word [0.5, 1.0] falls inside segment [0, 2]; word [2.5, 3.0] falls
        # inside segment [2, 4]. Each word must attach to exactly one segment.
        raw_segs = [
            SimpleNamespace(text="hi", start=0.0, end=2.0),
            SimpleNamespace(text="ho", start=2.0, end=4.0),
        ]
        raw_words = [
            SimpleNamespace(word="hi", start=0.5, end=1.0),
            SimpleNamespace(word="ho", start=2.5, end=3.0),
        ]
        segs = _segments_from_verbose(
            raw_segments=raw_segs,
            raw_words=raw_words,
            time_offset=100.0,
        )
        assert [w.text for w in segs[0].words] == ["hi"]
        assert [w.text for w in segs[1].words] == ["ho"]

    def test_time_offset_applied_to_segments_and_words(self) -> None:
        raw_segs = [SimpleNamespace(text="s", start=1.0, end=3.0)]
        raw_words = [SimpleNamespace(word="w", start=1.5, end=2.5)]
        segs = _segments_from_verbose(
            raw_segments=raw_segs,
            raw_words=raw_words,
            time_offset=600.0,
        )
        assert segs[0].start == 601.0
        assert segs[0].end == 603.0
        assert segs[0].words[0].start == 601.5
        assert segs[0].words[0].end == 602.5

    def test_no_words_still_returns_segments(self) -> None:
        raw_segs = [SimpleNamespace(text="lonely", start=0.0, end=5.0)]
        segs = _segments_from_verbose(raw_segments=raw_segs, raw_words=[], time_offset=0.0)
        assert segs[0].words == []
