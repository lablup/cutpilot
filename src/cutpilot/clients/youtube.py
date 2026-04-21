"""Remote source ingestion via yt-dlp.

Despite the module name, `yt-dlp` supports many sites (YouTube, Vimeo, Twitter/X,
SoundCloud, etc.). We accept any http(s) URL and let yt-dlp reject unsupported ones.

yt-dlp is a synchronous library; the public entrypoint wraps the blocking work in
the default thread executor so it composes with the rest of the async pipeline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

import structlog
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

log = structlog.get_logger()

_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class SourceDownloadError(RuntimeError):
    """Raised when yt-dlp fails to retrieve a remote source video."""


def is_url(source: str) -> bool:
    """True if `source` looks like an http(s) URL, false otherwise.

    Intentionally narrow: anything that isn't http(s) is treated as a local path
    so Windows drive letters, relative paths, and `file://` inputs don't get
    accidentally routed to yt-dlp.
    """
    parsed = urlparse(source.strip())
    return parsed.scheme.lower() in _URL_SCHEMES and bool(parsed.netloc)


async def download(*, url: str, target_path: Path) -> Path:
    """Download `url` into the run's work dir; return the resolved local path.

    yt-dlp may choose a different container than requested; we scan siblings
    with the same stem to find whatever it actually produced.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _download_sync, url, target_path)


def _download_sync(url: str, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    output_template = str(target_path.parent / f"{target_path.stem}.%(ext)s")
    options: dict[str, object] = {
        "outtmpl": output_template,
        # Cap at 1080p to keep downloads modest; fall back to whatever's best if
        # the site doesn't offer ≤1080p renditions.
        "format": "bv*[height<=1080]+ba/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # A YouTube share link can reference a playlist; we want the single video.
        "noplaylist": True,
        "restrictfilenames": True,
    }
    log.info("youtube.download.start", url=url, target=str(target_path))
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise SourceDownloadError(f"yt-dlp could not download {url}: {exc}") from exc

    resolved = _locate_output(target_path)
    if resolved is None:
        raise SourceDownloadError(
            f"yt-dlp reported success but no file found at "
            f"{target_path.parent}/{target_path.stem}.*"
        )

    title = info.get("title") if isinstance(info, dict) else None
    duration = info.get("duration") if isinstance(info, dict) else None
    log.info(
        "youtube.download.done",
        url=url,
        path=str(resolved),
        title=title,
        duration_seconds=duration,
    )
    return resolved


def _locate_output(target_path: Path) -> Path | None:
    """yt-dlp may finalize with a different extension than requested; prefer
    `target_path` itself, else any sibling sharing the stem."""
    if target_path.exists():
        return target_path
    matches = sorted(target_path.parent.glob(f"{target_path.stem}.*"))
    return matches[0] if matches else None
