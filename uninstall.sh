#!/data/data/com.termux/files/usr/bin/env bash
# uninstall.sh -- removes Reelix from Termux. Downloaded videos are never touched.

set -euo pipefail

REELIX_HOME="${REELIX_HOME:-$HOME/.reelix}"
CANDIDATE_BINS=(
    "/data/data/com.termux/files/usr/bin/reelix"
    "$HOME/.local/bin/reelix"
)

echo "Removing Reelix..."

for bin in "${CANDIDATE_BINS[@]}"; do
    if [ -f "$bin" ]; then
        rm -f "$bin"
        echo "  removed launcher: $bin"
    fi
done

if [ -d "$REELIX_HOME" ]; then
    rm -rf "$REELIX_HOME"
    echo "  removed application files: $REELIX_HOME"
fi

CONFIG_DIR="$HOME/.config/reelix"
read -r -p "Also remove config at $CONFIG_DIR? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
    rm -rf "$CONFIG_DIR"
    echo "  removed config: $CONFIG_DIR"
fi

echo "Done. Downloaded videos in your Movies folder were left untouched."
