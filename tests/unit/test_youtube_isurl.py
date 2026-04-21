"""Unit tests for `clients.youtube.is_url` — the gate between URL and local-path
routing in `pipeline._resolve_source`."""

from __future__ import annotations

import pytest

from cutpilot.clients.youtube import is_url


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # Real URLs
        ("https://youtu.be/cW_POtTfJVM", True),
        ("http://vimeo.com/12345", True),
        ("https://www.youtube.com/watch?v=abc", True),
        # Local paths (various flavors)
        ("/abs/path/to.mp4", False),
        ("relative/path.mp4", False),
        ("./local.mp4", False),
        ("~/home/video.mp4", False),
        ("", False),
        # Rejected schemes — only http(s) routes to yt-dlp
        ("ftp://example.com/v.mp4", False),
        ("file:///tmp/v.mp4", False),
        # Malformed http inputs without a host
        ("https://", False),
        ("http://", False),
    ],
)
def test_is_url(source: str, expected: bool) -> None:
    assert is_url(source) is expected


def test_whitespace_is_stripped() -> None:
    # A URL with leading/trailing whitespace still parses as a URL.
    assert is_url("  https://youtu.be/x  ") is True
