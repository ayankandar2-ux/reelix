"""Download-directory handling.

The final destination for downloaded videos is a fixed, user-chosen Android
path (configurable, but defaulting to /storage/emulated/0/Movies/Reelix)
-- not Termux's ~/storage/downloads symlink.
"""
from __future__ import annotations

from pathlib import Path

from .errors import StorageError
from .utils import sanitize_filename, unique_path


def ensure_download_dir(path: str) -> Path:
    """Create the download directory if needed and return it as a Path.

    Raises StorageError with a clear, actionable message if it can't be
    created (typically an Android storage-permission issue).
    """
    directory = Path(path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise StorageError(
            "Storage permission missing",
            "Reelix can't create the download folder due to missing storage "
            "permission. Run 'termux-setup-storage' in Termux, grant the "
            "storage permission when Android asks, then try again.",
        ) from exc
    except OSError as exc:
        raise StorageError(
            "Storage directory unavailable",
            f"Couldn't create or access '{path}': {exc}",
        ) from exc

    if not directory.is_dir() or not _writable(directory):
        raise StorageError(
            "Storage directory unavailable",
            f"'{path}' exists but Reelix can't write to it. Check Android "
            "storage permissions for Termux.",
        )
    return directory


def _writable(directory: Path) -> bool:
    probe = directory / ".vd_write_test"
    try:
        probe.touch(exist_ok=True)
        probe.unlink()
        return True
    except OSError:
        return False


def output_path(directory: Path, title: str, ext: str = "mp4") -> Path:
    filename = f"{sanitize_filename(title)}.{ext}"
    return unique_path(directory, filename)
