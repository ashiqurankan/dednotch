"""Shelf model: files, links and text snippets dropped on the notch."""
import hashlib
import json
import os
import urllib.parse
import uuid

from gi.repository import Gio, GLib, GdkPixbuf, Gtk

from .config import CACHE_DIR, DATA_DIR
from .services import Observable

SHELF_FILE = os.path.join(DATA_DIR, "shelf.json")
TEXT_DIR = os.path.join(CACHE_DIR, "shelf-text")


class ShelfItem:
    def __init__(self, kind, value, name=None, item_id=None):
        self.id = item_id or uuid.uuid4().hex
        self.kind = kind            # "file" | "link" | "text"
        self.value = value          # path | url | text
        self.name = name or self._default_name()
        self._thumb = None

    def _default_name(self):
        if self.kind == "file":
            return os.path.basename(self.value.rstrip("/")) or self.value
        if self.kind == "link":
            host = urllib.parse.urlparse(self.value).netloc
            return host or self.value
        first = self.value.strip().splitlines()[0] if self.value.strip() else "Text"
        return first[:60]

    def to_json(self):
        return {"id": self.id, "kind": self.kind, "value": self.value, "name": self.name}

    @property
    def path(self):
        if self.kind == "file":
            return self.value
        if self.kind == "text":
            os.makedirs(TEXT_DIR, exist_ok=True)
            path = os.path.join(TEXT_DIR, (self.name[:40].strip().replace("/", "_") or "Text") + ".txt")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self.value)
            return path
        return None

    @property
    def uri(self):
        if self.kind == "link":
            return self.value
        return GLib.filename_to_uri(self.path, None)

    def exists(self):
        return self.kind != "file" or os.path.exists(self.value)

    def thumbnail(self, size=56):
        if self._thumb is not None:
            return self._thumb or None
        pb = None
        try:
            if self.kind == "file":
                pb = _thumb_from_cache(self.value)
                if pb is None:
                    ctype, _ = Gio.content_type_guess(self.value, None)
                    if ctype and ctype.startswith("image/") and os.path.getsize(self.value) < 40_000_000:
                        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(self.value, 112, 112, True)
                if pb is None:
                    info = Gio.File.new_for_path(self.value).query_info(
                        "standard::icon", Gio.FileQueryInfoFlags.NONE, None)
                    pb = _icon_pixbuf(info.get_icon(), 112)
            elif self.kind == "link":
                pb = _icon_pixbuf(Gio.ThemedIcon.new_from_names(["text-html", "web-browser",
                                                                  "applications-internet"]), 112)
            else:
                pb = _icon_pixbuf(Gio.ThemedIcon.new_from_names(["text-x-generic"]), 112)
        except Exception:
            pb = None
        self._thumb = pb if pb is not None else False
        return pb


def _thumb_from_cache(path):
    uri = GLib.filename_to_uri(os.path.abspath(path), None)
    digest = hashlib.md5(uri.encode()).hexdigest()
    for size in ("x-large", "large", "normal"):
        candidate = os.path.join(GLib.get_user_cache_dir(), "thumbnails", size, digest + ".png")
        if os.path.exists(candidate) and os.path.getmtime(candidate) >= os.path.getmtime(path) - 1:
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(candidate, 112, 112, True)
            except GLib.Error:
                return None
    return None


def _icon_pixbuf(icon, size):
    theme = Gtk.IconTheme.get_default()
    info = theme.lookup_by_gicon(icon, size, Gtk.IconLookupFlags.FORCE_SIZE) if icon else None
    if info is None:
        info = theme.lookup_icon("text-x-generic", size, Gtk.IconLookupFlags.FORCE_SIZE)
    return info.load_icon() if info else None


class Shelf(Observable):
    def __init__(self):
        super().__init__()
        self.items = []
        self.selected = set()
        self._load()

    @property
    def empty(self):
        return not self.items

    def _load(self):
        try:
            with open(SHELF_FILE, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.items = [ShelfItem(r["kind"], r["value"], r.get("name"), r.get("id")) for r in raw]
        except (OSError, ValueError, KeyError):
            self.items = []
        self.cleanup_invalid()

    def save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(SHELF_FILE + ".tmp", "w", encoding="utf-8") as fh:
                json.dump([i.to_json() for i in self.items], fh, indent=1)
            os.replace(SHELF_FILE + ".tmp", SHELF_FILE)
        except OSError as exc:
            print("dednotch: could not save shelf:", exc)

    def cleanup_invalid(self):
        before = len(self.items)
        self.items = [i for i in self.items if i.exists()]
        if len(self.items) != before:
            self.save()
            self.emit("changed")

    def add_uris(self, uris):
        added = False
        known = {i.value for i in self.items}
        for uri in uris:
            uri = uri.strip()
            if not uri or uri.startswith("#"):
                continue
            if uri.startswith("file://"):
                try:
                    path = GLib.filename_from_uri(uri)[0]
                except GLib.Error:
                    continue
                if path in known:
                    continue
                self.items.append(ShelfItem("file", path))
            elif "://" in uri:
                if uri in known:
                    continue
                self.items.append(ShelfItem("link", uri))
            else:
                continue
            known.add(self.items[-1].value)
            added = True
        if added:
            self.save()
            self.emit("changed")
        return added

    def add_text(self, text):
        text = (text or "").strip()
        if not text:
            return False
        if "\n" not in text and text.startswith(("http://", "https://")):
            return self.add_uris([text])
        self.items.append(ShelfItem("text", text))
        self.save()
        self.emit("changed")
        return True

    def remove(self, ids):
        ids = set(ids)
        self.items = [i for i in self.items if i.id not in ids]
        self.selected -= ids
        self.save()
        self.emit("changed")

    def clear(self):
        self.items = []
        self.selected.clear()
        self.save()
        self.emit("changed")

    def get(self, item_id):
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def select(self, item_id, extend=False):
        if not extend:
            self.selected = {item_id}
        elif item_id in self.selected:
            self.selected.discard(item_id)
        else:
            self.selected.add(item_id)
        self.emit("selection")

    def selected_items(self):
        return [i for i in self.items if i.id in self.selected]
