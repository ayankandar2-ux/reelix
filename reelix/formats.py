"""Turn yt-dlp's raw `formats` list into the small, honest set of choices
the user actually sees.

Nothing here hardcodes format IDs -- every video is inspected fresh, since
the same format ID can mean different things on different sites (or even
different videos on the same site).
"""
from __future__ import annotations

from dataclasses import dataclass, field

STANDARD_HEIGHTS = [144, 240, 360, 480, 720, 1080, 1440, 2160]
NORMAL_MODE_TARGETS = [360, 480, 720]
PREFERRED_AUDIO_EXT = "m4a"


def _bucket_height(height: int) -> int:
    """Snap an arbitrary pixel height to the nearest standard resolution
    at or below it (e.g. 368 -> 360, 1088 -> 1080)."""
    best = STANDARD_HEIGHTS[0]
    for h in STANDARD_HEIGHTS:
        if h <= height:
            best = h
        else:
            break
    return best if height >= STANDARD_HEIGHTS[0] else height


def _format_size(fmt: dict) -> tuple[float | None, bool]:
    """Return (size_bytes, is_approximate) for a single format."""
    exact = fmt.get("filesize")
    if exact:
        return float(exact), False
    approx = fmt.get("filesize_approx")
    if approx:
        return float(approx), True
    return None, True


@dataclass
class StreamInfo:
    format_id: str
    ext: str
    vcodec: str = "none"
    acodec: str = "none"
    height: int | None = None
    fps: float | None = None
    tbr: float | None = None
    abr: float | None = None
    size_bytes: float | None = None
    size_approx: bool = True
    url: str | None = None
    http_headers: dict | None = None
    probing: bool = False

    @property
    def is_video(self) -> bool:
        return self.vcodec not in (None, "none")

    @property
    def is_audio(self) -> bool:
        return self.acodec not in (None, "none")


@dataclass
class QualityOption:
    label: str  # e.g. "720p"
    height: int
    video: StreamInfo
    audio: StreamInfo | None
    tier: str  # "LOW" or "HIGH"

    @property
    def _needs_separate_audio(self) -> bool:
        """False when self.video is already a muxed (progressive) stream
        that carries its own audio track -- pairing it with a second audio
        format would double-download audio for no benefit."""
        return not self.video.is_audio

    @property
    def format_selector(self) -> str:
        if self.audio is not None and self._needs_separate_audio:
            return f"{self.video.format_id}+{self.audio.format_id}"
        return self.video.format_id

    @property
    def total_size(self) -> float | None:
        if not self._needs_separate_audio or self.audio is None:
            return self.video.size_bytes
        if self.video.size_bytes is None or self.audio.size_bytes is None:
            return None
        return self.video.size_bytes + self.audio.size_bytes

    @property
    def is_approx(self) -> bool:
        v_approx = self.video.size_approx or self.video.size_bytes is None
        if not self._needs_separate_audio or self.audio is None:
            return v_approx
        a_approx = self.audio.size_approx or self.audio.size_bytes is None
        return v_approx or a_approx


def _parse_streams(formats: list[dict]) -> list[StreamInfo]:
    streams = []
    for fmt in formats:
        if fmt.get("format_id") is None:
            continue
        size, approx = _format_size(fmt)
        streams.append(
            StreamInfo(
                format_id=str(fmt.get("format_id")),
                ext=fmt.get("ext", "?"),
                vcodec=fmt.get("vcodec", "none") or "none",
                acodec=fmt.get("acodec", "none") or "none",
                height=fmt.get("height"),
                fps=fmt.get("fps"),
                tbr=fmt.get("tbr"),
                abr=fmt.get("abr"),
                size_bytes=size,
                size_approx=approx,
                url=fmt.get("url"),
                http_headers=fmt.get("http_headers"),
            )
        )
    return streams


def parse_streams(formats: list[dict]) -> list[StreamInfo]:
    """Public entry point for turning raw yt-dlp format dicts into
    StreamInfo objects."""
    return _parse_streams(formats)


def best_audio_stream(streams: list[StreamInfo]) -> StreamInfo | None:
    """Pick the best audio-only stream, preferring m4a/aac for broad
    compatibility with an MP4 container, falling back to whatever has the
    highest bitrate."""
    audio_only = [s for s in streams if s.is_audio and not s.is_video]
    if not audio_only:
        return None

    def sort_key(s: StreamInfo):
        return (s.ext == PREFERRED_AUDIO_EXT, s.abr or 0, s.size_bytes or 0)

    return max(audio_only, key=sort_key)


def best_video_for_bucket(streams: list[StreamInfo], target_height: int) -> StreamInfo | None:
    """Best video-only (or muxed) stream whose bucketed height matches
    target_height exactly, preferring mp4/avc1 and higher bitrate."""
    candidates = [
        s for s in streams
        if s.is_video and s.height and _bucket_height(s.height) == target_height
    ]
    if not candidates:
        return None

    # Prefer mp4/avc1-ish streams when practical, then fall back to bitrate.
    def preference(s: StreamInfo):
        prefers_mp4 = 1 if s.ext == "mp4" else 0
        return (prefers_mp4, s.tbr or 0, s.size_bytes or 0)

    return max(candidates, key=preference)


def available_buckets(streams: list[StreamInfo]) -> list[int]:
    heights = {_bucket_height(s.height) for s in streams if s.is_video and s.height}
    return sorted(h for h in heights if h in STANDARD_HEIGHTS)


def build_quality_options(info: dict) -> tuple[list[QualityOption], list[QualityOption]]:
    """Return (normal_options, advanced_only_options), both sorted low->high.

    normal_options only ever contains options from NORMAL_MODE_TARGETS that
    actually exist for this video. advanced_only_options contains every
    other resolution bucket that exists (e.g. 144p, 240p, 1080p, 1440p...).
    """
    formats = info.get("formats") or []
    streams = _parse_streams(formats)
    audio = best_audio_stream(streams)
    buckets = available_buckets(streams)

    normal, advanced = [], []
    for height in buckets:
        video = best_video_for_bucket(streams, height)
        if video is None:
            continue
        tier = "LOW" if height <= 360 else "HIGH"
        option = QualityOption(
            label=f"{height}p",
            height=height,
            video=video,
            audio=audio,
            tier=tier,
        )
        if height in NORMAL_MODE_TARGETS:
            normal.append(option)
        else:
            advanced.append(option)

    normal.sort(key=lambda o: o.height)
    advanced.sort(key=lambda o: o.height)
    return normal, advanced


def build_advanced_table(info: dict) -> list[StreamInfo]:
    """Every real format yt-dlp reported, for the raw Advanced view."""
    formats = info.get("formats") or []
    streams = _parse_streams(formats)
    # Show video streams first (highest to lowest), then audio-only.
    video_streams = sorted(
        (s for s in streams if s.is_video),
        key=lambda s: (s.height or 0, s.tbr or 0),
        reverse=True,
    )
    audio_streams = sorted(
        (s for s in streams if s.is_audio and not s.is_video),
        key=lambda s: s.abr or 0,
        reverse=True,
    )
    return video_streams + audio_streams
