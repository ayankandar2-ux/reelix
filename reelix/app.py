"""Reelix -- Universal Video Downloader. Main curses application.

Screen flow:

    URL_INPUT -> FETCHING -> QUALITY_SELECT -> (ADVANCED) -> DOWNLOADING
        -> COMPLETE / ERROR -> (N) back to URL_INPUT, (Q) quit
"""
from __future__ import annotations

import curses
import sys
import threading
import time
from pathlib import Path

from . import formats as fmt_module
from . import metadata
from . import storage
from . import ui
from .config import load_config, save_config
from .downloader import Download
from .errors import ReelixError, check_dependencies
from .utils import format_duration, format_eta, human_size, human_speed, looks_like_url

BOX_WIDTH = 56


class AppState:
    def __init__(self, config: dict):
        self.config = config
        self.url = ""
        self.info: dict | None = None
        self.normal_options: list = []
        self.advanced_options: list = []
        self.advanced_streams: list = []
        self.selected_index = 0
        self.error: ReelixError | None = None
        self.download: Download | None = None
        self.debug = config.get("debug", False)
        self.last_result: dict | None = None


def run() -> None:
    config = load_config()
    missing = check_dependencies()
    if missing:
        _print_dependency_error(missing)
        sys.exit(1)
    curses.wrapper(_main, config)


def _print_dependency_error(missing: list[ReelixError]) -> None:
    print("Reelix can't start -- missing dependencies:\n")
    for err in missing:
        print(f"  \u2717 {err.title}")
        print(f"    {err.message}\n")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _main(stdscr, config: dict) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    color_enabled = ui.init_colors(config.get("color_enabled", True))

    state = AppState(config)
    screen = "URL_INPUT"

    while True:
        stdscr.erase()
        if screen == "URL_INPUT":
            screen = _screen_url_input(stdscr, state, color_enabled)
        elif screen == "FETCHING":
            screen = _screen_fetching(stdscr, state, color_enabled)
        elif screen == "QUALITY_SELECT":
            screen = _screen_quality_select(stdscr, state, color_enabled)
        elif screen == "ADVANCED":
            screen = _screen_advanced(stdscr, state, color_enabled)
        elif screen == "DOWNLOADING":
            screen = _screen_downloading(stdscr, state, color_enabled)
        elif screen == "COMPLETE":
            screen = _screen_complete(stdscr, state, color_enabled)
        elif screen == "ERROR":
            screen = _screen_error(stdscr, state, color_enabled)
        elif screen == "QUIT":
            return
        else:
            return


# ---------------------------------------------------------------------------
# URL input
# ---------------------------------------------------------------------------

def _draw_header(stdscr, color_enabled: bool, x: int, y: int) -> int:
    ui.draw_box(stdscr, y, x, 4, BOX_WIDTH, color_enabled=color_enabled)
    title_attr = ui.attr(color_enabled, ui.COLOR_HEADER, bold=True)
    title = "\u26a1 REELIX"
    subtitle = "Universal Video Downloader"
    ui.safe_addstr(stdscr, y + 1, x + max(0, (BOX_WIDTH - len(title)) // 2), title, title_attr)
    sub_attr = ui.attr(color_enabled, ui.COLOR_MUTED)
    ui.safe_addstr(stdscr, y + 2, x + max(0, (BOX_WIDTH - len(subtitle)) // 2), subtitle, sub_attr)
    return y + 5


def _screen_url_input(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = _draw_header(stdscr, color_enabled, x, 1)

    ui.safe_addstr(stdscr, y, x, "Paste video URL", ui.attr(color_enabled, ui.COLOR_INFO, bold=True))
    ui.safe_addstr(stdscr, y + 1, x, "\u2500" * BOX_WIDTH, ui.attr(color_enabled, ui.COLOR_MUTED))
    ui.safe_addstr(stdscr, y + 3, x, "[Enter] Fetch   [Q] Quit", ui.attr(color_enabled, ui.COLOR_MUTED))
    stdscr.refresh()

    curses.curs_set(1)
    buf = list(state.url)
    prompt_y = y + 2
    while True:
        ui.safe_addstr(stdscr, prompt_y, x, " " * BOX_WIDTH, curses.A_NORMAL)
        line = "> " + "".join(buf)
        ui.safe_addstr(stdscr, prompt_y, x, line, ui.attr(color_enabled, ui.COLOR_MUTED))
        stdscr.move(prompt_y, min(x + 2 + len(buf), x + BOX_WIDTH - 1))
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (curses.KEY_ENTER, 10, 13):
            text = "".join(buf).strip()
            if not looks_like_url(text):
                _flash_message(stdscr, prompt_y + 2, x, "That doesn't look like a URL.", ui.COLOR_ERROR, color_enabled)
                continue
            state.url = text
            curses.curs_set(0)
            return "FETCHING"
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if buf:
                buf.pop()
        elif ch in (17, ord('q'), ord('Q')) and not buf:
            curses.curs_set(0)
            return "QUIT"
        elif 32 <= ch <= 126:
            buf.append(chr(ch))


def _flash_message(stdscr, y: int, x: int, text: str, pair: int, color_enabled: bool) -> None:
    ui.safe_addstr(stdscr, y, x, text, ui.attr(color_enabled, pair))
    stdscr.refresh()
    time.sleep(1.1)
    ui.safe_addstr(stdscr, y, x, " " * len(text), curses.A_NORMAL)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _screen_fetching(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = _draw_header(stdscr, color_enabled, x, 1)
    ui.safe_addstr(stdscr, y, x, "Fetching information...", ui.attr(color_enabled, ui.COLOR_INFO))
    stdscr.refresh()

    result: dict = {}

    def worker():
        try:
            result["info"] = metadata.fetch_info(state.url, debug=state.debug)
        except ReelixError as exc:
            result["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    frames = ["\u28f7", "\u28ef", "\u28df", "\u287f", "\u28bf", "\u28fb", "\u28fd", "\u28fe"]
    i = 0
    stdscr.nodelay(True)
    while thread.is_alive():
        ui.safe_addstr(stdscr, y, x + 25, frames[i % len(frames)], ui.attr(color_enabled, ui.COLOR_INFO))
        stdscr.refresh()
        time.sleep(0.08)
        i += 1
    stdscr.nodelay(False)
    thread.join()

    if "error" in result:
        state.error = result["error"]
        return "ERROR"

    info = result["info"]
    state.info = info
    state.normal_options, state.advanced_options = fmt_module.build_quality_options(info)
    state.advanced_streams = fmt_module.build_advanced_table(info)
    state.selected_index = 0

    if not state.normal_options and not state.advanced_options:
        state.error = ReelixError(
            "No compatible formats",
            "yt-dlp couldn't find a downloadable video+audio combination "
            "for this link.",
        )
        return "ERROR"

    ui.safe_addstr(stdscr, y, x, "\u2713 Video found", ui.attr(color_enabled, ui.COLOR_SUCCESS, bold=True))
    stdscr.refresh()
    time.sleep(0.4)
    return "QUALITY_SELECT"


# ---------------------------------------------------------------------------
# Shared video-info panel
# ---------------------------------------------------------------------------

def _draw_video_info(stdscr, state: AppState, color_enabled: bool, x: int, y: int) -> int:
    info = state.info or {}
    title = (info.get("title") or "Unknown title")
    if len(title) > BOX_WIDTH - 13:
        title = title[: BOX_WIDTH - 16] + "..."
    duration = format_duration(info.get("duration"))
    source = info.get("_source_name", "Unknown")

    label_attr = ui.attr(color_enabled, ui.COLOR_MUTED)
    value_attr = ui.attr(color_enabled, ui.COLOR_INFO)
    ui.safe_addstr(stdscr, y, x, "Title", label_attr)
    ui.safe_addstr(stdscr, y, x + 11, title, value_attr)
    ui.safe_addstr(stdscr, y + 1, x, "Duration", label_attr)
    ui.safe_addstr(stdscr, y + 1, x + 11, duration, value_attr)
    ui.safe_addstr(stdscr, y + 2, x, "Source", label_attr)
    ui.safe_addstr(stdscr, y + 2, x + 11, source, value_attr)
    return y + 4


# ---------------------------------------------------------------------------
# Quality selection
# ---------------------------------------------------------------------------

def _quality_rows(state: AppState) -> list[tuple[str, object | None]]:
    """Build the flat list of rows shown in the quality box: section
    headers (option=None) interleaved with selectable QualityOption rows."""
    rows: list[tuple[str, object | None]] = []
    low = [o for o in state.normal_options if o.tier == "LOW"]
    high = [o for o in state.normal_options if o.tier == "HIGH"]
    if low:
        rows.append(("LOW", None))
        for opt in low:
            rows.append((opt.label, opt))
    if high:
        rows.append(("HIGH", None))
        for opt in high:
            rows.append((opt.label, opt))
    return rows


def _screen_quality_select(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = 1
    y = _draw_video_info(stdscr, state, color_enabled, x, y)

    rows = _quality_rows(state)
    selectable = [i for i, (_, opt) in enumerate(rows) if opt is not None]
    if not selectable:
        return "ADVANCED"
    if state.selected_index not in selectable:
        state.selected_index = selectable[0]

    box_height = len(rows) + 4
    ui.draw_box(stdscr, y, x, box_height, BOX_WIDTH, title="QUALITY", color_enabled=color_enabled)

    row_y = y + 2
    for i, (label, opt) in enumerate(rows):
        if opt is None:
            ui.safe_addstr(stdscr, row_y, x + 2, label, ui.attr(color_enabled, ui.COLOR_WARNING, bold=True))
        else:
            is_selected = (i == state.selected_index)
            size = opt.total_size
            size_text = ("~" if opt.is_approx and size is not None else "") + human_size(size)
            marker = "\u25b8 " if is_selected else "  "
            line_attr = ui.attr(color_enabled, ui.COLOR_SUCCESS, bold=True) if is_selected else ui.attr(color_enabled, ui.COLOR_MUTED)
            text = f"{marker}{label:<8} {size_text}"
            ui.safe_addstr(stdscr, row_y, x + 3, text, line_attr)
        row_y += 1

    ui.draw_divider(stdscr, y + box_height - 2, x, BOX_WIDTH, color_enabled)
    help_attr = ui.attr(color_enabled, ui.COLOR_MUTED)
    ui.safe_addstr(stdscr, y + box_height - 1, x + 2,
                    "[\u2191\u2193] Select  [Enter] Download  [A] Advanced  [N] New  [Q] Quit", help_attr)
    stdscr.refresh()

    ch = stdscr.getch()
    if ch in (curses.KEY_UP, ord('k')):
        pos = selectable.index(state.selected_index)
        state.selected_index = selectable[(pos - 1) % len(selectable)]
    elif ch in (curses.KEY_DOWN, ord('j')):
        pos = selectable.index(state.selected_index)
        state.selected_index = selectable[(pos + 1) % len(selectable)]
    elif ch in (curses.KEY_ENTER, 10, 13):
        chosen = rows[state.selected_index][1]
        return _begin_download(state, chosen.format_selector, chosen.label)
    elif ch in (ord('a'), ord('A')):
        state.selected_index = 0
        return "ADVANCED"
    elif ch in (ord('n'), ord('N')):
        state.url = ""
        return "URL_INPUT"
    elif ch in (ord('q'), ord('Q')):
        return "QUIT"
    return "QUALITY_SELECT"


# ---------------------------------------------------------------------------
# Advanced formats
# ---------------------------------------------------------------------------

def _screen_advanced(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    width = min(max_x - 2, 68)
    x = ui.center_x(max_x, width)
    y = 1

    streams = state.advanced_streams
    visible = min(len(streams), max(4, max_y - 8))
    box_height = visible + 5
    ui.draw_box(stdscr, y, x, box_height, width, title="ADVANCED FORMATS", color_enabled=color_enabled)

    header = f"{'ID':<7}{'RES':<8}{'FPS':<6}{'EXT':<6}{'CODEC':<10}{'SIZE':<10}"
    ui.safe_addstr(stdscr, y + 1, x + 2, header, ui.attr(color_enabled, ui.COLOR_MUTED, bold=True))

    if state.selected_index >= len(streams):
        state.selected_index = 0

    row_y = y + 2
    for i, s in enumerate(streams[:visible]):
        res = f"{s.height}p" if s.is_video and s.height else "audio"
        fps = f"{int(s.fps)}" if s.fps else "-"
        codec = s.vcodec if s.is_video else s.acodec
        codec = codec.split(".")[0] if codec else "-"
        size_txt = ("~" if s.size_approx else "") + human_size(s.size_bytes)
        line = f"{s.format_id:<7}{res:<8}{fps:<6}{s.ext:<6}{codec:<10}{size_txt:<10}"
        is_selected = i == state.selected_index
        line_attr = ui.attr(color_enabled, ui.COLOR_SUCCESS, bold=True) if is_selected else ui.attr(color_enabled, ui.COLOR_MUTED)
        marker = "\u25b8 " if is_selected else "  "
        ui.safe_addstr(stdscr, row_y, x + 2, marker + line, line_attr)
        row_y += 1

    ui.draw_divider(stdscr, y + box_height - 2, x, width, color_enabled)
    ui.safe_addstr(stdscr, y + box_height - 1, x + 2,
                    "[\u2191\u2193] Select  [Enter] Download  [B] Back  [Q] Quit",
                    ui.attr(color_enabled, ui.COLOR_MUTED))
    stdscr.refresh()

    ch = stdscr.getch()
    if ch in (curses.KEY_UP, ord('k')):
        state.selected_index = (state.selected_index - 1) % len(streams)
    elif ch in (curses.KEY_DOWN, ord('j')):
        state.selected_index = (state.selected_index + 1) % len(streams)
    elif ch in (curses.KEY_ENTER, 10, 13):
        chosen = streams[state.selected_index]
        if chosen.is_video and not chosen.is_audio:
            audio = fmt_module.best_audio_stream(
                fmt_module.parse_streams(state.info.get("formats") or [])
            )
            selector = f"{chosen.format_id}+{audio.format_id}" if audio else chosen.format_id
        else:
            selector = chosen.format_id
        label = f"{chosen.height}p" if chosen.is_video and chosen.height else f"{chosen.ext} audio"
        state.selected_index = 0
        return _begin_download(state, selector, label)
    elif ch in (ord('b'), ord('B')):
        state.selected_index = 0
        return "QUALITY_SELECT"
    elif ch in (ord('q'), ord('Q')):
        return "QUIT"
    return "ADVANCED"


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _begin_download(state: AppState, format_selector: str, label: str) -> str:
    try:
        directory = storage.ensure_download_dir(state.config.get("download_dir"))
    except ReelixError as exc:
        state.error = exc
        return "ERROR"

    title = (state.info or {}).get("title") or "video"
    container = state.config.get("default_container", "mp4")
    dest = storage.output_path(directory, title, container)

    dl = Download(
        url=state.url,
        format_selector=format_selector,
        dest_path=dest,
        aria2_connections=state.config.get("aria2_connections", 8),
        aria2_split=state.config.get("aria2_split", 8),
        aria2_min_split=state.config.get("aria2_min_split_size", "1M"),
        container=container,
        debug=state.debug,
    )
    state.download = dl
    state.last_result = {"title": title, "quality": label, "container": container.upper(), "dest": str(directory)}
    dl.start()
    return "DOWNLOADING"


def _screen_downloading(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = 1
    dl = state.download
    result = state.last_result or {}

    box_height = 11
    ui.draw_box(stdscr, y, x, box_height, BOX_WIDTH, title="DOWNLOADING", color_enabled=color_enabled)

    title = result.get("title", "")
    if len(title) > BOX_WIDTH - 4:
        title = title[: BOX_WIDTH - 7] + "..."
    ui.safe_addstr(stdscr, y + 1, x + 2, title, ui.attr(color_enabled, ui.COLOR_INFO, bold=True))

    label_attr = ui.attr(color_enabled, ui.COLOR_MUTED)
    value_attr = ui.attr(color_enabled, ui.COLOR_MUTED)
    ui.safe_addstr(stdscr, y + 2, x + 2, "Quality", label_attr)
    ui.safe_addstr(stdscr, y + 2, x + 13, result.get("quality", "-"), value_attr)
    ui.safe_addstr(stdscr, y + 3, x + 2, "Format", label_attr)
    ui.safe_addstr(stdscr, y + 3, x + 13, result.get("container", "-"), value_attr)

    percent = 0.0
    downloaded = 0.0
    total = 0.0
    speed = None
    eta = None
    stage = "Starting..."

    stdscr.nodelay(True)
    deadline = time.time() + 0.5
    while time.time() < deadline:
        event = dl.poll(timeout=0.1)
        if event is None:
            if dl.done:
                break
            continue
        if event.kind == "progress":
            percent = event.percent or percent
            downloaded = event.downloaded_bytes or downloaded
            total = event.total_bytes or total
            speed = event.speed_bytes
            eta = event.eta_seconds
            stage = "Downloading"
        elif event.kind == "merging":
            stage = "Merging with FFmpeg..."
        elif event.kind == "finished":
            break
        if dl.done and dl.events.empty():
            break

    size_line = f"{human_size(downloaded)} / {human_size(total)}" if total else stage
    ui.safe_addstr(stdscr, y + 5, x + 2, size_line, ui.attr(color_enabled, ui.COLOR_MUTED))

    bar_width = BOX_WIDTH - 12
    bar = ui.progress_bar(percent, bar_width)
    bar_attr = ui.attr(color_enabled, ui.COLOR_SUCCESS)
    ui.safe_addstr(stdscr, y + 6, x + 2, bar, bar_attr)
    ui.safe_addstr(stdscr, y + 6, x + 3 + bar_width, f"{percent:5.1f}%", ui.attr(color_enabled, ui.COLOR_INFO, bold=True))

    ui.safe_addstr(stdscr, y + 8, x + 2, "Speed", label_attr)
    ui.safe_addstr(stdscr, y + 8, x + 13, human_speed(speed), value_attr)
    ui.safe_addstr(stdscr, y + 9, x + 2, "ETA", label_attr)
    ui.safe_addstr(stdscr, y + 9, x + 13, format_eta(eta), value_attr)

    stdscr.refresh()
    stdscr.nodelay(False)

    if dl.done:
        if dl.error is not None:
            state.error = dl.error
            return "ERROR"
        state.last_result["size"] = human_size(total) if total else "unknown"
        return "COMPLETE"
    return "DOWNLOADING"


# ---------------------------------------------------------------------------
# Complete / Error
# ---------------------------------------------------------------------------

def _screen_complete(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = 1
    result = state.last_result or {}
    box_height = 10
    ui.draw_box(stdscr, y, x, box_height, BOX_WIDTH, title="\u2713 DOWNLOAD COMPLETE", color_enabled=color_enabled)

    label_attr = ui.attr(color_enabled, ui.COLOR_MUTED)
    value_attr = ui.attr(color_enabled, ui.COLOR_SUCCESS)
    title = result.get("title", "")
    if len(title) > BOX_WIDTH - 10:
        title = title[: BOX_WIDTH - 13] + "..."
    ui.safe_addstr(stdscr, y + 1, x + 2, "Title", label_attr)
    ui.safe_addstr(stdscr, y + 1, x + 9, title, value_attr)
    ui.safe_addstr(stdscr, y + 2, x + 2, "Quality", label_attr)
    ui.safe_addstr(stdscr, y + 2, x + 9, result.get("quality", "-"), value_attr)
    ui.safe_addstr(stdscr, y + 3, x + 2, "Format", label_attr)
    ui.safe_addstr(stdscr, y + 3, x + 9, result.get("container", "-"), value_attr)
    ui.safe_addstr(stdscr, y + 4, x + 2, "Size", label_attr)
    ui.safe_addstr(stdscr, y + 4, x + 9, result.get("size", "-"), value_attr)
    ui.safe_addstr(stdscr, y + 6, x + 2, "Saved to:", label_attr)
    dest = result.get("dest", "")
    if len(dest) > BOX_WIDTH - 4:
        dest = "..." + dest[-(BOX_WIDTH - 7):]
    ui.safe_addstr(stdscr, y + 7, x + 2, dest, ui.attr(color_enabled, ui.COLOR_INFO))

    ui.safe_addstr(stdscr, y + box_height, x + 2, "[N] New download   [Q] Quit", label_attr)
    stdscr.refresh()

    ch = stdscr.getch()
    if ch in (ord('n'), ord('N')):
        state.url = ""
        state.info = None
        return "URL_INPUT"
    elif ch in (ord('q'), ord('Q')):
        return "QUIT"
    return "COMPLETE"


def _screen_error(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = 1
    err = state.error or ReelixError("Unknown error", "Something went wrong.")

    lines = _wrap(err.message, BOX_WIDTH - 4)
    box_height = 4 + len(lines) + 2
    ui.draw_box(stdscr, y, x, box_height, BOX_WIDTH, title=f"\u2717 {err.title.upper()}", color_enabled=color_enabled, border_pair=ui.COLOR_ERROR)

    row = y + 2
    for line in lines:
        ui.safe_addstr(stdscr, row, x + 2, line, ui.attr(color_enabled, ui.COLOR_ERROR))
        row += 1

    if state.debug and err.raw:
        ui.safe_addstr(stdscr, row + 1, x + 2, "(debug output written to stderr)", ui.attr(color_enabled, ui.COLOR_MUTED))

    ui.safe_addstr(stdscr, y + box_height, x + 2, "[R] Retry   [N] New URL   [Q] Quit", ui.attr(color_enabled, ui.COLOR_MUTED))
    stdscr.refresh()

    if state.debug and err.raw:
        print(err.raw, file=sys.stderr)

    ch = stdscr.getch()
    if ch in (ord('r'), ord('R')) and state.url:
        return "FETCHING"
    elif ch in (ord('n'), ord('N')):
        state.url = ""
        state.info = None
        return "URL_INPUT"
    elif ch in (ord('q'), ord('Q')):
        return "QUIT"
    return "ERROR"


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
