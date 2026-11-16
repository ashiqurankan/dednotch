"""Drawing helpers: fonts, SF-Symbols-style icons, shapes and images."""
import colorsys
import math
import os

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Pango, PangoCairo  # noqa: E402

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

WHITE = (1, 1, 1)
BLACK = (0, 0, 0)
GRAY = (0.557, 0.557, 0.576)          # SwiftUI .gray (dark)
SECONDARY = (0.65, 0.65, 0.65)        # Color(white: 0.65)
RED = (1.0, 0.27, 0.23)
GREEN = (0.19, 0.82, 0.35)
YELLOW = (1.0, 0.84, 0.04)

FAMILIES = {
    "text": "SF Pro Text, SF Pro, Inter, Cantarell, Ubuntu, Sans",
    "display": "SF Pro Display, SF Pro, Inter Display, Inter, Cantarell, Ubuntu, Sans",
    "rounded": "SF Pro Rounded, Nunito, Ubuntu, Cantarell, Sans",
}
WEIGHTS = {
    "ultralight": Pango.Weight.ULTRALIGHT, "thin": Pango.Weight.THIN,
    "light": Pango.Weight.LIGHT, "regular": Pango.Weight.NORMAL,
    "medium": Pango.Weight.MEDIUM, "semibold": Pango.Weight.SEMIBOLD,
    "bold": Pango.Weight.BOLD, "heavy": Pango.Weight.HEAVY,
}
# macOS SwiftUI text styles -> (pixel size, default weight)
STYLES = {
    "largeTitle": (26, "regular"), "title": (22, "regular"), "title2": (17, "regular"),
    "title3": (15, "regular"), "headline": (13, "semibold"), "body": (13, "regular"),
    "callout": (12, "regular"), "subheadline": (11, "regular"), "footnote": (10, "regular"),
    "caption": (10, "regular"), "caption2": (10, "regular"),
}

_FONT_OPTIONS = cairo.FontOptions()
_FONT_OPTIONS.set_antialias(cairo.ANTIALIAS_GRAY)
_FONT_OPTIONS.set_hint_style(cairo.HINT_STYLE_SLIGHT)
_FONT_OPTIONS.set_hint_metrics(cairo.HINT_METRICS_OFF)

_fd_cache = {}


def font_desc(size, weight="regular", family="text"):
    key = (size, weight, family)
    fd = _fd_cache.get(key)
    if fd is None:
        fd = Pango.FontDescription()
        fam = family
        if family == "text" and size >= 20:
            fam = "display"
        fd.set_family(FAMILIES.get(fam, fam))
        fd.set_weight(WEIGHTS.get(weight, Pango.Weight.NORMAL))
        fd.set_absolute_size(size * Pango.SCALE)
        _fd_cache[key] = fd
    return fd


def style(name, weight=None, family="text"):
    size, w = STYLES[name]
    return dict(size=size, weight=weight or w, family=family)


def make_layout(cr, text, size=13, weight="regular", family="text", width=None,
                ellipsize=True, align="left", lines=1, wrap=False):
    layout = PangoCairo.create_layout(cr)
    PangoCairo.context_set_font_options(layout.get_context(), _FONT_OPTIONS)
    layout.set_font_description(font_desc(size, weight, family))
    layout.set_text(text or "", -1)
    if width is not None:
        layout.set_width(max(1, int(width * Pango.SCALE)))
        if lines and lines > 1:
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_height(-lines)
        elif wrap:
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        if ellipsize:
            layout.set_ellipsize(ellipsize if isinstance(ellipsize, Pango.EllipsizeMode)
                                 else Pango.EllipsizeMode.END)
    layout.set_alignment({"left": Pango.Alignment.LEFT, "center": Pango.Alignment.CENTER,
                          "right": Pango.Alignment.RIGHT}[align])
    return layout


def layout_size(layout):
    _ink, logical = layout.get_pixel_extents()
    return logical.width, logical.height


def text_size(cr, text, size=13, weight="regular", family="text"):
    return layout_size(make_layout(cr, text, size, weight, family, ellipsize=False))


def draw_text(cr, text, x, y, color=WHITE, alpha=1.0, size=13, weight="regular",
              family="text", width=None, align="left", lines=1, valign="top", height=None,
              ellipsize=True):
    """Draw text with its logical box's top-left at (x, y). Returns (w, h)."""
    layout = make_layout(cr, text, size, weight, family, width, ellipsize, align, lines)
    w, h = layout_size(layout)
    if valign == "center" and height is not None:
        y = y + (height - h) / 2.0
    elif valign == "bottom" and height is not None:
        y = y + height - h
    set_color(cr, color, alpha)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)
    return w, h


def set_color(cr, color, alpha=1.0):
    if len(color) == 4:
        cr.set_source_rgba(color[0], color[1], color[2], color[3] * alpha)
    else:
        cr.set_source_rgba(color[0], color[1], color[2], alpha)


# ---------------------------------------------------------------------------
# Icons.  Uses the SF Symbols glyphs embedded in Apple's "SF Pro" font when it
# is installed (macOS look); otherwise falls back to the
# freedesktop symbolic icon theme.
# ---------------------------------------------------------------------------
SF = {
    "play.fill": 0x284, "pause.fill": 0x286, "backward.fill": 0x28A, "forward.fill": 0x28C,
    "shuffle": 0x29D, "repeat": 0x29E, "repeat.1": 0x29F,
    "speaker.fill": 0x2A1, "speaker.slash": 0x2A2, "speaker.slash.fill": 0x2A3,
    "speaker.wave.1.fill": 0x2A5, "speaker.wave.2.fill": 0x2A7, "speaker.wave.3.fill": 0x2A9,
    "mic.fill": 0x2B1, "mic.slash.fill": 0x2B3, "heart": 0x2B4, "heart.fill": 0x2B5,
    "bolt.fill": 0x2E5, "eye": 0x2ED, "square.and.arrow.up": 0x202,
    "tray.fill": 0x224, "tray.and.arrow.down.fill": 0x228, "house.fill": 0x39F,
    "gearshape": 0x8CB, "gearshape.fill": 0x8CC, "video.fill": 0x34A, "video": 0x349,
    "calendar": 0x249, "music.note": 0x46A, "gobackward.15": 0x384, "goforward.15": 0x383,
    "sun.min.fill": 0x1AC, "sun.max.fill": 0x1AD + 1, "sun.max": 0x1AD,
    "keyboard": 0x1F3, "info.circle": 0x174, "xmark": 0x184, "trash": 0x211,
    "folder": 0x215, "doc": 0x237, "link": 0x263, "clock": 0x42B,
}
FALLBACK = {
    "play.fill": "media-playback-start-symbolic", "pause.fill": "media-playback-pause-symbolic",
    "backward.fill": "media-skip-backward-symbolic", "forward.fill": "media-skip-forward-symbolic",
    "shuffle": "media-playlist-shuffle-symbolic", "repeat": "media-playlist-repeat-symbolic",
    "repeat.1": "media-playlist-repeat-song-symbolic",
    "speaker.fill": "audio-volume-low-symbolic", "speaker.slash": "audio-volume-muted-symbolic",
    "speaker.slash.fill": "audio-volume-muted-symbolic",
    "speaker.wave.1.fill": "audio-volume-low-symbolic",
    "speaker.wave.2.fill": "audio-volume-medium-symbolic",
    "speaker.wave.3.fill": "audio-volume-high-symbolic",
    "mic.fill": "audio-input-microphone-symbolic", "mic.slash.fill": "microphone-disabled-symbolic",
    "heart": "non-starred-symbolic", "heart.fill": "starred-symbolic",
    "bolt.fill": "battery-full-charging-symbolic", "eye": "view-reveal-symbolic",
    "square.and.arrow.up": "send-to-symbolic", "tray.fill": "mail-inbox-symbolic",
    "tray.and.arrow.down.fill": "document-save-symbolic", "house.fill": "go-home-symbolic",
    "gearshape": "emblem-system-symbolic", "gearshape.fill": "emblem-system-symbolic",
    "video.fill": "camera-web-symbolic", "video": "camera-web-symbolic",
    "calendar": "x-office-calendar-symbolic", "music.note": "audio-x-generic-symbolic",
    "gobackward.15": "media-seek-backward-symbolic", "goforward.15": "media-seek-forward-symbolic",
    "sun.min.fill": "display-brightness-symbolic", "sun.max.fill": "display-brightness-symbolic",
    "sun.max": "display-brightness-symbolic", "keyboard": "input-keyboard-symbolic",
    "info.circle": "help-about-symbolic", "xmark": "window-close-symbolic",
    "trash": "user-trash-symbolic", "folder": "folder-symbolic", "doc": "text-x-generic-symbolic",
    "link": "insert-link-symbolic", "clock": "alarm-symbolic",
}
_sf_available = None
_icon_cache = {}


def sf_available():
    global _sf_available
    if _sf_available is None:
        try:
            fams = [f.get_name() for f in PangoCairo.FontMap.get_default().list_families()]
            if "SF Pro" not in fams:
                _sf_available = False
            else:
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4)
                layout = PangoCairo.create_layout(cairo.Context(surf))
                layout.set_font_description(Pango.FontDescription("SF Pro 12"))
                layout.set_text(chr(0x100000 + SF["play.fill"]), -1)
                _sf_available = layout.get_unknown_glyphs_count() == 0
        except Exception:
            _sf_available = False
    return _sf_available


def _sf_layout(cr, name, size, weight):
    layout = PangoCairo.create_layout(cr)
    PangoCairo.context_set_font_options(layout.get_context(), _FONT_OPTIONS)
    fd = Pango.FontDescription()
    fd.set_family("SF Pro")
    fd.set_weight(WEIGHTS.get(weight, Pango.Weight.NORMAL))
    fd.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(fd)
    layout.set_text(chr(0x100000 + SF[name]), -1)
    return layout


def icon_extent(cr, name, size, weight="regular"):
    if sf_available() and name in SF:
        ink, _ = _sf_layout(cr, name, size, weight).get_pixel_extents()
        return ink.width, ink.height
    px = int(round(size * 1.15))
    return px, px


def draw_icon(cr, name, cx, cy, size=13, color=WHITE, alpha=1.0, weight="regular"):
    """Draw icon `name` centred at (cx, cy). `size` is the symbol point size."""
    if alpha <= 0.002:
        return
    if sf_available() and name in SF:
        layout = _sf_layout(cr, name, size, weight)
        ink, _ = layout.get_pixel_extents()
        set_color(cr, color, alpha)
        cr.move_to(cx - ink.x - ink.width / 2.0, cy - ink.y - ink.height / 2.0)
        PangoCairo.show_layout(cr, layout)
        return
    px = int(round(size * 1.15))
    key = (name, px, tuple(round(c, 3) for c in color[:3]))
    pb = _icon_cache.get(key)
    if pb is None:
        pb = False
        try:
            theme = Gtk.IconTheme.get_default()
            info = theme.lookup_icon(FALLBACK.get(name, name), px, Gtk.IconLookupFlags.FORCE_SIZE)
            if info:
                rgba = Gdk.RGBA(color[0], color[1], color[2], 1.0)
                pb, _sym = info.load_symbolic(rgba, None, None, None)
        except Exception:
            pb = False
        _icon_cache[key] = pb
    if pb:
        cr.save()
        Gdk.cairo_set_source_pixbuf(cr, pb, cx - pb.get_width() / 2.0, cy - pb.get_height() / 2.0)
        cr.paint_with_alpha(alpha)
        cr.restore()


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------
def _quad(cr, x0, y0, qx, qy, x1, y1):
    cr.curve_to(x0 + 2.0 / 3.0 * (qx - x0), y0 + 2.0 / 3.0 * (qy - y0),
                x1 + 2.0 / 3.0 * (qx - x1), y1 + 2.0 / 3.0 * (qy - y1), x1, y1)


def notch_path(cr, x, y, w, h, top_r, bottom_r):
    """Notch outline: flared top corners, round bottom."""
    min_x, max_x, min_y, max_y = x, x + w, y, y + h
    bottom_r = max(0.0, min(bottom_r, (w - 2 * top_r) / 2.0, h - top_r if h > top_r else 0.0))
    cr.new_path()
    cr.move_to(min_x, min_y)
    _quad(cr, min_x, min_y, min_x + top_r, min_y, min_x + top_r, min_y + top_r)
    cr.line_to(min_x + top_r, max_y - bottom_r)
    _quad(cr, min_x + top_r, max_y - bottom_r, min_x + top_r, max_y, min_x + top_r + bottom_r, max_y)
    cr.line_to(max_x - top_r - bottom_r, max_y)
    _quad(cr, max_x - top_r - bottom_r, max_y, max_x - top_r, max_y, max_x - top_r, max_y - bottom_r)
    cr.line_to(max_x - top_r, min_y + top_r)
    _quad(cr, max_x - top_r, min_y + top_r, max_x - top_r, min_y, max_x, min_y)
    cr.close_path()


def rounded_rect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_path()
    if r <= 0:
        cr.rectangle(x, y, w, h)
        return
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def capsule(cr, x, y, w, h):
    rounded_rect(cr, x, y, w, h, min(w, h) / 2.0)


def fill_rounded(cr, x, y, w, h, r, color, alpha=1.0):
    rounded_rect(cr, x, y, w, h, r)
    set_color(cr, color, alpha)
    cr.fill()


def dashed_rounded(cr, x, y, w, h, r, color, alpha, width=3, dash=10):
    rounded_rect(cr, x, y, w, h, r)
    cr.save()
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_dash([dash, dash])
    set_color(cr, color, alpha)
    cr.stroke()
    cr.restore()


def soft_shadow(cr, path_fn, radius, alpha):
    """Approximate a gaussian drop shadow around the current shape."""
    if alpha <= 0.002:
        return
    steps = max(3, int(radius))
    cr.save()
    for i in range(steps, 0, -1):
        path_fn()
        cr.set_line_width(i * 2.0 * radius / steps)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(0, 0, 0, alpha / steps * 0.9)
        cr.stroke()
    cr.restore()


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
class ImageCache:
    def __init__(self, limit=48):
        self._cache = {}
        self._limit = limit

    def surface(self, pixbuf, w, h, square=True):
        w, h = max(1, int(round(w))), max(1, int(round(h)))
        key = (id(pixbuf), w, h, square)
        surf = self._cache.get(key)
        if surf is None:
            pb = pixbuf
            if square:
                pw, ph = pb.get_width(), pb.get_height()
                side = min(pw, ph)
                if pw != ph:
                    pb = pb.new_subpixbuf((pw - side) // 2, (ph - side) // 2, side, side)
            pb = pb.scale_simple(w, h, GdkPixbuf.InterpType.HYPER if w < pb.get_width()
                                 else GdkPixbuf.InterpType.BILINEAR)
            surf = Gdk.cairo_surface_create_from_pixbuf(pb, 1, None)
            if len(self._cache) > self._limit:
                self._cache.clear()
            self._cache[key] = (surf, pixbuf)  # keep pixbuf alive so id() stays unique
            return surf
        return surf[0]


image_cache = ImageCache()


def draw_image(cr, pixbuf, x, y, w, h, radius=0.0, alpha=1.0):
    if pixbuf is None or alpha <= 0.002 or w < 1 or h < 1:
        return
    # Render at the integer size closest to the final size to keep it crisp.
    surf = image_cache.surface(pixbuf, w, h)
    sw, sh = surf.get_width(), surf.get_height()
    cr.save()
    rounded_rect(cr, x, y, w, h, radius)
    cr.clip()
    cr.translate(x, y)
    cr.scale(w / sw, h / sh)
    cr.set_source_surface(surf, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint_with_alpha(alpha)
    cr.restore()


_blur_cache = {}


def blurred_surface(pixbuf):
    """A tiny version of the image; upscaled with bilinear filtering it reads as
    a heavy gaussian blur (used for the album-art glow)."""
    key = id(pixbuf)
    surf = _blur_cache.get(key)
    if surf is None:
        side = min(pixbuf.get_width(), pixbuf.get_height())
        pb = pixbuf.new_subpixbuf((pixbuf.get_width() - side) // 2,
                                  (pixbuf.get_height() - side) // 2, side, side)
        pb = pb.scale_simple(6, 6, GdkPixbuf.InterpType.HYPER)
        surf = Gdk.cairo_surface_create_from_pixbuf(pb, 1, None)
        if len(_blur_cache) > 8:
            _blur_cache.clear()
        _blur_cache[key] = (surf, pixbuf)
        return surf
    return surf[0]


def draw_blur_glow(cr, pixbuf, cx, cy, w, h, alpha, rotate=math.radians(92)):
    if pixbuf is None or alpha <= 0.002:
        return
    surf = blurred_surface(pixbuf)
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(rotate)
    cr.scale(w / 6.0, h / 6.0)
    cr.translate(-3, -3)
    pat = cairo.SurfacePattern(surf)
    pat.set_filter(cairo.FILTER_BILINEAR)
    pat.set_extend(cairo.EXTEND_PAD)
    cr.set_source(pat)
    # fade the edges with a radial mask so it looks like a 40px blur
    mask = cairo.RadialGradient(3, 3, 0.5, 3, 3, 4.6)
    mask.add_color_stop_rgba(0, 0, 0, 0, alpha)
    mask.add_color_stop_rgba(0.6, 0, 0, 0, alpha * 0.6)
    mask.add_color_stop_rgba(1, 0, 0, 0, 0)
    cr.mask(mask)
    cr.restore()


def average_color(pixbuf):
    try:
        pb = pixbuf.scale_simple(12, 12, GdkPixbuf.InterpType.BILINEAR)
        data = pb.get_pixels()
        n = pb.get_n_channels()
        stride = pb.get_rowstride()
        r = g = b = count = 0
        for y in range(12):
            for x in range(12):
                i = y * stride + x * n
                if n == 4 and data[i + 3] < 32:
                    continue
                r += data[i]
                g += data[i + 1]
                b += data[i + 2]
                count += 1
        if not count:
            return WHITE
        return (r / count / 255.0, g / count / 255.0, b / count / 255.0)
    except Exception:
        return WHITE


def ensure_min_brightness(color, factor):
    h, s, v = colorsys.rgb_to_hsv(*color[:3])
    if v < factor:
        v = factor
    return colorsys.hsv_to_rgb(h, s, v)


def load_asset_pixbuf(name, w=None, h=None):
    path = os.path.join(ASSETS, name)
    try:
        if w and h:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, w, h, True)
        return GdkPixbuf.Pixbuf.new_from_file(path)
    except Exception:
        return None


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
