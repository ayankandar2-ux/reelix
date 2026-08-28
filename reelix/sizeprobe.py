"""Fills in file sizes that yt-dlp didn't report.

Progressive/muxed formats almost always carry a `filesize` or
`filesize_approx` already. Separate DASH video-only/audio-only streams
sometimes don't -- especially on non-YouTube sites. Rather than leave those
rows saying "unknown" (useless for judging download time), we ask the CDN
directly: a HEAD request (or, if the server doesn't support HEAD, a 1-byte
ranged GET) usually returns a Content-Length/Content-Range header without
downloading the file.

Kept dependency-free (stdlib `urllib` + `concurrent.futures`) and bounded:
a small thread pool with a short per-request timeout, only ever run against
the handful of rows actually shown in Advanced mode.
"""
from __future__ import annotations

import concurrent.futures
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 4.0
DEFAULT_MAX_WORKERS = 6


def _probe_one(stream, timeout: float) -> None:
    if stream.size_bytes is not None or not stream.url:
        stream.probing = False
        return
    try:
        _probe_head(stream, timeout) or _probe_ranged_get(stream, timeout)
    finally:
        stream.probing = False


def _apply_headers(req: urllib.request.Request, stream) -> None:
    for key, value in (stream.http_headers or {}).items():
        try:
            req.add_header(key, value)
        except (TypeError, ValueError):
            continue


def _probe_head(stream, timeout: float) -> bool:
    req = urllib.request.Request(stream.url, method="HEAD")
    _apply_headers(req, stream)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > 0:
                stream.size_bytes = float(length)
                stream.size_approx = False
                return True
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return False


def _probe_ranged_get(stream, timeout: float) -> bool:
    """Fallback for servers that reject/ignore HEAD: ask for byte 0 only
    and read the total size back out of Content-Range."""
    req = urllib.request.Request(stream.url, method="GET")
    _apply_headers(req, stream)
    req.add_header("Range", "bytes=0-0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_range = resp.headers.get("Content-Range", "")
            if "/" in content_range:
                total = content_range.rsplit("/", 1)[-1]
                if total.isdigit():
                    stream.size_bytes = float(total)
                    stream.size_approx = False
                    return True
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > 1:
                stream.size_bytes = float(length)
                stream.size_approx = False
                return True
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return False


def probe_sizes(streams: list, timeout: float = DEFAULT_TIMEOUT,
                 max_workers: int = DEFAULT_MAX_WORKERS) -> None:
    """Probe every stream missing a size, in parallel. Mutates the
    StreamInfo objects in place; safe to call from a background thread."""
    targets = [s for s in streams if s.size_bytes is None and s.url]
    if not targets:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_probe_one, s, timeout) for s in targets]
        concurrent.futures.wait(futures)


def mark_probing(streams: list) -> None:
    """Flip on the 'probing' flag for every stream about to be probed, so
    the UI can show a 'measuring...' placeholder immediately, before the
    background thread has actually started."""
    for s in streams:
        if s.size_bytes is None and s.url:
            s.probing = True
