#!/bin/sh
# Installs DedNotch for the current user (no root needed).
#   ./install.sh             install
#   ./install.sh --autostart install and start automatically at login
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
APPDIR="$HOME/.local/lib/dednotch"
BINDIR="$HOME/.local/bin"
PY=/usr/bin/python3

echo "==> Checking dependencies"
missing=""
check() { "$PY" -c "import gi; gi.require_version('$1', '$2'); from gi.repository import $1" 2>/dev/null || missing="$missing $3"; }
check Gtk 3.0 gir1.2-gtk-3.0
check Wnck 3.0 gir1.2-wnck-3.0
check Gst 1.0 gir1.2-gst-plugins-base-1.0
check AyatanaAppIndicator3 0.1 gir1.2-ayatanaappindicator3-0.1
"$PY" -c "import cairo, gi" 2>/dev/null || missing="$missing python3-gi python3-gi-cairo"
command -v wpctl >/dev/null 2>&1 || missing="$missing wireplumber"
if [ -n "$missing" ]; then
    echo "    Some optional/required packages are missing. Install them with:"
    echo "      sudo apt install$missing"
fi

echo "==> Installing to $APPDIR"
rm -rf "$APPDIR"
mkdir -p "$APPDIR" "$BINDIR" "$PREFIX/applications" "$PREFIX/icons/hicolor/256x256/apps"
cp -r "$SRC/dednotch" "$APPDIR/"
find "$APPDIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
cp "$SRC/LICENSE" "$APPDIR/"

cat > "$BINDIR/dednotch" <<EOF
#!/bin/sh
export PYTHONPATH="$APPDIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec $PY -m dednotch "\$@"
EOF
chmod +x "$BINDIR/dednotch"

cp "$SRC/dednotch/assets/dednotch.png" "$PREFIX/icons/hicolor/256x256/apps/dednotch.png"
cat > "$PREFIX/applications/dednotch.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DedNotch
GenericName=Dynamic Notch
Comment=A MacBook-style dynamic notch for your Linux desktop
Exec=$BINDIR/dednotch
Icon=dednotch
Categories=Utility;AudioVideo;
Keywords=notch;music;island;shelf;
StartupNotify=false
Actions=Settings;Quit;

[Desktop Action Settings]
Name=Settings
Exec=$BINDIR/dednotch --settings

[Desktop Action Quit]
Name=Quit
Exec=$BINDIR/dednotch --quit
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/applications" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$PREFIX/icons/hicolor" >/dev/null 2>&1 || true

if [ "$1" = "--autostart" ]; then
    mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
    cp "$PREFIX/applications/dednotch.desktop" "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/dednotch.desktop"
    echo "X-GNOME-Autostart-Delay=3" >> "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/dednotch.desktop"
    echo "==> Autostart enabled"
fi

echo "==> Done. Start it from the app grid (DedNotch) or run: dednotch"
case ":$PATH:" in *":$BINDIR:"*) ;; *) echo "    (add $BINDIR to your PATH to use the 'dednotch' command)";; esac
