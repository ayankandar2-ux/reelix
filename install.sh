#!/data/data/com.termux/files/usr/bin/env bash
# install.sh -- installs Reelix (Universal Video Downloader) for Termux.
#
# Usage:
#   bash install.sh
#
# After install, either restart your shell or run:
#   source ~/.bashrc
# then just type:
#   reelix

set -euo pipefail

REELIX_HOME="${REELIX_HOME:-$HOME/.reelix}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info()  { printf "\033[36m[*]\033[0m %s\n" "$1"; }
ok()    { printf "\033[32m[\xe2\x9c\x93]\033[0m %s\n" "$1"; }
warn()  { printf "\033[33m[!]\033[0m %s\n" "$1"; }
fail()  { printf "\033[31m[\xe2\x9c\x97]\033[0m %s\n" "$1"; }

echo "======================================"
echo "  Reelix -- Universal Video Downloader"
echo "  Installer"
echo "======================================"
echo

# 1. Check Termux
if [ -d "/data/data/com.termux/files/usr" ]; then
    ok "Termux detected"
    BIN_DIR="/data/data/com.termux/files/usr/bin"
else
    warn "Termux not detected -- continuing anyway (bin/ will be added to PATH via ~/.bashrc)"
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$BIN_DIR"
fi

# 2. Check Python
if command -v python3 >/dev/null 2>&1; then
    ok "Python found: $(python3 --version 2>&1)"
else
    fail "Python 3 not found. Install it with: pkg install python"
    exit 1
fi

# 3. Check FFmpeg
if command -v ffmpeg >/dev/null 2>&1; then
    ok "FFmpeg found"
else
    warn "FFmpeg not found. Install it with: pkg install ffmpeg"
fi

# 4. Check aria2c
if command -v aria2c >/dev/null 2>&1; then
    ok "aria2c found"
else
    warn "aria2c not found. Install it with: pkg install aria2"
fi

# 5. Check yt-dlp
if command -v yt-dlp >/dev/null 2>&1; then
    ok "yt-dlp found: $(yt-dlp --version 2>&1 | head -n1)"
else
    warn "yt-dlp not found. Install it with: pip install -U yt-dlp[default]"
fi

# 6. Check Deno (needed by yt-dlp-ejs for some sites' JS challenges)
if command -v deno >/dev/null 2>&1; then
    ok "Deno found: $(deno --version 2>&1 | head -n1)"
else
    warn "Deno not found. Some sites (e.g. YouTube bot checks) may need it."
    warn "  Install with: pkg install deno   (or see https://deno.land)"
fi

# 7. Create the project directory
info "Installing to $REELIX_HOME"
mkdir -p "$REELIX_HOME"

# 8. Copy the Reelix application
rm -rf "$REELIX_HOME/reelix"
cp -r "$PROJECT_DIR/reelix" "$REELIX_HOME/reelix"
ok "Application files copied"

# 9. Create the Reelix launcher
mkdir -p "$BIN_DIR"
cp "$PROJECT_DIR/bin/reelix" "$BIN_DIR/reelix"
chmod +x "$BIN_DIR/reelix"
ok "Launcher installed at $BIN_DIR/reelix"

# 10. Add launcher dir to PATH if necessary
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    SHELL_RC="$HOME/.bashrc"
    if ! grep -q "Reelix Universal Video Downloader" "$SHELL_RC" 2>/dev/null; then
        {
            echo ""
            echo "# Reelix Universal Video Downloader"
            echo "export PATH=\"$BIN_DIR:\$PATH\""
        } >> "$SHELL_RC"
        info "Added $BIN_DIR to PATH in $SHELL_RC"
    fi
fi

# 11. Create the storage directory if possible
DOWNLOAD_DIR="/storage/emulated/0/Movies/Reelix"
if mkdir -p "$DOWNLOAD_DIR" 2>/dev/null; then
    ok "Download folder ready: $DOWNLOAD_DIR"
else
    warn "Couldn't create '$DOWNLOAD_DIR' yet."
    warn "  Run 'termux-setup-storage' and grant storage permission, then try Reelix again."
fi

# 12. Run a dependency check
echo
info "Dependency summary:"
for tool in python3 ffmpeg aria2c yt-dlp deno; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool"
    else
        fail "$tool (missing)"
    fi
done

# 13. Tell the user how to launch Reelix
echo
echo "======================================"
ok "Installation complete"
echo "======================================"
echo
echo "Run this once in your current session:"
echo "  source ~/.bashrc"
echo
echo "Then launch Reelix anytime with:"
echo "  reelix"
echo
