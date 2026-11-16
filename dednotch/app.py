"""Application entry: owns the services, the notch window(s), tray icon and CLI."""
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import gfx  # noqa: E402
from .config import APP_ID, APP_NAME, settings  # noqa: E402
from .services import Observable  # noqa: E402

AUTOSTART_FILE = os.path.join(GLib.get_user_config_dir(), "autostart", "dednotch.desktop")


class Coordinator(Observable):
    """Sneak peeks, expanding views and current tab."""

    def __init__(self):
        super().__init__()
        self.sneak = {"show": False, "type": "music", "value": 0.0, "icon": ""}
        self.expanding = {"show": False, "type": "battery"}
        self.current_view = "home"
        self.hello_running = False
        self._sneak_timer = None
        self._exp_timer = None
        self._sneak_duration = 1.5

    def toggle_sneak(self, show, kind, value=0.0, icon="", duration=1.5):
        if kind != "music" and not settings["hud_replacement"]:
            return
        self.sneak.update(show=show, type=kind, value=float(value), icon=icon)
        self._sneak_duration = duration
        if show:
            self.bump_sneak()
        elif self._sneak_timer:
            GLib.source_remove(self._sneak_timer)
            self._sneak_timer = None
        self.emit("changed")

    def bump_sneak(self):
        if self._sneak_timer:
            GLib.source_remove(self._sneak_timer)
        self._sneak_timer = GLib.timeout_add(int(self._sneak_duration * 1000), self._hide_sneak)

    def _hide_sneak(self):
        self._sneak_timer = None
        self.sneak["show"] = False
        self.emit("changed")
        return False

    def toggle_expanding(self, show, kind):
        self.expanding.update(show=show, type=kind)
        if self._exp_timer:
            GLib.source_remove(self._exp_timer)
            self._exp_timer = None
        if show:
            self._exp_timer = GLib.timeout_add(2000 if kind == "download" else 3000, self._hide_exp)
        self.emit("changed")

    def _hide_exp(self):
        self._exp_timer = None
        self.expanding["show"] = False
        self.emit("changed")
        return False

    def finish_hello(self):
        if self.hello_running:
            self.hello_running = False
            settings.set("first_launch", False)
            self.emit("changed")
        return False


class DedNotchApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.windows = []
        self.settings_window = None
        self.indicator = None
        self._assets = {}
        self._started = False
        self.add_main_option("toggle", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open/close the notch", None)
        self.add_main_option("open", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Open the notch", None)
        self.add_main_option("close", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Close the notch", None)
        self.add_main_option("settings", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Show settings", None)
        self.add_main_option("toggle-mic", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Toggle microphone mute", None)
        self.add_main_option("toggle-sneak-peek", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Show a sneak peek of the current track", None)
        self.add_main_option("quit", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Quit DedNotch", None)

    # -- lifecycle ---------------------------------------------------------
    def do_command_line(self, command_line):
        opts = command_line.get_options_dict().end().unpack()
        if not self._started:
            self._startup_services()
        if opts.get("quit"):
            self.quit()
        elif opts.get("toggle"):
            self._active_window() and self._active_window().toggle()
        elif opts.get("open"):
            self._active_window() and self._active_window().open()
        elif opts.get("close"):
            self._active_window() and self._active_window().close(force=True)
        elif opts.get("settings"):
            self.show_settings()
        elif opts.get("toggle-mic"):
            self.system.toggle_mic()
        elif opts.get("toggle-sneak-peek"):
            if self.media.title:
                if settings["sneak_peek_style"] == "inline":
                    self.coord.toggle_expanding(True, "music")
                else:
                    self.coord.toggle_sneak(True, "music", duration=3.0)
        return 0

    def _startup_services(self):
        self._started = True
        self.hold()
        settings_gtk = Gtk.Settings.get_default()
        if settings_gtk is not None:
            settings_gtk.set_property("gtk-application-prefer-dark-theme", True)
        from .services.battery import BatteryService
        from .services.calendar import CalendarService
        from .services.fullscreen import FullscreenService
        from .services.media import MediaService
        from .services.system import SystemService
        from .services.webcam import WebcamService
        from .shelf import Shelf

        self.coord = Coordinator()
        self.media = MediaService()
        self.battery = BatteryService()
        self.calendar = CalendarService()
        self.webcam = WebcamService()
        self.system = SystemService()
        self.fullscreen = FullscreenService()
        self.shelf = Shelf()
        if not self.shelf.empty and settings["open_shelf_by_default"]:
            self.coord.current_view = "shelf"

        self.coord.connect(lambda *a: self.redraw())
        self.media.connect(self._on_media)
        self.battery.connect(self._on_battery)
        self.calendar.connect(lambda *a: self.redraw())
        self.webcam.connect(lambda *a: self.redraw())
        self.system.connect(self._on_system)
        self.fullscreen.connect(lambda *a: self._update_fullscreen())
        self.shelf.connect(lambda *a: self.redraw())
        settings.connect(self._on_setting)

        screen = Gdk.Screen.get_default()
        screen.connect("monitors-changed", lambda *a: GLib.timeout_add(500, self._rebuild_windows))
        Gdk.Display.get_default().connect("monitor-added", lambda *a: GLib.timeout_add(500, self._rebuild_windows))
        Gdk.Display.get_default().connect("monitor-removed", lambda *a: GLib.timeout_add(500, self._rebuild_windows))
        self._rebuild_windows()
        self._update_tray()

        if settings["first_launch"]:
            self.coord.hello_running = True
            self._play_welcome_sound()
            GLib.timeout_add(1500, self.show_settings)

    def _active_window(self):
        if not self.windows:
            return None
        try:
            seat = Gdk.Display.get_default().get_default_seat()
            _s, px, py = seat.get_pointer().get_position()
            mon = Gdk.Display.get_default().get_monitor_at_point(px, py)
            for w in self.windows:
                if w.monitor == mon:
                    return w
        except Exception:
            pass
        return self.windows[0]

    def _rebuild_windows(self):
        from .notch import NotchWindow
        display = Gdk.Display.get_default()
        monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
        if settings["show_on_all_displays"]:
            wanted = monitors
        else:
            pref = settings["preferred_display"]
            chosen = None
            for m in monitors:
                if pref and pref in (m.get_model() or "", _monitor_name(m)):
                    chosen = m
            if chosen is None:
                chosen = display.get_primary_monitor() or (monitors[0] if monitors else None)
            wanted = [chosen] if chosen else []
        for w in list(self.windows):
            if w.monitor not in wanted:
                self.windows.remove(w)
                w.destroy()
        have = {w.monitor for w in self.windows}
        for m in wanted:
            if m not in have:
                win = NotchWindow(self, m)
                self.add_window(win)
                win.show_all()
                self.windows.append(win)
        for w in self.windows:
            w.place()
        self._update_fullscreen()
        return False

    # -- events --------------------------------------------------------------
    def redraw(self):
        for w in self.windows:
            w.queue()

    def _on_media(self, what):
        if what == "track-changed" and settings["enable_sneak_peek"] and self.media.playing:
            if settings["sneak_peek_style"] == "standard":
                self.coord.toggle_sneak(True, "music")
            else:
                self.coord.toggle_expanding(True, "music")
        if what in ("state", "track-changed"):
            self._update_fullscreen()
        self.redraw()

    def _on_battery(self, what):
        if what == "notify" and settings["show_power_status_notifications"]:
            self.coord.toggle_expanding(True, "battery")
        self.redraw()

    def _on_system(self, kind, value):
        if settings["hud_replacement"]:
            self.coord.toggle_sneak(True, kind, value)

    def _on_setting(self, key, value):
        if key in ("show_on_all_displays", "preferred_display"):
            self._rebuild_windows()
        elif key == "menubar_icon":
            self._update_tray()
        elif key == "launch_at_login":
            set_autostart(value)
        elif key == "hide_notch_option":
            self._update_fullscreen()
        elif key == "show_mirror" and not value:
            for w in self.windows:
                w.camera_expanded = False
            self.webcam.stop()
        self.redraw()

    def _update_fullscreen(self):
        option = settings["hide_notch_option"]
        ident = (self.media.identity or "").lower()
        for w in self.windows:
            hide = False
            if option != "never":
                apps = self.fullscreen.fullscreen_on(w.monitor.get_geometry())
                if option == "always":
                    hide = bool(apps)
                elif apps and ident and self.media.playing:
                    first = ident.split()[0]
                    hide = any(first in a for a in apps)
            if hide != w.hide_on_closed:
                w.hide_on_closed = hide
                w.queue()

    # -- UI helpers ------------------------------------------------------------
    def show_settings(self):
        from .settings_window import SettingsWindow
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
            self.settings_window.connect("destroy", self._settings_closed)
        ts = Gtk.get_current_event_time() or GdkX11_time()
        self.settings_window.present_with_time(ts)
        win = self.settings_window.get_window()
        if win is not None:
            win.focus(ts)
        return False

    def _settings_closed(self, *_):
        self.settings_window = None

    def asset(self, name, height):
        key = (name, height)
        if key not in self._assets:
            pb = gfx.load_asset_pixbuf(name)
            if pb is not None:
                w = max(1, int(round(pb.get_width() * height / pb.get_height())))
                from gi.repository import GdkPixbuf
                pb = pb.scale_simple(w, height, GdkPixbuf.InterpType.HYPER)
            self._assets[key] = pb
        return self._assets[key]

    def _play_welcome_sound(self):
        try:
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            player = Gst.ElementFactory.make("playbin", "welcome")
            if player is None:
                return
            player.set_property("uri", GLib.filename_to_uri(os.path.join(gfx.ASSETS, "ded.m4a"), None))
            player.set_property("volume", 0.6)
            player.set_state(Gst.State.PLAYING)
            self._welcome_player = player

            def stop():
                player.set_state(Gst.State.NULL)
                return False
            GLib.timeout_add(6000, stop)
        except Exception:
            pass

    def _update_tray(self):
        want = settings["menubar_icon"]
        if not want:
            if self.indicator is not None:
                try:
                    from gi.repository import AyatanaAppIndicator3 as AI
                    self.indicator.set_status(AI.IndicatorStatus.PASSIVE)
                except Exception:
                    pass
            return
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AI
        except (ValueError, ImportError):
            return
        if self.indicator is None:
            icon = os.path.join(gfx.ASSETS, "tray.png")
            self.indicator = AI.Indicator.new("dednotch", icon, AI.IndicatorCategory.APPLICATION_STATUS)
            self.indicator.set_title(APP_NAME)
            menu = Gtk.Menu()
            for label, cb in (("Toggle Notch", lambda *_: self._active_window() and self._active_window().toggle()),
                              ("Settings", lambda *_: self.show_settings()),
                              (None, None),
                              ("Restart %s" % APP_NAME, lambda *_: self.restart()),
                              ("Quit", lambda *_: self.quit())):
                if label is None:
                    menu.append(Gtk.SeparatorMenuItem())
                    continue
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", cb)
                menu.append(mi)
            menu.show_all()
            self.indicator.set_menu(menu)
        self.indicator.set_status(AI.IndicatorStatus.ACTIVE)

    def restart(self):
        settings.flush()
        os.execv(sys.executable, [sys.executable, "-m", "dednotch"])

    def do_shutdown(self):
        settings.flush()
        try:
            self.webcam.stop()
        except Exception:
            pass
        Gtk.Application.do_shutdown(self)


def GdkX11_time():
    try:
        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11
        return GdkX11.x11_get_server_time(Gdk.get_default_root_window())
    except Exception:
        return 0


def launcher_command():
    """Command that starts DedNotch from outside (autostart, keyboard shortcuts)."""
    local = os.path.expanduser("~/.local/bin/dednotch")
    if os.path.exists(local):
        return local
    found = GLib.find_program_in_path("dednotch")
    if found:
        return found
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return "env PYTHONPATH=%s %s -m dednotch" % (pkg_root.replace(" ", "\\ "), sys.executable)


def _monitor_name(monitor):
    return "%s %s" % (monitor.get_manufacturer() or "", monitor.get_model() or "")


def set_autostart(enabled):
    if enabled:
        os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
        exe = launcher_command()
        with open(AUTOSTART_FILE, "w", encoding="utf-8") as fh:
            fh.write("[Desktop Entry]\nType=Application\nName=DedNotch\nComment=A notch for your Linux desktop\n"
                     "Exec=%s\nIcon=dednotch\nX-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=3\n"
                     "NoDisplay=false\n" % exe)
    else:
        try:
            os.remove(AUTOSTART_FILE)
        except OSError:
            pass


def main():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get("GDK_BACKEND"):
        # The notch is an override-redirect overlay; that needs X11/XWayland.
        os.environ["GDK_BACKEND"] = "x11"
    Gdk.set_allowed_backends("x11")
    app = DedNotchApp()
    return app.run(sys.argv)
