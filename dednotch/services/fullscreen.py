"""Detects fullscreen windows (X11/Wnck)."""
import gi

from . import Observable

try:
    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck
except (ValueError, ImportError):
    Wnck = None

from gi.repository import GLib  # noqa: E402


class FullscreenService(Observable):
    def __init__(self):
        super().__init__()
        self.fullscreen_rects = []   # list of (x, y, w, h, app_name)
        self.screen = None
        if Wnck is None:
            return
        try:
            Wnck.set_client_type(Wnck.ClientType.PAGER)
            self.screen = Wnck.Screen.get_default()
        except Exception:
            self.screen = None
            return
        if self.screen is None:
            return
        self.screen.connect("window-opened", self._on_window_opened)
        self.screen.connect("window-closed", lambda *a: self._schedule())
        self.screen.connect("active-workspace-changed", lambda *a: self._schedule())
        self.screen.connect("active-window-changed", lambda *a: self._schedule())
        self.screen.connect("window-stacking-changed", lambda *a: self._schedule())
        self._pending = None
        GLib.idle_add(self._initial)

    def _initial(self):
        self.screen.force_update()
        for w in self.screen.get_windows():
            self._on_window_opened(self.screen, w)
        self._scan()
        return False

    def _on_window_opened(self, _screen, window):
        window.connect("state-changed", lambda *a: self._schedule())
        window.connect("geometry-changed", lambda *a: self._schedule())
        self._schedule()

    def _schedule(self):
        if getattr(self, "_pending", None) is None:
            self._pending = GLib.timeout_add(150, self._scan)

    def _scan(self):
        """Record, per visible top window, whether it is fullscreen.

        Only the top-most normal window counts: a fullscreen video that sits
        behind other windows must not hide the notch."""
        self._pending = None
        rects = []
        if self.screen is not None:
            ws = self.screen.get_active_workspace()
            covered = []  # geometries of windows already seen (top-most first)
            for w in reversed(self.screen.get_windows_stacked() or []):
                try:
                    if w.is_minimized() or w.get_window_type() not in (Wnck.WindowType.NORMAL,
                                                                        Wnck.WindowType.DIALOG):
                        continue
                    if ws is not None and not w.is_on_workspace(ws):
                        continue
                    x, y, ww, hh = w.get_geometry()
                    cx, cy = x + ww / 2, y + hh / 2
                    hidden = any(gx <= cx < gx + gw and gy <= cy < gy + gh and gw * gh >= ww * hh * 0.5
                                 for gx, gy, gw, gh in covered)
                    covered.append((x, y, ww, hh))
                    if hidden or not w.is_fullscreen():
                        continue
                    app = w.get_application()
                    name = " ".join(filter(None, [w.get_class_group_name() or "",
                                                  w.get_class_instance_name() or "",
                                                  app.get_name() if app else ""])).lower()
                    rects.append((x, y, ww, hh, name))
                except Exception:
                    continue
        if rects != self.fullscreen_rects:
            self.fullscreen_rects = rects
            self.emit("changed")
        return False

    def fullscreen_on(self, geometry):
        """List of app names that are fullscreen on the monitor `geometry`."""
        gx, gy, gw, gh = geometry.x, geometry.y, geometry.width, geometry.height
        out = []
        for x, y, w, h, name in self.fullscreen_rects:
            cx, cy = x + w / 2, y + h / 2
            if gx <= cx < gx + gw and gy <= cy < gy + gh:
                out.append(name)
        return out
