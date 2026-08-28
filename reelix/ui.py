"""Small curses drawing helpers shared by every screen in app.py.

Kept deliberately minimal -- no external TUI framework, just stdlib
`curses`, which ships with Termux's Python and is lightweight enough for
an Android terminal.
"""
from __future__ import annotations

import curses

COLOR_SUCCESS = 1
COLOR_ERROR = 2
COLOR_WARNING = 3
COLOR_INFO = 4
COLOR_MUTED = 5
COLOR_HEADER = 6


def init_colors(enabled: bool = True) -> bool:
    """Initialize color pairs. Returns whether color is actually usable."""
    if not enabled or not curses.has_colors():
        return False
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(COLOR_SUCCESS, curses.COLOR_GREEN, bg)
    curses.init_pair(COLOR_ERROR, curses.COLOR_RED, bg)
    curses.init_pair(COLOR_WARNING, curses.COLOR_YELLOW, bg)
    curses.init_pair(COLOR_INFO, curses.COLOR_CYAN, bg)
    curses.init_pair(COLOR_MUTED, curses.COLOR_WHITE, bg)
    curses.init_pair(COLOR_HEADER, curses.COLOR_CYAN, bg)
    return True


def attr(color_enabled: bool, pair: int, bold: bool = False) -> int:
    if not color_enabled:
        return curses.A_BOLD if bold else curses.A_NORMAL
    a = curses.color_pair(pair)
    if bold:
        a |= curses.A_BOLD
    return a


def safe_addstr(win, y: int, x: int, text: str, attr_flags: int = 0) -> None:
    """addstr that silently clips instead of raising at screen edges."""
    max_y, max_x = win.getmaxyx()
    if y < 0 or y >= max_y or x >= max_x:
        return
    available = max_x - x - 1
    if available <= 0:
        return
    try:
        win.addstr(y, x, text[:available], attr_flags)
    except curses.error:
        pass


def draw_box(win, y: int, x: int, height: int, width: int, title: str = "",
             color_enabled: bool = True, border_pair: int = COLOR_INFO) -> None:
    """Draw a rounded-ish bordered panel with an optional title on the top
    edge, using plain ASCII box-drawing characters (Termux-safe)."""
    a = attr(color_enabled, border_pair)
    top = "\u256d" + "\u2500" * (width - 2) + "\u256e"
    bottom = "\u2570" + "\u2500" * (width - 2) + "\u256f"
    safe_addstr(win, y, x, top, a)
    for row in range(1, height - 1):
        safe_addstr(win, y + row, x, "\u2502", a)
        safe_addstr(win, y + row, x + width - 1, "\u2502", a)
    safe_addstr(win, y + height - 1, x, bottom, a)
    if title:
        label = f" {title} "
        safe_addstr(win, y, x + 2, label, attr(color_enabled, COLOR_HEADER, bold=True))


def draw_divider(win, y: int, x: int, width: int, color_enabled: bool = True) -> None:
    a = attr(color_enabled, COLOR_INFO)
    safe_addstr(win, y, x, "\u251c" + "\u2500" * (width - 2) + "\u2524", a)


def progress_bar(percent: float, width: int) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = int(width * percent / 100.0)
    return "\u2588" * filled + "\u2591" * (width - filled)


def center_x(screen_width: int, box_width: int) -> int:
    return max(0, (screen_width - box_width) // 2)
