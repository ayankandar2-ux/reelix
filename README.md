# Reelix — Universal Video Downloader

A polished, terminal-based video downloader built for **Android/Termux**,
wrapping `yt-dlp` + `aria2c` + `FFmpeg` behind a clean, professional TUI —
no walls of raw yt-dlp output, no accidental 600 MiB downloads.

```
~ $ reelix
```

## Highlights

- Explicit quality selection (360p / 480p / 720p) — never auto-picks the
  largest available stream.
- Shows the expected file size next to each quality *before* you download.
- Advanced mode for raw format IDs, codecs, FPS, and bitrate, hidden from
  the normal flow.
- Fast downloads via `aria2c` as yt-dlp's external downloader.
- Clean progress screen (percentage, speed, ETA) — no scrollback spam.
- Friendly error messages for the common failure modes (private videos,
  login-required content, rate limiting, missing dependencies, etc.)
  instead of raw Python tracebacks.
- Saves finished videos straight to
  `/storage/emulated/0/Movies/Reelix`.

## Requirements

Tested against:

| Tool    | Version   |
|---------|-----------|
| Python  | 3.14.6    |
| yt-dlp  | 2026.08.19|
| FFmpeg  | 8.1.2     |
| aria2c  | 1.37.0    |
| Deno    | 2.9.5     |
| yt-dlp-ejs | 0.8.0  |

`install.sh` checks for all of these and tells you what's missing and how
to install it — it won't silently swap out tools you already have working.

## Install

```bash
git clone <this-repo>
cd reelix
bash install.sh
source ~/.bashrc
reelix
```

The installer:

1. Verifies Termux/Python/FFmpeg/aria2c/yt-dlp/Deno.
2. Copies the app to `~/.reelix`.
3. Installs a `reelix` launcher onto your `PATH`.
4. Creates `/storage/emulated/0/Movies/Reelix` if it doesn't exist
   yet (run `termux-setup-storage` first if you haven't already).

## Using Reelix

1. Run `reelix`.
2. Paste a video URL and press Enter.
3. Pick a quality — only resolutions that actually exist for that video are
   shown, each with its expected download size.
4. Watch the clean progress screen.
5. Find your video in `Movies/Reelix`.

Keyboard reference:

| Key     | Action              |
|---------|---------------------|
| ↑ / ↓   | Move selection      |
| Enter   | Select / Download   |
| A       | Advanced formats    |
| B       | Back                |
| N       | New URL             |
| R       | Retry after error   |
| Q       | Quit                |

## Advanced mode

Press `A` on the quality screen to see every format yt-dlp reported for
the video — format ID, resolution, FPS, codec, extension, and size —
and download any specific stream directly. Normal users never need this.

## Configuration

`~/.config/reelix/config.json` is created automatically on first run with
sensible defaults (download directory, aria2 connection count, default
container, color on/off, debug mode). You never need to hand-edit it, but
it's there if you want to, e.g., point downloads somewhere else.

## Uninstall

```bash
bash uninstall.sh
```

This removes the app and launcher only — it never touches videos you've
already downloaded.

## Notes & limitations

- Reelix supports whatever `yt-dlp` supports (YouTube, Instagram, Facebook,
  TikTok, and hundreds of other sites) — it does not guarantee every site
  on the internet works, and it does not attempt to bypass logins, DRM, or
  other access controls. Videos that require authentication will show a
  clear error instead of failing silently.
- File sizes are exact when the source provides an exact `filesize`, and
  clearly marked with `~` when only an approximate size is available.
