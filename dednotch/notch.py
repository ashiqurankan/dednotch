
import datetime as dt
import math
import os
import random
import subprocess
import time

import cairo
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from . import gfx
from .anim import CLOSE, INTERACTIVE, OPEN, QUICK, SMOOTH, Animator, clamp, lerp
from .config import settings

WIN_W, WIN_H = 720, 262
OPEN_W, OPEN_H = 640, 190
R_OPEN = (19.0, 24.0)                            # cornerRadiusInsets.opened
R_CLOSED = (6.0, 14.0)                           # cornerRadiusInsets.closed
ART_R = (13.0, 4.0)                              # MusicPlayerImageSizes.cornerRadiusInset
SEC_FILL = (1, 1, 1, 0.12)                       # NSColor.secondarySystemFill (dark)
TRACK = gfx.GRAY + (0.3,)
HUD_TYPES = ("volume", "brightness", "backlight", "mic")
SHARE_PROVIDERS = ["Copy to Clipboard", "Email", "Bluetooth", "Show in Files"]


class Hit:
    __slots__ = ("x", "y", "w", "h", "key", "click", "press", "drag", "release", "right",
                 "double", "scroll", "data")

    def __init__(self, x, y, w, h, key, **kw):
        self.x, self.y, self.w, self.h, self.key = x, y, w, h, key
        for name in ("click", "press", "drag", "release", "right", "double", "scroll", "data"):
            setattr(self, name, kw.get(name))

    def contains(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


class NotchWindow(Gtk.Window):
    def __init__(self, app, monitor):
        Gtk.Window.__init__(self, type=Gtk.WindowType.POPUP)
        self.app = app
        self.monitor = monitor
        self.coord = app.coord
        self.media, self.battery, self.shelf = app.media, app.battery, app.shelf
        self.calendar, self.webcam, self.system = app.calendar, app.webcam, app.system

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_title("DedNotch")
        self.resize(WIN_W, WIN_H)
        self.set_size_request(WIN_W, WIN_H)

        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                             Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.SCROLL_MASK |
                             Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK |
                             Gdk.EventMask.ENTER_NOTIFY_MASK)
        self.add(self.area)
        self.area.connect("draw", self._on_draw)
        self.area.connect("motion-notify-event", self._on_motion)
        self.area.connect("button-press-event", self._on_press)
        self.area.connect("button-release-event", self._on_release)
        self.area.connect("scroll-event", self._on_scroll)
        self.area.connect("leave-notify-event", self._on_leave)
        self._setup_dnd()

        # -- view-model state --
        self.state = "closed"
        self.hovering = False
        self.hide_on_closed = False
        self.camera_expanded = False
        self.battery_popover = None
        self.prevent_close = 0
        self._hover_task = None
        self._ptr_inside = False
        self._grabbed = False

        # -- rendering state --
        self.anim = Animator()
        self.hits = []
        self.hover_key = None
        self.pressed = None
        self.drag_cb = None
        self.mouse = (-1.0, -1.0)
        self.now = time.monotonic()
        self._last_frame = 0.0
        self._tick_id = None
        self._timer_id = None
        self._want_fps = 0
        self.notch_rect = (0, 0, 0, 0)
        self._input_rect = None
        self._marquee = {}
        self._content_layer = None
        self._mcr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4))

        # gestures
        self.gesture = 0.0
        self._gesture_acc = 0.0
        self._gesture_end = None

        # calendar
        self.cal_date = dt.date.today()
        self.cal_list_scroll = 0.0
        self.cal_list_max = 0.0
        self.hovering_calendar = False

        # shelf
        self.shelf_scroll = 0.0
        self.shelf_scroll_max = 0.0
        self.drag_over = False
        self.drop_zone = None
        self.drop_event = False
        self.internal_drag = None
        self._press_item = None

        # visualizer bars: 4 bars, re-randomised every 0.3s
        self.bars_from = [0.35] * 4
        self.bars_to = [0.35] * 4
        self.bars_t0 = 0.0
        GLib.timeout_add(300, self._update_bars)

        # face blink every 3s
        self.blink_until = 0.0
        GLib.timeout_add(3000, self._blink)

        # hello animation
        self.hello_start = None

        self.connect("realize", self._on_realize)
        self.place()
        GLib.timeout_add(400, self._warm_up)
        GLib.timeout_add(50, self._poll_pointer)

    # ------------------------------------------------------------------
    # placement / geometry
    # ------------------------------------------------------------------
    def place(self):
        geo = self.monitor.get_geometry()
        self.geo = geo
        self.win_x = geo.x + geo.width // 2 - WIN_W // 2
        self.win_y = geo.y
        self.move(self.win_x, self.win_y)
        self.queue()

    def closed_height(self):
        if settings["notch_height_mode"] == "custom":
            return float(settings["notch_height"])
        wa = self.monitor.get_workarea()
        h = wa.y - self.geo.y
        if h <= 0 or h > 64:
            h = 32
        return float(h)

    def effective_closed_height(self):
        return 0.0 if self.hide_on_closed else self.closed_height()

    @property
    def closed_w(self):
        return float(settings["closed_notch_width"])

    def _warm_up(self):
        """Render the open views once offscreen so fonts, glyphs and images are
        cached before the first real open (avoids a hitch on first hover)."""
        try:
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIN_W, WIN_H)
            cr = cairo.Context(surf)
            cx, ch = WIN_W / 2.0, self.effective_closed_height()
            left, right, hh, top, bottom = self.open_layout(cx, ch)
            self._draw_header(cr, left, right, hh, 1.0)
            self._draw_home(cr, left, right, top, bottom, 1.0, interactive=False)
            self._draw_shelf(cr, left, right, top, bottom, 1.0, interactive=False)
            for name in ("play.fill", "pause.fill", "house.fill", "tray.fill", "calendar"):
                gfx.draw_icon(cr, name, 10, 10, 13)
        except Exception:
            pass
        self.hits = []
        return False

    def _on_realize(self, *_):
        self._update_input_shape(force=True)
        try:
            self.get_window().set_override_redirect(True)
        except Exception:
            pass

    def _update_input_shape(self, force=False):
        win = self.get_window()
        if win is None:
            return
        x, y, w, h = self.notch_rect
        if h < 10:
            h = 10
        rect = (int(math.floor(x)) - 2, 0, int(math.ceil(w)) + 4, int(math.ceil(y + h)) + 4)
        if self.drag_over and self.state == "closed":
            rect = (rect[0] - 20, 0, rect[2] + 40, rect[3] + 20)
        if not force and rect == self._input_rect:
            return
        self._input_rect = rect
        region = cairo.Region(cairo.RectangleInt(*rect))
        win.input_shape_combine_region(region, 0, 0)

    # ------------------------------------------------------------------
    # open / close
    # ------------------------------------------------------------------
    @property
    def is_open(self):
        return self.state == "open"

    def open(self):
        if self.state == "open":
            return
        self.state = "open"
        self.media.force_update()
        self.cal_date = dt.date.today()
        self.cal_list_scroll = -1  # auto-scroll on next draw
        self.shelf.cleanup_invalid()
        self.get_window() and self.get_window().raise_()
        self._input_rect = None
        self.queue()

    def close(self, force=False):
        if self.state == "closed":
            return
        if not force and (self.prevent_close > 0 or self.internal_drag):
            return
        self.state = "closed"
        self.coord.sneak["show"] = False
        if self.battery_popover is not None:
            self.battery_popover.destroy()
            self.battery_popover = None
        if self.camera_expanded:
            self.camera_expanded = False
            self.webcam.stop()
        if not self.shelf.empty and settings["open_shelf_by_default"] and settings["ded_shelf"]:
            self.coord.current_view = "shelf"
        elif not settings["open_last_tab_by_default"]:
            self.coord.current_view = "home"
        self.queue()

    def toggle(self):
        if self.is_open:
            self.close(force=True)
        else:
            self.open()

    # ------------------------------------------------------------------
    # frame scheduling
    # ------------------------------------------------------------------
    def queue(self, *_):
        self.area.queue_draw()

    def _ensure_tick(self):
        if self._tick_id is None:
            self._tick_id = self.area.add_tick_callback(self._on_tick)

    def _on_tick(self, *_):
        self.area.queue_draw()
        if self.anim.active():
            return True
        self._tick_id = None
        return False

    def _schedule_timer(self, fps):
        if fps <= 0:
            return
        if self._timer_id is not None and self._want_fps == fps:
            return
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._want_fps = fps
        self._timer_id = GLib.timeout_add(int(1000 / fps), self._on_timer)

    def _on_timer(self):
        self._timer_id = None
        self.area.queue_draw()
        return False

    def _update_bars(self):
        if self.media.playing:
            self.bars_from = self._bars_now()
            self.bars_to = [random.uniform(0.35, 1.0) for _ in range(4)]
            self.bars_t0 = time.monotonic()
        return True

    def _bars_now(self):
        if not self.media.playing:
            return [0.35] * 4
        # CABasicAnimation 0.3s with autoreverse: from -> to -> from
        t = (time.monotonic() - self.bars_t0) / 0.3
        k = t if t <= 1 else max(0.0, 2 - t)
        k = 0.5 - math.cos(math.pi * clamp(k)) / 2
        return [lerp(a, b, k) for a, b in zip(self.bars_from, self.bars_to)]

    def _blink(self):
        self.blink_until = time.monotonic() + 0.1
        if settings["show_not_human_face"] and self.state == "closed":
            self.queue()
            GLib.timeout_add(120, self._queue_once)
        return True

    def _queue_once(self):
        self.queue()
        return False

    # ------------------------------------------------------------------
    # hover (ContentView.handleHover)
    # ------------------------------------------------------------------
    def _poll_pointer(self):
        try:
            seat = Gdk.Display.get_default().get_default_seat()
            _scr, px, py = seat.get_pointer().get_position()
        except Exception:
            return True
        lx, ly = px - self.win_x, py - self.win_y
        inside = self._in_hover_zone(lx, ly)
        if self.battery_popover is not None and self.battery_popover.contains_pointer(px, py):
            inside = True
        if inside != self._ptr_inside:
            self._ptr_inside = inside
            self._handle_hover(inside)
        self._update_grab(lx, ly)
        return True

    # GNOME Shell routes every click inside the top-bar strip to its own panel,
    # even where our override-redirect window is drawn on top. While the pointer
    # is over the notch inside that strip we grab the pointer so clicks, scrolls
    # and hover highlights reach the notch; the grab is dropped as soon as the
    # pointer leaves.
    def _panel_height(self):
        try:
            wa = self.monitor.get_workarea()
            return max(0, wa.y - self.geo.y)
        except Exception:
            return 0

    def _update_grab(self, lx, ly):
        if self.pressed is not None or self.drag_cb is not None:
            return  # never drop the pointer in the middle of a click/drag
        x, _y, w, h = self.notch_rect
        want = (w > 0 and h >= 1 and x <= lx <= x + w and 0 <= ly < min(h, self._panel_height() + 1)
                and self.prevent_close == 0 and not self.internal_drag and not self.drag_over
                and not self.coord.hello_running and self.get_mapped())
        if want and not self._grabbed:
            win = self.get_window()
            if win is None:
                return
            seat = Gdk.Display.get_default().get_default_seat()
            status = seat.grab(win, Gdk.SeatCapabilities.POINTER | Gdk.SeatCapabilities.TOUCH,
                               True, None, None, None, None)
            self._grabbed = status == Gdk.GrabStatus.SUCCESS
            if self._grabbed:
                self.mouse = (lx, ly)
                self._on_motion(None, _PointerEvent(lx, ly))
        elif not want and self._grabbed:
            self.release_grab()

    def release_grab(self):
        if self._grabbed:
            self._grabbed = False
            Gdk.Display.get_default().get_default_seat().ungrab()

    def _in_hover_zone(self, lx, ly):
        x, y, w, h = self.notch_rect
        if w <= 0:
            return False
        if h < 1:
            h = 10  # zeroHeightHoverPadding
        pad = 0
        if self.state == "closed" and settings["extend_hover_area"]:
            pad = 30  # extendedHoverPadding
        if self.state == "open":
            pad = 2
        return x - pad <= lx <= x + w + pad and -1 <= ly <= y + h + pad

    def _handle_hover(self, hovering):
        if self.coord.hello_running:
            return
        if self._hover_task:
            GLib.source_remove(self._hover_task)
            self._hover_task = None
        if hovering:
            self.hovering = True
            self.queue()
            if self.state != "closed" or self.coord.sneak["show"] or not settings["open_notch_on_hover"]:
                return

            def do_open():
                self._hover_task = None
                if self.state == "closed" and self.hovering and not self.coord.sneak["show"]:
                    self.open()
                return False
            self._hover_task = GLib.timeout_add(int(settings["minimum_hover_duration"] * 1000) + 1, do_open)
        else:
            def do_leave():
                self._hover_task = None
                self.hovering = False
                if self.battery_popover is not None:
                    # dismiss the popover, then close
                    self.battery_popover.destroy()
                    self.battery_popover = None
                if self.state == "open" and not self.drag_over:
                    self.close()
                self.queue()
                return False
            self._hover_task = GLib.timeout_add(100, do_leave)

    # ------------------------------------------------------------------
    # mouse input
    # ------------------------------------------------------------------
    def _hit_at(self, x, y):
        for hit in reversed(self.hits):
            if hit.contains(x, y):
                return hit
        return None

    def _on_motion(self, _w, event):
        self.mouse = (event.x, event.y)
        if self.drag_cb is not None:
            self.drag_cb(event.x, event.y)
            self.queue()
            return True
        if self._press_item is not None:
            item_id, sx, sy, ev = self._press_item
            if abs(event.x - sx) > 6 or abs(event.y - sy) > 6:
                self._press_item = None
                self._start_item_drag(item_id, event)
                return True
        hit = self._hit_at(event.x, event.y)
        key = hit.key if hit else None
        if key != self.hover_key:
            self.hover_key = key
            self.queue()
        return True

    def _on_leave(self, _w, event):
        if self.hover_key is not None and self.drag_cb is None:
            self.hover_key = None
            self.queue()
        return False

    def _on_press(self, _w, event):
        self.mouse = (event.x, event.y)
        hit = self._hit_at(event.x, event.y)
        if event.button == 3:
            if hit is not None and hit.right:
                hit.right(event)
            else:
                self._show_notch_menu(event)
            return True
        if event.button != 1:
            return False
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            if hit is not None and hit.double:
                hit.double(event)
            return True
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True
        if self.state == "closed":
            # ContentView.onTapGesture { doOpen() }
            if not self.coord.hello_running:
                self.open()
            return True
        self.pressed = hit
        if hit is not None:
            if hit.press:
                hit.press(event)
            if hit.drag:
                self.drag_cb = hit.drag
                hit.drag(event.x, event.y)
        self.queue()
        return True

    def _on_release(self, _w, event):
        if event.button != 1:
            return False
        hit = self.pressed
        self.pressed = None
        self._press_item = None
        if self.drag_cb is not None:
            self.drag_cb = None
            if hit is not None and hit.release:
                hit.release(event.x, event.y)
        elif hit is not None and hit.contains(event.x, event.y) and hit.click:
            hit.click(event)
        self.queue()
        return True

    def _on_scroll(self, _w, event):
        ok, dx, dy = event.get_scroll_deltas()
        smooth = ok
        if not ok:
            d = event.direction
            dx = -1 if d == Gdk.ScrollDirection.LEFT else 1 if d == Gdk.ScrollDirection.RIGHT else 0
            dy = -1 if d == Gdk.ScrollDirection.UP else 1 if d == Gdk.ScrollDirection.DOWN else 0
        hit = self._hit_at(event.x, event.y)
        if self.state == "open" and hit is not None and hit.scroll and hit.scroll(dx, dy, smooth):
            self.queue()
            return True
        if not settings["enable_gestures"]:
            return True
        px = (dy * 10.0) if smooth else dy * 70.0
        sens = float(settings["gesture_sensitivity"])
        if self.state == "closed" and px < 0:
            self._gesture_acc += -px
            self.gesture = (self._gesture_acc / sens) * 20
            if self._gesture_acc > sens:
                self._gesture_acc = 0
                self.gesture = 0
                self.open()
        elif self.state == "open" and px > 0 and settings["close_gesture_enabled"] \
                and not self.hovering_calendar:
            self._gesture_acc += px
            self.gesture = (self._gesture_acc / sens) * -20
            if self._gesture_acc > sens:
                self._gesture_acc = 0
                self.gesture = 0
                self.hovering = False
                self.close()
        if self._gesture_end:
            GLib.source_remove(self._gesture_end)
        self._gesture_end = GLib.timeout_add(220, self._end_gesture)
        self.queue()
        return True

    def _end_gesture(self):
        self._gesture_end = None
        self._gesture_acc = 0
        self.gesture = 0
        self.queue()
        return False

    def hovered(self, key):
        return self.hover_key == key

    def add_hit(self, x, y, w, h, key, **kw):
        self.hits.append(Hit(x, y, w, h, key, **kw))

    # ------------------------------------------------------------------
    # state selection (ContentView.NotchLayout)
    # ------------------------------------------------------------------
    def closed_kind(self):
        c, m = self.coord, self.media
        exp, sneak = c.expanding, c.sneak
        playing_or_active = m.playing or not m.idle
        if c.hello_running:
            return "hello"
        if exp["show"] and exp["type"] == "battery" and settings["show_power_status_notifications"]:
            return "battery"
        if sneak["show"] and settings["inline_hud"] and sneak["type"] in HUD_TYPES:
            return "inline_hud"
        if (not exp["show"] or exp["type"] == "music") and playing_or_active \
                and settings["music_live_activity_enabled"] and not self.hide_on_closed:
            return "music"
        if not exp["show"] and not playing_or_active and settings["show_not_human_face"] \
                and not self.hide_on_closed:
            return "face"
        return "plain"

    def closed_extra_row(self):
        sneak = self.coord.sneak
        if not sneak["show"]:
            return None
        if sneak["type"] in HUD_TYPES and not settings["inline_hud"]:
            return "hud"
        if sneak["type"] == "music" and not self.hide_on_closed and settings["sneak_peek_style"] == "standard":
            return "music"
        return None

    def music_inline_expanded(self):
        exp = self.coord.expanding
        return exp["show"] and exp["type"] == "music" and settings["sneak_peek_style"] == "inline"

    def closed_target_size(self, kind, extra, ch):
        cw = self.closed_w
        s = max(0.0, ch - 12)
        if kind == "hello":
            return OPEN_W, OPEN_H
        if kind == "music":
            mid = 380 if self.music_inline_expanded() else cw - R_CLOSED[0]
            w = 14 + s + 8 + mid + 8 + s + 14
        elif kind == "face":
            w = 14 + s + 8 + (cw - 20) + 8 + 30 + 14
        elif kind == "battery":
            tw, _ = gfx.text_size(self._mcr, self.battery.status_text, 11)
            w = 14 + tw + (cw + 10) + 76 + 14
        elif kind == "inline_hud":
            side = 100 - (0 if self.hovering else 12)
            w = 14 + side + 8 + (cw - 20) + 8 + side + 14
        else:
            w = cw - 20 + 28
        h = ch
        if kind == "inline_hud" and self.hovering:
            h += 8
        if extra == "hud":
            h += 8 + 15 + 10
        elif extra == "music":
            h += 8 + 16 + 10
        return w, h

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------
    def _on_draw(self, _widget, cr):
        now = time.monotonic()
        dt_ = now - self._last_frame
        if dt_ <= 0 or dt_ > 0.25:
            dt_ = 1 / 60.0  # first frame after being idle
        dt_ = min(dt_, 1 / 30.0)
        self._last_frame = now
        self.now = now
        self.anim.step(dt_)
        self.hits = []
        self._fps = 0

        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        try:
            self._draw_notch(cr)
        except Exception:
            import traceback
            traceback.print_exc()

        self._update_input_shape()
        hit = self._hit_at(*self.mouse) if self.drag_cb is None else self.pressed
        self.hover_key = hit.key if hit else None
        if self.anim.active():
            self._ensure_tick()
        elif self._fps:
            self._schedule_timer(self._fps)
        return True

    def want_fps(self, fps):
        self._fps = max(self._fps, fps)

    def _draw_notch(self, cr):
        a = self.anim
        is_open = self.is_open
        spring = OPEN if is_open else CLOSE
        scaling = settings["corner_radius_scaling"]
        ch = self.effective_closed_height()
        kind = self.closed_kind()
        extra = self.closed_extra_row() if not is_open else None

        if is_open:
            tw, th = OPEN_W, OPEN_H
        else:
            tw, th = self.closed_target_size(kind, extra, ch)
            if ch <= 0 and kind != "hello":
                th = 0
        W = a.get("w", tw, spring)
        H = max(0.0, a.get("h", th, spring))
        big = (is_open or kind == "hello") and scaling
        if kind == "hello":
            spring = OPEN
        rt = a.get("rt", R_OPEN[0] if big else R_CLOSED[0], spring)
        rb = a.get("rb", R_OPEN[1] if big else R_CLOSED[1], spring)
        open_p = a.get("open_p", 1 if is_open else 0, spring)
        content_a = a.get("content_a", 1 if is_open else 0, (0.35, 1.0) if is_open else (0.22, 1.0))
        closed_a = a.get("closed_a", 0 if is_open else 1, (0.18, 1.0) if is_open else (0.35, 1.0))
        gesture = a.get("gesture", self.gesture, INTERACTIVE, eps=0.01)
        shadow_on = (is_open or self.hovering or kind == "hello") and settings["enable_shadow"]
        shadow_a = a.get("shadow", 0.7 if shadow_on else 0.0, INTERACTIVE)

        cx = WIN_W / 2.0
        x0 = cx - W / 2.0
        scale = max(0.6, 1.0 + gesture * 0.01) if abs(gesture) > 0.01 else 1.0
        self.notch_rect = (cx - W * scale / 2.0, 0.0, W * scale, H * scale)
        self.frame = dict(x0=x0, W=W, H=H, ch=ch, cx=cx, open_p=open_p, content_a=content_a,
                          closed_a=closed_a, kind=kind, extra=extra, scaling=scaling)
        if H < 0.5 or W < 1:
            return

        cr.save()
        if scale != 1.0:
            cr.translate(cx, 0)
            cr.scale(scale, scale)
            cr.translate(-cx, 0)

        def shape():
            gfx.notch_path(cr, x0, 0, W, H, rt, rb)

        gfx.soft_shadow(cr, shape, 6 if scaling else 4, shadow_a)
        shape()
        cr.set_source_rgb(0, 0, 0)
        cr.fill_preserve()
        cr.save()
        cr.clip()

        # ---- closed content -------------------------------------------------
        if closed_a > 0.003:
            self._draw_closed(cr, kind, extra, x0, W, H, ch, closed_a)

        # ---- open content ---------------------------------------------------
        if content_a > 0.003:
            gesture_fade = 1.0 - min(abs(gesture) * 0.1, 0.3) if abs(gesture) > 0.01 else 1.0
            self._draw_open(cr, cx, ch, content_a * gesture_fade, open_p)

        # matched-geometry album art (shared between closed live activity & home)
        self._draw_shared_art(cr, x0, W, ch, kind, closed_a, content_a, open_p)

        cr.restore()
        cr.restore()

    # ------------------------------------------------------------------
    # closed views
    # ------------------------------------------------------------------
    def _kind_alpha(self, name, active):
        return self.anim.get("kind:" + name, 1.0 if active else 0.0, SMOOTH)

    def _draw_closed(self, cr, kind, extra, x0, W, H, ch, alpha):
        for name in ("hello", "battery", "inline_hud", "music", "face"):
            ka = self._kind_alpha(name, kind == name) * alpha
            if ka <= 0.003:
                continue
            if name == "hello":
                self._draw_hello(cr, x0, W, H, ka)
            elif name == "battery":
                self._draw_battery_notification(cr, x0, W, ch, ka)
            elif name == "inline_hud":
                self._draw_inline_hud(cr, x0, W, ch, ka)
            elif name == "music":
                self._draw_music_live_activity(cr, x0, W, ch, ka)
            elif name == "face":
                self._draw_face(cr, x0 + W - 14 - 15, ch / 2.0, ka)

        for name in ("hud", "music"):
            ra = self.anim.get("row:" + name, 1.0 if extra == name else 0.0, SMOOTH) * alpha
            if ra <= 0.003:
                continue
            if name == "hud":
                self._draw_hud_row(cr, x0 + 14 + 4, ch + 8, W - 28 - 12, ra)
            else:
                self._draw_music_row(cr, x0 + 14, ch + 8, W - 28, ra)

    def _draw_music_live_activity(self, cr, x0, W, ch, alpha):
        s = max(0.0, ch - 12)
        m = self.media
        inline = self.music_inline_expanded()
        # spectrum on the right (AudioSpectrumView 16x12 masking a coloured rect)
        sx = x0 + W - 14 - s / 2.0
        color = m.avg_color if settings["colored_spectrogram"] else gfx.GRAY
        self._draw_spectrum(cr, sx, ch / 2.0, color, alpha)
        # inline sneak peek: title on the left, artist on the right of the black middle
        ia = self.anim.get("inline_text", 1.0 if inline else 0.0, SMOOTH)
        if ia > 0.01:
            mid_x = x0 + 14 + s + 8
            mid_w = W - 2 * (14 + s + 8)
            tcolor = m.avg_color if settings["colored_spectrogram"] else gfx.GRAY
            y = (ch - 16) / 2.0
            self.marquee(cr, "inline_title", m.title, mid_x, y, 100, 13, "regular", tcolor,
                         alpha * ia, min_duration=0.4)
            aw = min(140.0, max(0.0, mid_w - 100 - self.closed_w))
            if aw > 4:
                gfx.draw_text(cr, m.artist, mid_x + mid_w - aw, y, tcolor, alpha * ia, 13,
                              width=aw, align="right")

    def _draw_spectrum(self, cr, cx, cy, color, alpha):
        bars = self._bars_now()
        if self.media.playing:
            self.want_fps(24)
        top = gfx.mix(color, (1, 1, 1), 0.12)
        grad = cairo.LinearGradient(0, cy - 7, 0, cy + 7)
        grad.add_color_stop_rgba(0, top[0], top[1], top[2], alpha)
        grad.add_color_stop_rgba(1, color[0], color[1], color[2], alpha)
        total = 4 * 2 + 3 * 2
        x = cx - total / 2.0
        for i, k in enumerate(bars):
            h = min(12.0, 14.0 * k)
            gfx.rounded_rect(cr, x + i * 4, cy - h / 2.0, 2, h, 1)
            cr.set_source(grad)
            cr.fill()

    def _draw_face(self, cr, cx, cy, alpha):
        blinking = self.now < self.blink_until
        eye_h = self.anim.get("eye_h", 1.0 if blinking else 4.0, (0.2, 1.0))
        cr.save()
        gfx.set_color(cr, gfx.WHITE, alpha)
        top = cy - 12
        for ex in (cx - 4, cx + 4):
            gfx.rounded_rect(cr, ex - 2, top + 2 - eye_h / 2.0, 4, eye_h, 2)
            cr.fill()
        gfx.rounded_rect(cr, cx - 1.5, top + 8, 3, 4, 1.5)
        cr.fill()
        mx, my = cx - 7, top + 14
        cr.move_to(mx, my + 5)
        cr.curve_to(mx + 4.67, my + 8.33, mx + 9.33, my + 8.33, mx + 14, my + 5)
        cr.set_line_width(2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.stroke()
        cr.restore()

    def _draw_battery_notification(self, cr, x0, W, ch, alpha):
        b = self.battery
        _w, th = gfx.text_size(cr, b.status_text, 11)
        gfx.draw_text(cr, b.status_text, x0 + 14, (ch - th) / 2.0, gfx.WHITE, alpha, 11)
        self._draw_battery_icon(cr, x0 + W - 14 - 31, ch / 2.0, 30, alpha, notification=True)

    def _draw_battery_icon(self, cr, x, cy, bw, alpha, notification=False):
        """BatteryView: outline, coloured level fill and bolt/plug overlay."""
        b = self.battery
        body_w, body_h = bw - 2.5, 14.0
        top = cy - body_h / 2.0
        gfx.rounded_rect(cr, x + 0.5, top + 0.5, body_w - 1, body_h - 1, 4)
        cr.set_line_width(1)
        gfx.set_color(cr, gfx.WHITE, 0.5 * alpha)
        cr.stroke()
        gfx.rounded_rect(cr, x + body_w + 0.5, cy - 2.5, 2, 5, 1)
        gfx.set_color(cr, gfx.WHITE, 0.5 * alpha)
        cr.fill()
        level = clamp(b.level / 100.0)
        if b.low_power:
            color = gfx.YELLOW
        elif b.level <= 20 and not b.charging and not b.plugged_in:
            color = gfx.RED
        elif b.charging or b.plugged_in or b.level >= 100:
            color = gfx.GREEN
        else:
            color = gfx.WHITE
        fill_h = (bw - 2.75) - 18
        fill_w = level * (bw - 6)
        if fill_w > 0.5:
            gfx.rounded_rect(cr, x + 2, cy - fill_h / 2.0, fill_w, fill_h, 2.5)
            gfx.set_color(cr, color, alpha)
            cr.fill()
        icon = "bolt.png" if b.charging else "plug.png" if b.plugged_in else None
        if icon and (notification or settings["show_power_status_icons"]):
            pb = self.app.asset(icon, 17)
            if pb is not None:
                cr.save()
                Gdk.cairo_set_source_pixbuf(cr, pb, x + bw / 2.0 - pb.get_width() / 2.0 - 1,
                                            cy - pb.get_height() / 2.0)
                cr.paint_with_alpha(alpha)
                cr.restore()

    def _hud_icon_name(self, kind, value, icon=""):
        if kind == "volume":
            if icon:
                return icon
            if value <= 0:
                return "speaker.slash.fill"
            if value <= 0.3:
                return "speaker.wave.1.fill"
            if value <= 0.8:
                return "speaker.wave.2.fill"
            return "speaker.wave.3.fill"
        if kind == "brightness":
            return "sun.max.fill" if value > 0.6 else "sun.min.fill"
        if kind == "backlight":
            return "sun.max" if value > 0.5 else "sun.min.fill"
        if kind == "mic":
            return "mic.fill" if value > 0 else "mic.slash.fill"
        return "speaker.fill"

    def _draw_progress(self, cr, key, x, y, w, value, alpha, inline=False, on_change=None):
        """DraggableProgressBar."""
        dragging = self.drag_cb is not None and self.pressed is not None and self.pressed.key == key
        if inline:
            h = self.anim.get(key + ":h", 8 if dragging else 5, (0.3, 1.0))
        else:
            h = self.anim.get(key + ":h", 9 if dragging else 6, (0.3, 1.0))
        cy = y
        gfx.capsule(cr, x, cy - h / 2.0, w, h)
        gfx.set_color(cr, gfx.WHITE, 0.22 * alpha)
        cr.fill()
        use_accent = settings["system_event_indicator_use_accent"]
        accent = settings.accent()
        base = accent if use_accent else gfx.WHITE
        fw = clamp(value) * w
        if fw > 0.5:
            if settings["system_event_indicator_shadow"]:
                glow = gfx.ensure_min_brightness(accent, 0.7) if use_accent else gfx.WHITE
                for i in range(4, 0, -1):
                    gfx.capsule(cr, x - i, cy - h / 2.0 - i, fw + 2 * i + 3, h + 2 * i)
                    gfx.set_color(cr, glow, 0.07 * alpha)
                    cr.fill()
            gfx.capsule(cr, x, cy - h / 2.0, fw, h)
            if settings["enable_gradient"]:
                lo = gfx.ensure_min_brightness(base, 0.2) if use_accent else base
                grad = cairo.LinearGradient(x, 0, x + fw, 0)
                grad.add_color_stop_rgba(0, lo[0], lo[1], lo[2], (1 if use_accent else 0.2) * alpha)
                grad.add_color_stop_rgba(1, base[0], base[1], base[2], alpha)
                cr.set_source(grad)
            else:
                gfx.set_color(cr, base, alpha)
            cr.fill()
        if on_change is not None:
            def drag(px, _py):
                on_change(clamp((px - x) / max(1.0, w)))
            self.add_hit(x - 4, cy - 8, w + 8, 16, key, drag=drag)

    def _hud_change(self, kind):
        def change(v):
            self.coord.sneak["value"] = v
            self.coord.bump_sneak()
            if kind == "volume":
                self.system.set_volume(v)
            elif kind == "brightness":
                self.system.set_brightness(v)
        return change

    def _draw_hud_row(self, cr, x, y, w, alpha):
        """SystemEventIndicatorModifier (standard closed HUD)."""
        s = self.coord.sneak
        kind, value = s["type"], s["value"]
        cy = y + 7.5
        gfx.draw_icon(cr, self._hud_icon_name(kind, value, s["icon"]), x + 10, cy, 17, gfx.WHITE, alpha)
        bx = x + 20 + 14
        if kind == "mic":
            gfx.draw_text(cr, "Mic %s" % ("unmuted" if value > 0 else "muted"), bx, y, gfx.GRAY,
                          alpha, 13, valign="center", height=15)
            return
        pct = settings["show_closed_notch_hud_percentage"]
        bw = w - 34 - (14 + 35 if pct else 0)
        self._draw_progress(cr, "hudrow", bx, cy, bw, value, alpha, on_change=self._hud_change(kind))
        if pct:
            gfx.draw_text(cr, "%d%%" % int(value * 100), bx + bw + 14, y, gfx.WHITE, alpha, 12,
                          "medium", width=35, align="right", valign="center", height=15)

    def _draw_inline_hud(self, cr, x0, W, ch, alpha):
        """Inline HUD."""
        s = self.coord.sneak
        kind, value = s["type"], s["value"]
        side = 100 - (0 if self.hovering else 12)
        rowh = ch + (8 if self.hovering else 0)
        cy = rowh / 2.0
        lx = x0 + 14
        gfx.draw_icon(cr, self._hud_icon_name(kind, value, s["icon"]), lx + 10, cy, 13, gfx.WHITE, alpha)
        name = {"volume": "Volume", "brightness": "Brightness", "backlight": "Backlight", "mic": "Mic"}[kind]
        gfx.draw_text(cr, name, lx + 25, cy - 7, gfx.WHITE, alpha, 11, "medium", width=side - 25)
        rx = x0 + W - 14 - side
        if kind == "mic":
            gfx.draw_text(cr, "muted" if value <= 0 else "unmuted", rx, cy - 8, gfx.GRAY, alpha, 13,
                          width=side - 4, align="right")
            return
        label = None
        if kind == "volume" and value <= 0:
            label = "muted"
        elif settings["show_closed_notch_hud_percentage"]:
            label = "%d%%" % int(value * 100)
        lw = (gfx.text_size(cr, label, 10, "medium")[0] + 8) if label else 0
        self._draw_progress(cr, "inlinehud", rx, cy, side - 4 - lw, value, alpha, inline=True,
                            on_change=self._hud_change(kind))
        if label:
            gfx.draw_text(cr, label, rx + side - 4 - lw + 8, cy - 6.5, gfx.GRAY, alpha, 10, "medium")

    def _draw_music_row(self, cr, x, y, w, alpha):
        m = self.media
        color = gfx.ensure_min_brightness(m.avg_color, 0.6) if settings["player_color_tinting"] else gfx.GRAY
        gfx.draw_icon(cr, "music.note", x + 6, y + 8, 13, gfx.GRAY, alpha)
        self.marquee(cr, "sneak_music", "%s - %s" % (m.title, m.artist), x + 20, y, w - 20, 13,
                     "regular", color, alpha, min_duration=1)

    # ------------------------------------------------------------------
    # hello animation
    # ------------------------------------------------------------------
    HELLO = [
        ((0.00095, 0.88718),),
        ((0.19536, 0.31015), (0.00993, 0.87738), (0.16556, 0.56785)),
        ((0.15043, 0.04964), (0.22517, 0.05245), (0.1859, -0.068)),
        ((0.10028, 0.932), (0.11495, 0.16729), (0.09792, 1.02023)),
        ((0.18354, 0.47822), (0.10265, 0.84376), (0.12157, 0.47822)),
        ((0.22327, 0.88718), (0.25733, 0.51463), (0.19915, 0.81575)),
        ((0.38553, 0.71351), (0.2474, 0.95861), (0.33586, 0.89978)),
        ((0.35998, 0.45441), (0.43519, 0.52724), (0.38978, 0.4306)),
        ((0.35478, 0.87317), (0.33018, 0.47822), (0.27956, 0.71631)),
        ((0.53453, 0.62808), (0.42999, 1.03004), (0.51892, 0.6939)),
        ((0.57332, 0.00623), (0.55014, 0.56225), (0.63955, 0.05805)),
        ((0.48723, 0.60146), (0.5071, -0.04559), (0.48486, 0.50623)),
        ((0.54588, 0.91239), (0.48959, 0.6967), (0.50378, 0.87597)),
        ((0.70719, 0.51043), (0.58798, 0.9488), (0.6807, 0.64768)),
        ((0.73273, 0.01323), (0.73368, 0.37317), (0.76679, 0.03984)),
        ((0.67171, 0.15048), (0.69868, -0.01338), (0.68259, 0.07205)),
        ((0.69678, 0.92639), (0.66083, 0.22892), (0.62204, 0.86057)),
        ((0.87275, 0.47822), (0.77152, 0.99222), (0.78855, 0.42997)),
        ((0.91438, 0.89776), (0.9734, 0.51043), (0.92329, 0.85998)),
        ((0.79943, 0.69608), (0.87047, 1.08403), (0.77956, 1.0)),
        ((0.92006, 0.53081), (0.81523, 0.45436), (0.86282, 0.43277)),
        ((0.99905, 0.432), (0.95979, 0.57703), (0.98959, 0.4944)),
    ]
    HELLO_STOPS = [(0.0, (0.04, 0.52, 1.0)), (0.2, (0.75, 0.35, 0.95)), (0.4, (1.0, 0.27, 0.23)),
                   (0.5, (0.0, 0.78, 0.75)), (0.7, (0.37, 0.36, 0.9)), (0.9, (1.0, 0.18, 0.33)),
                   (1.0, (0.04, 0.52, 1.0))]

    def _hello_points(self, x, y, w, h):
        key = (round(x, 1), round(y, 1), w, h)
        if getattr(self, "_hello_key", None) == key:
            return self._hello_pts
        surf = cairo.ImageSurface(cairo.FORMAT_A8, 4, 4)
        c = cairo.Context(surf)
        c.set_tolerance(0.2)
        (p0,) = self.HELLO[0]
        c.move_to(x + p0[0] * w, y + p0[1] * h)
        for to, c1, c2 in self.HELLO[1:]:
            c.curve_to(x + c1[0] * w, y + c1[1] * h, x + c2[0] * w, y + c2[1] * h,
                       x + to[0] * w, y + to[1] * h)
        pts = []
        for kind, p in c.copy_path_flat():
            if kind in (cairo.PATH_MOVE_TO, cairo.PATH_LINE_TO):
                pts.append(p)
        lengths = [0.0]
        for i in range(1, len(pts)):
            lengths.append(lengths[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
        self._hello_key, self._hello_pts = key, (pts, lengths)
        return pts, lengths

    def _draw_hello(self, cr, x0, W, H, alpha):
        if self.hello_start is None:
            self.hello_start = self.now
        t = self.now - self.hello_start - 0.6
        self.want_fps(60)
        if t > 4.0:
            if self.coord.hello_running:
                GLib.idle_add(self.coord.finish_hello)
        if t <= 0:
            return
        u = clamp(t / 4.0)
        progress = u * u * (3 - 2 * u)  # easeInOut
        start = max(0.0, 2 * progress - 1.0)
        # same 185:80 proportions as HelloAnimation, scaled to the open notch
        hh = 110.0
        hw = hh * 185.0 / 80.0
        hx = x0 + (OPEN_W - hw) / 2.0 + (W - OPEN_W) / 2.0
        pts, lengths = self._hello_points(hx, (OPEN_H - hh) / 2.0 + 6, hw, hh)
        total = lengths[-1]
        a_len, b_len = start * total, progress * total
        seg = [p for p, l in zip(pts, lengths) if a_len <= l <= b_len]
        if len(seg) < 2:
            return
        grad = cairo.LinearGradient(hx, 0, hx + hw, 0)
        for stop, col in self.HELLO_STOPS:
            grad.add_color_stop_rgba(stop, col[0], col[1], col[2], alpha)
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for width, a in ((16, 0.10), (12, 0.14), (8, 0.22), (6, 0.3)):
            cr.new_path()
            cr.move_to(*seg[0])
            for p in seg[1:]:
                cr.line_to(*p)
            cr.set_line_width(width)
            cr.push_group()
            cr.set_source(grad)
            cr.stroke()
            cr.pop_group_to_source()
            cr.paint_with_alpha(a)
        cr.new_path()
        cr.move_to(*seg[0])
        for p in seg[1:]:
            cr.line_to(*p)
        cr.set_line_width(4)
        cr.set_source(grad)
        cr.stroke()
        cr.restore()

    # ------------------------------------------------------------------
    # open notch
    # ------------------------------------------------------------------
    def open_layout(self, cx, ch):
        pad = (R_OPEN[0] if settings["corner_radius_scaling"] else R_OPEN[1]) + 12
        left = cx - OPEN_W / 2.0 + pad
        right = cx + OPEN_W / 2.0 - pad
        header_h = max(24.0, ch)
        top = header_h + 8
        bottom = OPEN_H - 12
        return left, right, header_h, top, bottom

    def _draw_open(self, cr, cx, ch, alpha, open_p):
        left, right, header_h, top, bottom = self.open_layout(cx, ch)
        self._draw_header(cr, left, right, header_h, alpha)
        # transition: .scale(0.8, anchor: .top).combined(with: .opacity)
        # Content is rendered at 1:1 into an offscreen layer (so Pango's glyph
        # caches stay warm) and the finished bitmap is zoomed: re-shaping text
        # at a new scale every frame is what made the animation stutter.
        sc = 0.8 + 0.2 * alpha
        layer = self._content_layer
        if layer is None:
            layer = self._content_layer = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIN_W, OPEN_H)
        lc = cairo.Context(layer)
        lc.set_operator(cairo.OPERATOR_CLEAR)
        lc.paint()
        lc.set_operator(cairo.OPERATOR_OVER)
        view = self.coord.current_view if settings["ded_shelf"] else "home"
        home_a = self.anim.get("view:home", 1.0 if view == "home" else 0.0, SMOOTH)
        shelf_a = self.anim.get("view:shelf", 1.0 if view == "shelf" else 0.0, SMOOTH)
        if home_a > 0.003:
            self._draw_home(lc, left, right, top, bottom, home_a, interactive=view == "home")
        if shelf_a > 0.003:
            self._draw_shelf(lc, left, right, top, bottom, shelf_a, interactive=view == "shelf")
        cr.save()
        cr.translate(cx, 0)
        cr.scale(sc, sc)
        cr.translate(-cx, 0)
        cr.set_source_surface(layer, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD if sc < 0.999 else cairo.FILTER_NEAREST)
        cr.paint_with_alpha(alpha)
        cr.restore()

    # -- header -----------------------------------------------------
    def _draw_header(self, cr, left, right, hh, alpha):
        cy = hh / 2.0
        show_tabs = settings["ded_shelf"] and (not self.shelf.empty or settings["always_show_tabs"])
        if show_tabs:
            self._draw_tabs(cr, left, cy, alpha)
        s = self.coord.sneak
        x = right
        if s["show"] and s["type"] in HUD_TYPES and settings["show_open_notch_hud"]:
            self._draw_open_hud(cr, right, cy, alpha)
            return
        if settings["show_battery_indicator"] and self.battery.available:
            x = self._draw_battery_button(cr, x, cy, alpha)
            x -= 4
        if settings["settings_icon_in_notch"]:
            x -= 30
            self._capsule_button(cr, "gear", x, cy, "gearshape", alpha, self._open_settings)
            x -= 4
        if settings["show_mirror"]:
            x -= 30
            self._capsule_button(cr, "camera", x, cy, "video", alpha, self.toggle_camera)

    def _capsule_button(self, cr, key, x, cy, icon, alpha, action):
        pressed = self.pressed is not None and self.pressed.key == key and self.hovered(key)
        gfx.capsule(cr, x, cy - 15, 30, 30)
        cr.set_source_rgba(0, 0, 0, alpha)
        cr.fill()
        gfx.draw_icon(cr, icon, x + 15, cy, 14, gfx.WHITE, alpha * (0.6 if pressed else 1.0))
        self.add_hit(x, cy - 15, 30, 30, key, click=lambda e: action())

    def _draw_tabs(self, cr, left, cy, alpha):
        tabs = [("home", "house.fill"), ("shelf", "tray.fill")]
        x = left
        rects = []
        for view, icon in tabs:
            iw, _ = gfx.icon_extent(cr, icon, 13)
            w = iw + 30
            rects.append((view, icon, x, w))
            x += w
        cur = self.coord.current_view
        for view, icon, bx, bw in rects:
            if view == cur:
                tx = self.anim.get("tab_x", bx, SMOOTH)
                tw = self.anim.get("tab_w", bw, SMOOTH)
                gfx.capsule(cr, tx, cy - 13, tw, 26)
                gfx.set_color(cr, SEC_FILL, alpha)
                cr.fill()
        for view, icon, bx, bw in rects:
            ca = self.anim.get("tabc:" + view, 1.0 if view == cur else 0.0, SMOOTH)
            col = gfx.mix(gfx.GRAY, gfx.WHITE, ca)
            gfx.draw_icon(cr, icon, bx + bw / 2.0, cy, 13, col, alpha)
            self.add_hit(bx, cy - 13, bw, 26, "tab:" + view, click=lambda e, v=view: self.set_view(v))

    def set_view(self, view):
        self.coord.current_view = view
        self.queue()

    def _draw_battery_button(self, cr, right, cy, alpha):
        b = self.battery
        bw = 30
        x = right - (bw + 1)
        self._draw_battery_icon(cr, x, cy, bw, alpha)
        left = x
        if settings["show_battery_percentage"]:
            text = "%d%%" % int(b.level)
            tw, th = gfx.text_size(cr, text, 12)
            left = x - 8 - tw
            gfx.draw_text(cr, text, left, cy - th / 2.0, gfx.WHITE, alpha, 12)
        self.add_hit(left, cy - 15, right - left, 30, "battery", click=lambda e: self.toggle_battery_popover())
        return left

    def _draw_open_hud(self, cr, right, cy, alpha):
        """Open-notch HUD: capsule with icon, bar and percentage."""
        s = self.coord.sneak
        kind, value = s["type"], s["value"]
        pct = settings["show_open_notch_hud_percentage"]
        if kind == "mic":
            label = "Unmuted" if value > 0 else "Muted"
            inner = 20 + 8 + gfx.text_size(cr, label, 13, "medium")[0]
        else:
            inner = 20 + 8 + (65 if pct else 108) + (8 + 35 if pct else 0)
        w, h = inner + 20, 28
        x = right - w
        gfx.capsule(cr, x, cy - h / 2.0, w, h)
        cr.set_source_rgba(0, 0, 0, alpha)
        cr.fill_preserve()
        cr.set_line_width(1)
        cr.set_source_rgba(1, 1, 1, 0.1 * alpha)
        cr.stroke()
        ix = x + 10
        gfx.draw_icon(cr, self._hud_icon_name(kind, value, s["icon"]), ix + 10, cy, 14, gfx.WHITE, alpha,
                      weight="medium")
        bx = ix + 28
        if kind == "mic":
            gfx.draw_text(cr, label, bx, cy - 8, gfx.WHITE, alpha, 13, "medium")
            return
        bw = 65 if pct else 108
        self._draw_progress(cr, "openhud", bx, cy, bw, value, alpha, on_change=self._hud_change(kind))
        if pct:
            gfx.draw_text(cr, "%d%%" % int(value * 100), bx + bw + 8, cy - 7.5, gfx.GRAY, alpha, 12,
                          "medium", width=35, align="right")

    # -- home ---------------------------------------------------------
    def home_columns(self, left, right, top, bottom):
        content_h = bottom - top
        cam = settings["show_mirror"] and self.camera_expanded
        cal = settings["show_calendar"]
        spacing = 10 if (cam and cal) else 15
        mus_right = right
        cam_rect = cal_rect = None
        if cam:
            cam_rect = (right - content_h, top, content_h, content_h)
            mus_right = cam_rect[0] - spacing
        if cal:
            cw = 170 if cam else 215
            cal_rect = (mus_right - cw, top, cw, 120)
            mus_right = cal_rect[0] - spacing
        art = max(40.0, min(content_h - 10, (mus_right - left) * 0.45))
        return dict(mus=(left, top, mus_right - left, content_h), art=art, cal=cal_rect, cam=cam_rect)

    def _draw_home(self, cr, left, right, top, bottom, alpha, interactive=True):
        cols = self.home_columns(left, right, top, bottom)
        mx, my, mw, mh = cols["mus"]
        art = cols["art"]
        ctrl_x = mx + art + 10 + 8
        self._draw_music_controls(cr, ctrl_x, my, mx + mw - ctrl_x, mh, alpha, interactive)
        if cols["cal"]:
            self._draw_calendar(cr, *cols["cal"], alpha=alpha, interactive=interactive)
        if cols["cam"]:
            self._draw_camera(cr, *cols["cam"], alpha=alpha)

    def _art_rects(self, x0, W, ch):
        s = max(0.0, ch - 12)
        closed = (x0 + 14, (ch - s) / 2.0, s, s)
        cx = WIN_W / 2.0
        left, right, _hh, top, bottom = self.open_layout(cx, ch)
        cols = self.home_columns(left, right, top, bottom)
        A = cols["art"]
        opened = (left + 5, top + 5, A, A)
        return closed, opened

    def _draw_shared_art(self, cr, x0, W, ch, kind, closed_a, content_a, open_p):
        m = self.media
        music_a = self.anim.peek("kind:music") * closed_a
        view = self.coord.current_view if settings["ded_shelf"] else "home"
        home_a = content_a * self.anim.peek("view:home", 1.0 if view == "home" else 0.0)
        alpha = max(music_a, home_a)
        if alpha <= 0.003:
            return
        closed, opened = self._art_rects(x0, W, ch)
        p = clamp(open_p, -0.2, 1.2)
        x = lerp(closed[0], opened[0], p)
        y = lerp(closed[1], opened[1], p)
        size = lerp(closed[2], opened[2], p)
        radius = lerp(ART_R[1], ART_R[0] if settings["corner_radius_scaling"] else ART_R[1], clamp(open_p))
        playing = m.playing
        scale = self.anim.get("art_scale", 1.0 if (playing or not self.is_open) else 0.85, SMOOTH)
        if self.is_open or open_p > 0.02:
            # content transition scale (0.8 -> 1, anchor top) applies to the open art too
            sc = 0.8 + 0.2 * content_a
            cxw = WIN_W / 2.0
            ox, oy = cxw + (x - cxw) * sc, y * sc
            x, y, size = lerp(x, ox, open_p), lerp(y, oy, open_p), lerp(size, size * sc, open_p)
        ccx, ccy = x + size / 2.0, y + size / 2.0
        # lighting effect behind the album art
        if settings["lighting_effect"] and m.art is not None:
            glow_a = self.anim.get("glow", 0.5 if playing else 0.0, SMOOTH) * home_a
            if glow_a > 0.003:
                gfx.draw_blur_glow(cr, m.art, ccx, ccy, size * 1.3 * 1.5, size * 1.4 * 1.5, glow_a)
        s2 = size * scale
        ax, ay = ccx - s2 / 2.0, ccy - s2 / 2.0
        if m.art is not None:
            gfx.draw_image(cr, m.art, ax, ay, s2, s2, radius * scale, alpha)
        else:
            gfx.fill_rounded(cr, ax, ay, s2, s2, radius * scale, (0.16, 0.16, 0.17), alpha)
            gfx.draw_icon(cr, "music.note", ccx, ccy, max(6.0, s2 * 0.38), gfx.GRAY, alpha)
        # dark overlay when paused
        dark = self.anim.get("art_dark", 0.0 if playing else 0.8, SMOOTH) * home_a
        if dark > 0.003:
            g = cairo.RadialGradient(ccx, ccy, s2 * 0.15, ccx, ccy, s2 * 0.75)
            g.add_color_stop_rgba(0, 0, 0, 0, dark * 0.62)
            g.add_color_stop_rgba(1, 0, 0, 0, 0)
            cr.set_source(g)
            cr.rectangle(ccx - s2, ccy - s2, s2 * 2, s2 * 2)
            cr.fill()
        # app icon badge (open only)
        if self.is_open and not m.art_is_app_icon and m.app_icon is not None and home_a > 0.01:
            badge = 30 * scale
            bx = ax + s2 + 10 * scale - badge
            by = ay + s2 + 10 * scale - badge
            cr.save()
            sw = m.app_icon.get_width()
            cr.translate(bx, by)
            cr.scale(badge / sw, badge / sw)
            Gdk.cairo_set_source_pixbuf(cr, m.app_icon, 0, 0)
            cr.paint_with_alpha(home_a)
            cr.restore()
        if self.is_open and home_a > 0.5:
            self.add_hit(ax, ay, s2, s2, "art", click=lambda e: self.media.open_player())

    def _draw_music_controls(self, cr, x, y, w, h, alpha, interactive):
        m = self.media
        tint = settings["player_color_tinting"]
        sub_color = gfx.ensure_min_brightness(m.avg_color, 0.6) if tint else gfx.GRAY
        tx = x + 5
        tw = w - 5
        ty = y + 10
        title = m.title or ("Nothing playing" if not m.identity else m.identity)
        self.marquee(cr, "title", title, tx, ty, tw, 13, "semibold", gfx.WHITE, alpha)
        self.marquee(cr, "artist", m.artist, tx, ty + 16, tw, 13, "medium", sub_color, alpha)
        sy = ty + 32
        if settings["enable_lyrics"]:
            line = m.lyric_line(m.position())
            la = self.anim.get("lyrics_a", 1.0 if m.playing else 0.0, SMOOTH)
            self.marquee(cr, "lyrics", line, tx, sy, tw, 11, "regular",
                         gfx.GRAY if not m.fetching_lyrics else gfx.GRAY + (0.7,), alpha * la)
            sy += 14
            if m.playing:
                self.want_fps(4)
        # slider (MusicSliderView)
        sy += 4 + 5
        self._draw_music_slider(cr, tx, sy, tw, alpha, sub_color)
        # slot toolbar
        self._draw_slots(cr, x, y + h - 40, w, alpha)

    def _draw_music_slider(self, cr, x, y, w, alpha, sub_color):
        m = self.media
        key = "music_slider"
        dragging = self.drag_cb is not None and self.pressed is not None and self.pressed.key == key
        dur = max(0.0, m.duration)
        if dragging:
            value = self._slider_value
        else:
            value = m.position()
            if getattr(self, "_seek_hold", 0) > self.now:
                value = self._slider_value
        if m.playing and self.is_open:
            self.want_fps(10)
        h = self.anim.get(key + ":h", 9 if dragging else 5, (0.35, 0.7))
        cy = y + 5
        gfx.capsule(cr, x, cy - h / 2.0, w, h)
        gfx.set_color(cr, TRACK, alpha)
        cr.fill()
        sc = settings["slider_color"]
        color = gfx.WHITE
        if sc == "albumArt":
            color = gfx.ensure_min_brightness(m.avg_color, 0.8)
        elif sc == "accent":
            color = settings.accent()
        prog = clamp(value / dur) if dur > 0 else 0.0
        if prog * w > 0.5:
            cr.save()
            gfx.capsule(cr, x, cy - h / 2.0, w, h)
            cr.clip()
            cr.rectangle(x, cy - h / 2.0, prog * w, h)
            gfx.set_color(cr, color, alpha)
            cr.fill()
            cr.restore()
        times_color = sub_color if settings["player_color_tinting"] else gfx.GRAY
        ty = y + 10 + 6
        gfx.draw_text(cr, _fmt_time(value), x, ty, times_color, alpha, 10, "medium")
        dtext = _fmt_time(dur)
        dw, _ = gfx.text_size(cr, dtext, 10, "medium")
        gfx.draw_text(cr, dtext, x + w - dw, ty, times_color, alpha, 10, "medium")

        def drag(px, _py):
            if dur > 0 and m.can_seek:
                self._slider_value = clamp((px - x) / w) * dur

        def release(_px, _py):
            if dur > 0 and m.can_seek:
                self.media.seek_to(self._slider_value)
                self._seek_hold = self.now + 1.0
        self.add_hit(x, cy - 7, w, 14, key, drag=drag, release=release)

    def _draw_slots(self, cr, x, y, w, alpha):
        m = self.media
        slots = list(settings["music_control_slots"])[:7]
        while len(slots) < 3:
            slots.append("none")
        cam = settings["show_calendar"] and settings["show_mirror"] and self.camera_expanded
        if cam and len(slots) >= 5:
            slots = slots[1:-1]
        items = [s for s in slots if s != "none"]
        if not items:
            return
        sizes = [40 if s == "playPause" else (24 + 4 + (48 if self._vol_open else 0) if s == "volume" else 30)
                 for s in items]
        total = sum(sizes) + 6 * (len(items) - 1)
        bx = x + (w - total) / 2.0
        cy = y + 20
        for slot, size in zip(items, sizes):
            if slot == "playPause":
                self.hover_button(cr, "pp", "pause.fill" if m.playing else "play.fill", bx, cy, 40, 26,
                                  gfx.WHITE, alpha, self.media.play_pause)
            elif slot == "previous":
                self.hover_button(cr, "prev", "backward.fill", bx, cy, 30, 13, gfx.WHITE, alpha, self.media.previous)
            elif slot == "next":
                self.hover_button(cr, "next", "forward.fill", bx, cy, 30, 13, gfx.WHITE, alpha, self.media.next)
            elif slot == "shuffle":
                self.hover_button(cr, "shuffle", "shuffle", bx, cy, 30, 13,
                                  gfx.RED if m.shuffle else gfx.WHITE, alpha, self.media.toggle_shuffle)
            elif slot == "repeatMode":
                self.hover_button(cr, "repeat", "repeat.1" if m.repeat == "one" else "repeat", bx, cy, 30, 13,
                                  gfx.RED if m.repeat != "off" else gfx.WHITE, alpha, self.media.toggle_repeat)
            elif slot == "goBackward":
                self.hover_button(cr, "back15", "gobackward.15", bx, cy, 30, 13, gfx.WHITE, alpha,
                                  lambda: self.media.skip(-15))
            elif slot == "goForward":
                self.hover_button(cr, "fwd15", "goforward.15", bx, cy, 30, 13, gfx.WHITE, alpha,
                                  lambda: self.media.skip(15))
            elif slot == "favorite":
                self.hover_button(cr, "fav", "heart", bx, cy, 30, 13, gfx.WHITE, alpha * 0.35, None)
            elif slot == "volume":
                self._draw_volume_slot(cr, bx, cy, alpha)
            bx += size + 6

    _vol_open = False

    def _draw_volume_slot(self, cr, x, cy, alpha):
        m = self.media
        v = m.volume
        icon = ("speaker.slash" if not m.volume_supported else "speaker.slash.fill" if v == 0 else
                "speaker.wave.1.fill" if v < 0.33 else "speaker.wave.2.fill" if v < 0.66 else "speaker.wave.3.fill")
        gfx.draw_icon(cr, icon, x + 12, cy, 14, gfx.WHITE if m.volume_supported else gfx.GRAY, alpha,
                      weight="medium")

        def toggle(_e):
            if m.volume_supported:
                NotchWindow._vol_open = not NotchWindow._vol_open
                self.queue()
        self.add_hit(x, cy - 15, 24, 30, "vol_btn", click=toggle)
        if self._vol_open and m.volume_supported:
            bx = x + 28
            gfx.capsule(cr, bx, cy - 2.5, 48, 5)
            gfx.set_color(cr, TRACK, alpha)
            cr.fill()
            if v > 0:
                gfx.capsule(cr, bx, cy - 2.5, max(5.0, 48 * v), 5)
                gfx.set_color(cr, gfx.WHITE, alpha)
                cr.fill()

            def drag(px, _py):
                m.set_volume(clamp((px - bx) / 48.0))
            self.add_hit(bx - 2, cy - 8, 52, 16, "vol_slider", drag=drag)

    def hover_button(self, cr, key, icon, x, cy, size, icon_size, color, alpha, action):
        """Hover button: transparent square with grey capsule on hover."""
        hov = self.anim.get("hov:" + key, 1.0 if self.hovered(key) and action else 0.0, (0.3, 1.0))
        if hov > 0.003:
            gfx.capsule(cr, x, cy - size / 2.0, size, size)
            gfx.set_color(cr, gfx.GRAY, 0.2 * hov * alpha)
            cr.fill()
        pressed = self.pressed is not None and self.pressed.key == key and self.hovered(key)
        gfx.draw_icon(cr, icon, x + size / 2.0, cy, icon_size, color, alpha * (0.55 if pressed else 1.0))
        if action is not None:
            self.add_hit(x, cy - size / 2.0, size, size, key, click=lambda e: action())

    # -- calendar -----------------------------------------------------------
    def _draw_calendar(self, cr, x, y, w, h, alpha, interactive):
        sel = self.cal_date
        today = dt.date.today()
        accent = settings.accent()
        month = sel.strftime("%b")
        year = sel.strftime("%Y")
        mw, mh = gfx.draw_text(cr, month, x, y, gfx.WHITE, alpha, 15, "semibold")
        yw, _ = gfx.draw_text(cr, year, x, y + mh, gfx.SECONDARY, alpha, 15, "light")
        col_w = max(mw, yw)
        # wheel picker
        wx = x + col_w + 8
        ww = x + w - wx
        cell = 28.0
        past, future = 7, 14
        base = today - dt.timedelta(days=past)
        sel_index = (sel - base).days
        center_off = self.anim.get("cal_scroll", sel_index * cell, SMOOTH)
        cr.save()
        cr.rectangle(wx, y - 2, ww, 54)
        cr.clip()
        origin = wx + ww / 2.0 - cell / 2.0 - center_off
        for i in range(past + future + 1):
            cx = origin + i * cell
            if cx + cell < wx - cell or cx > wx + ww + cell:
                continue
            d = base + dt.timedelta(days=i)
            is_sel = d == sel
            is_today = d == today
            if is_sel:
                bg = gfx.mix(accent, (0, 0, 0), 0.55)
                gfx.fill_rounded(cr, cx, y, cell, 50, 8, bg, alpha)
            gfx.draw_text(cr, d.strftime("%a"), cx, y + 4, gfx.WHITE if is_sel else gfx.SECONDARY, alpha,
                          10, width=cell, align="center", ellipsize=False)
            ccy = y + 4 + 12 + 8 + 10
            if is_today:
                cr.arc(cx + cell / 2.0, ccy, 10, 0, 2 * math.pi)
                gfx.set_color(cr, accent, alpha)
                cr.fill()
            num_col = gfx.WHITE if is_sel else ((0.9, 0.9, 0.9) if is_today else gfx.SECONDARY)
            gfx.draw_text(cr, str(d.day), cx, ccy - 8, num_col, alpha, 13, "medium", width=cell,
                          align="center", ellipsize=False)
            if interactive:
                self.add_hit(cx, y, cell, 50, "day:%d" % i, click=lambda e, dd=d: self._select_day(dd),
                             scroll=self._cal_wheel_scroll)
        cr.restore()
        # edge fades
        for fx, a0, a1 in ((wx, 1, 0), (wx + ww - 20, 0, 1)):
            g = cairo.LinearGradient(fx, 0, fx + 20, 0)
            g.add_color_stop_rgba(0, 0, 0, 0, a0 * alpha)
            g.add_color_stop_rgba(1, 0, 0, 0, a1 * alpha)
            cr.rectangle(fx, y - 2, 20, 54)
            cr.set_source(g)
            cr.fill()
        if interactive:
            self.add_hit(wx, y, ww, 50, "wheel", scroll=self._cal_wheel_scroll)
        # events
        ey = y + 56
        eh = y + h - ey
        events = self._filtered_events(sel)
        if not events:
            self._draw_empty_events(cr, x, ey, w, eh, sel == today, alpha)
        else:
            self._draw_event_list(cr, x, ey, w, eh, events, alpha, interactive)
        if interactive:
            mx, my = self.mouse
            self.hovering_calendar = x <= mx <= x + w and y <= my <= y + h

    def _select_day(self, d):
        self.cal_date = d
        self.cal_list_scroll = -1
        self.queue()

    def _cal_wheel_scroll(self, dx, dy, smooth):
        step = dx if abs(dx) > abs(dy) else dy
        acc = getattr(self, "_wheel_acc", 0.0) + (step if smooth else step * 3)
        today = dt.date.today()
        moved = False
        while abs(acc) >= 2.5:
            nd = self.cal_date + dt.timedelta(days=1 if acc > 0 else -1)
            if today - dt.timedelta(days=7) <= nd <= today + dt.timedelta(days=14):
                self.cal_date = nd
                moved = True
            acc -= 2.5 if acc > 0 else -2.5
        self._wheel_acc = acc
        if moved:
            self.cal_list_scroll = -1
        return True

    def _filtered_events(self, day):
        out = []
        for e in self.calendar.events_for(day):
            if e.is_reminder and e.completed and settings["hide_completed_reminders"]:
                continue
            if e.all_day and settings["hide_all_day_events"]:
                continue
            out.append(e)
        return out

    def _draw_empty_events(self, cr, x, y, w, h, is_today, alpha):
        cx = x + w / 2.0
        top = y + max(0.0, (h - 52) / 2.0)
        gfx.draw_icon(cr, "calendar", cx, top + 10, 20, gfx.SECONDARY, alpha)
        gfx.draw_text(cr, "No events today" if is_today else "No events", x, top + 23, gfx.WHITE, alpha, 11,
                      width=w, align="center")
        gfx.draw_text(cr, "Enjoy your free time!", x, top + 38, gfx.SECONDARY, alpha, 10, width=w,
                      align="center")

    def _draw_event_list(self, cr, x, y, w, h, events, alpha, interactive):
        now = dt.datetime.now().astimezone()
        full = settings["show_full_event_titles"]
        rows = []
        for e in events:
            lines = None if full else (1 if e.is_reminder else 2)
            layout = gfx.make_layout(cr, e.title, 12, "medium", width=w - 4 - 3 - 48 - 4,
                                     lines=lines or 6, wrap=True)
            _tw, th = gfx.layout_size(layout)
            rh = th + (13 if e.location and not e.is_reminder else 0)
            rh = max(rh, 28 if not e.all_day and not e.is_reminder else 16) + 8
            rows.append((e, rh))
        total = sum(r for _e, r in rows)
        self.cal_list_max = max(0.0, total - h)
        if self.cal_list_scroll < 0:
            target = 0.0
            if settings["auto_scroll_to_next_event"]:
                acc = 0.0
                pick = None
                for e, rh in rows:
                    if not e.all_day and e.end > now:
                        pick = acc
                        break
                    acc += rh
                target = pick if pick is not None else 0.0
            self.cal_list_scroll = min(target, self.cal_list_max)
        scroll = self.anim.get("cal_list", self.cal_list_scroll, SMOOTH)
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        ry = y - scroll
        for i, (e, rh) in enumerate(rows):
            if ry + rh < y or ry > y + h:
                ry += rh
                continue
            past = (e.end <= now if not e.all_day else False) and e.start.date() == now.date()
            ra = alpha * (0.6 if past else 1.0)
            if e.is_reminder:
                if e.completed:
                    ra = alpha * 0.4
                cr.arc(x + 7, ry + 4 + 7, 6, 0, 2 * math.pi)
                cr.set_line_width(2)
                gfx.set_color(cr, e.calendar.color, alpha)
                cr.stroke()
                if e.completed:
                    cr.arc(x + 7, ry + 4 + 7, 4, 0, 2 * math.pi)
                    cr.fill()
                tx = x + 22
                gfx.draw_text(cr, e.title, tx, ry + 4, gfx.WHITE, ra, 12, width=w - 22 - 50)
                when = "All-day" if e.all_day else e.start.strftime("%H:%M")
                gfx.draw_text(cr, when, x + w - 48, ry + 5, gfx.WHITE, ra, 10, "medium" if e.all_day else "regular",
                              width=48, align="right")
            else:
                gfx.fill_rounded(cr, x, ry + 4, 3, rh - 8, 1.5, e.calendar.color, ra)
                tx = x + 7
                tw = w - 7 - 48 - 4
                lines = None if full else 2
                _w, th = gfx.draw_text(cr, e.title, tx, ry + 4, gfx.WHITE, ra, 12, "medium", width=tw,
                                       lines=lines or 6)
                if e.location:
                    gfx.draw_text(cr, e.location, tx, ry + 4 + th, gfx.SECONDARY, ra, 10, width=tw)
                if e.all_day:
                    gfx.draw_text(cr, "All-day", x + w - 48, ry + 4, gfx.WHITE, ra, 10, "medium", width=48,
                                  align="right")
                else:
                    gfx.draw_text(cr, e.start.strftime("%H:%M"), x + w - 48, ry + 4, gfx.WHITE, ra, 10,
                                  width=48, align="right")
                    gfx.draw_text(cr, e.end.strftime("%H:%M"), x + w - 48, ry + 17, gfx.SECONDARY, ra, 10,
                                  width=48, align="right")
            if i < len(rows) - 1:
                cr.rectangle(x + (22 if e.is_reminder else 7), ry + rh - 0.5, w - 7, 0.5)
                cr.set_source_rgba(0.56, 0.56, 0.58, 0.2 * alpha)
                cr.fill()
            if interactive:
                self.add_hit(x, max(y, ry), w, min(rh, y + h - ry), "event:%d" % i,
                             click=lambda ev, d=e: self._open_event(d), scroll=self._event_scroll)
            ry += rh
        cr.restore()
        if interactive:
            self.add_hit(x, y, w, h, "events", scroll=self._event_scroll)

    def _event_scroll(self, dx, dy, smooth):
        self.cal_list_scroll = clamp(self.cal_list_scroll + (dy * 12 if smooth else dy * 30), 0,
                                     self.cal_list_max)
        return True

    def _open_event(self, event):
        date = event.start.strftime("%Y-%m-%d")
        for cmd in (["gnome-calendar", "--date", date], ["xdg-open", "webcal://"]):
            if GLib.find_program_in_path(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                break

    # -- camera mirror --------------------------------------------------------
    def toggle_camera(self):
        if self.camera_expanded:
            self.camera_expanded = False
            self.webcam.stop()
        elif self.webcam.available:
            self.camera_expanded = True
        self.queue()

    def _draw_camera(self, cr, x, y, w, h, alpha):
        size = min(w, h)
        circle = settings["mirror_shape"] == "circle"
        r = size / 2.0 if circle else (12 if settings["corner_radius_scaling"] else ART_R[1])
        if self.webcam.running and self.webcam.frame is not None:
            frame = self.webcam.frame
            fw, fh = frame.get_width(), frame.get_height()
            sc = max(size / fw, size / fh)
            cr.save()
            gfx.rounded_rect(cr, x, y, size, size, r)
            cr.clip()
            cr.translate(x + size / 2.0, y + size / 2.0)
            cr.scale(sc, sc)
            cr.set_source_surface(frame, -fw / 2.0, -fh / 2.0)
            cr.paint_with_alpha(alpha)
            cr.restore()
        else:
            gfx.fill_rounded(cr, x, y, size, size, r, (20 / 255.0,) * 3, alpha)
            gfx.rounded_rect(cr, x + 0.5, y + 0.5, size - 1, size - 1, r)
            cr.set_source_rgba(1, 1, 1, 0.04 * alpha)
            cr.set_line_width(1)
            cr.stroke()
            gfx.draw_icon(cr, "video", x + size / 2.0, y + size / 2.0 - 8, size / 3.5, gfx.GRAY, alpha)
            gfx.draw_text(cr, "Mirror", x, y + size / 2.0 + size / 7.0, gfx.GRAY, alpha, 10, width=size,
                          align="center")

        def tap(_e):
            if self.webcam.running:
                self.webcam.stop()
            else:
                self.webcam.start()
        self.add_hit(x, y, size, size, "camera_view", click=tap)

    # -- shelf ---------------------------------------------------------
    def shelf_rects(self, left, right, top, bottom):
        h = bottom - top
        share = (left, top, h, h)
        panel = (left + h + 12, top, right - (left + h + 12), h)
        return share, panel

    def _draw_shelf(self, cr, left, right, top, bottom, alpha, interactive=True):
        share, panel = self.shelf_rects(left, right, top, bottom)
        accent = settings.accent()
        # -- FileShareView ---------------------------------------------------
        sx, sy, sw, sh = share
        targeted = self.drag_over and self.drop_zone == "share"
        g = cairo.LinearGradient(sx, sy, sx + sw, sy + sh)
        g.add_color_stop_rgba(0, 0.08, 0.08, 0.08, 0.35 * alpha)
        g.add_color_stop_rgba(1, 0.05, 0.05, 0.05, 0.20 * alpha)
        gfx.rounded_rect(cr, sx, sy, sw, sh, 12)
        cr.set_source(g)
        cr.fill()
        gfx.dashed_rounded(cr, sx + 1.5, sy + 1.5, sw - 3, sh - 3, 12, accent if targeted else gfx.WHITE,
                           (0.9 if targeted else 0.1) * alpha)
        provider = settings["quick_share_provider"]
        if provider not in SHARE_PROVIDERS:
            provider = SHARE_PROVIDERS[0]
        ccx, ccy = sx + sw / 2.0, sy + sh / 2.0 - 10
        cr.arc(ccx, ccy, 27.5, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, (0.11 if targeted else 0.09) * alpha)
        cr.fill()
        isc = self.anim.get("share_scale", 1.06 if targeted else 1.0, (0.36, 0.7))
        gfx.draw_icon(cr, "square.and.arrow.up", ccx, ccy - 1, 24 * isc, accent if targeted else gfx.GRAY, alpha)
        label = {"Copy to Clipboard": "Copy", "Show in Files": "Files"}.get(provider, provider)
        gfx.draw_text(cr, label, sx + 6, ccy + 27.5 + 5, gfx.WHITE, 0.8 * alpha, 13, "semibold", "rounded",
                      width=sw - 12, align="center")
        if interactive:
            self.add_hit(sx, sy, sw, sh, "share", click=lambda e: self._share_pick())
        # -- panel ----------------------------------------------------------
        px, py, pw, ph = panel
        ptarget = self.drag_over and self.drop_zone == "shelf"
        gfx.dashed_rounded(cr, px + 1.5, py + 1.5, pw - 3, ph - 3, 16, accent if ptarget else gfx.WHITE,
                           (0.9 if ptarget else 0.1) * alpha)
        if interactive:
            self.add_hit(px, py, pw, ph, "panel", click=lambda e: self._clear_selection(),
                         scroll=self._shelf_scroll)
        if self.shelf.empty:
            ccx, ccy = px + pw / 2.0, py + ph / 2.0
            gfx.draw_icon(cr, "tray.and.arrow.down.fill", ccx, ccy - 14, 20, gfx.WHITE, alpha)
            gfx.draw_text(cr, "Drop files here", px, ccy + 2, gfx.GRAY, alpha, 15, "medium", "rounded",
                          width=pw, align="center")
            return
        inner_x, inner_w = px + 16 - 8, pw - 32 + 16
        item_w, item_h, spacing = 115.0, 108.0, 8.0
        total = len(self.shelf.items) * (item_w + spacing) - spacing
        self.shelf_scroll_max = max(0.0, total - inner_w)
        self.shelf_scroll = clamp(self.shelf_scroll, 0, self.shelf_scroll_max)
        off = self.anim.get("shelf_scroll", self.shelf_scroll, QUICK)
        iy = py + (ph - item_h) / 2.0
        cr.save()
        gfx.rounded_rect(cr, px + 3, py + 3, pw - 6, ph - 6, 14)
        cr.clip()
        ix = inner_x - off
        for item in self.shelf.items:
            if ix + item_w >= px and ix <= px + pw:
                self._draw_shelf_item(cr, item, ix, iy, item_w, item_h, alpha, interactive)
            ix += item_w + spacing
        cr.restore()

    def _draw_shelf_item(self, cr, item, x, y, w, h, alpha, interactive):
        accent = settings.accent()
        selected = item.id in self.shelf.selected
        if selected:
            gfx.fill_rounded(cr, x, y, w, h, 12, accent + (0.15,), alpha)
            gfx.rounded_rect(cr, x + 0.5, y + 0.5, w - 1, h - 1, 12)
            gfx.set_color(cr, accent, 0.6 * alpha)
            cr.set_line_width(1)
            cr.stroke()
        thumb = item.thumbnail()
        ix, iy = x + (w - 56) / 2.0, y + 10
        if thumb is not None:
            tw, th = thumb.get_width(), thumb.get_height()
            sc = min(56.0 / tw, 56.0 / th)
            dw, dh = tw * sc, th * sc
            cr.save()
            gfx.rounded_rect(cr, ix + (56 - dw) / 2.0, iy + (56 - dh) / 2.0, dw, dh, 12 if item.kind == "file" and
                             thumb.get_has_alpha() is False else 0)
            cr.clip()
            cr.translate(ix + (56 - dw) / 2.0, iy + (56 - dh) / 2.0)
            cr.scale(sc, sc)
            Gdk.cairo_set_source_pixbuf(cr, thumb, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint_with_alpha(alpha)
            cr.restore()
        gfx.draw_text(cr, item.name, x + 5, iy + 56 + 2, gfx.WHITE, alpha, 12, "medium", width=w - 10,
                      align="center", lines=2)
        if interactive:
            self.add_hit(x, y, w, h, "item:" + item.id,
                         press=lambda e, i=item: self._item_press(i, e),
                         click=lambda e, i=item: self._item_click(i, e),
                         double=lambda e, i=item: self._open_items([i]),
                         right=lambda e, i=item: self._item_menu(i, e),
                         scroll=self._shelf_scroll)

    def _shelf_scroll(self, dx, dy, smooth):
        if self.shelf_scroll_max <= 0:
            return False  # nothing to scroll: let the close gesture through
        d = dx if abs(dx) > abs(dy) else dy
        self.shelf_scroll = clamp(self.shelf_scroll + (d * 25 if smooth else d * 60), 0, self.shelf_scroll_max)
        return True

    def _clear_selection(self):
        self.shelf.selected.clear()
        self.queue()

    def _item_press(self, item, event):
        self._press_item = (item.id, event.x, event.y, event)

    def _item_click(self, item, event):
        extend = bool(event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK))
        self.shelf.select(item.id, extend)
        self.queue()

    def _open_items(self, items):
        for item in items:
            try:
                Gio.AppInfo.launch_default_for_uri(item.uri, None)
            except GLib.Error as exc:
                print("dednotch: cannot open", item.uri, exc)

    def _item_menu(self, item, event):
        if item.id not in self.shelf.selected:
            self.shelf.select(item.id)
        items = self.shelf.selected_items() or [item]
        menu = Gtk.Menu()

        def add(label, cb, sensitive=True):
            mi = Gtk.MenuItem(label=label)
            mi.set_sensitive(sensitive)
            mi.connect("activate", lambda *_: cb())
            menu.append(mi)
            return mi
        add("Open", lambda: self._open_items(items))
        files = [i for i in items if i.kind != "link"]
        add("Show in Files", lambda: self._reveal(files[0]), sensitive=bool(files))
        add("Copy", lambda: _copy_items(items))
        add("Copy Path", lambda: _copy_text("\n".join(i.path if i.kind != "link" else i.value for i in items)))
        share = Gtk.MenuItem(label="Share")
        sub = Gtk.Menu()
        for prov in SHARE_PROVIDERS:
            mi = Gtk.MenuItem(label=prov)
            mi.connect("activate", lambda _m, p=prov: share_items(items, p))
            sub.append(mi)
        share.set_submenu(sub)
        menu.append(share)
        menu.append(Gtk.SeparatorMenuItem())
        add("Remove from Shelf", lambda: self.shelf.remove([i.id for i in items]))
        add("Clear Shelf", self.shelf.clear)
        self._popup_menu(menu, event)

    def _reveal(self, item):
        path = item.path
        if GLib.find_program_in_path("nautilus"):
            subprocess.Popen(["nautilus", "--select", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(os.path.dirname(path), None), None)

    def _share_pick(self):
        self.release_grab()
        self.prevent_close += 1
        dialog = Gtk.FileChooserNative.new("Share files", None, Gtk.FileChooserAction.OPEN, "_Share", "_Cancel")
        dialog.set_select_multiple(True)
        resp = dialog.run()
        uris = dialog.get_uris() if resp == Gtk.ResponseType.ACCEPT else []
        dialog.destroy()
        self.prevent_close -= 1
        if uris:
            from .shelf import ShelfItem
            items = [ShelfItem("file", GLib.filename_from_uri(u)[0]) for u in uris if u.startswith("file://")]
            share_items(items, settings["quick_share_provider"])

    # ------------------------------------------------------------------
    # marquee text
    # ------------------------------------------------------------------
    def marquee(self, cr, key, text, x, y, w, size, weight, color, alpha, min_duration=3.0, family="text"):
        if alpha <= 0.003 or w <= 2:
            return
        layout = gfx.make_layout(cr, text or "", size, weight, family, ellipsize=False)
        tw, th = gfx.layout_size(layout)
        if tw <= w:
            gfx.set_color(cr, color, alpha)
            cr.move_to(x, y)
            from gi.repository import PangoCairo
            PangoCairo.show_layout(cr, layout)
            self._marquee.pop(key, None)
            return
        st = self._marquee.get(key)
        if st is None or st[0] != text:
            st = (text, self.now)
            self._marquee[key] = st
        dur = (tw + 10) / 30.0
        period = min_duration + dur
        t = (self.now - st[1]) % period
        off = 0.0 if t < min_duration else (t - min_duration) / dur * (tw + 20)
        if t >= min_duration:
            self.want_fps(30)
        else:
            self.want_fps(2)
        from gi.repository import PangoCairo
        cr.save()
        cr.rectangle(x, y - 3, w, th + 6)
        cr.clip()
        gfx.set_color(cr, color, alpha)
        for dx in (0.0, tw + 20):
            cr.move_to(x - off + dx, y)
            PangoCairo.show_layout(cr, layout)
        cr.restore()

    # ------------------------------------------------------------------
    # menus & popovers
    # ------------------------------------------------------------------
    def _popup_menu(self, menu, event):
        self.release_grab()
        menu.show_all()
        self.prevent_close += 1

        def done(*_):
            self.prevent_close = max(0, self.prevent_close - 1)
            GLib.timeout_add(150, self._recheck_hover)
        menu.connect("deactivate", done)
        menu.attach_to_widget(self.area, None)
        menu.popup_at_pointer(event)

    def _recheck_hover(self):
        self._ptr_inside = not self._ptr_inside
        self._poll_pointer()
        return False

    def _open_settings(self):
        self.release_grab()
        self.app.show_settings()

    def _show_notch_menu(self, event):
        menu = Gtk.Menu()
        mi = Gtk.MenuItem(label="Settings")
        mi.connect("activate", lambda *_: self._open_settings())
        menu.append(mi)
        menu.append(Gtk.SeparatorMenuItem())
        q = Gtk.MenuItem(label="Quit DedNotch")
        q.connect("activate", lambda *_: self.app.quit())
        menu.append(q)
        self._popup_menu(menu, event)

    def toggle_battery_popover(self):
        if self.battery_popover is not None:
            self.battery_popover.destroy()
            self.battery_popover = None
            return
        x, _y, w, _h = self.notch_rect
        self.battery_popover = BatteryPopover(self, self.win_x + x + w - 290, self.win_y + OPEN_H - 4)

    # ------------------------------------------------------------------
    # drag and drop (ShelfDropService / DragDetector)
    # ------------------------------------------------------------------
    def _setup_dnd(self):
        self.area.drag_dest_set(0, [], Gdk.DragAction.COPY)
        targets = Gtk.TargetList.new([])
        targets.add_uri_targets(1)
        targets.add_text_targets(2)
        self.area.drag_dest_set_target_list(targets)
        self.area.connect("drag-motion", self._on_drag_motion)
        self.area.connect("drag-leave", self._on_drag_leave)
        self.area.connect("drag-drop", self._on_drag_drop)
        self.area.connect("drag-data-received", self._on_drag_data)
        self.area.connect("drag-data-get", self._on_drag_data_get)
        self.area.connect("drag-end", self._on_drag_end)
        self.area.connect("drag-begin", self._on_drag_begin)

    def _on_drag_motion(self, _w, ctx, x, y, time_):
        if self.internal_drag:
            Gdk.drag_status(ctx, 0, time_)
            return True
        if not settings["ded_shelf"]:
            return False
        if not self.drag_over:
            self.drag_over = True
            self.drop_event = False
            if self.state == "closed":
                self.coord.current_view = "shelf"
                self.open()
        if self.coord.current_view != "shelf":
            self.coord.current_view = "shelf"
        cxw = WIN_W / 2.0
        left, right, _hh, top, bottom = self.open_layout(cxw, self.effective_closed_height())
        share, panel = self.shelf_rects(left, right, top, bottom)
        zone = "shelf"
        if share[0] <= x <= share[0] + share[2] and share[1] <= y <= share[1] + share[3]:
            zone = "share"
        if zone != self.drop_zone:
            self.drop_zone = zone
        Gdk.drag_status(ctx, Gdk.DragAction.COPY, time_)
        self.queue()
        return True

    def _on_drag_leave(self, _w, _ctx, _time):
        self.drag_over = False
        self.queue()

        def later():
            if self.drop_event:
                self.drop_event = False
                return False
            if not self.drag_over:
                self.drop_zone = None
                if not self._ptr_inside:
                    self.close()
                self.queue()
            return False
        GLib.timeout_add(500, later)

    def _on_drag_drop(self, widget, ctx, _x, _y, time_):
        self.drop_event = True
        target = widget.drag_dest_find_target(ctx, None)
        if target is None or target.name() == "NONE":
            return False
        widget.drag_get_data(ctx, target, time_)
        return True

    def _on_drag_data(self, _w, ctx, _x, _y, data, _info, time_):
        zone = self.drop_zone or "shelf"
        uris = data.get_uris() or []
        text = data.get_text() if not uris else None
        ok = True
        if zone == "share":
            from .shelf import ShelfItem
            items = [ShelfItem("file", GLib.filename_from_uri(u)[0]) for u in uris if u.startswith("file://")]
            if not items and text:
                items = [ShelfItem("text", text)]
            share_items(items, settings["quick_share_provider"])
        elif uris:
            ok = self.shelf.add_uris(uris) or True
        elif text:
            ok = self.shelf.add_text(text)
        Gtk.drag_finish(ctx, bool(ok), False, time_)
        self.drop_zone = None
        self.drop_event = True
        self.queue()

    def _start_item_drag(self, item_id, event):
        if item_id not in self.shelf.selected:
            self.shelf.select(item_id)
        items = self.shelf.selected_items()
        if not items:
            return
        self.release_grab()
        self.internal_drag = [i.id for i in items]
        targets = Gtk.TargetList.new([])
        targets.add_uri_targets(0)
        if all(i.kind == "text" for i in items):
            targets.add_text_targets(1)
        self.drag_cb = None
        self.pressed = None
        self.area.drag_begin_with_coordinates(targets, Gdk.DragAction.COPY, 1, event, int(event.x), int(event.y))

    def _on_drag_begin(self, _w, ctx):
        if not self.internal_drag:
            return
        item = self.shelf.get(self.internal_drag[0])
        pb = item.thumbnail() if item else None
        if pb is not None:
            pb = pb.scale_simple(56, int(56 * pb.get_height() / max(1, pb.get_width())),
                                 GdkPixbuf.InterpType.BILINEAR)
            Gtk.drag_set_icon_pixbuf(ctx, pb, pb.get_width() // 2, pb.get_height() // 2)

    def _on_drag_data_get(self, _w, _ctx, data, info, _time):
        items = [self.shelf.get(i) for i in (self.internal_drag or [])]
        items = [i for i in items if i is not None]
        if info == 1 and items:
            data.set_text("\n".join(i.value for i in items), -1)
        else:
            data.set_uris([i.uri for i in items])

    def _on_drag_end(self, _w, ctx):
        ids = self.internal_drag or []
        self.internal_drag = None
        if ids and settings["auto_remove_shelf_items"]:
            self.shelf.remove(ids)
        GLib.timeout_add(150, self._recheck_hover)


# ---------------------------------------------------------------------------
# battery popover (BatteryMenuView)
# ---------------------------------------------------------------------------
class BatteryPopover(Gtk.Window):
    W = 280

    def __init__(self, notch, x, y):
        Gtk.Window.__init__(self, type=Gtk.WindowType.POPUP)
        self.notch = notch
        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.area = Gtk.DrawingArea()
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.add(self.area)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("motion-notify-event", lambda *a: self.area.queue_draw())
        self.lines = self._lines()
        self.H = 16 + 22 + 16 + len(self.lines) * 22 + 16 + 1 + 16 + 20 + 16
        self.resize(self.W, self.H)
        self.pos = (int(x), int(y))
        self.move(*self.pos)
        self.show_all()
        self._button = (0, 0, 0, 0)

    def contains_pointer(self, px, py):
        x, y = self.pos
        return x <= px <= x + self.W and y - 10 <= py <= y + self.H

    def _lines(self):
        b = self.notch.battery
        out = [("Max Capacity: %d%%" % int(b.max_capacity), None)]
        if b.low_power:
            out.append(("Low Power Mode", "bolt.fill"))
        if b.charging:
            out.append(("Charging", "bolt.fill"))
        if b.plugged_in:
            out.append(("Plugged In", "bolt.fill"))
        if b.time_to_full > 0:
            out.append(("Time to Full Charge: %d min" % b.time_to_full, "clock"))
        if not b.charging and b.plugged_in and b.level >= 80:
            out.append(("Charging on Hold: Desktop Mode", None))
        return out

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        gfx.fill_rounded(cr, 0.5, 0.5, self.W - 1, self.H - 1, 12, (0.12, 0.12, 0.13), 0.97)
        gfx.rounded_rect(cr, 0.5, 0.5, self.W - 1, self.H - 1, 12)
        cr.set_source_rgba(1, 1, 1, 0.1)
        cr.set_line_width(1)
        cr.stroke()
        b = self.notch.battery
        y = 16
        gfx.draw_text(cr, "Battery Status", 16, y, gfx.WHITE, 1, 13, "semibold")
        pct = "%d%%" % int(b.level)
        pw, _ = gfx.text_size(cr, pct, 13, "semibold")
        gfx.draw_text(cr, pct, self.W - 16 - pw, y, gfx.WHITE, 1, 13, "semibold")
        y += 22 + 16
        for text, icon in self.lines:
            x = 16
            if icon:
                gfx.draw_icon(cr, icon, x + 7, y + 7, 11, gfx.WHITE)
                x += 20
            gfx.draw_text(cr, text, x, y, gfx.WHITE, 1, 11)
            y += 22
        y += 8
        cr.rectangle(16, y, self.W - 32, 1)
        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.fill()
        y += 1 + 16
        label = "Battery Settings"
        lw, _ = gfx.text_size(cr, label, 13)
        bx = (self.W - lw - 22) / 2.0
        gfx.draw_icon(cr, "gearshape", bx + 7, y + 8, 13, gfx.WHITE)
        gfx.draw_text(cr, label, bx + 22, y, gfx.WHITE, 1, 13)
        self._button = (0, y - 8, self.W, 36)

    def _press(self, _w, event):
        x, y, w, h = self._button
        if y <= event.y <= y + h:
            subprocess.Popen(["gnome-control-center", "power"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            self.notch.toggle_battery_popover()
        return True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class _PointerEvent:
    def __init__(self, x, y):
        self.x, self.y = x, y
def _fmt_time(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def _copy_text(text):
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)


def _copy_items(items):
    files = [i for i in items if i.kind == "file"]
    if not files:
        _copy_text("\n".join(i.value for i in items))
        return
    # Nautilus-compatible clipboard: copy file URIs as text, which Files accepts on paste.
    uris = "\n".join(i.uri for i in files)
    clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    targets = [Gtk.TargetEntry.new("x-special/gnome-copied-files", 0, 0),
               Gtk.TargetEntry.new("text/uri-list", 0, 1),
               Gtk.TargetEntry.new("UTF8_STRING", 0, 2)]

    def get(_clip, sel, info, _data):
        if info == 0:
            sel.set(Gdk.Atom.intern("x-special/gnome-copied-files", False), 8, ("copy\n" + uris).encode())
        elif info == 1:
            sel.set_uris([i.uri for i in files])
        else:
            sel.set_text("\n".join(i.path for i in files), -1)
    clip.set_with_data(targets, get, lambda *a: None, None)
    clip.store()


def share_items(items, provider):
    if not items:
        return
    if provider == "Email" and GLib.find_program_in_path("xdg-email"):
        cmd = ["xdg-email"]
        for i in items:
            if i.kind == "link":
                cmd += ["--body", i.value]
            else:
                cmd += ["--attach", i.path]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif provider == "Bluetooth" and GLib.find_program_in_path("bluetooth-sendto"):
        subprocess.Popen(["bluetooth-sendto"] + [i.path for i in items if i.kind != "link"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif provider == "Show in Files":
        target = items[0]
        if target.kind != "link" and GLib.find_program_in_path("nautilus"):
            subprocess.Popen(["nautilus", "--select", target.path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    else:
        _copy_items(items)
