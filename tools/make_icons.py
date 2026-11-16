"""Generates dednotch.png (app icon), tray.png (top-bar icon) and the
bolt.png / plug.png battery status glyphs."""
import os
import sys

import cairo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dednotch import gfx  # noqa: E402

OUT = gfx.ASSETS


def app_icon(size=256):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(s)
    k = size / 256.0
    cr.scale(k, k)
    gfx.rounded_rect(cr, 16, 16, 224, 224, 52)
    g = cairo.LinearGradient(0, 16, 0, 240)
    g.add_color_stop_rgb(0, 0.20, 0.21, 0.26)
    g.add_color_stop_rgb(1, 0.07, 0.07, 0.09)
    cr.set_source(g)
    cr.fill()
    # the notch
    gfx.notch_path(cr, 58, 16, 140, 62, 10, 26)
    cr.set_source_rgb(0, 0, 0)
    cr.fill()
    # spectrum bars inside the notch
    colors = [(0.04, 0.52, 1.0), (0.75, 0.35, 0.95), (1.0, 0.27, 0.23), (0.0, 0.78, 0.75)]
    for i, h in enumerate((16, 30, 22, 12)):
        x = 106 + i * 12
        gfx.rounded_rect(cr, x, 46 - h / 2, 6, h, 3)
        cr.set_source_rgb(*colors[i])
        cr.fill()
    # the "D"
    gfx.draw_text(cr, "D", 0, 92, gfx.WHITE, 0.95, 120, "heavy", "rounded", width=256, align="center",
                  ellipsize=False)
    s.write_to_png(os.path.join(OUT, "dednotch.png"))


def tray_icon(size=22):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(s)
    gfx.notch_path(cr, 1, 4, 20, 9, 2, 4)
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    for i, h in enumerate((3, 5, 4)):
        gfx.rounded_rect(cr, 7 + i * 3.2, 17 - h / 2, 1.8, h, 0.9)
        cr.fill()
    s.write_to_png(os.path.join(OUT, "tray.png"))


GLYPH_W, GLYPH_H, OUTLINE = 45, 64, 5.5


def _outlined_glyph(name, shapes):
    """White glyph with a thick black outline (sits on top of the battery fill).

    `shapes` is a list of callables that each add one closed path to the context;
    all are outlined first, then filled, so overlapping parts merge cleanly.
    """
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, GLYPH_W, GLYPH_H)
    cr = cairo.Context(s)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for shape in shapes:
        shape(cr)
        cr.set_source_rgb(0, 0, 0)
        cr.set_line_width(OUTLINE * 2)
        cr.stroke()
    for shape in shapes:
        shape(cr)
        cr.set_source_rgb(1, 1, 1)
        cr.fill()
    s.write_to_png(os.path.join(OUT, name))


def bolt_icon():
    def bolt(cr):
        for i, (x, y) in enumerate([(29.5, 6.0), (8.5, 36.0), (21.0, 36.0), (16.0, 58.0),
                                    (37.0, 27.0), (24.5, 27.0)]):
            (cr.move_to if i == 0 else cr.line_to)(x, y)
        cr.close_path()
    _outlined_glyph("bolt.png", [bolt])


def plug_icon():
    def prong(x):
        return lambda cr: gfx.rounded_rect(cr, x, 6.0, 4.0, 13.0, 2.0)

    def body(cr):
        cr.move_to(8.0, 20.0)
        cr.line_to(37.0, 20.0)
        cr.line_to(37.0, 30.0)
        cr.curve_to(37.0, 37.0, 32.0, 42.0, 27.0, 43.0)
        cr.line_to(18.0, 43.0)
        cr.curve_to(13.0, 42.0, 8.0, 37.0, 8.0, 30.0)
        cr.close_path()

    def cable(cr):
        gfx.rounded_rect(cr, 19.0, 42.0, 7.0, 15.0, 2.5)

    _outlined_glyph("plug.png", [prong(14.0), prong(27.0), body, cable])


if __name__ == "__main__":
    app_icon()
    tray_icon()
    bolt_icon()
    plug_icon()
    print("icons written to", OUT)
