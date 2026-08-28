"""Custom exceptions and translation of raw tool output into readable
messages. Nothing in here should ever let a raw Python traceback reach the
user during normal operation -- app.py catches ReelixError at the top level and
renders it inside a clean error panel.
"""
from __future__ import annotations


class ReelixError(Exception):
    """A user-facing error with a short title and a plain-language body."""

    def __init__(self, title: str, message: str, raw: str = ""):
        super().__init__(message)
        self.title = title
        self.message = message
        self.raw = raw


class DependencyError(ReelixError):
    pass


class StorageError(ReelixError):
    pass


class NetworkError(ReelixError):
    pass


# Patterns to look for in yt-dlp/aria2c stderr, checked in order. The first
# match wins. Keep this list ordered from most specific to most generic.
_PATTERNS: list[tuple[str, str, str]] = [
    ("sign in to confirm you", "Bot verification required",
     "YouTube is asking for bot verification on this video. Try again in a "
     "moment, or make sure Deno/EJS support is installed."),
    ("confirm your age", "Age-restricted video",
     "This video is age-restricted and needs an authenticated session to "
     "download. Public, non-restricted videos will work normally."),
    ("private video", "Private video",
     "This video is private and can't be accessed without an account that "
     "has permission to view it."),
    ("login required", "Login required",
     "This content requires you to be logged in. Reelix does not handle "
     "authenticated/cookie-based downloads."),
    ("requires payment", "Paid content",
     "This content is behind a paywall and can't be downloaded."),
    ("http error 429", "Rate limited",
     "The source is temporarily rate-limiting requests (HTTP 429). Wait a "
     "bit before trying again."),
    ("this video is unavailable", "Video unavailable",
     "The video is unavailable. It may have been removed or made private."),
    ("video unavailable", "Video unavailable",
     "The video is unavailable. It may have been removed or made private."),
    ("geo", "Geo-restricted",
     "This content is not available in your region."),
    ("unsupported url", "Unsupported website",
     "This website isn't supported by the downloader engine."),
    ("no video formats found", "No downloadable formats",
     "No compatible video/audio formats were found for this link."),
    ("requested format is not available", "Format unavailable",
     "The requested quality is no longer available for this video."),
    ("unable to download webpage", "Network error",
     "Couldn't reach the source website. Check your internet connection."),
    ("temporary failure in name resolution", "Network error",
     "Couldn't resolve the site's address. Check your internet connection."),
    ("no space left on device", "Storage full",
     "There isn't enough free storage space to complete this download."),
    ("permission denied", "Storage permission missing",
     "Reelix doesn't have permission to write to the download folder. Run "
     "termux-setup-storage and grant storage access, then try again."),
    ("ffmpeg", "FFmpeg problem",
     "FFmpeg reported a problem while merging the video and audio streams."),
]


def map_ytdlp_error(raw_output: str) -> ReelixError:
    """Turn raw yt-dlp/aria2c stderr text into a friendly ReelixError."""
    lowered = (raw_output or "").lower()
    for needle, title, message in _PATTERNS:
        if needle in lowered:
            return ReelixError(title, message, raw=raw_output)
    return ReelixError(
        "Download error",
        "Something went wrong while talking to the source site. "
        "Enable Debug mode for the full technical output.",
        raw=raw_output,
    )


def check_dependencies() -> list[ReelixError]:
    """Return a list of DependencyError for any missing required tool."""
    from .utils import which

    missing = []
    required = {
        "yt-dlp": "Install with: pip install -U yt-dlp[default]",
        "ffmpeg": "Install with: pkg install ffmpeg",
        "aria2c": "Install with: pkg install aria2",
    }
    for tool, hint in required.items():
        if which(tool) is None:
            missing.append(
                DependencyError(
                    f"{tool} not found",
                    f"Reelix needs '{tool}' to work but it isn't on your PATH. {hint}",
                )
            )
    return missing
