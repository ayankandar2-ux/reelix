"""Runs the actual download as a subprocess and yields progress events.

Built on the user's proven working command:

    yt-dlp --downloader aria2c \\
           --downloader-args "aria2c:-x 8 -s 8 -k 1M" \\
           -f "<video_id>+<audio_id>" \\
           --merge-output-format mp4 \\
           -o "<dest>/<title>.%(ext)s" \\
           URL

The key difference from the original test is that the format selector is
always a specific, explicitly-chosen pair of format IDs -- never the
"best available" wildcard that pulled a 610 MiB file.
"""
from __future__ import annotations

import os
import pty
import subprocess
import threading
import time
from pathlib import Path
from queue import Empty, Queue

from .errors import ReelixError, map_ytdlp_error
from .progress import ProgressEvent, parse_line


class Download:
    """A running (or finished) download. Call .start() then poll .events
    for ProgressEvent objects until .done is True."""

    def __init__(
        self,
        url: str,
        format_selector: str,
        dest_path: Path,
        aria2_connections: int = 8,
        aria2_split: int = 8,
        aria2_min_split: str = "1M",
        container: str = "mp4",
        debug: bool = False,
    ):
        self.url = url
        self.format_selector = format_selector
        self.dest_path = Path(dest_path)
        self.aria2_connections = aria2_connections
        self.aria2_split = aria2_split
        self.aria2_min_split = aria2_min_split
        self.container = container
        self.debug = debug

        self.events: Queue[ProgressEvent] = Queue()
        self.done = False
        self.error: ReelixError | None = None
        self.returncode: int | None = None

        # Latest known state, updated directly from _pump as events are
        # parsed -- kept here (not just in the queue) so the UI always has
        # something current to draw even if a redraw's poll window misses
        # the exact moment an event was queued. Without this the display
        # was resetting to blank/zero on every redraw that didn't happen
        # to catch an event, making it look frozen even while the file
        # was actually growing on disk.
        self.percent = 0.0
        self.downloaded_bytes = 0.0
        self.total_bytes = 0.0
        self.speed_bytes: float | None = None
        self.eta_seconds: float | None = None
        self.stage = "Starting..."
        # Timestamp of the last _apply() update, so the UI can extrapolate
        # smooth in-between progress at its own redraw rate instead of only
        # visibly moving on the rare occasions aria2c itself prints a line
        # (which can be as infrequent as one line total for a small/fast
        # download that finishes in under a second).
        self.last_update_time: float = time.monotonic()
        self._start_time: float = time.monotonic()
        self._last_sample_time: float | None = None
        self._last_sample_bytes: float = 0.0

        self._proc: subprocess.Popen | None = None
        self._master_fd: int | None = None
        self._thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._cancelled = False

    def _build_command(self) -> list[str]:
        # yt-dlp needs the output template's extension placeholder; the
        # final container is forced separately via --merge-output-format.
        out_template = str(self.dest_path.with_suffix("")) + ".%(ext)s"
        aria2_args = (
            f"-x {self.aria2_connections} -s {self.aria2_split} "
            f"-k {self.aria2_min_split}"
        )
        cmd = [
            "yt-dlp",
            "--newline",
            "--no-warnings",
            "--no-playlist",
            "--downloader", "aria2c",
            "--downloader-args", f"aria2c:{aria2_args}",
            "-f", self.format_selector,
            "--merge-output-format", self.container,
            "-o", out_template,
            self.url,
        ]
        return cmd

    def start(self) -> None:
        cmd = self._build_command()
        try:
            # A plain pipe makes aria2c think nothing is watching in real
            # time, so it holds back its live per-second status line and
            # may only flush a line at the very end (or barely at all on
            # a big download) -- that's what made progress look dead even
            # though the file was genuinely growing. A pseudo-terminal
            # makes it believe it's attached to a real terminal, same as
            # running it by hand, so it keeps printing live updates.
            master_fd, slave_fd = pty.openpty()
            self._proc = subprocess.Popen(
                cmd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
            )
            os.close(slave_fd)
            self._master_fd = master_fd
        except FileNotFoundError as exc:
            self.error = ReelixError("yt-dlp missing", "yt-dlp isn't on your PATH.")
            self.done = True
            raise self.error from exc

        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        """Read the subprocess output (via its pseudo-terminal) byte-by-byte
        and split on either '\\n' or a bare '\\r'. aria2c redraws its
        progress line in place using '\\r' with no trailing '\\n' --
        reacting to it immediately means we never wait on a newline that
        might not come until the whole download finishes."""
        assert self._proc is not None and self._master_fd is not None
        buf = bytearray()
        fd = self._master_fd
        while True:
            try:
                chunk = os.read(fd, 1)
            except OSError:
                # A pty raises EIO once the slave side has been closed by
                # the child exiting -- that's the normal end-of-output
                # signal for a pty, not an error.
                break
            if not chunk:
                break
            if chunk in (b"\n", b"\r"):
                if buf:
                    raw_line = buf.decode("utf-8", errors="replace")
                    self._stderr_lines.append(raw_line)
                    event = parse_line(raw_line)
                    if event is not None:
                        self._apply(event)
                        self.events.put(event)
                    buf.clear()
            else:
                buf += chunk
        if buf:
            raw_line = buf.decode("utf-8", errors="replace")
            self._stderr_lines.append(raw_line)
            event = parse_line(raw_line)
            if event is not None:
                self._apply(event)
                self.events.put(event)
        try:
            os.close(fd)
        except OSError:
            pass
        self._proc.wait()
        self.returncode = self._proc.returncode
        if self.returncode != 0 and not self._cancelled:
            self.error = map_ytdlp_error("".join(self._stderr_lines))
        self.done = True
        self.events.put(ProgressEvent(kind="finished"))

    def _apply(self, event: ProgressEvent) -> None:
        """Update the persistent latest-state snapshot from a parsed event.

        Speed/ETA are computed here from our own byte-delta samples rather
        than trusted verbatim from aria2c's line, because aria2c only
        prints roughly once a second -- a small/fast download can finish
        inside that window and produce exactly one line (the 100% one)
        with no speed/ETA fields on it at all. Sampling ourselves means we
        always have a number, even for downloads too quick for aria2c to
        report on."""
        now = time.monotonic()
        if event.kind == "progress":
            if event.downloaded_bytes is not None:
                new_bytes = event.downloaded_bytes
                if self._last_sample_time is not None:
                    dt = now - self._last_sample_time
                    dbytes = new_bytes - self._last_sample_bytes
                    if dt > 0.05 and dbytes >= 0:
                        self.speed_bytes = dbytes / dt
                elif event.speed_bytes:
                    # First sample: nothing to diff against yet, so use
                    # aria2c's own reported speed if it gave us one.
                    self.speed_bytes = event.speed_bytes
                self._last_sample_time = now
                self._last_sample_bytes = new_bytes
                self.downloaded_bytes = new_bytes
            if event.total_bytes is not None:
                self.total_bytes = event.total_bytes
            if event.percent is not None:
                self.percent = event.percent
            if self.speed_bytes and self.total_bytes:
                remaining = max(self.total_bytes - self.downloaded_bytes, 0.0)
                self.eta_seconds = remaining / self.speed_bytes if self.speed_bytes > 0 else None
            elif event.eta_seconds is not None:
                self.eta_seconds = event.eta_seconds
            self.stage = "Downloading"
        elif event.kind == "merging":
            self.stage = "Merging with FFmpeg..."
        self.last_update_time = now

    def poll(self, timeout: float = 0.2) -> ProgressEvent | None:
        try:
            return self.events.get(timeout=timeout)
        except Empty:
            return None

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
