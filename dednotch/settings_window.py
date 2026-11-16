"""Settings window built with native GTK widgets."""
import os

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from . import gfx
from .config import APP_NAME, DEFAULT_SLOTS, RELEASE_NAME, VERSION, hex_to_rgb, rgb_to_hex, settings

CSS = b"""
.dn-sidebar { background-color: alpha(@theme_fg_color, 0.04); }
.dn-sidebar row { padding: 7px 10px; border-radius: 8px; margin: 1px 8px; }
.dn-page { padding: 18px 26px 26px 26px; }
.dn-group-title { font-weight: 600; opacity: 0.7; margin-top: 14px; margin-bottom: 6px; }
.dn-group { border-radius: 10px; background-color: alpha(@theme_fg_color, 0.05);
            border: 1px solid alpha(@theme_fg_color, 0.07); }
.dn-group row { padding: 9px 14px; }
.dn-group row:not(:last-child) { border-bottom: 1px solid alpha(@theme_fg_color, 0.06); }
.dn-sub { opacity: 0.6; font-size: 0.9em; }
.dn-title { font-size: 1.5em; font-weight: 700; }
"""

SLOT_CHOICES = [("none", "Empty"), ("previous", "Previous"), ("playPause", "Play/Pause"), ("next", "Next"),
                ("shuffle", "Shuffle"), ("repeatMode", "Repeat"), ("volume", "Volume"),
                ("goBackward", "Back 15s"), ("goForward", "Forward 15s")]


class SettingsWindow(Gtk.Window):
    def __init__(self, app):
        Gtk.Window.__init__(self, title="%s Settings" % APP_NAME)
        self.app = app
        self.set_default_size(760, 560)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_from_file(os.path.join(gfx.ASSETS, "dednotch.png"))
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        header = Gtk.HeaderBar(title="%s Settings" % APP_NAME, show_close_button=True)
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.add(root)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        side = Gtk.ListBox()
        side.get_style_context().add_class("dn-sidebar")
        side.set_size_request(190, -1)
        side.set_selection_mode(Gtk.SelectionMode.SINGLE)
        side_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        side_scroll.add(side)
        side_scroll.get_style_context().add_class("dn-sidebar")
        root.pack_start(side_scroll, False, False, 0)
        root.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0)
        root.pack_start(self.stack, True, True, 0)

        pages = [("General", "gearshape", self.page_general), ("Appearance", "eye", self.page_appearance),
                 ("Media", "music.note", self.page_media), ("Calendar", "calendar", self.page_calendar),
                 ("HUDs", "speaker.wave.2.fill", self.page_hud), ("Battery", "bolt.fill", self.page_battery),
                 ("Shelf", "tray.fill", self.page_shelf), ("Shortcuts", "keyboard", self.page_shortcuts),
                 ("Advanced", "gearshape.fill", self.page_advanced), ("About", "info.circle", self.page_about)]
        for name, icon, builder in pages:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10)
            box.pack_start(_icon_image(icon), False, False, 0)
            box.pack_start(Gtk.Label(label=name, xalign=0), True, True, 0)
            row.add(box)
            row.page = name
            side.add(row)
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            page.get_style_context().add_class("dn-page")
            builder(page)
            scroller.add(page)
            self.stack.add_named(scroller, name)
        side.connect("row-selected", lambda _l, r: r and self.stack.set_visible_child_name(r.page))
        side.select_row(side.get_row_at_index(0))
        self.show_all()

    # ---------------------------------------------------------------- helpers
    def group(self, page, title=None, footer=None):
        if title:
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.get_style_context().add_class("dn-group-title")
            page.pack_start(lbl, False, False, 0)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.get_style_context().add_class("dn-group")
        page.pack_start(lb, False, False, 0)
        if footer:
            f = Gtk.Label(label=footer, xalign=0, wrap=True)
            f.get_style_context().add_class("dn-sub")
            f.set_margin_top(6)
            page.pack_start(f, False, False, 0)
        return lb

    def row(self, group, label, widget, subtitle=None):
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(spacing=12)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        l1 = Gtk.Label(label=label, xalign=0, wrap=True)
        labels.pack_start(l1, False, False, 0)
        if subtitle:
            l2 = Gtk.Label(label=subtitle, xalign=0, wrap=True)
            l2.get_style_context().add_class("dn-sub")
            labels.pack_start(l2, False, False, 0)
        box.pack_start(labels, True, True, 0)
        if widget is not None:
            widget.set_valign(Gtk.Align.CENTER)
            box.pack_end(widget, False, False, 0)
        row.add(box)
        group.add(row)
        return row

    def toggle(self, group, key, label, subtitle=None, on_change=None):
        sw = Gtk.Switch(active=bool(settings[key]))

        def changed(s, _p):
            settings.set(key, s.get_active())
            if on_change:
                on_change(s.get_active())
        sw.connect("notify::active", changed)
        return self.row(group, label, sw, subtitle)

    def combo(self, group, key, label, options, subtitle=None):
        cb = Gtk.ComboBoxText()
        for value, text in options:
            cb.append(str(value), text)
        cb.set_active_id(str(settings[key]))
        cb.connect("changed", lambda c: settings.set(key, _coerce(c.get_active_id(), settings[key])))
        return self.row(group, label, cb, subtitle)

    def slider(self, group, key, label, lo, hi, step, fmt="%.0f", subtitle=None):
        box = Gtk.Box(spacing=8)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_draw_value(False)
        scale.set_size_request(200, -1)
        scale.set_value(float(settings[key]))
        value = Gtk.Label(label=fmt % settings[key])
        value.set_width_chars(5)

        def changed(s):
            v = round(s.get_value() / step) * step
            settings.set(key, int(v) if isinstance(settings[key], int) else float(v))
            value.set_text(fmt % v)
        scale.connect("value-changed", changed)
        box.pack_start(scale, False, False, 0)
        box.pack_start(value, False, False, 0)
        return self.row(group, label, box, subtitle)

    # ---------------------------------------------------------------- pages
    def page_general(self, page):
        g = self.group(page, "System features")
        self.toggle(g, "menubar_icon", "Show menu bar icon", "Adds a DedNotch icon to the top bar (needs the AppIndicator extension).")
        self.toggle(g, "launch_at_login", "Launch at login")
        self.toggle(g, "show_on_all_displays", "Show on all displays")
        display = Gdk.Display.get_default()
        opts = [("", "Primary display")]
        for i in range(display.get_n_monitors()):
            m = display.get_monitor(i)
            name = "%s %s" % (m.get_manufacturer() or "", m.get_model() or "")
            opts.append((name, name.strip() or "Display %d" % (i + 1)))
        self.combo(g, "preferred_display", "Preferred display", opts)

        g = self.group(page, "Notch sizing")
        self.combo(g, "notch_height_mode", "Notch height", [("menubar", "Match top bar height"),
                                                            ("custom", "Custom height")])
        self.slider(g, "notch_height", "Custom notch height", 15, 45, 1)
        self.slider(g, "closed_notch_width", "Closed notch width", 120, 320, 1,
                    subtitle="Linux displays have no camera notch, so pick how wide the closed notch is.")

        g = self.group(page, "Gesture control")
        self.toggle(g, "enable_gestures", "Enable gestures", "Scroll down on the notch to open it.")
        self.toggle(g, "close_gesture_enabled", "Close gesture", "Scroll up on the open notch to close it.")
        self.slider(g, "gesture_sensitivity", "Gesture sensitivity", 100, 300, 100,
                    subtitle="Lower values react to shorter swipes.")

        g = self.group(page, "Notch behavior")
        self.toggle(g, "open_notch_on_hover", "Open notch on hover")
        self.toggle(g, "extend_hover_area", "Extend hover area")
        self.toggle(g, "open_last_tab_by_default", "Remember last tab")
        self.slider(g, "minimum_hover_duration", "Hover delay", 0, 1, 0.1, "%.1fs")

    def page_appearance(self, page):
        g = self.group(page, "General")
        self.toggle(g, "always_show_tabs", "Always show tabs")
        self.toggle(g, "settings_icon_in_notch", "Show settings icon in notch")
        self.toggle(g, "enable_shadow", "Enable window shadow")
        self.toggle(g, "corner_radius_scaling", "Corner radius scaling")
        g = self.group(page, "Media")
        self.toggle(g, "colored_spectrogram", "Colored spectrogram")
        self.toggle(g, "player_color_tinting", "Player tinting")
        self.toggle(g, "lighting_effect", "Enable blur effect behind album art")
        self.combo(g, "slider_color", "Slider color", [("white", "White"), ("albumArt", "Match album art"),
                                                       ("accent", "Accent color")])
        g = self.group(page, "Idle")
        self.toggle(g, "show_not_human_face", "Show cool face animation while inactive")
        g = self.group(page, "Additional features")
        self.toggle(g, "show_mirror", "Enable ded mirror", "Adds a camera button to the notch for a quick webcam mirror.")
        self.combo(g, "mirror_shape", "Mirror shape", [("circle", "Circle"), ("rectangle", "Square")])

    def page_media(self, page):
        g = self.group(page, "Media source",
                       footer="DedNotch follows any MPRIS player: Spotify, browsers, Rhythmbox, VLC, mpv, …")
        opts = [("auto", "Automatic (most recent player)")]
        for bus, ident in self.app.media.available_players():
            opts.append((bus, ident))
        if settings["media_player"] not in [o[0] for o in opts]:
            opts.append((settings["media_player"], settings["media_player"]))
        self.combo(g, "media_player", "Music source", opts)

        g = self.group(page, "Media playback live activity")
        self.toggle(g, "music_live_activity_enabled", "Show music live activity")
        self.toggle(g, "enable_sneak_peek", "Show sneak peek on playback changes")
        self.combo(g, "sneak_peek_style", "Sneak peek style", [("standard", "Default"), ("inline", "Inline")])
        self.slider(g, "wait_interval", "Media inactivity timeout", 0, 10, 1, "%.0fs")
        self.combo(g, "hide_notch_option", "Full screen behavior",
                   [("always", "Hide for all apps"), ("nowPlayingOnly", "Hide for media app only"),
                    ("never", "Never hide")])

        g = self.group(page, "Media controls",
                       footer="Customize which controls appear in the music player.")
        self.toggle(g, "enable_lyrics", "Show lyrics below artist name", "Lyrics are fetched from lrclib.net.")
        slots = list(settings["music_control_slots"]) + ["none"] * 5
        for i in range(5):
            cb = Gtk.ComboBoxText()
            for value, text in SLOT_CHOICES:
                cb.append(value, text)
            cb.set_active_id(slots[i])

            def changed(c, idx=i):
                cur = (list(settings["music_control_slots"]) + ["none"] * 5)[:5]
                cur[idx] = c.get_active_id()
                settings.set("music_control_slots", cur)
            cb.connect("changed", changed)
            self.row(g, "Slot %d" % (i + 1), cb)
        reset = Gtk.Button(label="Reset to default")
        reset.connect("clicked", lambda *_: (settings.set("music_control_slots", list(DEFAULT_SLOTS)),
                                             self._rebuild_page("Media")))
        self.row(g, "", reset)

    def page_calendar(self, page):
        g = self.group(page, "General")
        self.toggle(g, "show_calendar", "Show calendar")
        self.toggle(g, "hide_completed_reminders", "Hide completed reminders")
        self.toggle(g, "hide_all_day_events", "Hide all-day events")
        self.toggle(g, "auto_scroll_to_next_event", "Auto-scroll to next event")
        self.toggle(g, "show_full_event_titles", "Always show full event titles")
        g = self.group(page, "Calendars",
                       footer="Calendars come from GNOME Calendar / Online Accounts. You can also add .ics files.")
        cals = self.app.calendar.calendars
        if not cals:
            self.row(g, "No calendars found", None, "Add an account in Settings → Online Accounts.")
        for cal in cals:
            sw = Gtk.Switch(active=cal.id not in settings["disabled_calendars"])

            def changed(s, _p, cid=cal.id):
                dis = [c for c in settings["disabled_calendars"] if c != cid]
                if not s.get_active():
                    dis.append(cid)
                settings.set("disabled_calendars", dis)
            sw.connect("notify::active", changed)
            dot = Gtk.Label()
            dot.set_markup('<span foreground="%s">●</span>' % rgb_to_hex(cal.color))
            box = Gtk.Box(spacing=8)
            box.pack_start(dot, False, False, 0)
            box.pack_start(sw, False, False, 0)
            self.row(g, cal.title, box)
        add = Gtk.Button(label="Add .ics file…")
        add.connect("clicked", self._add_ics)
        self.row(g, "", add)

    def _add_ics(self, *_):
        dlg = Gtk.FileChooserNative.new("Add calendar file", self, Gtk.FileChooserAction.OPEN, "_Add", "_Cancel")
        flt = Gtk.FileFilter()
        flt.set_name("iCalendar")
        flt.add_pattern("*.ics")
        dlg.add_filter(flt)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            path = dlg.get_filename()
            files = list(settings["extra_ics_files"])
            if path and path not in files:
                files.append(path)
                settings.set("extra_ics_files", files)
            self._rebuild_page("Calendar")
        dlg.destroy()

    def page_hud(self, page):
        g = self.group(page, None)
        self.toggle(g, "hud_replacement", "Replace system HUD",
                    "Shows volume, brightness and microphone changes in the notch. "
                    "(GNOME's own on-screen display is still shown as well.)")
        g = self.group(page, "General")
        self.combo(g, "enable_gradient", "Progress bar style", [("False", "Hierarchical"), ("True", "Gradient")])
        self.toggle(g, "system_event_indicator_shadow", "Enable glowing effect")
        self.toggle(g, "system_event_indicator_use_accent", "Tint progress bar with accent color")
        g = self.group(page, "Open Notch")
        self.toggle(g, "show_open_notch_hud", "Show HUD in open notch")
        self.toggle(g, "show_open_notch_hud_percentage", "Show percentage")
        g = self.group(page, "Closed Notch")
        self.combo(g, "inline_hud", "HUD style", [("False", "Default"), ("True", "Inline")])
        self.toggle(g, "show_closed_notch_hud_percentage", "Show percentage")

    def page_battery(self, page):
        g = self.group(page, "General")
        self.toggle(g, "show_battery_indicator", "Show battery indicator")
        self.toggle(g, "show_power_status_notifications", "Show power status notifications")
        g = self.group(page, "Battery Information")
        self.toggle(g, "show_battery_percentage", "Show battery percentage")
        self.toggle(g, "show_power_status_icons", "Show power status icons")

    def page_shelf(self, page):
        g = self.group(page, "General")
        self.toggle(g, "ded_shelf", "Enable shelf")
        self.toggle(g, "open_shelf_by_default", "Open shelf by default if items are present")
        self.toggle(g, "auto_remove_shelf_items", "Remove from shelf after dragging")
        g = self.group(page, "Quick Share",
                       footer="Choose what happens with files dropped on the share box (left of the shelf) "
                              "or picked by clicking it.")
        from .notch import SHARE_PROVIDERS
        self.combo(g, "quick_share_provider", "Quick Share service", [(p, p) for p in SHARE_PROVIDERS])
        clear = Gtk.Button(label="Clear shelf")
        clear.connect("clicked", lambda *_: self.app.shelf.clear())
        self.row(g, "Shelf items: %d" % len(self.app.shelf.items), clear)

    def page_shortcuts(self, page):
        g = self.group(page, "Keyboard shortcuts",
                       footer="Shortcuts are registered as GNOME custom keyboard shortcuts, so they work "
                              "everywhere. You can change them later in Settings → Keyboard.")
        self.shortcut_rows = []
        for name, cmd, binding in SHORTCUTS:
            lbl = Gtk.Label(label=_pretty_binding(binding))
            lbl.get_style_context().add_class("dn-sub")
            self.row(g, name, lbl, "dednotch %s" % cmd)
        btn = Gtk.Button(label="Install shortcuts in GNOME")
        btn.connect("clicked", self._install_shortcuts)
        self.row(g, "", btn)

    def _install_shortcuts(self, button):
        ok = install_gnome_shortcuts()
        button.set_label("Shortcuts installed ✓" if ok else "Could not install shortcuts")

    def page_advanced(self, page):
        g = self.group(page, "Accent color")
        self.toggle(g, "use_custom_accent_color", "Use custom accent color",
                    "Otherwise the Yaru/GNOME accent color is used.")
        btn = Gtk.ColorButton()
        r, gg, b = hex_to_rgb(settings["custom_accent_color"])
        btn.set_rgba(Gdk.RGBA(r, gg, b, 1))
        btn.connect("color-set", lambda c: settings.set("custom_accent_color", rgb_to_hex(
            (c.get_rgba().red, c.get_rgba().green, c.get_rgba().blue))))
        self.row(g, "Custom accent color", btn)
        g = self.group(page, "Maintenance")
        hello = Gtk.Button(label="Replay")
        hello.connect("clicked", lambda *_: self._replay_hello())
        self.row(g, "Replay welcome animation", hello)
        reset = Gtk.Button(label="Reset")
        reset.get_style_context().add_class("destructive-action")
        reset.connect("clicked", self._reset)
        self.row(g, "Reset all settings", reset)

    def _replay_hello(self):
        for w in self.app.windows:
            w.hello_start = None
            w.close(force=True)
        self.app.coord.hello_running = True
        self.app._play_welcome_sound()
        self.app.redraw()

    def _reset(self, *_):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.OK_CANCEL, text="Reset all DedNotch settings?")
        if dlg.run() == Gtk.ResponseType.OK:
            settings.reset()
        dlg.destroy()

    def page_about(self, page):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(20)
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(os.path.join(gfx.ASSETS, "dednotch.png"), 112, 112, True)
        box.pack_start(Gtk.Image.new_from_pixbuf(pb), False, False, 0)
        title = Gtk.Label(label=APP_NAME)
        title.get_style_context().add_class("dn-title")
        box.pack_start(title, False, False, 0)
        page.pack_start(box, False, False, 0)
        g = self.group(page, "Version info")
        self.row(g, "Release name", Gtk.Label(label=RELEASE_NAME))
        self.row(g, "Version", Gtk.Label(label=VERSION))
        g = self.group(page, "Credits")
        self.row(g, "A macOS like notch for Linux", _link_button("GitHub", "https://github.com/ashiqurankan/dednotch"),
                 "DedNotch © 2026 Ded Studio. ")
        foot = Gtk.Label(label="Made with love by Ded Studio")
        foot.get_style_context().add_class("dn-sub")
        foot.set_margin_top(18)
        page.pack_start(foot, False, False, 0)

    def _rebuild_page(self, name):
        GLib.idle_add(self._do_rebuild, name)

    def _do_rebuild(self, name):
        builder = {"Media": self.page_media, "Calendar": self.page_calendar}[name]
        old = self.stack.get_child_by_name(name)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.get_style_context().add_class("dn-page")
        builder(page)
        scroller.add(page)
        visible = self.stack.get_visible_child_name()
        self.stack.remove(old)
        self.stack.add_named(scroller, name)
        scroller.show_all()
        self.stack.set_visible_child_name(visible)
        return False


# ---------------------------------------------------------------------------
SHORTCUTS = [
    ("Toggle notch open", "--toggle", "<Primary><Shift>i"),
    ("Toggle sneak peek", "--toggle-sneak-peek", "<Primary><Shift>h"),
    ("Toggle microphone", "--toggle-mic", "<Primary><Shift>m"),
]
KB_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KB_CUSTOM = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
KB_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"


def install_gnome_shortcuts():
    try:
        media = Gio.Settings.new(KB_SCHEMA)
        paths = list(media.get_strv("custom-keybindings"))
        from .app import launcher_command
        exe = launcher_command()
        for idx, (name, cmd, binding) in enumerate(SHORTCUTS):
            path = KB_BASE + "dednotch%d/" % idx
            kb = Gio.Settings.new_with_path(KB_CUSTOM, path)
            kb.set_string("name", "DedNotch: " + name)
            kb.set_string("command", "%s %s" % (exe, cmd))
            kb.set_string("binding", binding)
            if path not in paths:
                paths.append(path)
        media.set_strv("custom-keybindings", paths)
        Gio.Settings.sync()
        return True
    except Exception as exc:
        print("dednotch: shortcut install failed:", exc)
        return False


def _pretty_binding(b):
    return b.replace("<Primary>", "Ctrl+").replace("<Shift>", "Shift+").replace("<Alt>", "Alt+").replace(
        "<Super>", "Super+").upper()


def _icon_image(name):
    surf = _render_icon(name)
    return Gtk.Image.new_from_surface(surf)


def _render_icon(name):
    import cairo
    scale = 2
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 20 * scale, 20 * scale)
    surf.set_device_scale(scale, scale)
    cr = cairo.Context(surf)
    fg = Gtk.StyleContext().get_color(Gtk.StateFlags.NORMAL)
    gfx.draw_icon(cr, name, 10, 10, 13, (fg.red, fg.green, fg.blue) if fg.alpha > 0 else gfx.WHITE)
    return surf


def _link_button(label, url):
    b = Gtk.LinkButton.new_with_label(url, label)
    return b


def _coerce(value, current):
    if isinstance(current, bool):
        return value == "True"
    if isinstance(current, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return current
    if isinstance(current, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return current
    return value
