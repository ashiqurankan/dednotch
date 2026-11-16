"""DedNotch — a dynamic notch for the Linux desktop."""
import gi

for _ns, _ver in (("Gtk", "3.0"), ("Gdk", "3.0"), ("GdkPixbuf", "2.0"), ("Pango", "1.0"),
                  ("PangoCairo", "1.0")):
    gi.require_version(_ns, _ver)
