
import copy
import json
import os

from gi.repository import GLib

APP_ID = "io.github.dednotch"
APP_NAME = "DedNotch"
VERSION = "1.0.0"
RELEASE_NAME = "Ded Star"

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "dednotch")
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "dednotch")
CACHE_DIR = os.path.join(GLib.get_user_cache_dir(), "dednotch")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

DEFAULT_SLOTS = ["none", "previous", "playPause", "next", "none"]

DEFAULTS = {
    # General
    "menubar_icon": True,
    "launch_at_login": False,
    "show_on_all_displays": False,
    "preferred_display": "",          # monitor connector / model, "" = primary
    "automatically_switch_display": True,
    # Notch sizing (Linux displays have no physical notch -> "non notch" modes)
    "notch_height_mode": "menubar",   # "menubar" | "custom"
    "notch_height": 32,
    "closed_notch_width": 185,
    # Behaviour
    "minimum_hover_duration": 0.3,
    "open_notch_on_hover": True,
    "extend_hover_area": False,
    "enable_gestures": True,
    "close_gesture_enabled": True,
    "gesture_sensitivity": 200.0,
    "open_last_tab_by_default": False,
    "always_show_tabs": True,
    # Appearance
    "show_mirror": False,
    "mirror_shape": "rectangle",      # "rectangle" | "circle"
    "settings_icon_in_notch": True,
    "lighting_effect": True,
    "enable_shadow": True,
    "corner_radius_scaling": True,
    "show_not_human_face": False,
    "show_calendar": False,
    "slider_color": "white",          # "white" | "albumArt" | "accent"
    "player_color_tinting": True,
    "use_music_visualizer": True,
    "use_custom_accent_color": False,
    "custom_accent_color": "#0A84FF",
    # Media
    "media_player": "auto",           # "auto" or an MPRIS bus-name fragment
    "music_live_activity_enabled": True,
    "colored_spectrogram": True,
    "enable_sneak_peek": False,
    "sneak_peek_style": "standard",   # "standard" | "inline"
    "wait_interval": 3.0,
    "enable_lyrics": False,
    "music_control_slots": list(DEFAULT_SLOTS),
    "hide_notch_option": "nowPlayingOnly",  # "always" | "nowPlayingOnly" | "never"
    # Calendar
    "hide_completed_reminders": True,
    "hide_all_day_events": False,
    "show_full_event_titles": False,
    "auto_scroll_to_next_event": True,
    "disabled_calendars": [],
    "extra_ics_files": [],
    # Battery
    "show_power_status_notifications": True,
    "show_battery_indicator": True,
    "show_battery_percentage": True,
    "show_power_status_icons": True,
    # HUD
    "hud_replacement": False,
    "inline_hud": False,
    "enable_gradient": False,
    "system_event_indicator_shadow": False,
    "system_event_indicator_use_accent": False,
    "show_open_notch_hud": True,
    "show_open_notch_hud_percentage": True,
    "show_closed_notch_hud_percentage": False,
    # Shelf
    "ded_shelf": True,
    "open_shelf_by_default": True,
    "copy_on_drag": False,
    "auto_remove_shelf_items": False,
    "quick_share_provider": "Copy to Clipboard",
    # Internal
    "first_launch": True,
}

YARU_ACCENTS = {
    "bark": "#787859", "sage": "#657b69", "olive": "#4B8501", "viridian": "#03875B",
    "prussiangreen": "#308280", "blue": "#0073E5", "purple": "#7764D8",
    "magenta": "#B34CB3", "red": "#DA3450",
}


class Settings:
    """A tiny observable JSON-backed key/value store."""

    def __init__(self):
        self._values = copy.deepcopy(DEFAULTS)
        self._listeners = []
        self._save_source = None
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            for key, value in stored.items():
                if key in DEFAULTS:
                    self._values[key] = value
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self._values[key]

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set(self, key, value):
        if self._values.get(key) == value:
            return
        self._values[key] = value
        self._schedule_save()
        for cb in list(self._listeners):
            try:
                cb(key, value)
            except Exception as exc:  # never let a listener break settings
                print("dednotch: settings listener failed:", exc)

    def reset(self):
        for key, value in DEFAULTS.items():
            if key != "first_launch":
                self.set(key, copy.deepcopy(value))

    def connect(self, callback):
        self._listeners.append(callback)

    def _schedule_save(self):
        if self._save_source is None:
            self._save_source = GLib.timeout_add(300, self._save)

    def _save(self):
        self._save_source = None
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._values, fh, indent=2)
            os.replace(tmp, SETTINGS_FILE)
        except OSError as exc:
            print("dednotch: could not save settings:", exc)
        return False

    def flush(self):
        if self._save_source is not None:
            GLib.source_remove(self._save_source)
            self._save()

    # -- derived values ---------------------------------------------------
    def accent(self):
        """Effective accent colour as an (r, g, b) tuple in 0..1."""
        if self["use_custom_accent_color"]:
            return hex_to_rgb(self["custom_accent_color"])
        try:
            from gi.repository import Gio
            theme = Gio.Settings.new("org.gnome.desktop.interface").get_string("gtk-theme").lower()
            if theme.startswith("yaru"):
                parts = theme.replace("-dark", "").split("-")
                if len(parts) > 1 and parts[1] in YARU_ACCENTS:
                    return hex_to_rgb(YARU_ACCENTS[parts[1]])
                return hex_to_rgb("#E95420")  # default Yaru orange
        except Exception:
            pass
        return hex_to_rgb("#0A84FF")


def hex_to_rgb(value):
    value = value.lstrip("#")
    if len(value) != 6:
        return (0.04, 0.52, 1.0)
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c * 255)))) for c in rgb[:3])


settings = Settings()
