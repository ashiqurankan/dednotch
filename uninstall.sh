#!/bin/sh
# Removes DedNotch (keeps your settings unless --purge is given).
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
"$HOME/.local/bin/dednotch" --quit 2>/dev/null || true
rm -rf "$HOME/.local/lib/dednotch"
rm -f "$HOME/.local/bin/dednotch" "$PREFIX/applications/dednotch.desktop" \
      "$PREFIX/icons/hicolor/256x256/apps/dednotch.png" "$CONF/autostart/dednotch.desktop"
if [ "$1" = "--purge" ]; then
    rm -rf "$CONF/dednotch" "$PREFIX/dednotch" "${XDG_CACHE_HOME:-$HOME/.cache}/dednotch"
fi
echo "DedNotch removed."
