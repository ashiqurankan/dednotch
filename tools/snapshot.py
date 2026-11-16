"""Render notch states offscreen to PNG (visual testing without touching the screen).

usage: python3 tools/snapshot.py [--demo] OUTDIR [STATE ...]

--demo replaces live media/battery data with neutral placeholders (used for docs/).
"""
import copy
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dednotch  # noqa: E402,F401  (pins GI versions)
import cairo  # noqa: E402
from gi.repository import Gdk, GLib  # noqa: E402

from dednotch import notch as N  # noqa: E402
from dednotch.app import Coordinator  # noqa: E402
from dednotch.config import DEFAULTS, settings  # noqa: E402
from dednotch.services.battery import BatteryService  # noqa: E402
from dednotch.services.calendar import CalendarService  # noqa: E402
from dednotch.services.media import MediaService  # noqa: E402
from dednotch.services.system import SystemService  # noqa: E402
from dednotch.services.webcam import WebcamService  # noqa: E402
from dednotch.shelf import Shelf  # noqa: E402


class FakeApp:
    def __init__(self):
        self.coord = Coordinator()
        self.media = MediaService()
        self.battery = BatteryService()
        self.calendar = CalendarService()
        self.webcam = WebcamService()
        self.system = SystemService()
        self.shelf = Shelf()
        self._assets = {}

    def asset(self, name, h):
        from dednotch.app import DedNotchApp
        return DedNotchApp.asset(self, name, h)

    def show_settings(self):
        pass


def demo_artwork(size=300):
    """Neutral gradient cover art, so screenshots never show real media."""
    from gi.repository import Gdk as _Gdk
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(s)
    g = cairo.LinearGradient(0, 0, size, size)
    g.add_color_stop_rgb(0, 0.36, 0.42, 0.95)
    g.add_color_stop_rgb(1, 0.85, 0.35, 0.65)
    cr.set_source(g)
    cr.paint()
    cr.set_source_rgba(1, 1, 1, 0.85)
    cr.arc(size * 0.5, size * 0.5, size * 0.22, 0, 6.2832)
    cr.set_line_width(size * 0.06)
    cr.stroke()
    cr.arc(size * 0.5, size * 0.5, size * 0.05, 0, 6.2832)
    cr.fill()
    return _Gdk.pixbuf_get_from_surface(s, 0, 0, size, size)


def use_demo_media(media):
    """Freeze the media service on placeholder data instead of live MPRIS players."""
    from dednotch import gfx
    media._sync = lambda *a, **k: None
    media._player_changed = lambda *a, **k: None
    media.title, media.artist, media.album = "Demo Track", "Demo Artist", "Demo Album"
    media.art = demo_artwork()
    media.art_is_app_icon = False
    media.app_icon = None
    media.identity = "Player"
    media.avg_color = gfx.average_color(media.art)
    media.duration = 214.0
    media.playing = True
    media.idle = False
    media.can_seek = True
    media.lyrics, media.synced_lyrics = "", []
    media.position = lambda: 131.0
    media.emit("art")
    media.emit("state")


def pump(sec):
    end = time.monotonic() + sec
    ctx = GLib.MainContext.default()
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.005)


def render(win, path, frames=90, bg=(0.35, 0.45, 0.6)):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, N.WIN_W, N.WIN_H)
    for _ in range(frames):
        win._last_frame = time.monotonic() - 1 / 60.0
        cr = cairo.Context(surf)
        win._on_draw(None, cr)
        win.now = time.monotonic()
    out = cairo.ImageSurface(cairo.FORMAT_RGB24, N.WIN_W, N.WIN_H)
    cr = cairo.Context(out)
    g = cairo.LinearGradient(0, 0, N.WIN_W, N.WIN_H)
    g.add_color_stop_rgb(0, *bg)
    g.add_color_stop_rgb(1, bg[2], bg[0], bg[1])
    cr.set_source(g)
    cr.paint()
    cr.rectangle(0, 0, N.WIN_W, 32)
    cr.set_source_rgba(0.1, 0.1, 0.1, 0.85)
    cr.fill()
    cr.set_source_surface(surf, 0, 0)
    cr.paint()
    out.write_to_png(path)


def main():
    args = sys.argv[1:]
    demo = "--demo" in args
    args = [a for a in args if a != "--demo"]
    outdir = args[0]
    os.makedirs(outdir, exist_ok=True)
    app = FakeApp()
    pump(1.5)
    if demo:
        settings._values.update(copy.deepcopy(DEFAULTS))  # ignore the local settings.json
        use_demo_media(app.media)
        app.battery.available, app.battery.level = True, 80.0
        app.battery.charging = app.battery.plugged_in = False
        app.calendar.reload = lambda *a, **k: None
        app.calendar.calendars, app.calendar.components, app.calendar._day_cache = [], [], {}
    mon = Gdk.Display.get_default().get_primary_monitor()
    win = N.NotchWindow(app, mon)
    win.closed_height = lambda: 32.0
    print("media:", app.media.title, "|", app.media.artist, "| playing", app.media.playing,
          "| art", app.media.art is not None, "| idle", app.media.idle)
    states = args[1:] or ["closed", "open", "shelf", "calendar", "hud", "inline", "battery", "face", "hover"]
    for st in states:
        settings._values.update(show_calendar=False, show_not_human_face=False, hud_replacement=True,
                                inline_hud=False, show_mirror=False)
        app.coord.sneak["show"] = False
        app.coord.expanding["show"] = False
        win.hovering = False
        win.close(force=True)
        app.coord.current_view = "home"
        if st == "open":
            win.open()
        elif st == "shelf":
            win.open()
            app.coord.current_view = "shelf"
        elif st == "calendar":
            settings._values["show_calendar"] = True
            settings._values["show_mirror"] = True
            win.open()
        elif st == "hud":
            app.coord.sneak.update(show=True, type="volume", value=0.62)
        elif st == "inline":
            settings._values["inline_hud"] = True
            app.coord.sneak.update(show=True, type="brightness", value=0.4)
        elif st == "battery":
            app.battery.status_text = "Charging battery"
            app.coord.expanding.update(show=True, type="battery")
        elif st == "face":
            settings._values["show_not_human_face"] = True
            app.media.playing = False
            app.media.idle = True
        elif st == "hover":
            win.hovering = True
        render(win, os.path.join(outdir, "snap_%s.png" % st))
        print("wrote", st)


main()
