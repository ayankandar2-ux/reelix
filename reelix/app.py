"""Reelix -- Universal Video Downloader. Main curses application.

Screen flow:

    URL_INPUT -> FETCHING -> QUALITY_SELECT -> (ADVANCED) -> DOWNLOADING
        -> COMPLETE / ERROR -> (N) back to URL_INPUT, (Q) quit
"""
from __future__ import annotations

import curses
import os
import re
import sys
import threading
import time
from pathlib import Path

from . import formats as fmt_module
from . import history
from . import metadata
from . import sizeprobe
from . import storage
from . import ui
from . import updatecheck
from .config import load_config, save_config
from .downloader import Download
from .errors import ReelixError, check_dependencies
from .utils import format_duration, format_eta, human_size, human_speed, looks_like_url

BOX_WIDTH = 56
ADVANCED_PROBE_MAX_WAIT = 3.0  # seconds we'll auto-refresh the screen while probing
MAX_AUTO_RETRIES = 2


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
        self.last_format_selector: str = ""
        self.retry_count = 0
        self.history_index = 0
        self.update_notice: str | None = None
        self.advanced_probe_started = False
        self.advanced_probe_thread: threading.Thread | None = None


def run() -> None:
    config = load_config()
    missing = check_dependencies()
    if missing:
        _print_dependency_error(missing)
        sys.exit(1)
    os.environ.setdefault("ESCDELAY", "25")
    _set_bracketed_paste(False)
    try:
        curses.wrapper(_main, config)
    finally:
        _set_bracketed_paste(True)


def _set_bracketed_paste(enabled: bool) -> None:
    """Tell the terminal whether to wrap pasted text in ESC[200~ ... ESC[201~
    markers. Our curses input loop reads one keystroke at a time and doesn't
    understand those markers -- when it hits the leading ESC it stalls trying
    to resolve it as a special key and can swallow the first several
    characters of a fast paste. Disabling it for the app's session avoids
    that entirely; it's restored on exit so the shell's own paste handling
    (e.g. bash's multi-line-safe paste) isn't affected afterward."""
    try:
        sys.stdout.write("\x1b[?2004h" if enabled else "\x1b[?2004l")
        sys.stdout.flush()
    except Exception:
        pass


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
    threading.Thread(target=_check_update_background, args=(state,), daemon=True).start()
    screen = "URL_INPUT"

    while True:
        stdscr.erase()
        if screen == "URL_INPUT":
            screen = _screen_url_input(stdscr, state, color_enabled)
        elif screen == "HISTORY":
            screen = _screen_history(stdscr, state, color_enabled)
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


def _check_update_background(state: AppState) -> None:
    try:
        remote = updatecheck.check_for_update()
    except Exception:
        remote = None
    if remote:
        state.update_notice = f"Update available: v{remote} (you have v{updatecheck.local_version()})"


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


def _more_input_imminent(stdscr) -> bool:
    """Peek for a character that's already queued right behind the one we
    just read. If one shows up almost instantly, we're in the middle of a
    fast burst (typed-ahead or pasted text) rather than a single deliberate
    keypress. Any character found is pushed back so the main loop reads it
    normally on its next pass."""
    stdscr.timeout(40)
    try:
        ch = stdscr.getch()
    finally:
        stdscr.timeout(-1)
    if ch == -1:
        return False
    curses.ungetch(ch)
    return True


def _try_read_bracketed_paste(stdscr) -> str | None:
    """Called right after reading a raw ESC (27). If the terminal is
    actually mid-paste and sent a bracketed-paste block despite us asking
    it not to (ESC[200~ ... ESC[201~), consume the whole thing and hand it
    back as one string, so it lands in the input buffer atomically instead
    of being picked apart character-by-character with the leading bytes
    lost. Returns None if this ESC wasn't actually a paste marker (a plain
    ESC keypress is simply ignored, as before)."""
    stdscr.timeout(50)
    try:
        for expected in "[200~":
            if stdscr.getch() != ord(expected):
                return None
        payload = []
        while True:
            ch = stdscr.getch()
            if ch == -1:
                break  # paste stream stalled; return whatever we captured
            if ch == 27:
                tail = []
                is_end = True
                for expected in "[201~":
                    c2 = stdscr.getch()
                    tail.append(c2)
                    if c2 != ord(expected):
                        is_end = False
                        break
                if is_end:
                    break
                payload.append(chr(27))
                for c2 in tail:
                    if c2 is not None and 32 <= c2 <= 126:
                        payload.append(chr(c2))
                continue
            if 32 <= ch <= 126:
                payload.append(chr(ch))
        return "".join(payload)
    finally:
        stdscr.timeout(-1)


def _screen_url_input(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = _draw_header(stdscr, color_enabled, x, 1)

    ui.safe_addstr(stdscr, y, x, "Paste video URL", ui.attr(color_enabled, ui.COLOR_INFO, bold=True))
    ui.safe_addstr(stdscr, y + 1, x, "\u2500" * BOX_WIDTH, ui.attr(color_enabled, ui.COLOR_MUTED))
    ui.safe_addstr(stdscr, y + 3, x, "[Enter] Fetch   [H] History   [Q] Quit", ui.attr(color_enabled, ui.COLOR_MUTED))
    if state.update_notice:
        ui.safe_addstr(stdscr, y + 5, x, state.update_notice[:BOX_WIDTH], ui.attr(color_enabled, ui.COLOR_INFO))
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
        if ch == 27:
            pasted = _try_read_bracketed_paste(stdscr)
            if pasted:
                buf.extend(pasted)
            continue
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
        elif ch in (ord('h'), ord('H')) and not buf:
            if _more_input_imminent(stdscr):
                # Another character is already queued right behind this one
                # -- this is the start of typed/pasted text (e.g. a URL
                # beginning with "http"), not a deliberate single tap on
                # the History shortcut. Treat it as ordinary text.
                buf.append(chr(ch))
            else:
                curses.curs_set(0)
                return "HISTORY"
        elif ch == 17 and not buf:
            curses.curs_set(0)
            return "QUIT"
        elif ch in (ord('q'), ord('Q')) and not buf:
            if _more_input_imminent(stdscr):
                buf.append(chr(ch))
            else:
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
# History
# ---------------------------------------------------------------------------

def _screen_history(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    x = ui.center_x(max_x, BOX_WIDTH)
    y = 1
    entries = history.load_history()

    box_height = max(4, min(max_y - y - 2, 4 + max(1, len(entries))))
    ui.draw_box(stdscr, y, x, box_height, BOX_WIDTH, title="DOWNLOAD HISTORY", color_enabled=color_enabled)

    label_attr = ui.attr(color_enabled, ui.COLOR_MUTED)
    if not entries:
        ui.safe_addstr(stdscr, y + 2, x + 2, "No downloads yet.", label_attr)
    else:
        if state.history_index >= len(entries):
            state.history_index = len(entries) - 1
        if state.history_index < 0:
            state.history_index = 0
        visible_rows = box_height - 2
        start = max(0, min(state.history_index - visible_rows // 2, max(0, len(entries) - visible_rows)))
        row = y + 1
        for i in range(start, min(start + visible_rows, len(entries))):
            entry = entries[i]
            selected = i == state.history_index
            marker = "\u25b8 " if selected else "  "
            when = entry.get("when", "")[:10]
            title = entry.get("title", "")
            quality = entry.get("quality", "")
            budget = BOX_WIDTH - 4 - len(marker) - len(quality) - len(when) - 4
            if len(title) > budget:
                title = title[: max(0, budget - 1)] + "\u2026"
            line = f"{marker}{title}  {quality}  {when}"
            attr = ui.attr(color_enabled, ui.COLOR_SUCCESS, bold=selected) if selected else label_attr
            ui.safe_addstr(stdscr, row, x + 1, line[: BOX_WIDTH - 2], attr)
            row += 1

    ui.safe_addstr(stdscr, y + box_height, x + 2, "[\u2191\u2193] Select  [Enter] Re-download  [B] Back  [Q] Quit", label_attr)
    stdscr.refresh()

    ch = stdscr.getch()
    if ch in (curses.KEY_UP, ord('k')):
        state.history_index = max(0, state.history_index - 1)
    elif ch in (curses.KEY_DOWN, ord('j')):
        state.history_index = min(max(0, len(entries) - 1), state.history_index + 1)
    elif ch in (curses.KEY_ENTER, 10, 13) and entries:
        entry = entries[state.history_index]
        state.url = entry.get("url", "")
        state.info = None
        return "FETCHING" if state.url else "HISTORY"
    elif ch in (ord('b'), ord('B'), 27):
        return "URL_INPUT"
    elif ch in (ord('q'), ord('Q')):
        return "QUIT"
    return "HISTORY"


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
    state.advanced_probe_started = False
    state.advanced_probe_thread = None

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

def _ensure_advanced_probe_started(state: AppState) -> None:
    if state.advanced_probe_started:
        return
    state.advanced_probe_started = True
    sizeprobe.mark_probing(state.advanced_streams)
    thread = threading.Thread(
        target=sizeprobe.probe_sizes,
        args=(state.advanced_streams,),
        daemon=True,
    )
    state.advanced_probe_thread = thread
    thread.start()


def _advanced_still_probing(state: AppState) -> bool:
    return any(s.probing for s in state.advanced_streams)


def _short_format_id(format_id: str, max_len: int = 7) -> str:
    """Some sites (Instagram, Facebook) hand back format IDs that are a huge
    opaque number plus a short readable tag, e.g.
    'dash-18113169793891943v720p' or 'dash-18113159506891943aaudio'.
    Showing the whole thing just fills the column with noise the user can't
    tell apart. Trim to the trailing letters+digits run that starts with a
    letter (the readable tag) -- falls back to the full id untouched for
    normal short/simple ids like '137' that have nothing to trim."""
    if len(format_id) <= max_len:
        return format_id
    match = re.search(r"[a-zA-Z][a-zA-Z0-9]*$", format_id)
    tail = match.group(0) if match else format_id
    if len(tail) > max_len:
        tail = tail[-max_len:]
    return tail


def _draw_advanced_table(stdscr, state: AppState, color_enabled: bool, x: int, y: int, width: int) -> int:
    streams = state.advanced_streams
    visible = min(len(streams), max(4, stdscr.getmaxyx()[0] - 8))
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
        if s.size_bytes is not None:
            size_txt = ("~" if s.size_approx else "") + human_size(s.size_bytes)
        elif s.probing:
            size_txt = "measuring..."
        else:
            size_txt = "unknown"
        short_id = _short_format_id(s.format_id)
        line = f"{short_id:<7}{res:<8}{fps:<6}{s.ext:<6}{codec:<10}{size_txt:<10}"
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
    return box_height


def _screen_advanced(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    width = min(max_x - 2, 68)
    x = ui.center_x(max_x, width)
    y = 1

    streams = state.advanced_streams
    _ensure_advanced_probe_started(state)
    _draw_advanced_table(stdscr, state, color_enabled, x, y, width)

    # While sizes are still being measured, auto-refresh the table so
    # results appear live without the user needing to press a key.
    ch = -1
    if _advanced_still_probing(state):
        stdscr.timeout(150)
        deadline = time.time() + ADVANCED_PROBE_MAX_WAIT
        while _advanced_still_probing(state) and time.time() < deadline:
            ch = stdscr.getch()
            if ch != -1:
                break
            _draw_advanced_table(stdscr, state, color_enabled, x, y, width)
        stdscr.timeout(-1)  # back to blocking

    if ch == -1:
        ch = stdscr.getch()

    if ch in (curses.KEY_UP, ord('k')):
        state.selected_index = (state.selected_index - 1) % len(streams)
    elif ch in (curses.KEY_DOWN, ord('j')):
        state.selected_index = (state.selected_index + 1) % len(streams)
    elif ch in (curses.KEY_ENTER, 10, 13):
        chosen = streams[state.selected_index]
        if chosen.is_video and not chosen.is_audio:
            audio = fmt_module.best_audio_stream(
                fmt_module.parse_streams(state.info.get("formats") or [], (state.info or {}).get("duration"))
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
    state.last_format_selector = format_selector
    dl.start()
    return "DOWNLOADING"


def _screen_downloading(stdscr, state: AppState, color_enabled: bool) -> str:
    max_y, max_x = stdscr.getmaxyx()
    dl = state.download
    result = state.last_result or {}

    # Draw the current frame immediately -- right after the main loop's own
    # erase() -- so the screen is never left blank while we wait below.
    # Previously the box/lines were only drawn *after* the poll loop, which
    # meant every ~0.5s pass showed: erase (blank) -> wait -> draw (visible)
    # -> immediately erased again by the next pass. That erase/wait/draw gap
    # is exactly what looked like fast on/off flicker.
    header = "Downloading..."
    ui.safe_addstr(stdscr, 0, 2, header, ui.attr(color_enabled, ui.COLOR_INFO, bold=True))

    title = result.get("title", "")
    if title:
        ui.safe_addstr(stdscr, 1, 2, title[: max(0, max_x - 4)], ui.attr(color_enabled, ui.COLOR_MUTED))

    box_y = 3
    box_x = 0
    box_width = max_x
    box_height = max(3, max_y - box_y - 2)
    box_title = "LIVE OUTPUT (PAUSED)" if dl.paused else "LIVE OUTPUT"
    ui.draw_box(stdscr, box_y, box_x, box_height, box_width, title=box_title, color_enabled=color_enabled)

    inner_width = box_width - 4
    visible_rows = box_height - 2
    lines = dl.recent_lines(max(0, visible_rows))
    row = box_y + 1
    for line in lines:
        ui.safe_addstr(stdscr, row, box_x + 2, line[: max(0, inner_width)], ui.attr(color_enabled, ui.COLOR_MUTED))
        row += 1
        if row >= box_y + box_height - 1:
            break

    footer = "[P] Pause/Resume   [C] Cancel"
    ui.safe_addstr(stdscr, max_y - 1, box_x + 2, footer, ui.attr(color_enabled, ui.COLOR_MUTED))

    stdscr.refresh()

    # Now wait/poll for up to 0.5s -- the screen above stays fully drawn and
    # static the whole time, so nothing flickers. New output that arrives
    # during this window shows up on the *next* pass, not this one.
    stdscr.nodelay(True)
    deadline = time.time() + 0.5
    while time.time() < deadline:
        ch = stdscr.getch()
        if ch in (ord('c'), ord('C')):
            dl.cancel()
        elif ch in (ord('p'), ord('P')):
            dl.toggle_pause()
        event = dl.poll(timeout=0.1)
        if event is None:
            if dl.done:
                break
            continue
        if event.kind == "finished":
            break
    stdscr.nodelay(False)

    if dl.done:
        if dl.cancelled:
            state.url = ""
            state.info = None
            state.retry_count = 0
            return "URL_INPUT"
        if dl.error is not None:
            if state.retry_count < MAX_AUTO_RETRIES:
                state.retry_count += 1
                try:
                    if dl.dest_path.exists():
                        dl.dest_path.unlink()
                except OSError:
                    pass
                _flash_message(
                    stdscr, max_y - 1, box_x + 2,
                    f"Download failed -- retrying ({state.retry_count}/{MAX_AUTO_RETRIES})...",
                    ui.COLOR_ERROR, color_enabled,
                )
                return _begin_download(state, state.last_format_selector, result.get("quality", ""))
            state.retry_count = 0
            state.error = dl.error
            return "ERROR"
        state.retry_count = 0
        size = human_size(dl.total_bytes) if dl.total_bytes else "unknown"
        state.last_result["size"] = size
        history.add_entry(history.entry_now(
            url=state.url,
            title=result.get("title", ""),
            quality=result.get("quality", ""),
            container=result.get("container", ""),
            size=size,
            dest=result.get("dest", ""),
        ))
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
        state.retry_count = 0
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
