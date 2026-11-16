"""Volume, microphone and brightness sources for the HUD replacement.

Volume/mic use WirePlumber's `wpctl` (PipeWire), brightness uses GNOME
Settings Daemon over D-Bus (falls back to /sys/class/backlight for reading).
"""
import glob
import os
import re
import subprocess

from gi.repository import Gio, GLib

from ..config import settings
from . import Observable

GSD = "org.gnome.SettingsDaemon.Power"
GSD_PATH = "/org/gnome/SettingsDaemon/Power"


def _wpctl(*args):
    try:
        return subprocess.run(["wpctl", *args], capture_output=True, text=True, timeout=1).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _parse_volume(text):
    m = re.search(r"Volume:\s*([0-9.]+)", text or "")
    vol = float(m.group(1)) if m else 0.0
    return vol, "[MUTED]" in (text or "")


class SystemService(Observable):
    """Emits ("volume"|"brightness"|"backlight"|"mic", value) on changes."""

    def __init__(self):
        super().__init__()
        self.volume, self.muted = 0.0, False
        self.mic_muted = False
        self.brightness = 0.0
        self.backlight = 0.0
        self._poll_source = None
        self._has_wpctl = GLib.find_program_in_path("wpctl") is not None
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.screen = self._proxy(GSD + ".Screen")
        self.keyboard = self._proxy(GSD + ".Keyboard")
        if self.screen is not None:
            self.screen.connect("g-properties-changed", self._on_screen_changed)
            self.brightness = self._read_brightness()
        if self.keyboard is not None:
            self.keyboard.connect("g-properties-changed", self._on_kbd_changed)
            v = self.keyboard.get_cached_property("Brightness")
            self.backlight = (v.unpack() / 100.0) if v is not None else 0.0
        self.volume, self.muted = _parse_volume(_wpctl("get-volume", "@DEFAULT_AUDIO_SINK@"))
        _vol, self.mic_muted = _parse_volume(_wpctl("get-volume", "@DEFAULT_AUDIO_SOURCE@"))
        settings.connect(lambda k, v: self._update_polling() if k == "hud_replacement" else None)
        self._update_polling()

    def _proxy(self, iface):
        try:
            p = Gio.DBusProxy.new_sync(self.bus, Gio.DBusProxyFlags.NONE, None, GSD, GSD_PATH,
                                       iface, None)
            if p.get_name_owner() and p.get_cached_property("Brightness") is not None:
                return p
        except GLib.Error:
            pass
        return None

    # -- brightness ---------------------------------------------------------
    def _read_brightness(self):
        if self.screen is not None:
            v = self.screen.get_cached_property("Brightness")
            if v is not None and v.unpack() >= 0:
                return v.unpack() / 100.0
        for dev in glob.glob("/sys/class/backlight/*"):
            try:
                with open(os.path.join(dev, "actual_brightness")) as fh:
                    cur = int(fh.read())
                with open(os.path.join(dev, "max_brightness")) as fh:
                    mx = int(fh.read())
                return cur / mx if mx else 0.0
            except (OSError, ValueError):
                continue
        return 0.0

    def _on_screen_changed(self, _p, changed, _inv):
        changed = changed.unpack()
        if "Brightness" in changed and changed["Brightness"] >= 0:
            value = changed["Brightness"] / 100.0
            if abs(value - self.brightness) > 1e-4:
                self.brightness = value
                self.emit("brightness", value)

    def _on_kbd_changed(self, _p, changed, _inv):
        changed = changed.unpack()
        if "Brightness" in changed:
            self.backlight = changed["Brightness"] / 100.0
            self.emit("backlight", self.backlight)

    def set_brightness(self, value):
        value = max(0.0, min(1.0, value))
        self.brightness = value
        if self.screen is not None:
            self.bus.call(GSD, GSD_PATH, "org.freedesktop.DBus.Properties", "Set",
                          GLib.Variant("(ssv)", (GSD + ".Screen", "Brightness",
                                                 GLib.Variant("i", int(round(value * 100))))),
                          None, Gio.DBusCallFlags.NONE, 1000, None, None)

    # -- volume -------------------------------------------------------------
    def set_volume(self, value):
        value = max(0.0, min(1.0, value))
        self.volume = value
        if self._has_wpctl:
            subprocess.Popen(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "%.3f" % value],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if value > 0 and self.muted:
                subprocess.Popen(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])

    def toggle_mic(self):
        if not self._has_wpctl:
            return
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"], timeout=1)
        _vol, self.mic_muted = _parse_volume(_wpctl("get-volume", "@DEFAULT_AUDIO_SOURCE@"))
        self.emit("mic", 0.0 if self.mic_muted else 1.0)

    def _update_polling(self):
        want = settings["hud_replacement"] and self._has_wpctl
        if want and self._poll_source is None:
            self._poll_source = GLib.timeout_add(250, self._poll)
        elif not want and self._poll_source is not None:
            GLib.source_remove(self._poll_source)
            self._poll_source = None

    def _poll(self):
        proc = Gio.Subprocess.new(["sh", "-c", "wpctl get-volume @DEFAULT_AUDIO_SINK@; "
                                   "wpctl get-volume @DEFAULT_AUDIO_SOURCE@"],
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        proc.communicate_utf8_async(None, None, self._on_poll)
        return True

    def _on_poll(self, proc, res):
        try:
            _ok, out, _err = proc.communicate_utf8_finish(res)
        except GLib.Error:
            return
        lines = (out or "").strip().splitlines()
        if len(lines) < 1:
            return
        vol, muted = _parse_volume(lines[0])
        if abs(vol - self.volume) > 1e-3 or muted != self.muted:
            self.volume, self.muted = vol, muted
            self.emit("volume", 0.0 if muted else min(1.0, vol))
        if len(lines) > 1:
            _v, mic_muted = _parse_volume(lines[1])
            if mic_muted != self.mic_muted:
                self.mic_muted = mic_muted
                self.emit("mic", 0.0 if mic_muted else 1.0)
